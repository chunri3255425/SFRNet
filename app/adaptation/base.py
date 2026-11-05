"""适配层基类接口。

适配层红线：
  - 不含任何判定逻辑
  - 不懂"摩尔纹"是什么
"""

from app.adaptation.camera.base import Frame, ICameraAdapter
from app.adaptation.runtime.base import IRuntimeAdapter
from app.adaptation.relay.base import IFrameRelay
from app.adaptation.device_io.base import IDeviceIO

__all__ = [
    "Frame",
    "ICameraAdapter",
    "IRuntimeAdapter",
    "IFrameRelay",
    "IDeviceIO",
]
