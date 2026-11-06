"""启动序列（SDD §4.3.2）。

BOOTING → READY / DEGRADED / FATAL
分级原则：模型缺失、摄像头缺失 = 致命；配置损坏 = 非致命（退默认值）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, fields, is_dataclass

import numpy as np

from app.adaptation.camera.base import ICameraAdapter, frame_shape_error
from app.adaptation.device_io.base import IDeviceIO
from app.adaptation.runtime.base import IRuntimeAdapter
from app.logic_layer.config import AppConfig


def _builtin_defaults() -> dict:
    """从 AppConfig 的 dataclass 默认值导出内置默认配置（单一真源）。"""
    cfg = AppConfig()
    out: dict = {}
    for f in fields(cfg):
        value = getattr(cfg, f.name)
        if is_dataclass(value):
            out[f.name] = {k: getattr(value, k) for k in value.__dataclass_fields__}
        else:
            out[f.name] = value
    out.setdefault("indicate", {"gpio_pin": None})
    return out

# 镜像内置默认值：**由 AppConfig 的 dataclass 默认值导出**，不再手写第二份。
# （早期 DEFAULTS 与 config.py / configs/default.yaml 三处并存，模型路径各不相同，
#  开发机永远拿错路径。现在只有 config.py 一处写默认值。）
DEFAULTS: dict = _builtin_defaults()


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

    def bootstrap(self, overrides: dict | None = None) -> BootReport:
        """执行启动序列。

        Args:
            overrides: 命令行/调用侧的现场覆盖（如 {"runtime": {"model_path": ...}}），
                       在内置默认值与持久配置**之后**合入，因此必须在首次
                       load 之前生效——早期版本把 --model 留在 bootstrap 之后赋值，
                       对首次加载是死标志。
        """
        t0 = time.time()
        warnings: list[str] = []

        # STEP 1 读配置（失败 → 内置默认值，非致命）
        cfg = self._merge_config(overrides)

        # STEP 2 日志已由 IDeviceIO 初始化（构造时），失败已降级 stdout
        self._io.log("INFO", "BOOTING: 启动序列开始")

        # STEP 3 平台探测与装配已由 factory 在构造前完成，此处仅记录
        # STEP 4 加载模型 + warmup（致命）
        # 故异常按类型分类，不再一律贴 E-WEIGHT-NOT-FOUND 标签
        # ——实测：一次 TypeError（后端实现 bug）被报成“权重找不到”，直接带偏排障方向。
        model_path = cfg["runtime"]["model_path"]
        try:
            self._runtime.load(model_path)
            self._runtime.warmup()
            self._io.log("INFO", f"模型加载与预热完成: {model_path}")
        except FileNotFoundError as e:
            return self._fatal(f"E-WEIGHT-NOT-FOUND: {e}", t0, cfg)
        except Exception as e:
            # 已带 E-* 分类码的后端异常原样上抛；其余归为后端故障
            code = str(e) if str(e).startswith("E-") else f"E-RUNTIME-FAIL: {type(e).__name__}"
            return self._fatal(f"{code} / 加载失败: {e}", t0, cfg)

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
            # 校像素而不只校“非空”：早先只判 data.size == 0，而形状坏掉的帧
            # （实测 (480,1,1)）能一路过自检、到真正推理才炸，把恢复引到
            # “重载模型”这个无关方向上。报的尺寸也从 data 取，不信元数据。
            err = frame_shape_error(frame)
            if err:
                raise RuntimeError(err)
            self._io.log("INFO",
                         "连通性自检通过: "
                         f"{frame.data.shape[1]}x{frame.data.shape[0]}x3")
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
    def _merge_config(self, overrides: dict | None = None) -> dict:
        """镜像内置默认值 → 现场持久配置 → 调用侧覆盖，逐层深合。"""
        cfg: dict = {k: dict(v) if isinstance(v, dict) else v for k, v in DEFAULTS.items()}
        for layer in (self._io.read_config() or {}, overrides or {}):
            for section, values in layer.items():
                if isinstance(values, dict) and isinstance(cfg.get(section), dict):
                    cfg[section].update(values)
                else:
                    cfg[section] = values
        return cfg

    def _fatal(self, reason: str, t0: float, cfg: dict) -> BootReport:
        self._io.log("ERROR", f"FATAL: {reason}")
        return BootReport(status="FATAL", fatal_reason=reason,
                          elapsed_ms=(time.time() - t0) * 1000, config=cfg)
