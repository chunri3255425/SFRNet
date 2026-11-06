"""唯一组合根：三层装配 + 四种运行形态。

用法：
  python -m app.main                             # 板端常驻检测循环（默认）
  python -m app.main --backend torch             # 开发机用 PyTorch 后端
  python -m app.main --frames 5                  # 只跑 5 帧自检
  python -m app.main --detect a.jpg              # 单张图判定后退出（巡检/调试）
  python -m app.main --serve-net                 # 起 HTTP 出口并常驻（读 network 节）
  python -m app.main --selftest                  # 不加载权重，只验网络装配

本文件是全项目**唯一**同时看见三层具体类型的位置：业务层因此不必互相 import。

两条运行路径的分工（详见 docs/ARCHITECTURE.md）：
  常驻循环 → DetectionEngine（后端无关，numpy 进 numpy 出，板端不依赖 torch）
  单次图像 → InferenceService（主机侧批处理栈，产出概率图/掩膜，供 HTTP 与 --detect）
判定语义只有一处实现：logic_layer.decider.Decider（全流程恰好一次 sigmoid）。
配置真源只有一处：logic_layer.config（dataclass 默认值 ← yaml ← 平台侧 ← 命令行）。
"""

from __future__ import annotations

import argparse
import io as _io
import signal
import sys
import time
from typing import Any, Optional

from app import adaptation as ada
from app.adaptation.base import (
    DetectionResultDTO,
    IDeviceIO,
    NetServerOptions,
    NetworkConfig,
)
from app.logic_layer.config import ConfigManager
from app.logic_layer.engine import DetectionEngine, DetectionRequest


# =============================================================================
# 单次图像路径：把 InferenceService 适配成适配层的 IImageService / IDetectionEngine
# =============================================================================


class ServiceFacade:
    """协议适配器（不是状态机本体）。

    同时承担 DTO 化：张量结果 → 可序列化的 PNG 字节与标量摘要，
    使适配层与网络层完全不需要 import torch。
    """

    def __init__(self, service: Any, cm: ConfigManager,
                 device_io: Optional[IDeviceIO] = None):
        self._service = service
        self._io = device_io
        self._state = "READY"
        inf = cm.config.inference
        mdl = cm.config.model
        # 判定与切块参数从配置真源取，调用侧不再各自兜默认值
        self._params = dict(
            model_id=mdl.active,
            tile_size=inf.tile_size,
            stride=inf.stride,
            threshold=inf.threshold,
            coverage_threshold=inf.coverage_threshold,
            device=inf.device,
            apply_morphology=inf.apply_morphology,
        )

    # ---- IDetectionEngine
    def engine_state(self) -> str:
        return self._state

    # ---- IImageService
    def detect_bytes(self, data: bytes, name: str = "") -> DetectionResultDTO:
        from PIL import Image

        img = Image.open(_io.BytesIO(data))
        img.load()
        return self._run(img, name or "upload")

    def detect_file(self, path: str) -> DetectionResultDTO:
        from PIL import Image

        img = Image.open(path)
        img.load()
        return self._run(img, str(path))

    # ---- 内部
    def _run(self, image: Any, name: str) -> DetectionResultDTO:
        from app.logic_layer.inference_service import InferenceRequest

        self._state = "BUSY"
        t0 = time.perf_counter()
        try:
            result = self._service.run(InferenceRequest(source=image, **self._params))
        except Exception:
            self._state = "FATAL"
            if self._io:
                self._io.log("ERROR", f"detect failed: {name}")
            raise
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        self._state = "READY"

        prob = result.prob_map.squeeze().detach().cpu().numpy()
        mask = result.binary_mask.squeeze().detach().cpu().numpy()
        return DetectionResultDTO(
            source_id=name,
            has_moire=bool(result.has_moire),
            coverage=float(result.coverage),
            max_prob=float(prob.max()) if prob.size else 0.0,
            mean_prob=float(prob.mean()) if prob.size else 0.0,
            elapsed_ms=elapsed_ms,
            mask_png=_to_png(mask),
            prob_png=_to_png(prob),
            warnings=tuple(result.warnings),
            ts=time.time(),
        )


def _to_png(arr: Any) -> Optional[bytes]:
    """[H,W] float in [0,1] → 灰度 PNG 字节。"""
    if arr is None or getattr(arr, "size", 0) == 0:
        return None
    import numpy as np
    from PIL import Image

    u8 = (np.clip(arr, 0.0, 1.0) * 255.0).astype(np.uint8)
    buf = _io.BytesIO()
    Image.fromarray(u8, mode="L").save(buf, format="PNG")
    return buf.getvalue()


# =============================================================================
# 配置装配：dataclass 默认 ← configs/default.yaml ← 平台侧 ← 命令行
# =============================================================================


def build_config(io: IDeviceIO, args: argparse.Namespace) -> ConfigManager:
    """四段合并，返回唯一的配置对象。"""
    cm = ConfigManager()          # 已含 dataclass 默认 + configs/default.yaml
    cm.merge(io.read_config() or {})   # 板端 /etc + /var/lib 覆盖；开发机同文件
    cli: dict[str, Any] = {}
    runtime: dict[str, Any] = {}
    if args.model:
        runtime["model_path"] = args.model
    if args.backend != "auto":
        runtime["backend"] = args.backend
    if runtime:
        cli["runtime"] = runtime
    inference: dict[str, Any] = {}
    if args.threshold is not None:
        inference["threshold"] = args.threshold
    if args.coverage_threshold is not None:
        inference["coverage_threshold"] = args.coverage_threshold
    if args.tile_size is not None:
        inference["tile_size"] = args.tile_size
    if inference:
        cli["inference"] = inference
    if args.model:
        cli["model"] = {"weights_path": args.model}   # 单次图像路径读 model.weights_path
    if args.relay:
        cli["relay"] = {"enabled": True, "host": args.relay_host, "port": args.relay_port}
    cm.merge(cli)
    return cm


# =============================================================================
# 命令行
# =============================================================================


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser("moireguard")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--detect", metavar="IMAGE", help="对单张图判定后退出")
    g.add_argument("--serve-net", action="store_true", help="启动网络出口并常驻")
    g.add_argument("--selftest", action="store_true", help="不加载权重，仅验网络装配")

    p.add_argument("--backend", default="auto", choices=["auto", "rknn", "onnx", "torch"],
                   help="推理后端（auto：板端 rknn / 开发机 torch，选定不回退）")
    p.add_argument("--model", default=None, help="模型文件路径（覆盖配置，首次加载前生效）")
    p.add_argument("--camera", default=None, help="取帧源：/dev/video0 或 图片/视频路径")
    p.add_argument("--frames", type=int, default=0, help="只检测 N 帧（0=常驻循环）")
    p.add_argument("--interval-ms", type=int, default=0, help="帧间隔（0=全速）")
    p.add_argument("--threshold", type=float, default=None, help="概率二值化阈值（覆盖配置）")
    p.add_argument("--coverage-threshold", dest="coverage_threshold", type=float,
                   default=None, help="判定占比门槛（覆盖配置）")
    p.add_argument("--tile-size", type=int, default=None, help="滑窗边长（覆盖配置）")
    p.add_argument("--relay", action="store_true", help="启用 WiFi 画面回传")
    p.add_argument("--relay-host", default="192.168.1.100")
    p.add_argument("--relay-port", type=int, default=9000)
    p.add_argument("--host", default=None, help="覆盖 network.host")
    p.add_argument("--port", type=int, default=None, help="覆盖 network.port")
    p.add_argument("--token", default=None, help="覆盖 network.token")
    return p.parse_args(argv)


def _net_config(cm: ConfigManager, io: IDeviceIO, args: argparse.Namespace) -> NetworkConfig:
    cfg = NetworkConfig.from_dict(cm.config.network)
    if args.host:
        cfg.host = args.host
    if args.port:
        cfg.port = args.port
    if args.token is not None:
        cfg.token = args.token
    return cfg


# =============================================================================
# 入口
# =============================================================================


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    platform = ada.detect_platform()
    io = ada.make_device_io(platform)
    cm = build_config(io, args)

    # ── 自测：不碰权重与 torch，只验端点/鉴权/生命周期 ──
    if args.selftest:
        class _Stub:
            def detect_bytes(self, data, name=""):
                raise RuntimeError("E-ENGINE-NOT-READY: selftest 不加载权重")

            def detect_file(self, path):
                raise RuntimeError("E-ENGINE-NOT-READY: selftest 不加载权重")

            def engine_state(self) -> str:
                return "READY"

        return _serve(_Stub(), io, _net_config(cm, io, args), block=False)

    # ── 单次图像 / 网络常驻：走主机侧服务栈 ──
    if args.detect or args.serve_net:
        from app.model_layer.registry import ModelRegistry

        try:
            service = _build_host_service(cm)
        except Exception as e:
            io.log("FATAL", f"boot failed: {e}")
            return 2
        facade = ServiceFacade(service, cm, io)
        if args.detect:
            dto = facade.detect_file(args.detect)
            print(f"file={dto.source_id} has_moire={int(dto.has_moire)} "
                  f"coverage={dto.coverage:.6f} max_prob={dto.max_prob:.4f} "
                  f"elapsed_ms={dto.elapsed_ms:.1f}")
            return 0
        cfg = _net_config(cm, io, args)
        cfg.enabled = True
        return _serve(facade, io, cfg, block=True)

    # ── 默认：板端常驻检测循环（状态机 + 错误恢复 + 优雅退出）──
    # 取帧源：命令行 > 配置（仅板端适用）。开发机不给 --camera 那么故意
    # 落到假源，不该去撞一个不存在的 /dev/video0。
    if args.camera:
        source: Optional[str] = args.camera
    elif platform in (ada.factory.LINUX, ada.factory.OHOS):
        source = cm.config.camera.device
    else:
        source = None
    camera = ada.make_camera(platform=platform, device=source)
    runtime = ada.make_runtime(cm.config.runtime.backend)
    relay = ada.make_relay(enabled=cm.config.relay.enabled,
                           host=cm.config.relay.host, port=cm.config.relay.port) \
        if cm.config.relay.enabled else None
    engine = DetectionEngine(camera, runtime, io, relay)

    # ★ 命令行/平台配置在 bootstrap 之前交给引擎（bootstrap 会先加载模型）
    if not engine.bootstrap(cm.as_dict()):
        io.log("ERROR", f"初始化失败: {engine.report.fatal_reason}")
        return 1

    inf = engine.report.config["inference"]
    req = DetectionRequest(
        threshold=float(inf.get("threshold", 0.5)),
        coverage_threshold=float(inf.get("coverage_threshold", 0.01)),
        interval_ms=args.interval_ms,
        tile_size=int(inf.get("tile_size", 512)),
    )

    signal.signal(signal.SIGINT, lambda *_: engine.stop())
    signal.signal(signal.SIGTERM, lambda *_: engine.stop())

    if args.frames > 0:
        for _ in range(args.frames):
            # 逐帧日志已由 engine.detect_once 按附录 B 格式输出，这里只接手退出码
            engine.detect_once(req)
        engine.shutdown()
    else:
        engine.run_forever(req)
    return 0


def _build_host_service(cm: ConfigManager):
    """主机侧单次图像服务（权重缺失/结构不匹配按 v2.2 §2 属致命）。"""
    from app.logic_layer.inference_service import InferenceService
    from app.model_layer.registry import ModelRegistry

    return InferenceService(ModelRegistry(), cm)


def _serve(image_service: Any, io: IDeviceIO, cfg: NetworkConfig, block: bool) -> int:
    """网络出口装配：HTTP 服务只认 IImageService / IDetectionEngine 协议。"""
    from app.adaptation.network.http_server import HttpNetServer

    server = HttpNetServer(
        NetServerOptions(image_service=image_service,
                         engine=image_service if hasattr(image_service, "engine_state") else None,
                         config=cfg),
        io,
    )
    host, port = server.start()
    print(f"MoireGuard serving on http://{host}:{port}  (Ctrl-C 退出)", file=sys.stderr)
    if not block:
        # 自测模式：起服务、打两个端点、收摊
        import urllib.request

        try:
            for path in ("/status", "/healthz"):
                with urllib.request.urlopen(f"http://{host}:{port}{path}", timeout=3) as r:
                    print(f"GET {path} -> {r.status} {r.read().decode('utf-8')[:120]}")
        finally:
            server.stop()
        print("SELFTEST PASS: 网络出口装配可用")
        return 0

    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
        io.indicate(False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
