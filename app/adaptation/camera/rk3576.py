"""RK3576 专用摄像头适配器：使用 v4l2-ctl 抓帧 + rawpy 去马赛克。"""

from __future__ import annotations

import subprocess
import time

import cv2
import numpy as np

from app.adaptation.camera.base import Frame, ICameraAdapter


class CaptureError(RuntimeError):
    """取帧失败。"""


class Rk3576CameraAdapter(ICameraAdapter):
    """RK3576 平台的摄像头适配器，处理 10-bit Bayer → RGB 转换。"""

    def __init__(self, device: str = "/dev/video0",
                 width: int = 1920, height: int = 1080, fps: int = 15):
        self._device = device
        self._width = width
        self._height = height
        self._fps = fps
        self._seq = 0

    def open(self, **kwargs) -> None:
        """验证设备可用。"""
        # 简化验证：只检查设备是否存在
        import os
        if not os.path.exists(self._device):
            raise CaptureError(f"摄像头设备不存在: {self._device}")

    def grab(self, timeout_ms: int = 3000) -> Frame:
        """抓取一帧并转换为 RGB。"""
        deadline = time.monotonic() + timeout_ms / 1000.0
        
        while time.monotonic() < deadline:
            try:
                # 使用 v4l2-ctl 抓取原始帧
                cmd = [
                    "v4l2-ctl",
                    f"--device={self._device}",
                    f"--set-fmt-video=width={self._width},height={self._height},pixelformat=RG10",
                    "--stream-mmap=1",
                    "--stream-count=1",
                    "--stream-to=-"
                ]
                result = subprocess.run(cmd, capture_output=True, timeout=5)
                
                if result.returncode != 0 or len(result.stdout) == 0:
                    time.sleep(0.1)
                    continue
                
                # 将 10-bit Bayer 转换为 8-bit RGB
                rgb = self._bayer_to_rgb(result.stdout)
                
                self._seq += 1
                return Frame(
                    data=rgb,
                    width=rgb.shape[1],
                    height=rgb.shape[0],
                    channels=3,
                    timestamp=time.time(),
                    frame_id=f"rk3576-{self._seq:06d}",
                )
                
            except Exception as e:
                if time.monotonic() >= deadline:
                    raise CaptureError(f"取帧超时: {e}")
                time.sleep(0.1)
        
        raise CaptureError(f"取帧超时 (>{timeout_ms}ms)")

    def _bayer_to_rgb(self, raw_data: bytes) -> np.ndarray:
        """将 10-bit Bayer 原始数据转换为 8-bit RGB 图像。"""
        # 10-bit packed 格式，每个像素 10 bits
        # 简化处理：先转为 16-bit，然后提取高位
        total_pixels = self._width * self._height
        expected_bytes = total_pixels * 2  # 10-bit packed 约等于 2 bytes/pixel
        
        if len(raw_data) < expected_bytes:
            # 数据不足，填充或裁剪
            raw_data = raw_data + b'\x00' * (expected_bytes - len(raw_data))
        
        # 解析为 16-bit 数组
        raw_16 = np.frombuffer(raw_data[:expected_bytes], dtype=np.uint16)
        raw_16 = raw_16.reshape((self._height, self._width))
        
        # 提取高 8 位（简化处理，忽略 ISP 去马赛克）
        raw_8 = (raw_16 >> 2).astype(np.uint8)
        
        # 使用 OpenCV 的去马赛克（虽然不完美，但可以快速验证）
        # RG10 是 RGGB Bayer 格式
        rgb = cv2.cvtColor(raw_8, cv2.COLOR_BAYER_RG2RGB)
        
        return rgb

    def close(self) -> None:
        """释放资源。"""
        pass
