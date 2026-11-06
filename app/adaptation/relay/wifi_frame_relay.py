"""WiFi 画面回传实现（TCP + JPEG 帧序列，4 字节长度前缀分包）。

实现 SDD §4.5.2 的七条规则：
  R1 单槽队列丢新不堵旧    R2 push 非阻塞        R3 降帧率回传
  R4 可叠加判定角标        R5 失败退避重连仅告警  R6 回传前降采样
  R7 默认关闭

上位机接收端 20 行即可（读 4 字节长度前缀 + JPEG blob）。
"""

from __future__ import annotations

import socket
import struct
import threading
import time

import cv2
import numpy as np

from app.adaptation.camera.base import Frame
from app.adaptation.relay.base import IFrameRelay

# 单槽队列容量恒为 1（R1）
_QUEUE_DEPTH = 1
_MAX_FRAME_DROPS_BEFORE_WARN = 50


class WifiFrameRelay(IFrameRelay):
    """TCP 长连接 + JPEG 帧序列回传。"""

    def __init__(self,
                 interval_frames: int = 6,
                 jpeg_quality: int = 75,
                 downscale: int = 1080,
                 overlay_result: bool = True):
        self._interval_frames = max(1, interval_frames)
        self._jpeg_quality = jpeg_quality
        self._downscale = downscale
        self._overlay_result = overlay_result

        self._host = ""
        self._port = 0
        self._sock: socket.socket | None = None
        self._lock = threading.Lock()
        self._sender: threading.Thread | None = None
        self._running = False

        self._pending: tuple[bytes,] | None = None     # 单槽：JPEG blob
        self._fail_streak = 0
        self._frame_count = 0

    # ── 生命周期 ─────────────────────────────
    def start(self, host: str, port: int, **kwargs) -> None:
        self._host = host
        self._port = port
        self._interval_frames = max(1, kwargs.get("interval_frames", self._interval_frames))
        self._jpeg_quality = kwargs.get("jpeg_quality", self._jpeg_quality)
        self._downscale = kwargs.get("downscale", self._downscale)
        self._overlay_result = kwargs.get("overlay_result", self._overlay_result)
        self._running = True
        self._connect()
        self._sender = threading.Thread(target=self._send_loop, daemon=True, name="frame-relay")
        self._sender.start()

    def stop(self) -> None:
        self._running = False
        if self._sender is not None:
            self._sender.join(timeout=2.0)
            self._sender = None
        with self._lock:
            if self._sock is not None:
                try:
                    self._sock.close()
                except OSError:
                    pass
                self._sock = None

    # ── 连接管理（R5：固定 2s 退避，仅记失败不致命）──
    def _connect(self) -> bool:
        try:
            sock = socket.create_connection((self._host, self._port), timeout=2.0)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            with self._lock:
                self._sock = sock
            self._fail_streak = 0
            return True
        except OSError:
            return False

    # ── 推送入口（R2：非阻塞，≤5ms）──────────
    def push(self, frame: Frame, result=None) -> bool:
        if not self._running:
            return False
        self._frame_count += 1
        if self._frame_count % self._interval_frames != 0:   # R3 降帧率
            return False
        try:
            jpeg = self._encode(frame, result)
        except Exception:
            return False
        with self._lock:
            if self._pending is not None:
                pass  # 旧帧被覆盖（R1）
            self._pending = (jpeg,)
        return True

    # ── 编码（R4 角标 + R6 降采样）────────────
    def _encode(self, frame: Frame, result) -> bytes:
        img = frame.data
        if self._downscale and max(img.shape[0], img.shape[1]) > self._downscale:
            scale = self._downscale / max(img.shape[0], img.shape[1])
            img = cv2.resize(img, (int(img.shape[1] * scale), int(img.shape[0] * scale)),
                             interpolation=cv2.INTER_AREA)
        if self._overlay_result and result is not None:
            text = f"has_moire={result.has_moire}  cov={result.coverage:.4f}"
            color = (0, 0, 255) if result.has_moire else (0, 200, 0)
            cv2.putText(img, text, (12, 36), cv2.FONT_HERSHEY_SIMPLEX,
                        1.0, color, 2, cv2.LINE_AA)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, self._jpeg_quality])
        if not ok:
            raise RuntimeError("JPEG 编码失败")
        return buf.tobytes()

    # ── 发送线程 ─────────────────────────────
    def _send_loop(self) -> None:
        while self._running:
            with self._lock:
                item = self._pending
                self._pending = None
                sock = self._sock
            if item is None:
                time.sleep(0.01)
                continue
            if sock is None:
                self._fail_streak += 1
                time.sleep(2.0)                       # R5 固定退避
                self._connect()
                continue
            try:
                payload = struct.pack("<I", len(item[0])) + item[0]
                sock.sendall(payload)
                self._fail_streak = 0
            except OSError:
                self._fail_streak += 1
                with self._lock:
                    try:
                        self._sock.close()
                    except OSError:
                        pass
                    self._sock = None
                time.sleep(2.0)
                self._connect()

    @property
    def fail_streak(self) -> int:
        """连续失败计数（供 engine 逐帧循环判降级，SDD C-12）。"""
        return self._fail_streak
