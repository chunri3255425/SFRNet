"""DetectionEngine — 板端检测引擎（状态机主体 + 单帧检测 + 常驻循环 + 错误恢复）。

实现 SDD §4.3.1~§4.3.7 与 CONTROL_LOGIC 的关键规则：
  C-01 尺寸准入：对齐到 32 的倍数（reflect/replicate 自适应，§4.2）
  C-02 分块决策：单块 / 一维滑窗 / 二维滑窗（plan() 短维兜底，修复 D-01b）
  C-06 全流程恰好一次 sigmoid（在 Decider 内）
  C-10~C-13 取帧重试 5 次 / 推理重试 3 次 / 坏帧连续 10 帧 / 超时预算
  R1/R2   画面回传非阻塞旁路

推理后端只依赖 IRuntimeAdapter（numpy 进 / numpy 出），不 import torch。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

import cv2
import numpy as np

from app.adaptation.camera.base import Frame, ICameraAdapter
from app.adaptation.device_io.base import IDeviceIO
from app.adaptation.relay.base import IFrameRelay
from app.adaptation.runtime.base import IRuntimeAdapter
from app.logic_layer.bootstrapper import Bootstrapper, DEFAULTS
from app.logic_layer.decider import Decider

TILE = 512
ALIGN = 32                      # 模型最深下采样 8 × 四象限 2×2 → 边长必须是 32 的倍数
RETRY_CAPTURE_MAX = 5           # C-10
RETRY_INFER_MAX = 3             # C-11
BAD_FRAME_STREAK_MAX = 10       # C-12
GRAB_TIMEOUT_MS = 3000          # C-13
INFER_TIMEOUT_MS = 5000

VALID_STATES = ("BOOTING", "READY", "DETECTING", "DEGRADED", "FATAL", "SHUTDOWN")


@dataclass
class DetectionRequest:
    threshold: float = 0.5
    coverage_threshold: float = 0.01
    interval_ms: int = 0            # 0 = 连续全速
    tile_size: int = TILE


@dataclass
class DetectionResult:
    has_moire: bool | None = None   # None = 无有效结论（BOOTING/坏帧）
    coverage: float = 0.0
    elapsed_ms: float = 0.0
    frame_id: str = ""
    tiles: int = 0
    warnings: list[str] = field(default_factory=list)


class MoireEngineError(RuntimeError):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind            # capture | infer | bad_frame


class DetectionEngine:
    """状态机 + 单帧检测 + 常驻循环。"""

    def __init__(self, camera: ICameraAdapter, runtime: IRuntimeAdapter,
                 io: IDeviceIO, relay: IFrameRelay | None = None):
        self._camera = camera
        self._runtime = runtime
        self._io = io
        self._relay = relay
        self._decider = Decider()
        self.state = "BOOTING"
        self._stop_event = threading.Event()
        self._retry = {"capture": 0, "infer": 0}
        self._bad_frame_streak = 0
        self._last_valid: DetectionResult | None = None
        self._frame_seq = 0
        self.report: "Bootstrapper.BootReport | None" = None

    # ── 生命周期 ─────────────────────────────
    def bootstrap(self) -> bool:
        """上电初始化，返回是否达到可用状态（READY/DEGRADED）。"""
        report = Bootstrapper(self._camera, self._runtime, self._io).bootstrap()
        self.report = report
        self.state = report.status
        if report.status == "FATAL":
            return False
        # 画面回传默认关闭（R7），按配置启用
        relay_cfg = report.config.get("relay", DEFAULTS["relay"])
        if relay_cfg.get("enabled") and self._relay is not None:
            try:
                self._relay.start(relay_cfg["host"], relay_cfg["port"],
                                  interval_frames=relay_cfg.get("interval_frames", 6),
                                  jpeg_quality=relay_cfg.get("jpeg_quality", 75),
                                  downscale=relay_cfg.get("downscale", 1080),
                                  overlay_result=relay_cfg.get("overlay_result", True))
                self._io.log("INFO", f"画面回传已启用: {relay_cfg['host']}:{relay_cfg['port']}")
            except Exception as e:
                self._io.log("WARN", f"画面回传启动失败（旁路，仅告警）: {e}")
                report.warnings.append("relay_start_failed")
        return True

    def run_forever(self, req: DetectionRequest | None = None) -> None:
        """常驻检测循环（SDD §4.3.7 骨架）。"""
        req = req or DetectionRequest()
        while not self._stop_event.is_set():
            if self.state in ("FATAL", "SHUTDOWN"):
                break
            t0 = time.time()
            try:
                result = self.detect_once(req)
                self._publish(result)
                self._retry["capture"] = self._retry["infer"] = 0
                self._bad_frame_streak = 0
                if self.state == "DEGRADED":
                    self.state = "READY"
            except MoireEngineError as e:
                self._handle_error(e)
            except Exception as e:                     # 未知异常
                self._io.log("ERROR", f"未知异常: {e!r}")
                self._handle_error(MoireEngineError("unknown", str(e)))
            if req.interval_ms > 0:
                remaining = req.interval_ms / 1000 - (time.time() - t0)
                if remaining > 0:
                    time.sleep(remaining)
        self.shutdown()

    def shutdown(self) -> None:
        """优雅退出：停收新帧 → 等当前帧 → 按序释放 → 退出码 0。"""
        if self.state == "SHUTDOWN":
            return
        self._stop_event.set()
        self._io.log("INFO", "SHUTDOWN: 释放资源")
        if self._relay is not None:
            self._relay.stop()
        try:
            self._camera.close()
        except Exception:
            pass
        try:
            self._runtime.unload()
        except Exception:
            pass
        self.state = "SHUTDOWN"

    def stop(self) -> None:
        self._stop_event.set()

    # ── 单帧检测（SDD §4.3.3）─────────────────
    def detect_once(self, req: DetectionRequest) -> DetectionResult:
        if self.state not in ("READY", "DEGRADED"):
            raise MoireEngineError("busy", f"状态 {self.state} 不接受检测请求")
        self.state = "DETECTING"
        t0 = time.time()
        result = DetectionResult()

        # 1. 取帧（超时 3000ms）
        frame = self._camera.grab(timeout_ms=GRAB_TIMEOUT_MS)
        self._frame_seq += 1
        result.frame_id = frame.frame_id

        # 2. 尺寸准入 C-01：对齐 32 倍数（策略 A：零信息损失填充）
        padded, (pad_h, pad_w) = self._align(frame.data)

        # 3. 分块决策 C-02 + 逐块推理 + 融合
        logits = self._infer_padded(padded, req.tile_size)

        # 4. 裁回原尺寸 + 判定（唯一一次 sigmoid 在 Decider）
        if pad_h or pad_w:
            logits = logits[: frame.data.shape[0], : frame.data.shape[1]]
        decision = self._decider.decide(logits)

        result.has_moire = decision.has_moire
        result.coverage = decision.coverage
        result.tiles = self._last_tiles
        result.elapsed_ms = (time.time() - t0) * 1000
        self._last_valid = result

        # 5. 旁路：画面回传（非阻塞，失败不致命 R2/R5）
        if self._relay is not None:
            try:
                self._relay.push(frame, result)
            except Exception:
                pass

        # 6. 指示 + 日志一行（附录 B）
        self._io.indicate(decision.has_moire)
        self._io.log("INFO",
                     f"frame={result.frame_id} has_moire={int(result.has_moire)} "
                     f"coverage={result.coverage:.6f} tiles={result.tiles} "
                     f"total_ms={result.elapsed_ms:.0f}")
        return result

    # ── 错误恢复（SDD §4.3.5 / CONTROL_LOGIC §6）──
    def _handle_error(self, err: MoireEngineError) -> None:
        if err.kind == "capture":
            self._retry["capture"] += 1
            if self._retry["capture"] > RETRY_CAPTURE_MAX:
                self._fatal("camera 恢复超限")
                return
            self._io.log("WARN", f"取帧失败，重开摄像头（{self._retry['capture']}/{RETRY_CAPTURE_MAX}）")
            time.sleep(2.0)
            try:
                self._camera.close()
                self._camera.open()
            except Exception as e:
                self._io.log("WARN", f"摄像头重开失败: {e}")
            self.state = "DEGRADED"

        elif err.kind == "infer":
            self._retry["infer"] += 1
            if self._retry["infer"] > RETRY_INFER_MAX:
                self._fatal("推理恢复超限")
                return
            self._io.log("WARN", f"推理失败，重载模型（{self._retry['infer']}/{RETRY_INFER_MAX}）")
            time.sleep(2.0)
            try:
                self._runtime.unload()
                self._runtime.load(self.report.config["runtime"]["model_path"]
                                   if self.report else DEFAULTS["runtime"]["model_path"])
                self._runtime.warmup()
            except Exception as e:
                self._io.log("WARN", f"模型重载失败: {e}")
            self.state = "DEGRADED"

        elif err.kind == "bad_frame":
            self._bad_frame_streak += 1
            if self._bad_frame_streak > BAD_FRAME_STREAK_MAX:
                self._io.log("WARN", f"连续坏帧 {self._bad_frame_streak}，DEGRADED 持续告警")
                self.state = "DEGRADED"
            # 沿用最近一次有效结果（T13），不跳变
        else:
            self.state = "DEGRADED"

    def _fatal(self, reason: str) -> None:
        self.state = "FATAL"
        self._io.log("ERROR", f"FATAL: {reason}")
        # FATAL：布尔固定 False（CONTROL_LOGIC §1.1）
        self._io.indicate(False)

    def _publish(self, result: DetectionResult) -> None:
        # DEGRADED 沿用旧值 + 警告标记；正常帧刷新
        self._last_valid = result

    # ── 尺寸准入 C-01 / §4.2 ──────────────────
    @staticmethod
    def _align(img: np.ndarray) -> tuple[np.ndarray, tuple[int, int]]:
        """反射/复制填充到最近的 32 的倍数。返回 (padded, (pad_h, pad_w))。"""
        h, w = img.shape[:2]
        new_h = max(ALIGN, -(-h // ALIGN) * ALIGN)
        new_w = max(ALIGN, -(-w // ALIGN) * ALIGN)
        pad_h, pad_w = new_h - h, new_w - w
        if pad_h == 0 and pad_w == 0:
            return img, (0, 0)
        mode = "reflect" if (pad_h < h and pad_w < w) else "replicate"   # §4.2 实测边界
        return cv2.copyMakeBorder(img, 0, pad_h, 0, pad_w, cv2.BORDER_REPLICATE) \
            if mode == "replicate" else \
            cv2.copyMakeBorder(img, 0, pad_h, 0, pad_w, cv2.BORDER_REFLECT), (pad_h, pad_w)

    # ── 分块 + 前向 + 融合（logits 域，C-05 余弦窗）──
    def _infer_padded(self, img: np.ndarray, tile: int) -> np.ndarray:
        h, w = img.shape[:2]
        rows = DetectionEngine._axis(h, tile, tile // 2)
        cols = DetectionEngine._axis(w, tile, tile // 2)
        num = np.zeros((h, w), dtype=np.float64)
        den = np.zeros((h, w), dtype=np.float64)
        window = DetectionEngine._hann2d(tile)
        tiles = 0
        for y in rows:
            for x in cols:
                patch = img[y:y + tile, x:x + tile]
                if patch.shape[0] < tile or patch.shape[1] < tile:
                    patch, _ = DetectionEngine._align(patch)
                patch = patch.astype(np.float32) / 255.0
                tensor = np.ascontiguousarray(patch.transpose(2, 0, 1))[None]
                out = self._runtime.infer(tensor)[0, 0]
                ph, pw = patch.shape[:2]
                if out.shape != (ph, pw):          # 后端输出与 patch 同尺寸；兜底裁剪
                    out = out[:ph, :pw]
                # 权重窗口按实际 patch 尺寸取（单块小图路径 patch < tile）
                win = window[:ph, :pw] if (ph, pw) == (tile, tile) else \
                    DetectionEngine._hann2d(min(ph, pw))[:ph, :pw] \
                    if ph == pw else np.maximum(
                        np.outer(np.hanning(ph + 2)[1:-1], np.hanning(pw + 2)[1:-1]), 1e-6)
                num[y:y + ph, x:x + pw] += out * win
                den[y:y + ph, x:x + pw] += win
                tiles += 1
                if self._stop_event.is_set():
                    raise MoireEngineError("bad_frame", "cancelled")
        self._last_tiles = tiles
        return (num / np.maximum(den, 1e-8))[:h, :w]

    @staticmethod
    def _axis(full: int, tile: int, stride: int) -> list[int]:
        """坐标生成（修复 D-01b：短维不足补 [0]）。"""
        if full <= tile:
            return [0]
        coords, pos = [], 0
        while pos + tile <= full:
            coords.append(pos)
            pos += stride
        if coords[-1] + tile < full:
            coords.append(full - tile)     # 尾块贴边
        return sorted(set(coords))

    @staticmethod
    def _hann2d(size: int) -> np.ndarray:
        w1 = np.hanning(size + 2)[1:-1]
        w2 = np.outer(w1, w1)
        return np.maximum(w2, 1e-6)
