"""本地模拟器摄像头：从视频文件 / 图片 / 笔记本摄像头循环供帧。

★ 仅新增文件，不改动任何现有代码（本地模拟用，板端仍走 V4L2CameraAdapter）。

用法（经 run_sim.py 或直接构造）：
    FileCamera(source="test.mp4")              # 视频文件，循环播放
    FileCamera(source="test.jpg")              # 单张图片，恒定供帧
    FileCamera(source=0)                       # 笔记本内置摄像头
"""

from __future__ import annotations

import time
from pathlib import Path

import cv2
import numpy as np

from app.adaptation.camera.base import Frame, ICameraAdapter


class CaptureError(RuntimeError):
    """取帧失败。"""


class FileCamera(ICameraAdapter):
    """模拟器摄像头实现（开发机 Windows/Linux 通用）。"""

    def __init__(self, source, fps: float = 15.0):
        self._source = source
        self._fps = fps
        self._cap: cv2.VideoCapture | None = None
        self._static: np.ndarray | None = None
        self._seq = 0
        self._last_t = 0.0
        self._last: np.ndarray | None = None

    def open(self, **kwargs) -> None:
        if self._cap is not None or self._static is not None:
            return
        if isinstance(self._source, int):
            cap = cv2.VideoCapture(self._source, cv2.CAP_DSHOW)
        else:
            suffix = Path(str(self._source)).suffix.lower()
            if suffix in {".jpg", ".jpeg", ".png", ".bmp"}:   # 单图 → 静态帧路径
                img = cv2.imread(str(self._source))
                if img is None:
                    raise CaptureError(f"无法读取图片: {self._source}")
                self._static = img
                return
            cap = cv2.VideoCapture(str(self._source))
        if cap.isOpened():
            self._cap = cap
            return
        # 退路：当作单张图片
        img = cv2.imread(str(self._source))
        if img is None:
            raise CaptureError(f"无法打开视频源或图片: {self._source}")
        self._static = img

    def grab(self, timeout_ms: int = 3000) -> Frame:
        """取一帧（按目标帧率节流，模拟真实摄像头节奏）。"""
        interval = 1.0 / self._fps
        wait = self._last_t + interval - time.time()
        if wait > 0:
            time.sleep(min(wait, timeout_ms / 1000.0))
        self._last_t = time.time()

        if self._static is not None:
            rgb = cv2.cvtColor(self._static, cv2.COLOR_BGR2RGB)
        else:
            assert self._cap is not None
            ok, bgr = self._cap.read()
            if not ok or bgr is None:
                # 视频结束 → 循环重播
                self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ok, bgr = self._cap.read()
            if not ok or bgr is None:
                raise CaptureError("模拟器取帧失败")
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)

        self._last = rgb
        self._seq += 1
        return Frame(data=rgb, width=rgb.shape[1], height=rgb.shape[0],
                     channels=3, timestamp=time.time(),
                     frame_id=f"sim-{self._seq:06d}")

    def read(self) -> np.ndarray:
        """返回最近一帧的 RGB 副本（模拟器专用，供显示层复用）。"""
        if self._last is None:
            self.grab()
        return self._last.copy()

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None
        self._static = None

    def info(self) -> dict:
        src = self._source if not isinstance(self._source, int) else f"camera:{self._source}"
        return {"type": "file_sim", "source": str(src), "fps": self._fps}
