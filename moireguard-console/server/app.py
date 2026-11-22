"""MoireGuard Console 聚合服务（F-B 最小可用版）。

职责只有一件事：把板端**已有**的两条出口转成前端 `src/api/client.ts` 约定的
四个端点，让 UI 吃到真数据而不是 Demo 模拟。

    GET /api/devices                    → [{serial, name, host, http_port, relay_enabled}]
    GET /api/devices/:serial/status     → {engine_state, service_ready, sse_clients, uptime_s}
    GET /api/events                     → SSE，data 为 DetectionEvent
    GET /api/devices/:serial/stream     → MJPEG（multipart/x-mixed-replace）

为什么必须有人中转（不是偷懒）：
  1. 板端 HTTP 面是 /healthz /status /stream /detect，前端要的是 /api/*，名字与
     形状都不同；
  2. **板端没有任何 HTTP 图像端点**。画面走的是 `WifiFrameRelay`：板子作为 TCP
     **客户端主动连出**上位机、按 `struct.pack("<I", len) + JPEG` 一帧帧推
     （SDD §4.5.2）。浏览器要的是"访问一个 URL 就能持续取图"。方向是反的，
     所以本服务必须先当 TCP 服务端把帧接住，再转成 HTTP MJPEG 供浏览器拉。
  3. 板端 SSE 的 data 是 `DetectionResultDTO.summary()`，其中**没有** `result`
     与 `stage1_conf`（板端 DTO 缺字段，见 ARCHITECTURE.md §2.2 勘误）。本服务
     不造假数据，缺的字段原样缺失，由前端渲染成"未知"。

依赖：只用标准库。板端产品路径不装 PyTorch（SDD §1.2），这台机器上的控制台后端
也不该引入需要翻墙才能装上的东西。

用法：
    python3 app.py --listen 127.0.0.1:8000 --relay-bind 0.0.0.0:9000 \
        --board 98fed70982ab4c63=192.168.31.53:8340 --board-name 实验室-1号板
"""

from __future__ import annotations

import argparse
import json
import queue
import socket
import struct
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
from urllib.request import Request, urlopen

# ── 全局共享状态 ──────────────────────────────────────────────
# relay 单槽：只保留"当前画面"，与 SDD R1 丢旧保新的口径一致
_frame_lock = threading.Lock()
_latest_jpeg: bytes | None = None
_frame_ts: float = 0.0
_frame_seq: int = 0

_events: list[queue.Queue] = []      # 已连接的浏览器 SSE 队列
_events_lock = threading.Lock()

BOARDS: list[dict] = []              # [{serial, name, host, http_port, token}]
RELAY_BIND = ("0.0.0.0", 9000)
BOARD_ONLINE_STALE_S = 20.0          # 超过这个时间没收到 relay 帧就判 relay 不活跃


def board_by_serial(serial: str) -> dict | None:
    for b in BOARDS:
        if b["serial"] == serial:
            return b
    return None


def board_base(b: dict) -> str:
    return f"http://{b['host']}:{b['http_port']}"


# ── relay 接收端：接住板子主动连进来的 TCP 帧流 ─────────────────
class RelayReceiver(threading.Thread):
    """TCP 服务端：accept 板端 relay 连接，按 4 字节长度前缀切 JPEG。

    一台板子一条长连接；连接断了就退回等待（R5：失败只告警，不致命）。
    注意这里**不区分是哪块板** —— relay 协议本身没有握手字段，所以单槽里
    存的是"最后收到的那一帧"。多板并发时谁最后推就显示谁，属 F-B 最小版的
    已知限制，需在 F-B 完整版加 serial 握手后按 serial 分槽。
    """

    def __init__(self) -> None:
        super().__init__(daemon=True, name="relay-recv")
        self._running = True

    def run(self) -> None:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(RELAY_BIND)
        srv.listen(4)
        print(f"[relay] 监听 {RELAY_BIND[0]}:{RELAY_BIND[1]}（等板子连进来）", flush=True)
        while self._running:
            try:
                conn, addr = srv.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn, addr),
                             daemon=True, name="relay-conn").start()

    def _serve(self, conn: socket.socket, addr) -> None:
        global _latest_jpeg, _frame_ts, _frame_seq
        print(f"[relay] 板子连入 {addr}", flush=True)
        conn.settimeout(30.0)
        try:
            while self._running:
                head = self._recv_exact(conn, 4)
                if head is None:
                    break
                (size,) = struct.unpack("<I", head)
                if size == 0 or size > 32 * 1024 * 1024:
                    print(f"[relay] 帧长异常 {size}，断开", flush=True)
                    break
                blob = self._recv_exact(conn, size)
                if blob is None:
                    break
                with _frame_lock:
                    _latest_jpeg = blob
                    _frame_ts = time.time()
                    _frame_seq += 1
        except OSError as e:
            print(f"[relay] 连接中断：{e}", flush=True)
        finally:
            try:
                conn.close()
            except OSError:
                pass
            print(f"[relay] {addr} 已断开", flush=True)

    @staticmethod
    def _recv_exact(conn: socket.socket, n: int) -> bytes | None:
        buf = b""
        while len(buf) < n:
            try:
                chunk = conn.recv(n - len(buf))
            except OSError:
                return None
            if not chunk:
                return None
            buf += chunk
        return buf

    def stop(self) -> None:
        self._running = False


# ── 板端 SSE 转发：一条连接读多台板的 /stream ───────────────────
class BoardSseForwarder(threading.Thread):
    """读板端 /stream 的 `data: {...}` 行，补上 serial 后广播给浏览器。

    板端 /stream 是鉴权端点（token 非空时要 Bearer），/healthz 免鉴权。
    """

    def __init__(self, board: dict) -> None:
        super().__init__(daemon=True, name=f"sse-{board['serial'][:8]}")
        self._board = board
        self._running = True

    def run(self) -> None:
        while self._running:
            try:
                self._pump()
            except Exception as e:                       # 断线/板子重启都只重连
                print(f"[sse] {self._board['serial'][:8]} 中断，3s 后重连：{e}", flush=True)
                time.sleep(3.0)

    def _pump(self) -> None:
        url = f"{board_base(self._board)}/stream"
        req = Request(url, headers={"Accept": "text/event-stream"})
        if self._board.get("token"):
            req.add_header("Authorization", f"Bearer {self._board['token']}")
        with urlopen(req, timeout=60) as resp:
            for raw in resp:
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                if not line.startswith("data:"):
                    continue
                try:
                    payload = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue
                payload.setdefault("serial", self._board["serial"])
                # 板端 DTO 用 source_id 表帧号，前端 DetectionEvent 要 frame_id
                if "frame_id" not in payload and "source_id" in payload:
                    payload["frame_id"] = payload["source_id"]
                payload.setdefault("ts", time.time())
                self._broadcast(payload)

    @staticmethod
    def _broadcast(event: dict) -> None:
        with _events_lock:
            qs = list(_events)
        for q in qs:
            try:
                q.put_nowait(event)             # 浏览器慢就丢，绝不回压板端
            except queue.Full:
                pass

    def stop(self) -> None:
        self._running = False


# ── HTTP 服务：前端约定的四个端点 ───────────────────────────────
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:      # 静音默认访问日志
        pass

    def _json(self, code: int, payload) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        parts = [p for p in path.split("/") if p]

        if path == "/api/devices":
            return self._devices()
        if path == "/api/events":
            return self._events_sse()
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "devices" and parts[3] == "stream":
            return self._mjpeg(parts[2])
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "devices" and parts[3] == "status":
            return self._status(parts[2])
        if path in ("/", "/healthz"):
            return self._json(200, {"ok": True, "boards": len(BOARDS)})
        self._json(404, {"error": "not-found", "path": path})

    # GET /api/devices
    def _devices(self) -> None:
        with _frame_lock:
            relay_fresh = _latest_jpeg is not None and (time.time() - _frame_ts) < BOARD_ONLINE_STALE_S
        out = []
        for b in BOARDS:
            out.append({
                "serial": b["serial"],
                "name": b["name"],
                "host": b["host"],
                "http_port": b["http_port"],
                # relay_enabled 报的是"当前是否真有帧在流"，不是配置值 ——
                # 前端 DeviceCard 用它决定显示"画面未就绪"还是"relay off"
                "relay_enabled": relay_fresh,
            })
        self._json(200, out)

    # GET /api/devices/:serial/status —— 原样代理板端 /status
    def _status(self, serial: str) -> None:
        b = board_by_serial(serial)
        if b is None:
            return self._json(404, {"error": "unknown-serial", "serial": serial})
        try:
            req = Request(f"{board_base(b)}/status")
            if b.get("token"):
                req.add_header("Authorization", f"Bearer {b['token']}")
            with urlopen(req, timeout=4) as resp:
                self._json(200, json.loads(resp.read().decode("utf-8")))
        except Exception as e:
            # 明确回 503 + 原因，而不是让 vite 代理的 500 冒充"系统炸了"
            self._json(503, {"error": "board-unreachable",
                             "detail": f"{type(e).__name__}: {e}",
                             "board": board_base(b)})

    # GET /api/events —— 浏览器 SSE
    def _events_sse(self) -> None:
        q: queue.Queue = queue.Queue(maxsize=64)
        with _events_lock:
            _events.append(q)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            self.wfile.write(b"retry: 3000\n: moireguard-console\n\n")
            self.wfile.flush()
            while True:
                try:
                    item = q.get(timeout=10.0)
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    continue
                self.wfile.write(f"data: {json.dumps(item, ensure_ascii=False)}\n\n".encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            with _events_lock:
                if q in _events:
                    _events.remove(q)

    # GET /api/devices/:serial/stream —— relay 帧转 MJPEG
    def _mjpeg(self, serial: str) -> None:
        b = board_by_serial(serial)
        if b is None:
            return self._json(404, {"error": "unknown-serial", "serial": serial})
        boundary = "moireguard"
        self.send_response(200)
        self.send_header("Content-Type", f"multipart/x-mixed-replace; boundary={boundary}")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        last_seq = -1
        idle_started = time.time()
        try:
            while True:
                with _frame_lock:
                    jpeg, seq = _latest_jpeg, _frame_seq
                if jpeg is None:
                    # 还没收到任何帧：先给一张说明图，避免 <img> 报错图标
                    if time.time() - idle_started > 30:
                        self._json_no_stream(503, "relay 尚无帧：板端 relay 未开启或板子未连入")
                        return
                    time.sleep(0.3)
                    continue
                if seq == last_seq:
                    time.sleep(0.05)                     # 同一帧，等下一帧（约 20fps 上限）
                    continue
                last_seq = seq
                self.wfile.write(f"--{boundary}\r\n".encode())
                self.wfile.write(b"Content-Type: image/jpeg\r\n")
                self.wfile.write(f"Content-Length: {len(jpeg)}\r\n\r\n".encode())
                self.wfile.write(jpeg)
                self.wfile.write(b"\r\n")
                self.wfile.flush()
                idle_started = time.time()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def _json_no_stream(self, code: int, reason: str) -> None:
        body = json.dumps({"error": reason}, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


# ── 装配 ─────────────────────────────────────────────────────
def main() -> int:
    global BOARDS, RELAY_BIND
    p = argparse.ArgumentParser(description="MoireGuard Console 聚合服务（F-B 最小版）")
    p.add_argument("--listen", default="127.0.0.1:8000", help="前端代理指向的地址（vite 默认 8000）")
    p.add_argument("--relay-bind", default="0.0.0.0:9000",
                   help="板端 relay 主动连进来的监听地址；板子要能路由到这里")
    p.add_argument("--board", action="append", default=[], metavar="SERIAL=HOST:PORT",
                   help="可重复；例：98fed70982ab4c63=192.168.31.53:8340")
    p.add_argument("--board-name", default="实验室-1号板")
    p.add_argument("--token", default="", help="板端 network.token 非空时传（仅内存，不落盘）")
    args = p.parse_args()

    host, port = args.listen.rsplit(":", 1)
    rhost, rport = args.relay_bind.rsplit(":", 1)
    RELAY_BIND = (rhost, int(rport))

    BOARDS = []
    for spec in args.board:
        serial, _, hp = spec.partition("=")
        h, _, pt = hp.partition(":")
        BOARDS.append({"serial": serial, "name": args.board_name,
                       "host": h, "http_port": int(pt or 8340), "token": args.token})
    if not BOARDS:
        print("!! 没给 --board，前端设备墙会是空列表", flush=True)

    RelayReceiver().start()
    for b in BOARDS:
        BoardSseForwarder(b).start()

    srv = ThreadingHTTPServer((host, int(port)), Handler)
    print(f"[http] 聚合服务 {host}:{port}；已登记板子 {len(BOARDS)} 台", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("[http] 退出", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
