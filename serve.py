"""
板端常驻服务入口（组合根）

把三层装配成一个可运行进程：配置 → 注册表 → 推理服务 → 网络出口。

    python serve.py --serve-net            # 启 HTTP 出口（读 configs/default.yaml 的 network 节）
    python serve.py --detect a.jpg         # 单次判定后退出（调试/巡检用）
    python serve.py --selftest             # 不加载权重，只验网络层装配是否通

本文件是组合根：允许同时看见三层具体类型，业务层因此不必互相 import。
注意：这里的 ServiceFacade 只是"协议适配器"，不是 v2.2 的 DetectionEngine
状态机（engine.py / supervisor.py 仍未实现，缺口 D-07 保持开放）。
"""

from __future__ import annotations

import argparse
import io
import sys
import time
from typing import Any, Optional

from moireguard.adaptation.base import (
    DetectionResultDTO,
    IDeviceIO,
    NetServerOptions,
    NetworkConfig,
)


class ServiceFacade:
    """
    把 InferenceService 适配成适配层的 IImageService / IDetectionEngine 协议。

    同时承担 DTO 化：张量结果 → 可序列化的 PNG 字节与标量摘要，
    使适配层与网络层完全不需要 import torch。
    """

    def __init__(self, service: Any, device_io: Optional[IDeviceIO] = None):
        self._service = service
        self._io = device_io
        self._state = "READY"
        self._count = 0

    # ---- IDetectionEngine
    def engine_state(self) -> str:
        return self._state

    # ---- IImageService
    def detect_bytes(self, data: bytes, name: str = "") -> DetectionResultDTO:
        import numpy as np  # 局部 import：仅在真推理路径需要
        from PIL import Image

        img = Image.open(io.BytesIO(data))
        img.load()
        return self._run(img, name or "upload")

    def detect_file(self, path: str) -> DetectionResultDTO:
        from PIL import Image

        img = Image.open(path)
        img.load()
        return self._run(img, str(path))

    # ---- 内部
    def _run(self, image: Any, name: str) -> DetectionResultDTO:
        from moireguard.logic_layer.inference_service import InferenceRequest

        self._state = "BUSY"
        t0 = time.perf_counter()
        try:
            result = self._service.run(InferenceRequest(source=image, model_id="sfrnet_uhd"))
        except Exception:
            self._state = "FATAL"
            if self._io:
                self._io.log("ERROR", f"detect failed: {name}")
            raise
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        self._count += 1
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
    buf = io.BytesIO()
    Image.fromarray(u8, mode="L").save(buf, format="PNG")
    return buf.getvalue()


def build_stack(cfg_network_only: bool = False):
    """装配配置/IO/网络参数。返回 (config_manager, device_io, net_cfg)。"""
    from moireguard.logic_layer.config import ConfigManager

    cm = ConfigManager()
    platform = None
    device_io = None
    if not cfg_network_only:
        from moireguard.adaptation import factory

        platform = factory.detect_platform()
        device_io = factory.build_device_io(platform)
    net_cfg = NetworkConfig.from_dict(cm.config.network)
    return cm, device_io, net_cfg


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="MoireGuard 板端服务入口")
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--serve-net", action="store_true", help="启动网络出口并常驻")
    g.add_argument("--detect", metavar="IMAGE", help="对单张图执行一次判定后退出")
    g.add_argument("--selftest", action="store_true", help="不加载权重，仅验证网络层装配")
    parser.add_argument("--host", default=None, help="覆盖 network.host")
    parser.add_argument("--port", type=int, default=None, help="覆盖 network.port")
    parser.add_argument("--token", default=None, help="覆盖 network.token")
    args = parser.parse_args(argv)

    cm, device_io, net_cfg = build_stack(cfg_network_only=args.selftest)
    if args.host:
        net_cfg.host = args.host
    if args.port:
        net_cfg.port = args.port
    if args.token is not None:
        net_cfg.token = args.token
    net_cfg.enabled = True

    from moireguard.adaptation.network.http_server import HttpNetServer

    if args.selftest:
        # 不碰权重与 torch：用一个抛错的 image_service 验证端点/鉴权/生命周期通
        class _Stub:
            def detect_bytes(self, data, name=""):
                raise RuntimeError("E-ENGINE-NOT-READY: selftest 不加载权重")

            def detect_file(self, path):
                raise RuntimeError("E-ENGINE-NOT-READY: selftest 不加载权重")

        facade = _Stub()
        server = HttpNetServer(NetServerOptions(image_service=facade, config=net_cfg), device_io)
        host, port = server.start()
        try:
            import urllib.request

            with urllib.request.urlopen(f"http://{host}:{port}/status", timeout=3) as r:
                print("GET /status ->", r.status, r.read().decode("utf-8"))
            with urllib.request.urlopen(f"http://{host}:{port}/healthz", timeout=3) as r:
                print("GET /healthz ->", r.status, r.read().decode("utf-8"))
        finally:
            server.stop()
        print("SELFTEST PASS: 网络出口装配可用")
        return 0

    # 真装配：加载权重（缺失/不匹配按 v2.2 §2 属致命，直接退出非零）
    from moireguard.logic_layer.inference_service import InferenceService
    from moireguard.model_layer.registry import ModelRegistry

    try:
        registry = ModelRegistry()
        service = InferenceService(registry, cm)
        facade = ServiceFacade(service, device_io)
    except Exception as e:
        if device_io:
            device_io.log("FATAL", f"boot failed: {e}")
        else:
            print(f"FATAL boot failed: {e}", file=sys.stderr)
        return 2

    if args.detect:
        dto = facade.detect_file(args.detect)
        print(
            f"file={dto.source_id} has_moire={int(dto.has_moire)} "
            f"coverage={dto.coverage:.6f} max_prob={dto.max_prob:.4f} "
            f"elapsed_ms={dto.elapsed_ms:.1f}"
        )
        return 0

    server = HttpNetServer(
        NetServerOptions(image_service=facade, engine=facade, config=net_cfg), device_io
    )
    host, port = server.start()
    print(f"MoireGuard serving on http://{host}:{port}  (Ctrl-C 退出)", file=sys.stderr)

    def _shutdown(signum, frame):
        print("signal received, stopping", file=sys.stderr)
        raise KeyboardInterrupt

    import signal

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _shutdown)
        except (ValueError, AttributeError):
            pass  # 非主线程/平台不支持时依赖 KeyboardInterrupt

    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
        if device_io:
            device_io.indicate(False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
