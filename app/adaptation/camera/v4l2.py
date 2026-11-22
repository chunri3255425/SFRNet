"""Linux 摄像头实现（IMX415 经 RKISP 暴露 /dev/video*）。

取帧优先走 OpenCV 的 GStreamer 后端，失败再回退 cv2.CAP_V4L2。
顺序不能反：板端 rkisp/rkcif 的采集节点一律只 advertise **Multiplanar**
（QUERYCAP caps=0x04201000），而板载 OpenCV 4.5.4 的 V4L2 后端不支持多平面，
对 /dev/video33 直接 `can't create capture`（os.open() 却是成功的 —— 不是权限问题）。
BGR→RGB 转换在此完成，上层拿到的 Frame.data 恒为 RGB。
"""

from __future__ import annotations

import os
import time

import cv2

from app.adaptation.camera.base import Frame, ICameraAdapter


class CaptureError(RuntimeError):
    """取帧失败。"""


def _resolve_mainpath(device: str) -> str:
    """把设备节点归一到**接了 sensor 的** rkisp mainpath。

    厂商镜像的 udev 符号链接 /dev/video-camera0 指向 video51，而 video51 属于
    rkisp-vir3 —— 它的上游 link 是空的，QUERYCAP 只报 800x600，根本取不到流。
    真正挂着 imx415（经 dphy0 → rkcif-mipi-lvds）的是 rkisp-vir0 的 mainpath
    = /dev/video33。实测两块同镜像板映射一致，故对已知错节点做定向替换；
    其余节点（USB 摄像头等）原样透传，不做多余猜测。
    """
    real = os.path.realpath(device)
    wrong = ("/dev/video51", "/dev/video42")   # rkisp-vir3 / rkisp-vir1：上游无 sensor
    if real in wrong or device in wrong:
        return "/dev/video33"
    return device


def _gst_pipeline(device: str, width: int, height: int) -> str:
    """rkisp mainpath → BGR 的 GStreamer 管线。

    mainpath 原生输出 NV12；videoconvert 落到 BGR 后交 appsink。
    drop=1/max-buffers=1/sync=false 让取帧端不缓冲积压 —— 检测要的是"当前画面"，
    宁可丢旧帧也不能让下游慢把整条管线拖住。
    """
    return (
        f"v4l2src device={device} ! "
        f"video/x-raw,format=NV12,width={width},height={height} ! "
        "videoconvert ! video/x-raw,format=BGR ! "
        "appsink drop=1 max-buffers=1 sync=false"
    )


class V4L2CameraAdapter(ICameraAdapter):
    """Linux 摄像头（Sony IMX415 @ MIPI CSI-2，经 RKISP；亦兼容 USB/V4L2 单平面源）。"""

    def __init__(self, device: str = "/dev/video33",
                 width: int = 1920, height: int = 1080, fps: int = 30):
        self._device = device
        self._width = width
        self._height = height
        self._fps = fps
        self._cap: cv2.VideoCapture | None = None
        self._backend = ""

    def open(self, **kwargs) -> None:
        if self._cap is not None and self._cap.isOpened():
            return
        width = int(kwargs.get("width", self._width))
        height = int(kwargs.get("height", self._height))
        self._width, self._height = width, height
        device = _resolve_mainpath(self._device)

        errors: list[str] = []

        # 路径 1：GStreamer（板端唯一可用；OpenCV 需带 GStreamer 后端编译）
        if hasattr(cv2, "CAP_GSTREAMER"):
            cap = cv2.VideoCapture(_gst_pipeline(device, width, height), cv2.CAP_GSTREAMER)
            if cap.isOpened():
                self._cap, self._backend = cap, f"gstreamer({device})"
                return
            cap.release()
            errors.append("GStreamer 管线打不开（管线错或 OpenCV 未链接 GStreamer）")

        # 路径 2：传统 V4L2 单平面后端 —— 只对 USB 摄像头这类非多平面源有效
        cap = cv2.VideoCapture(device, cv2.CAP_V4L2)
        if cap.isOpened():
            # USB 摄像头常见 MJPG 编码，可显著降低带宽压力
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
            cap.set(cv2.CAP_PROP_FPS, kwargs.get("fps", self._fps))
            self._cap, self._backend = cap, f"v4l2({device})"
            return
        cap.release()
        errors.append(f"V4L2 后端打不开 {device}（多平面节点不受 OpenCV V4L2 支持）")

        raise CaptureError(
            f"无法打开摄像头 {self._device}（归一后 {device}）: " + "；".join(errors)
        )

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
