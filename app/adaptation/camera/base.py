"""ICameraAdapter — 把"取一帧"抽象出来，屏蔽 V4L2 / 鸿蒙相机 Kit 差异。"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np


@dataclass
class Frame:
    """一帧原始图像。"""

    data: np.ndarray          # HxWx3, uint8, RGB
    width: int
    height: int
    channels: int
    timestamp: float
    frame_id: str


def frame_shape_error(frame: "Frame") -> str:
    """返回空串 = 形状合契约；否则一句可入日志的错因。

    为什么不只靠 width/height 元数据：下游一律从 data.shape 取尺寸（_align、
    分块坐标），元数据只对日志负责。假相机就出现过 data=(480,1,1) 而
    width/height 仍写 640x480，于是一直“合规”。成本：三次 shape 比较，纳秒级。
    """
    data = getattr(frame, "data", None)
    if data is None or getattr(data, "size", 0) == 0:
        return "空帧（data.size == 0）"
    if data.ndim != 3 or data.shape[2] != 3:
        return f"不是 HxWx3 彩图：shape={data.shape}"
    if (data.shape[0], data.shape[1]) != (frame.height, frame.width):
        return (f"元数据与像素不符：data={data.shape[:2]} "
                f"declared={frame.height}x{frame.width}")
    return ""


class ICameraAdapter(ABC):
    """摄像头抽象接口。

    实现约定：
      - grab() 阻塞取一帧，超时抛 CaptureError
      - open()/close() 幂等
    """

    @abstractmethod
    def open(self, **kwargs) -> None: ...

    @abstractmethod
    def grab(self, timeout_ms: int = 3000) -> Frame: ...

    @abstractmethod
    def close(self) -> None: ...

    @staticmethod
    def _now() -> float:
        return time.time()
