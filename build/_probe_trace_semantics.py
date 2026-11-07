"""硬路由 trace 语义探针：验证 ONNX 是否把 FRM 的路由决策钉死在了 dummy 输入上。

背景：FRM.process_block_hard_routing 用 `(preds == i).nonzero()` + `if len(idx) > 0`
做数据相关分支。torch.onnx（TorchScript trace）在导出时只会记录 dummy 那一刻走到的
分支，其余分支不进图。若如此，则同一个 ONNX 换一张真实图跑，输出会与 torch 明显偏离
——而导出脚本里的"dummy 自检"永远发现不了这件事。

    python build/_probe_trace_semantics.py
"""
import sys
from pathlib import Path

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

IN_SIZE = 512
MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)
ONNX = "build/models/sfrnet.onnx"


def to_chw(path: str) -> np.ndarray:
    """预处理与 build/export_model.py:load_calib_images 逐字一致，保证对比公平。"""
    import cv2
    img = cv2.imread(path)
    if img is None:
        raise RuntimeError(f"读不到图: {path}")
    img = cv2.cvtColor(cv2.resize(img, (IN_SIZE, IN_SIZE)), cv2.COLOR_BGR2RGB)
    x = (img.astype(np.float32) / 255.0 - MEAN) / STD
    return np.transpose(x, (2, 0, 1))[None]


def main() -> int:
    import onnxruntime as ort
    from app.model_layer.registry import ModelRegistry

    core, _, _ = ModelRegistry().create(model_id="sfrnet_uhd", device="cpu")
    state = torch.load("assets/weights/SFR_best.pth", map_location="cpu", weights_only=False)
    if isinstance(state, dict) and "state_dict" in state:
        state = state["state_dict"]
    core.load_state_dict(state, strict=True)
    net = core._model if hasattr(core, "_model") else core
    net.eval()

    sess = ort.InferenceSession(ONNX, providers=["CPUExecutionProvider"])

    cases = {
        "dummy(高斯随机, 与导出时同分布)": np.random.randn(1, 3, IN_SIZE, IN_SIZE).astype(np.float32),
        "真实摩尔纹照片": to_chw("samples/moire_photo.jpg"),
        "干净屏摄(截图)": to_chw("samples/clean_screenshot.png"),
    }

    print(f"{'输入':34s} {'torch 输出统计':>26s} {'ONNX 输出统计':>24s} 最大相对偏差")
    print("-" * 112)
    for name, x in cases.items():
        x = x.astype(np.float32)
        ref = net(torch.from_numpy(x)).detach().numpy()
        got = sess.run(None, {"input": x})[0]
        rel = np.abs(ref - got).max() / (np.abs(ref).max() + 1e-9)
        print(f"{name:34s} "
              f"mean={ref.mean():+.4f} std={ref.std():.4f}   "
              f"mean={got.mean():+.4f} std={got.std():.4f}   "
              f"rel={rel:.3e} {'⚠️ 路由被钉死' if rel > 0.05 else 'ok'}")
        print(f"{'':34s} 相关系数 ={np.corrcoef(ref.ravel(), got.ravel())[0, 1]:+.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
