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
