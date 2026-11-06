"""
板端网络出口：纯标准库 HTTP 服务（REST 判定 + SSE 结果广播）

端点：
  GET  /status   引擎与适配层运行快照（只读，无依赖）
  POST /detect   收图判定。body 为图像原始字节，或 JSON {name,data_b64|path}
  GET  /stream   SSE：每产生一次判定就推送一行 data: {json}
  GET  /healthz  存活探针（systemd/监控可用；**免鉴权**，其余端点一律过 token）

设计约束：
  - 只消费 base.py 的三个协议（IImageService / IDetectionEngine / IResultSink
    的进程内等价物），不 import logic_layer / torch。
  - 依赖全部由组合根构造后注入，本模块可离线单测（假 service 即可）。
  - 鉴权：network.token 非空时要求 Authorization: Bearer <token>；
    空 token 视为"仅限开发机"，/status 启动时会打 WARN 日志。
"""

from __future__ import annotations

import base64
import json
import queue
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional

from app.adaptation.base import (
    DetectionResultDTO,
    IDeviceIO,
    IDetectionEngine,
    IImageService,
    NetServerOptions,
)

_SSE_SENTINEL = None  # 队列里收到 None = 服务停止，客户端断开
_DRAIN_HARD_CAP = 512 * 1024 * 1024  # 排空请求体的硬上限，防“声明体量无限大”把服务挂住


class _Broker:
    """SSE 订阅者注册表：publish 非阻塞，慢消费者被丢弃而非拖死主循环。"""

    def __init__(self):
        self._clients: set[queue.Queue] = set()
        self._lock = threading.Lock()

    def register(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=16)
        with self._lock:
            self._clients.add(q)
        return q

    def unregister(self, q: queue.Queue) -> None:
        with self._lock:
            self._clients.discard(q)

    def publish(self, dto: DetectionResultDTO) -> None:
        with self._lock:
            clients = list(self._clients)
        for q in clients:
            try:
                q.put_nowait(dto)
            except queue.Full:
                pass  # 追不上的订阅者丢事件，不阻塞判定主流程

    def close_all(self) -> None:
        with self._lock:
            clients = list(self._clients)
            self._clients.clear()
        for q in clients:
            try:
                q.put_nowait(_SSE_SENTINEL)
            except queue.Full:
                pass

    @property
    def client_count(self) -> int:
        with self._lock:
            return len(self._clients)


class HttpNetServer:
    """
    网络出口服务。生命周期：start() 后台线程监听 → stop() 优雅关闭。

    实现 IResultSink 协议（提供 publish(dto)），因此组合根可以把
    本服务直接挂到检测循环的结果出口上，无需任何适配胶水。
    """

    def __init__(self, options: NetServerOptions, device_io: Optional[IDeviceIO] = None):
        self._opts = options
        self._io = device_io
        self._broker = _Broker()
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._started_at = 0.0
        self._shutdown_flag = {"down": False}

    # ------------------------------------------------------------ IResultSink
    def publish(self, result: DetectionResultDTO) -> None:
        """广播一次判定结果（SSE 通道）。非阻塞。"""
        self._broker.publish(result)

    # ------------------------------------------------------------ 生命周期
    def start(self) -> tuple[str, int]:
        """后台线程启动监听，返回实际绑定的 (host, port)。"""
        cfg = self._opts.config
        if cfg.enabled and not cfg.token and cfg.host not in ("127.0.0.1", "localhost", "::1"):
            self._log("WARN", f"net: 绑定 {cfg.host} 且未配置 token，任何网段可达者都能调用推理")

        broker, opts, io = self._broker, self._opts, self._io
        max_bytes = cfg.max_upload_mb * 1024 * 1024
        started_at = self._started_at or time.time()
        shutdown_flag = self._shutdown_flag  # 与 stop() 共享的停止标志

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            server_version = "MoireGuard/0.1"

            # ---- 基础
            def log_message(self, fmt: str, *args: Any) -> None:  # 静默默认访问日志
                pass

            def _authorized(self) -> bool:
                if not cfg.token:
                    return True
                return self.headers.get("Authorization", "") == f"Bearer {cfg.token}"

            def _json(self, code: int, payload: dict) -> None:
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _declared_length(self) -> int:
                """Content-Length 头；非法返回 -1（区分“没写”与“写错了”）。"""
                raw = self.headers.get("Content-Length")
                if raw is None or raw == "":
                    return 0
                try:
                    return int(raw)
                except ValueError:
                    return -1

            def _drain(self, length: int) -> None:
                """排空未被消费的请求体。

                keep-alive 下若不排空就回错误响应，残留字节会被当成下一个请求解析，
                客户端看到的是 Connection reset 而不是我们发的 413。分块读只花带宽，
                不占内存；超过硬上限则直接断连，不陪无限大的声明体量玩。
                """
                remaining = min(length, _DRAIN_HARD_CAP)
                while remaining > 0:
                    chunk = self.rfile.read(min(65536, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                if length > _DRAIN_HARD_CAP:
                    self.close_connection = True

            def _reject(self, code: int, payload: dict, *, drain: bool = True) -> None:
                """先排空请求体再回错误响应。

                顺序不能倒：客户端可能声明 `Connection: close`（urllib 默认就这样），
                先回响应后关连接时，仍在写入的客户端看到的是 reset 而不是我们的状态码。
                实测“先回后排”下 urllib 连发 12 次超限上传仍有相当比例拿不到 413。

                drain=False 用于未通过鉴权的场合：不该为一个身份不明的发送方
                读最多 512 MB 的数据，宁可它拿不到完整响应。排空受 _DRAIN_HARD_CAP 约束。
                """
                declared = self._declared_length()
                if drain and declared > 0:
                    self._drain(declared)
                elif declared > _DRAIN_HARD_CAP:
                    self.close_connection = True
                self._json(code, payload)

            def _read_body(self) -> Optional[bytes]:
                length = self._declared_length()
                if length < 0:
                    self._reject(400, {"error": "bad Content-Length"})
                    return None
                if length > max_bytes:
                    self._reject(413, {"error": f"upload too large, limit={cfg.max_upload_mb}MB"})
                    return None
                return self.rfile.read(length)

            # ---- 路由
            def do_GET(self) -> None:  # noqa: N802 (http.server 命名约定)
                # /healthz 免鉴权：systemd / 监控探针不会带 Bearer，给它加鉴权
                # 等于让"服务健康"被判成"服务已死"（配 token 实测返回 401）。
                # 该端点不触发推理、不回诊断数据，只有一枚 {"ok": true}。
                if self.path == "/healthz":
                    self._json(200, {"ok": True})
                    return
                if not self._authorized():
                    self._json(401, {"error": "unauthorized"})
                    return
                if self.path == "/status":
                    self._handle_status()
                elif self.path == "/stream":
                    self._handle_sse()
                else:
                    self._json(404, {"error": "not found", "endpoints": ["/healthz", "/status", "/detect", "/stream"]})

            def do_POST(self) -> None:  # noqa: N802
                if not self._authorized():
                    # 未鉴权不排空：不给身份不明的发送方白读一大块数据
                    self._reject(401, {"error": "unauthorized"}, drain=False)
                    return
                if self.path != "/detect":
                    self._reject(404, {"error": "not found"})
                    return
                self._handle_detect()

            # ---- 端点实现
            def _handle_status(self) -> None:
                state = "UNASSEMBLED"
                if opts.engine is not None:
                    state = opts.engine.engine_state()
                payload = {
                    "engine_state": state,
                    "service_ready": opts.image_service is not None,
                    "sse_clients": broker.client_count,
                    "uptime_s": round(time.time() - _started(), 1),
                }
                if io is not None and hasattr(io, "snapshot"):
                    payload["adaptation"] = io.snapshot()
                self._json(200, payload)

            def _handle_detect(self) -> None:
                if opts.image_service is None:
                    self._json(503, {"error": "E-ENGINE-NOT-READY: 推理服务未装配"})
                    return
                body = self._read_body()
                if body is None:
                    return
                want_mask = self.headers.get("X-With-Mask", "") == "1"
                try:
                    ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip()
                    if ctype == "application/json":
                        req = json.loads(body.decode("utf-8"))
                        name = str(req.get("name") or "upload")
                        if req.get("data_b64"):
                            raw = base64.b64decode(req["data_b64"])
                            dto = opts.image_service.detect_bytes(raw, name)
                        elif req.get("path"):
                            if not cfg.allow_paths:
                                self._json(403, {"error": "path 引用未启用 (network.allow_paths)"})
                                return
                            dto = opts.image_service.detect_file(str(req["path"]))
                        else:
                            self._json(400, {"error": "JSON 需含 data_b64 或 path"})
                            return
                    else:
                        name = self.headers.get("X-File-Name", "upload")
                        dto = opts.image_service.detect_bytes(body, name)
                except Exception as e:
                    code = str(e).split(":")[0] if ":" in str(e) and str(e).startswith("E-") else "E-DETECT-FAILED"
                    self._json(500, {"error": code, "detail": str(e)[:500]})
                    return

                payload: dict[str, Any] = {"status": "ok", **dto.summary()}
                if want_mask and dto.mask_png:
                    payload["mask_png_b64"] = base64.b64encode(dto.mask_png).decode("ascii")
                broker.publish(dto)
                self._json(200, payload)

            def _handle_sse(self) -> None:
                q = broker.register()
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("X-Accel-Buffering", "no")
                    self.send_header("Connection", "keep-alive")
                    self.end_headers()
                    self.wfile.write(b"retry: 3000\n: moireguard stream\n\n")
                    self.wfile.flush()
                    while not _shutting_down():
                        try:
                            item = q.get(timeout=cfg.sse_heartbeat_s)
                        except queue.Empty:
                            self.wfile.write(b": keepalive\n\n")
                            self.wfile.flush()
                            continue
                        if item is _SSE_SENTINEL:
                            break
                        data = json.dumps(item.summary(), ensure_ascii=False)
                        self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass  # 客户端随时可走，不是异常
                finally:
                    broker.unregister(q)

        def _started() -> float:
            return started_at

        def _shutting_down() -> bool:
            return shutdown_flag["down"]

        self._server = ThreadingHTTPServer((cfg.host, cfg.port), Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="moireguard-http", daemon=True
        )
        self._started_at = time.time()
        self._thread.start()
        bound = self._server.server_address[:2]
        self._log("INFO", f"net: listening on http://{bound[0]}:{bound[1]} (token={'on' if cfg.token else 'off'})")
        return str(bound[0]), int(bound[1])

    def stop(self) -> None:
        """优雅关闭：停监听 → 通知所有 SSE 客户端 → 释放端口。幂等。"""
        srv, th = self._server, self._thread
        self._shutdown_flag["down"] = True  # SSE 循环据此自行退出
        self._server = None
        if srv is not None:
            srv.shutdown()
            srv.server_close()
        self._broker.close_all()
        if th is not None:
            th.join(timeout=3.0)
        self._log("INFO", "net: stopped")

    # ------------------------------------------------------------ 辅助
    def _log(self, level: str, msg: str) -> None:
        if self._io is not None:
            self._io.log(level, msg)
