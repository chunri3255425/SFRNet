"""适配层平台探测与实现装配。

★ 全项目唯一允许出现平台分支的地方（SDD §6 风险 #3）。
"""

from __future__ import annotations

import sys

from app.adaptation.camera.base import ICameraAdapter
from app.adaptation.device_io.base import IDeviceIO
from app.adaptation.relay.base import IFrameRelay
from app.adaptation.runtime.base import IRuntimeAdapter


def detect_platform() -> str:
    """返回 'linux' | 'ohos' | 'host'（开发机）。"""
    if sys.platform == "linux":
        # OpenHarmony 用户态仍可能报 linux；用 OH 特征文件二次判别
        from pathlib import Path
        if Path("/etc/ohos_release").exists() or Path("/system/etc").exists():
            return "ohos"
        return "linux"
    return "host"


def make_camera(platform: str | None = None, **kwargs) -> ICameraAdapter:
    platform = platform or detect_platform()
    if platform == "linux":
        from app.adaptation.camera.v4l2 import V4L2CameraAdapter
        return V4L2CameraAdapter(**kwargs)
    if platform == "ohos":
        from app.adaptation.camera.ohos import OhosCameraAdapter
        return OhosCameraAdapter(**kwargs)
    # 开发机：优先挂 V4L2（WSL/USB 摄像头），失败时调用方自行 mock
    from app.adaptation.camera.v4l2 import V4L2CameraAdapter
    return V4L2CameraAdapter(**kwargs)


def make_runtime(backend: str = "auto") -> IRuntimeAdapter:
    """按 backend 装配推理后端，带软回退链：
    auto: 板端 rknn → onnx → torch；开发机 torch 优先。
    """
    platform = detect_platform()
    order = {
        "rknn": ["rknn"],
        "onnx": ["onnx"],
        "torch": ["torch"],
        "auto": ["rknn", "onnx", "torch"] if platform in ("linux", "ohos") else ["torch", "onnx"],
    }[backend]

    from app.adaptation.runtime.base import RuntimeLoadError
    last_err: Exception | None = None
    for name in order:
        try:
            if name == "rknn":
                from app.adaptation.runtime.rknn_runtime import RknnRuntime
                return RknnRuntime()
            if name == "onnx":
                from app.adaptation.runtime.onnx_runtime import OnnxRuntime
                return OnnxRuntime()
            if name == "torch":
                from app.adaptation.runtime.torch_runtime import TorchRuntime
                return TorchRuntime()
        except ImportError as e:   # 后端依赖缺失 → 回退
            last_err = e
            continue
    raise RuntimeLoadError(f"无可用推理后端（尝试 {order}）: {last_err}")


def make_relay(enabled: bool = False, **kwargs) -> IFrameRelay | None:
    """画面回传默认关闭（R7）。enabled=False 返回 None。"""
    if not enabled:
        return None
    from app.adaptation.relay.wifi_frame_relay import WifiFrameRelay
    return WifiFrameRelay(**kwargs)


def make_device_io(platform: str | None = None, **kwargs) -> IDeviceIO:
    platform = platform or detect_platform()
    if platform in ("linux", "ohos"):
        from app.adaptation.device_io.linux_io import LinuxDeviceIO
        return LinuxDeviceIO(**kwargs)
    from app.adaptation.device_io.linux_io import HostDeviceIO
    return HostDeviceIO(**kwargs)
