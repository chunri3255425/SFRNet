"""适配层平台探测与实现装配。

★ 全项目唯一允许出现平台分支的地方（SDD §6 风险 #3）。

两条纪律（合并时定调，取代早期实现的两种"温柔"做法）：

1. **不做静默回退**。`backend="auto"` 只按平台**选定一个**后端，选不上就抛错。
   早期 auto 链是 rknn→onnx→torch：板子上 NPU 转换链没跑通时，程序会悄悄用
   CPU 上的 torch 跑起来，日志看不出来，"以为上了 NPU"是最难排查的事故形态。
2. **未实现 ≠ 不可用**。未落地的前端（鸿蒙相机 / RKNN / ONNX）显式抛
   NotImplementedError 并写清缺什么；调用方绝不因"文件不存在"而吃一个
   ModuleNotFoundError。
"""

from __future__ import annotations

import os
import sys

from app.adaptation.camera.base import ICameraAdapter
from app.adaptation.device_io.base import IDeviceIO
from app.adaptation.relay.base import IFrameRelay
from app.adaptation.runtime.base import IRuntimeAdapter

LINUX = "linux"
OHOS = "ohos"
HOST = "host"       # 开发机（Windows / macOS / 无板端依赖的 Linux 调试）


def detect_platform() -> str:
    """返回 'linux' | 'ohos' | 'host'；环境变量 MOIREGUARD_PLATFORM 可强制覆盖。

    “板端”按**是否装了产品镜像**判，不按 sys.platform 判：同一份
    Ubuntu 基线的 WSL / PC Linux 与开发板都报 linux，拿 sys.platform 当板端
    会让开发机去读 /usr/share/moireguard/models/model.rknn（根远端旧缺陷的成因）。
    板端特征：产品配置目录 / 模型目录存在，或 rknnlite 可导入。
    """
    forced = os.environ.get("MOIREGUARD_PLATFORM", "").strip().lower()
    if forced in (LINUX, OHOS, HOST):
        return forced
    if sys.platform.startswith("linux") or sys.platform.startswith("ohos"):
        from pathlib import Path
        # OpenHarmony 用户态仍可能报 linux，需要正特征来判别。只认
        # /etc/ohos_release：原判据里的 /system/etc **已实测被推翻**——RK3576 板
        # 厂商的 Ubuntu 22.04 固件本身就带 /system/etc/{firmware,lib}（装的是供应商
        # 固件），拿它当 OH 特征会把真板子判成鸿蒙，相机被装配成未实现的
        # OhosCameraAdapter 后直接 FATAL。真 OH 硬件请用 MOIREGUARD_PLATFORM=ohos
        # 显式指定（该分支至今无硬件可验，不做默认猜测）。
        if Path("/etc/ohos_release").exists():
            return OHOS
        if (Path("/etc/moireguard").exists()
                or Path("/usr/share/moireguard").exists()
                or _has_module("rknnlite")):
            return LINUX
        return HOST
    return HOST


def _has_module(name: str) -> bool:
    import importlib.util
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def make_camera(platform: str | None = None, device: str | None = None,
                **kwargs) -> ICameraAdapter:
    """装配取帧实现。

    Args:
        platform: detect_platform() 结果，None 则自动探测
        device:   取帧源。"/dev/video*" 走 V4L2；文件/目录走 FileCamera；
                  纯数字（如 "0"）走笔记本/USB 摄像头；
                  留空在板端默认 /dev/video0，在开发机默认假源
    """
    platform = platform or detect_platform()

    # 显式给了 device 就以 device 为准，平台只负责挑**默认**源。原来 OHOS 分支
    # 排在最前面直接 return，等于在鸿蒙判定下无声丢弃调用侧的 --camera（实测
    # 板端 `--camera xxx.jpg` 因此根本到不了 FileCamera）。
    if device is None:
        if platform == OHOS:
            from app.adaptation.camera.ohos import OhosCameraAdapter
            return OhosCameraAdapter(**kwargs)     # 未实现，open() 显式抛错
        if platform == LINUX:
            device = "/dev/video0"                 # 板端没得选：只接 CSI 摄像头
        else:
            from app.adaptation.camera.fake import FakeCameraAdapter
            return FakeCameraAdapter(**kwargs)

    # 数字索引（如 "0"）→ 笔记本/USB 摄像头（Windows DirectShow / Linux V4L2）
    if device.isdigit():
        from app.adaptation.camera.file_camera import FileCamera
        return FileCamera(source=int(device), **kwargs)

    if device.startswith("/dev/"):
        if platform == OHOS:
            # 设备节点在 OH 上没有可用实现，继续显式抛错而不是假装能用
            from app.adaptation.camera.ohos import OhosCameraAdapter
            return OhosCameraAdapter(**kwargs)
        # Linux 平台使用 RK3576 专用适配器（处理 10-bit Bayer → RGB）
        if platform == LINUX:
            from app.adaptation.camera.rk3576 import Rk3576CameraAdapter
            return Rk3576CameraAdapter(device=device, **kwargs)
        from app.adaptation.camera.v4l2 import V4L2CameraAdapter
        return V4L2CameraAdapter(device=device, **kwargs)

    # 非设备节点一律当文件/视频源（本地模拟器、抽检素材）
    from app.adaptation.camera.file_camera import FileCamera
    return FileCamera(device, **kwargs)


def make_runtime(backend: str = "auto", **kwargs) -> IRuntimeAdapter:
    """按 backend 装配推理后端。

    auto：板端（linux/ohos）= rknn，开发机 = torch。**选定即定死，不回退**；
    选不上的错里会直接给出下一步命令。
    """
    platform = detect_platform()
    if backend == "auto":
        backend = "torch" if platform == HOST else "rknn"

    if backend == "torch":
        from app.adaptation.runtime.torch_runtime import TorchRuntime
        return TorchRuntime(**kwargs)

    if backend == "rknn":
        try:
            from app.adaptation.runtime.rknn_runtime import RknnRuntime
            return RknnRuntime(**kwargs)
        except ImportError as e:
            raise RuntimeError(
                f"E-BACKEND-UNAVAILABLE: rknnlite 未安装（{e}）。板端勿以 torch "
                f"顶替 —— 那会让「NPU 已就位」的结论失真。请装 rknn-toolkit-lite2，"
                f"或开发机显式 --backend torch"
            ) from e

    if backend == "onnx":
        try:
            from app.adaptation.runtime.onnx_runtime import OnnxRuntime
            return OnnxRuntime(**kwargs)
        except ImportError as e:
            raise RuntimeError(
                f"E-BACKEND-UNAVAILABLE: onnxruntime 未安装（{e}）"
            ) from e

    raise ValueError(f"E-BACKEND-UNKNOWN: {backend}（可选 auto/torch/rknn/onnx）")


def make_relay(enabled: bool = False, **kwargs) -> IFrameRelay | None:
    """画面回传默认关闭（SDD §4.5.2 R7）。enabled=False 返回 None。"""
    if not enabled:
        return None
    from app.adaptation.relay.wifi_frame_relay import WifiFrameRelay
    return WifiFrameRelay(**kwargs)


def make_device_io(platform: str | None = None, **kwargs) -> IDeviceIO:
    """配置落点 / 日志 / 物理指示。开发机走 stdout + 内存配置的 HostDeviceIO。"""
    platform = platform or detect_platform()
    if platform in (LINUX, OHOS):
        from app.adaptation.device_io.linux_io import LinuxDeviceIO
        return LinuxDeviceIO(**kwargs)
    from app.adaptation.device_io.linux_io import HostDeviceIO
    return HostDeviceIO(**kwargs)


def make_net_options(network_cfg: dict | None = None):
    """网络出口装配位：只吃配置 dict，服务与引擎由组合根注入。"""
    from app.adaptation.base import NetServerOptions, NetworkConfig
    return NetServerOptions(config=NetworkConfig.from_dict(network_cfg))
