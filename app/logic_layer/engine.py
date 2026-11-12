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
import traceback
from collections.abc import Sequence
from dataclasses import dataclass, field

import cv2
import numpy as np

from app.adaptation.base import DetectionResultDTO, IResultSink
from app.adaptation.camera.base import Frame, ICameraAdapter, frame_shape_error
from app.adaptation.device_io.base import IDeviceIO
from app.adaptation.relay.base import IFrameRelay
from app.adaptation.runtime.base import IRuntimeAdapter
from app.logic_layer.bootstrapper import Bootstrapper, DEFAULTS
from app.logic_layer.decider import Decider
from app.model_layer.detectors.yolo_detector import YoloGateError

TILE = 512
ALIGN = 32                      # 模型最深下采样 8 × 四象限 2×2 → 边长必须是 32 的倍数
RETRY_CAPTURE_MAX = 5           # C-10
RETRY_INFER_MAX = 3             # C-11
BAD_FRAME_STREAK_MAX = 10       # C-12
RETRY_UNKNOWN_MAX = 3           # CONTROL_LOGIC §6.1 “未知异常”行
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
    has_moire: bool | None = None   # None = 无有效结论（BOOTING/坏帧）；v2.4.1 起为派生兼容字段
    result: str | None = None       # 三态对外承诺：screen_detected / has_moire / no_moire
    stage1_conf: float | None = None  # Stage-1 最高置信度（仅级联开启时有意义，诊断用）
    coverage: float = 0.0
    elapsed_ms: float = 0.0
    frame_id: str = ""
    tiles: int = 0
    max_prob: float = 0.0
    mean_prob: float = 0.0        # 仅当有广播订阅者时由 Decider 计算
    warnings: list[str] = field(default_factory=list)


class MoireEngineError(RuntimeError):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind            # capture | infer | bad_frame


class DetectionEngine:
    """状态机 + 单帧检测 + 常驻循环。"""

    def __init__(self, camera: ICameraAdapter, runtime: IRuntimeAdapter,
                 io: IDeviceIO, relay: IFrameRelay | None = None,
                 sinks: Sequence[IResultSink] | None = None,
                 yolo_gate: "object | None" = None):
        """Args:
            sinks: 逐帧判定的广播出口（HTTP-SSE、串口、MQTT 皆为 IResultSink
                   实现）。由组合根注入，引擎不关心具体协议；空元组时广播
                   零成本（不构造 DTO、不做额外归约）。
            yolo_gate: [v2.4.1] Stage-1 门控（YoloScreenGate），None=级联禁用
                       （行为与 v2.3 完全一致）。由组合根按 cascade 配置装配，
                       加载失败不致命（P3）。
        """
        self._camera = camera
        self._runtime = runtime
        self._io = io
        self._relay = relay
        self._gate = yolo_gate
        self._sinks: tuple[IResultSink, ...] = tuple(sinks or ())
        self._sink_fail = 0
        self._decider = Decider()
        self.state = "BOOTING"
        self._stop_event = threading.Event()
        self._retry = {"capture": 0, "infer": 0, "unknown": 0}
        self._fatal_reason = ""
        self._relay_started = False
        self._bad_frame_streak = 0
        self._last_valid: DetectionResult | None = None
        self._frame_seq = 0
        self.report: "Bootstrapper.BootReport | None" = None

    # ── 生命周期 ─────────────────────────────
    def bootstrap(self, overrides: dict | None = None) -> bool:
        """上电初始化，返回是否达到可用状态（READY/DEGRADED）。

        Args:
            overrides: 调用侧配置覆盖（如 {"runtime": {"model_path": ...}}），
                       由组合根从命令行传入，在首次加载前生效。
        """
        report = Bootstrapper(self._camera, self._runtime, self._io).bootstrap(overrides)
        self.report = report
        self.state = report.status
        if report.status == "FATAL":
            return False
        # 画面回传默认关闭（R7），按配置启用
        relay_cfg = report.config.get("relay", DEFAULTS["relay"])
        if relay_cfg.get("enabled") and self._relay is not None and not self._relay_started:
            # 守住“已启”标记：未知异常恢复会重跑 bootstrap（回 STEP 4），再调一次
            # relay.start 会再多起一个发送线程、旧连接无人回收。
            try:
                self._relay.start(relay_cfg["host"], relay_cfg["port"],
                                  interval_frames=relay_cfg.get("interval_frames", 6),
                                  jpeg_quality=relay_cfg.get("jpeg_quality", 75),
                                  downscale=relay_cfg.get("downscale", 1080),
                                  overlay_result=relay_cfg.get("overlay_result", True))
                self._relay_started = True
                self._io.log("INFO", f"画面回传已启用: {relay_cfg['host']}:{relay_cfg['port']}")
            except Exception as e:
                self._io.log("WARN", f"画面回传启动失败（旁路，仅告警）: {e}")
                report.warnings.append("relay_start_failed")
        return True

    @property
    def fatal_reason(self) -> str:
        """本次运行是否以 FATAL 收摊（原因文本，空串 = 未触发）。

        必须单独存：run_forever 跳出循环后总会走 shutdown()，而它把 state 写成
        SHUTDOWN，光看状态名分不出“人工停服务”和“恢复超限”。退出码要靠它。
        """
        return self._fatal_reason

    def attach_sink(self, sink: IResultSink) -> None:
        """构造之后再挂一个广播出口。

        组合根需要它来解开一个引用环：HTTP 服务要拿引擎做 /status，而引擎
        又要拿 HTTP 服务做广播，两者互相引用，必须有一个先起。给它一个显式
        挂载口，而不是让调用侧去改 `_sinks` 私有属性。
        """
        self._sinks = self._sinks + (sink,)

    def engine_state(self) -> str:
        """IDetectionEngine 协议实现：只读状态名，供网络出口 /status 采集。

        加这个方法是为了让常驻引擎能直接挂进 HTTP 出口，而无需另搭
        一层 facade（它不访问任何内部可变结构，也就不引入新的竞态）。
        """
        return self.state

    def run_forever(self, req: DetectionRequest | None = None,
                    max_frames: int = 0) -> None:
        """检测循环（SDD §4.3.7 骨架）。

        Args:
            req: 逐帧参数
            max_frames: >0 时跑满 N 帧就退出（0=直到 stop()/FATAL）。限帧与常驻
                共用同一个循环体，所以 `--frames N` 也一样会广播结果、也会走
                C-10~C-13 错误恢复与 DEGRADED 恢复。以前由调用侧手写 for 循环直接
                调 detect_once，限帧运行既不推 SSE，也不复位重试计数。
        """
        req = req or DetectionRequest()
        n = 0
        while not self._stop_event.is_set():
            if self.state in ("FATAL", "SHUTDOWN"):
                break
            if max_frames and n >= max_frames:
                break
            n += 1
            t0 = time.time()
            try:
                result = self.detect_once(req)
                self._publish(result)
                self._retry["capture"] = self._retry["infer"] = self._retry["unknown"] = 0
                self._bad_frame_streak = 0
                if self.state == "DEGRADED":
                    self.state = "READY"
            except MoireEngineError as e:
                self._handle_error(e)
            except Exception as e:                     # 未知异常
                # 日志在 _handle_error 里统一记（带堆栈、有上限）：早期这里
                # 先无条件 ERROR 一行再降级，配合 interval_ms=0 就是无限刷屏。
                self._handle_error(MoireEngineError("unknown", repr(e)))
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
            self._relay_started = False
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

        # 1. 取帧（超时 3000ms）。§6.1 把“grab 超时/异常”归为取帧失败走 C-10，
        #    所以适配器抛的原生异常必须在此换成 kind="capture"：否则它会被上面
        #    兜底成 unknown，走不到“重开摄像头”而是去重新初始化，且重试上限从 5
        #    变成 3（fake 相机的 fail_after 注入实测跑偏过）。
        try:
            frame = self._camera.grab(timeout_ms=GRAB_TIMEOUT_MS)
        except MoireEngineError:
            raise
        except Exception as e:
            raise MoireEngineError("capture", repr(e)) from e
        self._frame_seq += 1
        result.frame_id = frame.frame_id

        # 1b. 坏帧闸（§5.3）：形状不合法归 bad_frame，不归 capture/infer。
        #     它是“这一帧的像素坏了”，重开摄像头和重载模型都救不了；而代价只是
        #     三次 shape 比较（纳秒级），相对单帧推理的百毫秒量级可以忽略。
        bad = frame_shape_error(frame)
        if bad:
            raise MoireEngineError("bad_frame", bad)

        # 2. [v2.4.1] 级联门控 Stage-1（SDD §3.3 / §4.3.2 步骤 3.5）：
        #    有屏幕（含灰区 prefer_screen）→ 短路出 screen_detected，不跑 SFRNet；
        #    门控失败 → P3 降级：本会话内禁用级联，回退全图路径（不 FATAL）。
        gate_conf: float | None = None
        if self._gate is not None:
            try:
                gate_conf = self._gate.screen_conf_max(frame.data)
            except YoloGateError as e:
                self._gate = None
                result.warnings.append("cascade_disabled")
                self._io.log("WARN", f"Stage-1 门控失败，禁用级联回退全图路径（P3）: {e}")
            else:
                if self._gate.screened(gate_conf):
                    result.result = "screen_detected"
                    result.has_moire = False        # 派生兼容：screen_detected ≠ 摩尔纹
                    result.stage1_conf = gate_conf
                    result.elapsed_ms = (time.time() - t0) * 1000
                    self._last_valid = result
                    if self._relay is not None:
                        try:
                            self._relay.push(frame, result)
                        except Exception:
                            pass                    # R2：旁路非阻塞
                    self._io.indicate(result.result)
                    self._io.log("INFO",
                                 f"frame={result.frame_id} result=screen_detected "
                                 f"stage1_conf={gate_conf:.3f} total_ms={result.elapsed_ms:.0f}")
                    self.state = "READY"
                    return result

        # 3. 尺寸准入 C-01：对齐 32 倍数（策略 A：零信息损失填充）
        padded, (pad_h, pad_w) = self._align(frame.data)

        # 4. 分块决策 C-02 + 逐块推理 + 融合（同上：前向异常归 C-11）
        try:
            logits = self._infer_padded(padded, req.tile_size)
        except MoireEngineError:
            raise
        except Exception as e:
            raise MoireEngineError("infer", repr(e)) from e

        # 5. 裁回原尺寸 + 判定（唯一一次 sigmoid 在 Decider）
        if pad_h or pad_w:
            logits = logits[: frame.data.shape[0], : frame.data.shape[1]]
        # 双门限按**本次请求**生效：早期实现只用构造期的 Decider()，
        # req.threshold / req.coverage_threshold 被静默忽略。
        if (req.threshold, req.coverage_threshold) != (self._decider.threshold,
                                                       self._decider.coverage_threshold):
            self._decider = Decider(req.threshold, req.coverage_threshold)
        decision = self._decider.decide(logits, want_mean=bool(self._sinks))

        result.has_moire = decision.has_moire
        result.result = "has_moire" if decision.has_moire else "no_moire"
        result.stage1_conf = gate_conf
        result.coverage = decision.coverage
        result.max_prob = decision.prob_max
        result.mean_prob = decision.prob_mean
        result.tiles = self._last_tiles
        result.elapsed_ms = (time.time() - t0) * 1000
        self._last_valid = result

        # 6. 旁路：画面回传（非阻塞，失败不致命 R2/R5）
        if self._relay is not None:
            try:
                self._relay.push(frame, result)
            except Exception:
                pass

        # 7. 指示 + 日志一行（附录 B；v2.4.1 三态透传，物理映射见 device_io.base）
        self._io.indicate(result.result)
        self._io.log("INFO",
                     f"frame={result.frame_id} result={result.result} "
                     f"coverage={result.coverage:.6f} tiles={result.tiles} "
                     f"total_ms={result.elapsed_ms:.0f}")
        self.state = "READY"        # 恢复状态机（否则下一帧入口检查必然 busy）
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
            if self._bad_frame_streak == BAD_FRAME_STREAK_MAX + 1:
                # 只在跨阈值时告警一次：以前是 “> 上限” 就每帧刷一行，全速循环下
                # 同样是日志洪水（与板端 flash 寿命直接相关）。
                self._io.log("WARN", f"连续 {self._bad_frame_streak} 帧坏帧，DEGRADED 持续告警")
                self.state = "DEGRADED"
            # 沿用最近一次有效结果（T13），不跳变

        elif err.kind == "busy":
            # 并发请求撞进 DETECTING：这不是故障，不计数、不重启（为了一个调用
            # 时序问题把整个引擎拆掉重新初始化，代价方向错了）。调用方自行退避。
            self._io.log("WARN", f"检测请求被拒（引擎忙）: {err}")

        else:                                        # 未知异常（CONTROL_LOGIC §6.1）
            self._retry["unknown"] += 1
            n = self._retry["unknown"]
            if n > RETRY_UNKNOWN_MAX:
                self._fatal(f"unknown 恢复超限（{RETRY_UNKNOWN_MAX} 次）: {err}")
                return
            # 记一次完整堆栈（而不是每帧一行无上下文 ERROR），然后退避、重新初始化
            self._io.log("ERROR",
                         f"未知异常（第 {n}/{RETRY_UNKNOWN_MAX} 次）{err}，重新初始化:\n"
                         f"{traceback.format_exc().rstrip()}")
            time.sleep(2.0)                          # §6.1：固定 2s，不用指数退避
            if not self.bootstrap(self.report.config if self.report else None):
                self._fatal("未知异常后重新初始化失败: " + (self.report.fatal_reason
                                                          if self.report else ""))
            else:
                self.state = "DEGRADED"              # 恢复后仍需一次成功帧才回 READY

    def _fatal(self, reason: str) -> None:
        self.state = "FATAL"
        self._fatal_reason = reason          # run_forever 会走 shutdown() 覆盖 state，
                                             # 靠它才能把“正常收摊”和“恢复超限”区分开
        self._io.log("ERROR", f"FATAL: {reason}")
        # FATAL：布尔固定 False（CONTROL_LOGIC §1.1）
        try:
            self._io.indicate(False)
        except Exception as e:
            # 指示器本约定“不抛”（两个实现都自行降级），但这是停机路径：为了一个
            # 写坏的 GPIO 把 shutdown 打断、资源不释放，代价不对等。
            self._io.log("WARN", f"致命路径指示失败（已忽略）: {e!r}")

    def _publish(self, result: DetectionResult) -> None:
        # DEGRADED 沿用旧值 + 警告标记；正常帧刷新
        self._last_valid = result
        if not self._sinks:
            return                      # 零成本：没人订阅就不造 DTO
        # 热路径只携带标量：mask_png/prob_png 保持 None。4K 图 PNG 编码是百毫秒
        # 级且会分配大块内存，需要图像的客户端走 /detect 的 X-With-Mask 显式 opt-in。
        dto = DetectionResultDTO(
            source_id=result.frame_id,
            has_moire=bool(result.has_moire),
            coverage=result.coverage,
            max_prob=result.max_prob,
            mean_prob=result.mean_prob,
            elapsed_ms=result.elapsed_ms,
            warnings=tuple(result.warnings),
            ts=time.time(),
        )
        for sink in self._sinks:
            try:
                sink.publish(dto)
            except Exception as e:
                # 广播面是旁路：它挂了就掉线，绝不允许拖死检测循环。
                self._sink_fail += 1
                if self._sink_fail <= 3:        # 只告警前 3 次，防爆日志
                    self._io.log("WARN", f"结果广播失败（旁路，已忽略）: {e!r}")

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
