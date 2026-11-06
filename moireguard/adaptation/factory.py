"""
平台探测与实现装配

★ 全项目唯一允许出现平台分支的地方（v2.2 §6 约束 3）。
其余各层只依赖 base.py 的抽象，通过本模块拿到具体实现。

探测顺序：环境变量 MOIREGUARD_PLATFORM 覆盖 → sys.platform 判定。
未知平台不静默兜底，直接抛错——无头镜像里"猜错平台"比"启动失败"更难排查。
"""

from __future__ import annotations

import os
import sys
from typing import Optional

from moireguard.adaptation.base import (
    ICameraAdapter,
    IDeviceIO,
    IRuntimeAdapter,
    NetServerOptions,
    NetworkConfig,
)

LINUX = "linux"
OHOS = "ohos"
DEV = "dev"  # 无硬件的开发/单测装配（假相机 + Torch 后端 + stdout IO）


def detect_platform() -> str:
    """返回 LINUX / OHOS / DEV 之一；MOIREGUARD_PLATFORM 可强制覆盖。"""
    forced = os.environ.get("MOIREGUARD_PLATFORM", "").strip().lower()
    if forced in (LINUX, OHOS, DEV):
        return forced
    if sys.platform.startswith("linux"):
        return LINUX
    return DEV


def build_device_io(platform: str, log_path: Optional[str] = None) -> IDeviceIO:
    """按平台装配配置落点/日志/物理指示。"""
    if platform in (LINUX, DEV):
        from moireguard.adaptation.device_io.linux_io import LinuxDeviceIO
        return LinuxDeviceIO(log_path=log_path)
    if platform == OHOS:
        raise NotImplementedError(
            "E-PLATFORM-PENDING: 鸿蒙 IDeviceIO 未实现（沙箱 filesDir/cacheDir 路径待定）"
        )
    raise ValueError(f"E-PLATFORM-UNKNOWN: {platform}")


def build_camera(platform: str, **kwargs) -> ICameraAdapter:
    """
    按平台装配取帧实现。

    Args:
        platform: detect_platform() 的结果
        kwargs: device (V4L2 路径) / width/height/period_px/fail_after/black_after (假源)
    """
    if platform == LINUX:
        device = kwargs.get("device")
        if device:  # 显式给了 /dev/video* 才走真机
            from moireguard.adaptation.camera.v4l2 import V4L2CameraAdapter  # 待建
            return V4L2CameraAdapter(device)
        from moireguard.adaptation.camera.fake import FakeCameraAdapter
        return FakeCameraAdapter(**{k: v for k, v in kwargs.items() if k != "device"})
    if platform == OHOS:
        raise NotImplementedError("E-PLATFORM-PENDING: 鸿蒙 ICameraAdapter 未实现（相机 Kit / NAPI）")
    from moireguard.adaptation.camera.fake import FakeCameraAdapter
    return FakeCameraAdapter(**kwargs)


def build_runtime(platform: str, device: str = "cpu") -> IRuntimeAdapter:
    """
    按平台装配推理后端。

    当前只有 Torch eager 可用；ONNX Runtime / RKNN NPU 后端属 Q7 转换链，
    未落地前显式抛错，绝不静默降级（部署期假装上了 NPU 是最坏的事故）。
    """
    backend = os.environ.get("MOIREGUARD_RUNTIME", "torch").strip().lower()
    if backend == "torch":
        from moireguard.adaptation.runtime.torch_runtime import TorchRuntimeAdapter
        return TorchRuntimeAdapter(device=device)
    if backend == "onnxruntime":
        raise NotImplementedError("E-BACKEND-PENDING: ONNX Runtime 后端未实现（见 build/export_model.py 规划）")
    if backend == "rknn":
        raise NotImplementedError("E-BACKEND-PENDING: RKNN NPU 后端未实现，Q7 转换链尚未跑通")
    raise ValueError(f"E-BACKEND-UNKNOWN: {backend}")


def build_net_options(config: NetworkConfig) -> NetServerOptions:
    """网络出口装配位：服务与引擎由组合根注入，这里只带配置。"""
    return NetServerOptions(config=config)
