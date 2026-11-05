"""Linux V4L2 摄像头实现（IMX415 经 RKISP 驱动暴露 /dev/video*）。

使用 OpenCV 的 V4L2 后端取帧，BGR→RGB 转换在此完成，
上层拿到的 Frame.data 恒为 RGB。
"""

from __future__ import annotations

import time

import cv2
import numpy as np

from app.adaptation.camera.base import Frame, ICameraAdapter


class CaptureError(RuntimeError):
    """取帧失败。"""


class V4L2CameraAdapter(ICameraAdapter):
    """Linux V4L2 摄像头（Sony IMX415 @ MIPI CSI，经 RKISP）。"""

    def __init__(self, device: str = "/dev/video0",
                 width: int = 3840, height: int = 2160, fps: int = 30):
        self._device = device
        self._width = width
        self._height = height
        self._fps = fps
        self._cap: cv2.VideoCapture | None = None

    def open(self, **kwargs) -> None:
        if self._cap is not None and self._cap.isOpened():
            return
        cap = cv2.VideoCapture(self._device, cv2.CAP_V4L2)
        if not cap.isOpened():
            raise CaptureError(f"无法打开摄像头 {self._device}")
        # IMX415 原生 4K@30fps；MJPG 降低 USB/CSI 带宽压力
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, kwargs.get("width", self._width))
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, kwargs.get("height", self._height))
        cap.set(cv2.CAP_PROP_FPS, kwargs.get("fps", self._fps))
        self._cap = cap

    def grab(self, timeout_ms: int = 3000) -> Frame:
        if self._cap is None:
            raise CaptureError("摄像头未打开")
        deadline = time.monotonic() + timeout_ms / 1000.0
        while True:
            ok, bgr = self._cap.read()
            if ok:
                rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
                return Frame(
                    data=rgb,
                    width=rgb.shape[1],
                    height=rgb.shape[0],
                    channels=3,
                    timestamp=time.time(),
                    frame_id=f"{int(time.time() * 1000):013d}",
                )
            if time.monotonic() > deadline:
                raise CaptureError(f"取帧超时（>{timeout_ms}ms）")

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None
