"""适配层 — 屏蔽平台差异（摄像头 / 推理后端 / 画面回传 / 设备 IO / 网络出口）。

对外只暴露工厂函数与抽象基类：组合根 `from app import adaptation as ada`
后直接 ada.make_*()。早期本文件只导出 `factory` 模块本身，导致
`ada.make_camera(...)` 直接 AttributeError（入口代码从没跑起来过）。
"""

from app.adaptation import factory
from app.adaptation.base import (
    DetectionEvent,
    DetectionResultDTO,
    ICameraAdapter,
    IDetectionEngine,
    IDeviceIO,
    IFrameRelay,
    IImageService,
    IResultSink,
    IRuntimeAdapter,
    Frame,
    NetServerOptions,
    NetworkConfig,
)
from app.adaptation.factory import (
    detect_platform,
    make_camera,
    make_device_io,
    make_net_options,
    make_relay,
    make_runtime,
)

__all__ = [
    "factory",
    "detect_platform",
    "make_camera",
    "make_runtime",
    "make_relay",
    "make_device_io",
    "make_net_options",
    "Frame",
    "ICameraAdapter",
    "IRuntimeAdapter",
    "IFrameRelay",
    "IDeviceIO",
    "DetectionResultDTO",
    "DetectionEvent",
    "IImageService",
    "IDetectionEngine",
    "IResultSink",
    "NetworkConfig",
    "NetServerOptions",
]
