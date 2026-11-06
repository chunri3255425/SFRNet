"""本地模拟器入口：不改任何现有代码，把 FileCamera 接进 DetectionEngine。

用法（在 D:/VIEW/SFRNet 下）：
  python run_sim.py --source build/test_moire.jpg --frames 10 --show
  python run_sim.py --source 测试视频.mp4 --frames 30 --show --relay
  python run_sim.py --source 0 --backend torch --show            # 笔记本摄像头
  python run_sim.py --source build/test_moire.jpg --relay        # 配合 view_relay.py 看画面

--show 会弹出实时窗口：左=采集帧，右=概率热图，角标显示 has_moire/coverage。
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import cv2
import numpy as np

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")

WINDOW = "MoireGuard Sim"


def parse_args(argv=None):
    p = argparse.ArgumentParser("moireguard-sim")
    p.add_argument("--source", required=True,
                   help="视频文件 / 图片路径 / 摄像头序号(0/1)")
    p.add_argument("--backend", default="onnx", choices=["onnx", "torch"])
    p.add_argument("--model", default=None,
                   help="onnx 默认 build/models/SFR_best.onnx；torch 默认 assets/weights/SFR_best.pth")
    p.add_argument("--fps", type=float, default=2.0, help="模拟取帧帧率（默认 2，方便肉眼观察）")
    p.add_argument("--frames", type=int, default=10, help="0=常驻循环（Ctrl+C / 按 q 退出）")
    p.add_argument("--interval-ms", type=int, default=0)
    p.add_argument("--show", action="store_true", help="弹出实时显示窗口")
    p.add_argument("--relay", action="store_true", help="启用 WiFi 画面回传")
    p.add_argument("--relay-host", default="127.0.0.1")
    p.add_argument("--relay-port", type=int, default=9000)
    return p.parse_args(argv)


def _show_frame(frame_rgb: np.ndarray, result) -> None:
    """弹窗显示：左半采集帧 + 角标；右半概率热图。按 q 可退出。"""
    bgr = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)
    prob_map = getattr(result, "prob_map", None)

    if prob_map is not None:
        pm = prob_map.detach().cpu().numpy() if hasattr(prob_map, "detach") else np.asarray(prob_map)
        pm = np.clip((pm - pm.min()) / (pm.max() - pm.min() + 1e-9), 0, 1)
        heat = cv2.applyColorMap((pm * 255).astype(np.uint8), cv2.COLORMAP_JET)
        heat = cv2.resize(heat, (bgr.shape[1], bgr.shape[0]))
        panel = np.concatenate([bgr, heat], axis=1)
    else:
        panel = cv2.hconcat([bgr, bgr])

    color = (0, 0, 255) if result.has_moire else (0, 200, 0)
    tag = f"has_moire={int(result.has_moire)}  coverage={result.coverage:.4f}  tiles={result.tiles}  {result.elapsed_ms:.0f}ms"
    cv2.rectangle(panel, (0, 0), (panel.shape[1], 46), (30, 30, 30), -1)
    cv2.putText(panel, tag, (12, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2, cv2.LINE_AA)
    cv2.imshow(WINDOW, panel)
    if cv2.waitKey(1) & 0xFF == ord("q"):
        raise KeyboardInterrupt


def main(argv=None) -> int:
    args = parse_args(argv)

    from app.adaptation.camera.file_camera import FileCamera
    from app.adaptation.device_io.linux_io import HostDeviceIO
    from app.adaptation.relay.wifi_frame_relay import WifiFrameRelay
    from app.logic_layer.engine import DetectionEngine, DetectionRequest

    # ── 模型 ──
    if args.model:
        model_path = str(Path(args.model).resolve())
    elif args.backend == "onnx":
        model_path = str(ROOT / "build" / "models" / "SFR_best.onnx")
    else:
        model_path = str(ROOT / "assets" / "weights" / "SFR_best.pth")
    if not Path(model_path).exists():
        print(f"[sim] 模型不存在: {model_path}")
        print("      onnx 先跑: PYTHONPATH=. python build/export_model.py onnx "
              "--weights assets/weights/SFR_best.pth --out build/models/SFR_best.onnx")
        return 1

    if args.backend == "onnx":
        from app.adaptation.runtime.onnx_runtime import OnnxRuntime
        runtime = OnnxRuntime()
    else:
        from app.adaptation.runtime.torch_runtime import TorchRuntime
        runtime = TorchRuntime()

    # ── 装配（模拟器专用：FileCamera + HostDeviceIO，复用 engine/relay 不动一个字）──
    source = int(args.source) if args.source.isdigit() else args.source
    camera = FileCamera(source, fps=args.fps)
    io = HostDeviceIO(config={
        "runtime": {"model_path": model_path, "backend": args.backend},
        "relay": {"enabled": args.relay, "host": args.relay_host,
                  "port": args.relay_port},
    })
    relay = WifiFrameRelay() if args.relay else None
    engine = DetectionEngine(camera=camera, runtime=runtime, io=io, relay=relay)

    if not engine.bootstrap():
        print(f"[sim] 初始化失败: {engine.report.fatal_reason}")
        return 1
    print(f"[sim] 状态={engine.state}  backend={args.backend}  model={Path(model_path).name}")

    req = DetectionRequest(interval_ms=args.interval_ms)
    n = 0
    try:
        while True:
            try:
                result = engine.detect_once(req)
            except Exception as e:
                # 模拟器容错：单帧失败不退出，重置状态后重试
                print(f"[sim] 第{n+1}帧失败: {type(e).__name__}: {e}")
                if engine.state not in ("FATAL", "SHUTDOWN"):
                    engine.state = "DEGRADED"
                time.sleep(0.5)
                continue
            n += 1
            # 取最新帧用于显示（detect_once 内部已 grab，这里直接向 camera 再要同一帧的显示副本）
            if args.show:
                _show_frame(camera.read(), result)
            if args.frames > 0 and n >= args.frames:
                break
            if args.interval_ms > 0:
                time.sleep(max(0.0, args.interval_ms / 1000.0 - result.elapsed_ms / 1000.0))
    except KeyboardInterrupt:
        print("\n[sim] 收到退出信号")
    finally:
        engine.shutdown()
        if args.show:
            cv2.waitKey(1500)          # 窗口停留 1.5s 让你看清最后一帧
            cv2.destroyAllWindows()
    print(f"[sim] 完成，共检测 {n} 帧，最终结果: has_moire={result.has_moire} "
          f"coverage={result.coverage:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
