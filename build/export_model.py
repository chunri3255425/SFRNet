"""模型转换流水线：SFR_best.pth → ONNX → .rknn（int8 量化 + 一致性比对）。

分工（SDD §1.2，不可装反）：
  - rknn-toolkit2   装在 x86 开发机，做模型转换（本脚本）
  - rknn-lite2      装在板端，做推理（app/adaptation/runtime/rknn_runtime.py）

用法：
  # 1) pth → ONNX（开发机，有 PyTorch 环境）
  python build/export_model.py onnx --weights assets/weights/SFR_best.pth \
      --out build/models/sfrnet.onnx

  # 2) ONNX → RKNN int8（需 rknn-toolkit2 环境，板型 RK3576）
  python build/export_model.py rknn --onnx build/models/sfrnet.onnx \
      --out build/models/sfrnet.rknn --calib build/calib/ --target rk3588s

  # 3) 量化一致性比对（可选但强烈建议，SDD §6 风险 #7）
  python build/export_model.py verify --onnx build/models/sfrnet.onnx \
      --rknn build/models/sfrnet.rknn --calib build/calib/
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

# SFRNet 输入规格
IN_SIZE = 512
MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


# ──────────────────────────── pth → ONNX ────────────────────────────
def export_onnx(weights: str, out: str) -> int:
    import torch

    from app.model_layer.registry import ModelRegistry
    core, _, _ = ModelRegistry().create(model_id="sfrnet_uhd", device="cpu")
    state = torch.load(weights, map_location="cpu", weights_only=False)
    if isinstance(state, dict) and "state_dict" in state:
        state = state["state_dict"]
    core.load_state_dict(state, strict=True)
    core.eval()
    # SFRNetDetector 是包装类，真正的 nn.Module 在 _model 里（torch.onnx.export 需要 nn.Module）
    net = core._model if hasattr(core, "_model") else core
    net.eval()

    dummy = torch.randn(1, 3, IN_SIZE, IN_SIZE)
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    torch.onnx.export(
        net, dummy, out,
        input_names=["input"], output_names=["logits"],
        opset_version=17, dynamo=False,
    )
    print(f"[onnx] 导出完成: {out}")

    # 数值自检：torch vs onnx
    try:
        import onnxruntime as ort
        sess = ort.InferenceSession(out, providers=["CPUExecutionProvider"])
        ref = net(dummy).detach().numpy()
        got = sess.run(None, {"input": dummy.numpy()})[0]
        rel = np.abs(ref - got).max() / (np.abs(ref).max() + 1e-9)
        print(f"[onnx] 数值一致性 最大相对误差 = {rel:.2e} "
              f"{'✅ 通过' if rel < 1e-3 else '❌ 超差，检查算子'}")
        return 0 if rel < 1e-3 else 1
    except ImportError:
        print("[onnx] 未安装 onnxruntime，跳过数值自检")
        return 0


# ──────────────────────────── ONNX → RKNN ────────────────────────────
def load_calib_images(calib_dir: str, max_n: int = 200) -> list[np.ndarray]:
    """量化校准集：必须用真实屏摄图（SDD §6 风险 #7），禁用合成图。"""
    import cv2

    imgs = []
    for p in sorted(Path(calib_dir).glob("*.jpg"))[:max_n]:
        img = cv2.imread(str(p))
        if img is None:
            continue
        img = cv2.cvtColor(cv2.resize(img, (IN_SIZE, IN_SIZE)), cv2.COLOR_BGR2RGB)
        x = (img.astype(np.float32) / 255.0 - MEAN) / STD
        imgs.append(np.transpose(x, (2, 0, 1))[None])   # NCHW
    if not imgs:
        raise RuntimeError(f"校准集为空: {calib_dir}（需 100–200 张真实屏摄图）")
    print(f"[rknn] 校准集加载 {len(imgs)} 张")
    return imgs


def export_rknn(onnx: str, out: str, calib: str, target: str = "rk3588s") -> int:
    try:
        from rknn.api import RKNN
    except ImportError:
        print("[rknn] ❌ 未安装 rknn-toolkit2（该步骤必须在 x86 Linux 开发机执行，"
              "Windows 下用 WSL/Docker: docker pull lucklove/rknn-toolkit2）")
        return 2

    rknn = RKNN(verbose=False)
    if rknn.config(
        mean_values=[[*MEAN.tolist()]],
        std_values=[[*STD.tolist()]],
        target_platform=target,
        quantized_dtype="w8a8",
        quantized_method="channel",
        quant_img_RGB2BGR=False,
    ) != 0:
        return 1
    if rknn.load_onnx(model=onnx) != 0:
        print("[rknn] ❌ ONNX 加载失败（检查算子覆盖，SDD §6 风险 #2）")
        return 1
    print("[rknn] 开始 int8 量化（首次约数分钟）…")
    if rknn.build(do_quantization=True, dataset=_write_calib_list(calib)) != 0:
        print("[rknn] ❌ 量化失败")
        return 1
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    if rknn.export_rknn(out) != 0:
        print("[rknn] ❌ 导出失败")
        return 1
    print(f"[rknn] ✅ 导出完成: {out}")
    rknn.release()
    return 0


def _write_calib_list(calib_dir: str) -> str:
    import tempfile
    files = sorted(str(p) for p in Path(calib_dir).glob("*.jpg"))[:200]
    list_file = Path(tempfile.mkstemp(suffix=".txt")[1])
    list_file.write_text("\n".join(files), encoding="utf-8")
    return str(list_file)


# ──────────────────────── 量化一致性比对 ────────────────────────
def verify(onnx: str, rknn: str, calib: str) -> int:
    """SDD §4.1 要点 3：量化前后 logits 一致性，相对误差 > 5% 告警。"""
    try:
        from rknnlite.api import RKNNLite   # 或用 rknn-toolkit2 的 init_runtime(target=None)
    except ImportError:
        print("[verify] ❌ 需要 rknn 环境（开发机可装 rknn-toolkit2 后用其模拟）")
        return 2
    import onnxruntime as ort

    sess = ort.InferenceSession(onnx, providers=["CPUExecutionProvider"])
    lite = RKNNLite()
    lite.load_rknn(rknn)
    lite.init_runtime()

    worst = 0.0
    for x in load_calib_images(calib, max_n=50):
        ref = sess.run(None, {"input": x})[0]
        got = lite.inference(inputs=[np.transpose(x, (0, 2, 3, 1))])[0]
        if got.ndim == 4 and got.shape[1] == 1:
            got = np.transpose(got, (0, 3, 1, 2))
        rel = np.abs(ref - got).max() / (np.abs(ref).max() + 1e-9)
        worst = max(worst, rel)
    print(f"[verify] 最大相对误差 = {worst:.4f} "
          f"{'✅ 通过（<5%）' if worst < 0.05 else '⚠ 超差 → 降位宽或回退 fp16 .rknn（SDD §6 风险 #7）'}")
    return 0 if worst < 0.05 else 1


def main() -> int:
    p = argparse.ArgumentParser("export_model")
    sub = p.add_subparsers(dest="cmd", required=True)

    po = sub.add_parser("onnx"); po.add_argument("--weights", required=True); po.add_argument("--out", required=True)
    pr = sub.add_parser("rknn"); pr.add_argument("--onnx", required=True); pr.add_argument("--out", required=True)
    pr.add_argument("--calib", default="build/calib"); pr.add_argument("--target", default="rk3588s")
    pv = sub.add_parser("verify"); pv.add_argument("--onnx", required=True); pv.add_argument("--rknn", required=True)
    pv.add_argument("--calib", default="build/calib")

    args = p.parse_args()
    if args.cmd == "onnx":
        return export_onnx(args.weights, args.out)
    if args.cmd == "rknn":
        return export_rknn(args.onnx, args.out, args.calib, args.target)
    return verify(args.onnx, args.rknn, args.calib)


if __name__ == "__main__":
    raise SystemExit(main())
