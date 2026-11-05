"""入口：bootstrap → run_forever / detect_once。

用法：
  python -m app.main                      # 常驻检测循环（板端默认）
  python -m app.main --backend torch      # 开发机用 PyTorch 后端
  python -m app.main --model x.rknn      # 指定模型文件
  python -m app.main --frames 5           # 只跑 5 帧自检
"""

from __future__ import annotations

import argparse
import signal
import sys

from app import adaptation as ada
from app.adaptation.relay.wifi_frame_relay import WifiFrameRelay
from app.logic_layer.engine import DetectionEngine, DetectionRequest


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser("moireguard")
    p.add_argument("--backend", default="auto",
                   choices=["auto", "rknn", "onnx", "torch"],
                   help="推理后端（auto: 板端 rknn→onnx→torch，开发机 torch 优先）")
    p.add_argument("--model", default=None, help="模型文件路径（覆盖配置）")
    p.add_argument("--camera", default="/dev/video0", help="摄像头设备节点")
    p.add_argument("--frames", type=int, default=0, help="只检测 N 帧（0=常驻循环）")
    p.add_argument("--interval-ms", type=int, default=0, help="帧间隔（0=全速）")
    p.add_argument("--relay", action="store_true", help="启用 WiFi 画面回传")
    p.add_argument("--relay-host", default="192.168.1.100")
    p.add_argument("--relay-port", type=int, default=9000)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])

    # ── 装配三层（factory 是唯一平台分支处）──
    camera = ada.make_camera(device=args.camera)
    runtime = ada.make_runtime(args.backend)
    io = ada.make_device_io()
    relay = WifiFrameRelay() if args.relay else None

    engine = DetectionEngine(camera, runtime, io, relay)

    # ── 上电初始化 ──
    if not engine.bootstrap():
        io.log("ERROR", f"初始化失败: {engine.report.fatal_reason}")
        return 1

    # ── 停止信号 → 优雅退出 ──
    signal.signal(signal.SIGINT, lambda *_: engine.stop())
    signal.signal(signal.SIGTERM, lambda *_: engine.stop())

    req = DetectionRequest(interval_ms=args.interval_ms)
    if args.model:
        engine.report.config["runtime"]["model_path"] = args.model

    try:
        if args.frames > 0:
            for _ in range(args.frames):
                engine.detect_once(req)
            engine.shutdown()
        else:
            engine.run_forever(req)
    except KeyboardInterrupt:
        engine.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
