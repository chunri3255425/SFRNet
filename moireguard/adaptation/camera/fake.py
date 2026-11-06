"""
内存假相机：开发机与单测的 ICameraAdapter 实现。

用途：
  - 无摄像头环境跑通常驻循环（daemon --fake-camera）
  - 注入故障验证控制逻辑 C-10（取帧失败重试→FATAL）与
    C-12（连续坏帧→DEGRADED）：fail_after / black_after 参数
"""

from __future__ import annotations

import time

import numpy as np

from moireguard.adaptation.base import Frame, ICameraAdapter


class FakeCameraAdapter(ICameraAdapter):
    """
    合成周期性条纹帧（摩尔纹的域内近似）。

    Attributes:
        width/height: 帧尺寸
        period_px: 条纹周期，越小越"细密"
        fail_after: 第 N 帧起持续抛 RuntimeError（模拟设备掉线）
        black_after: 第 N 帧起输出全黑帧（模拟坏帧）
    """

    def __init__(
        self,
        width: int = 640,
        height: int = 480,
        period_px: float = 8.0,
        fail_after: int = -1,
        black_after: int = -1,
    ):
        self.width = width
        self.height = height
        self.period_px = period_px
        self.fail_after = fail_after
        self.black_after = black_after
        self._seq = 0
        self._opened = False

    def open(self, **kwargs) -> None:
        self._opened = True
        self._seq = 0

    def grab(self) -> Frame:
        if not self._opened:
            raise RuntimeError("E-CAMERA-NOT-OPEN: 先调用 open()")
        self._seq += 1
        if self.fail_after >= 0 and self._seq > self.fail_after:
            raise RuntimeError(f"E-CAMERA-GRAB: 模拟取帧失败 (frame {self._seq})")

        if self.black_after >= 0 and self._seq > self.black_after:
            arr = np.zeros((self.height, self.width, 3), dtype=np.uint8)
        else:
            yy = np.arange(self.height)[:, None]
            stripe = (np.sin(2 * np.pi * yy / self.period_px) > 0)
            arr = np.where(stripe[:, :, None], 205, 50).astype(np.uint8)
            arr = np.repeat(arr, 1, axis=2) if arr.shape[2] == 1 else arr

        return Frame(
            width=self.width,
            height=self.height,
            channels=None,
            timestamp=time.time(),
            data=arr,
            source_id=f"fake-{self._seq:06d}",
        )

    def close(self) -> None:
        self._opened = False
