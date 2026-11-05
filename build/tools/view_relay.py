"""上位机画面查看器 — 接收板端 WiFi 回传的 JPEG 帧并显示。

与 app/adaptation/relay/wifi_frame_relay.py 对应：
  TCP listen :9000 → 每帧 = 4 字节小端长度前缀 + JPEG blob

用法：
  python build/tools/view_relay.py --listen 9000
"""

from __future__ import annotations

import argparse
import socket
import struct

import cv2
import numpy as np


def serve(port: int) -> None:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("", port))
    srv.listen(1)
    print(f"[view] 等待板端连接 :{port} …")
    conn, addr = srv.accept()
    print(f"[view] 板端已连接: {addr}")
    buf = b""
    try:
        while True:
            while len(buf) < 4:
                buf += conn.recv(65536)
            (size,) = struct.unpack("<I", buf[:4])
            buf = buf[4:]
            while len(buf) < size:
                buf += conn.recv(65536)
            jpeg, buf = buf[:size], buf[size:]
            img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
            if img is not None:
                cv2.imshow("MoireGuard Relay", img)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
    except (ConnectionResetError, KeyboardInterrupt):
        pass
    finally:
        conn.close()
        srv.close()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    p = argparse.ArgumentParser("view_relay")
    p.add_argument("--listen", type=int, default=9000)
    serve(p.parse_args().listen)
