"""
内存假相机：开发机与单测的 ICameraAdapter 实现。

用途：
  - 无摄像头环境跑通常驻循环（开发机裸跑 python -m app.main 即此路径）
  - fail_after：第 N 帧起抛异常，验证 C-10（取帧失败重试→FATAL）
  - black_after：第 N 帧起输出全黑帧。**注意**：CONTROL_LOGIC §5.3 的
    全黑/全白判据当前代码里没有，所以全黑帧会被当成正常输入送进模型并
    得出一个布尔结论，C-12（连续坏帧→DEGRADED）走不到；保留此参数仅供
    下游看门狗/显示侧人工核对
"""

from __future__ import annotations

import time

import numpy as np

from app.adaptation.base import Frame, ICameraAdapter


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

    def grab(self, timeout_ms: int = 3000) -> Frame:
        """合成一帧（timeout_ms 收下不用：合成没有等待，但必须接得住）。

        签名必须与 ICameraAdapter.grab 逐参数一致。这里曾经写成 grab(self)，
        于是引擎按 CONTROL_LOGIC §6.1 调用 camera.grab(timeout_ms=3000) 时直接
        TypeError；而开发机默认形态用的就是本类，等价于常驻循环一帧都出不来。
        """
        del timeout_ms            # 显式声明“有意不用”，而不是靠签名不接
        if not self._opened:
            raise RuntimeError("E-CAMERA-NOT-OPEN: 先调用 open()")
        self._seq += 1
        if self.fail_after >= 0 and self._seq > self.fail_after:
            raise RuntimeError(f"E-CAMERA-GRAB: 模拟取帧失败 (frame {self._seq})")

        if self.black_after >= 0 and self._seq > self.black_after:
            arr = np.zeros((self.height, self.width, 3), dtype=np.uint8)
        else:
            # 沿行明暗交替的横纹（契约：HxWx3 uint8 RGB）。
            # 曾写成 np.where(stripe[:, :, None], 205, 50)：stripe 只有 (H,1)，
            # 广播出来的“帧”是 (H,1,1)，后面 transpose(2,0,1) 直接 ValueError，
            # 而自检只看 data.size 是否非零，照样判它“通过”。
            band = np.where(
                np.sin(2 * np.pi * np.arange(self.height) / self.period_px) > 0,
                205, 50).astype(np.uint8)                      # (H,)
            arr = np.empty((self.height, self.width, 3), dtype=np.uint8)
            arr[..., 0] = arr[..., 1] = arr[..., 2] = band[:, None]

        return Frame(
            data=arr,
            width=self.width,
            height=self.height,
            channels=3,
            timestamp=time.time(),
            frame_id=f"fake-{self._seq:06d}",
        )

    def close(self) -> None:
        self._opened = False
