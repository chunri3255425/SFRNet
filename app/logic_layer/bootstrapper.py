"""启动序列（SDD §4.3.2）。

BOOTING → READY / DEGRADED / FATAL
分级原则：模型缺失、摄像头缺失 = 致命；配置损坏 = 非致命（退默认值）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np

from app.adaptation.camera.base import ICameraAdapter
from app.adaptation.device_io.base import IDeviceIO
from app.adaptation.runtime.base import IRuntimeAdapter

# 镜像内置默认值（配置完全失效时的兜底）
DEFAULTS = {
    "camera": {"device": "/dev/video0", "width": 3840, "height": 2160, "fps": 30},
    "runtime": {"backend": "auto", "model_path": "assets/weights/model.rknn"},
    "inference": {"tile_size": 512, "stride": 256, "threshold": 0.5,
                  "coverage_threshold": 0.01},
    "relay": {"enabled": False, "host": "192.168.1.100", "port": 9000,
              "interval_frames": 6, "jpeg_quality": 75, "downscale": 1080,
              "overlay_result": True},
    "indicate": {"gpio_pin": None},
}


@dataclass
class BootReport:
    status: str                     # READY | DEGRADED | FATAL
    fatal_reason: str = ""
    warnings: list[str] = field(default_factory=list)
    elapsed_ms: float = 0.0
    config: dict = field(default_factory=dict)


class Bootstrapper:
    """按固定顺序执行 7 步启动序列，失败立即判定致命/非致命，不静默跳过。"""

    def __init__(self, camera: ICameraAdapter, runtime: IRuntimeAdapter, io: IDeviceIO):
        self._camera = camera
        self._runtime = runtime
        self._io = io

    def bootstrap(self) -> BootReport:
        t0 = time.time()
        warnings: list[str] = []

        # STEP 1 读配置（失败 → 内置默认值，非致命）
        cfg = self._merge_config()

        # STEP 2 日志已由 IDeviceIO 初始化（构造时），失败已降级 stdout
        self._io.log("INFO", "BOOTING: 启动序列开始")

        # STEP 3 平台探测与装配已由 factory 在构造前完成，此处仅记录
        # STEP 4 加载模型 + warmup（致命）
        model_path = cfg["runtime"]["model_path"]
        try:
            self._runtime.load(model_path)
            self._runtime.warmup()
            self._io.log("INFO", f"模型加载与预热完成: {model_path}")
        except Exception as e:
            return self._fatal(f"E-WEIGHT-NOT-FOUND / 加载失败: {e}", t0, cfg)

        # STEP 5 打开摄像头（致命，重试 3 次）
        cam_cfg = cfg["camera"]
        ok = False
        for attempt in range(1, 4):
            try:
                self._camera.open(**cam_cfg)
                ok = True
                break
            except Exception as e:
                self._io.log("WARN", f"摄像头打开失败（第 {attempt}/3 次）: {e}")
                time.sleep(2.0)
        if not ok:
            return self._fatal("camera_open_fail×3", t0, cfg)

        # STEP 6 连通性自检（非致命：可能摄像头预热中）
        try:
            frame = self._camera.grab(timeout_ms=3000)
            if frame.data.size == 0:
                raise RuntimeError("空帧")
            self._io.log("INFO", f"连通性自检通过: {frame.width}x{frame.height}")
        except Exception as e:
            warnings.append("self_check_failed")
            self._io.log("WARN", f"连通性自检失败（允许继续）: {e}")

        # STEP 7 完成
        elapsed = (time.time() - t0) * 1000
        status = "DEGRADED" if warnings else "READY"
        self._io.log("INFO", f"BOOTING 完成: {status}, 耗时 {elapsed:.0f}ms")
        return BootReport(status=status, warnings=warnings,
                          elapsed_ms=elapsed, config=cfg)

    # ── 内部 ─────────────────────────────────
    def _merge_config(self) -> dict:
        """镜像内置默认值 → 现场覆盖（全有或全无）。"""
        cfg: dict = {k: dict(v) if isinstance(v, dict) else v for k, v in DEFAULTS.items()}
        override = self._io.read_config() or {}
        for section, values in override.items():
            if isinstance(values, dict) and isinstance(cfg.get(section), dict):
                cfg[section].update(values)
            else:
                cfg[section] = values
        return cfg

    def _fatal(self, reason: str, t0: float, cfg: dict) -> BootReport:
        self._io.log("ERROR", f"FATAL: {reason}")
        return BootReport(status="FATAL", fatal_reason=reason,
                          elapsed_ms=(time.time() - t0) * 1000, config=cfg)
