"""模型转换流水线：SFR_best.pth → ONNX → .rknn（int8 量化 + 一致性比对）。

分工（SDD §1.2，不可装反）：
  - rknn-toolkit2   装在 x86 开发机，做模型转换（本脚本）。✅ **版本已实测判定**（见
                    docs/guides/BOARD_BRINGUP.md §13.1）：用 PyPI 能装到的 **2.3.2**（cp312/cp310 有 x86_64 wheel），
                    产物能被板上厂商 **2.1.0** `librknnrt.so` 加载并推理成功（仅报 version-not-match warning），
                    **不需要替系统库**。⚠ 早期写的“与板上同主版本/钉 2.1.0”已被实测推翻（PyPI 根本无 2.1.0）。
                    转换环境需 `setuptools<81`，并处理 `onnx.mapping` 被删的问题（§13.2）。
  - rknn-lite2      装在板端，做推理（app/adaptation/runtime/rknn_runtime.py）；
                    推理接口叫 `inference()`，没有 `forward()`

用法：
  # 1) pth → ONNX（开发机，有 PyTorch 环境）
  python build/export_model.py onnx --weights assets/weights/SFR_best.pth \
      --out build/models/sfrnet.onnx

  # 2) ONNX → RKNN int8（需 rknn-toolkit2 环境，板型 RK3576）
  python build/export_model.py rknn --onnx build/models/sfrnet.onnx \
      --out build/models/sfrnet.rknn --calib build/calib/ --target rk3576

  # 2') 不量化（fp16，不需校准集）：版本兼容性判定与精度上限参照用
  python build/export_model.py rknn --onnx build/models/sfrnet.onnx \
      --out build/models/sfrnet_fp16.rknn --no-quant

  ✅ 上面两步均已实测跑通（2026-11-07，BOARD_BRINGUP §14）：fp16 产物 1 m 47 s 转出，
  板端 NPU 输出对 onnxruntime 参照最大相对误差 0.0059、相关系数 1.0000。
  （早期写过的“因 FRM nonzero 而跑不通”只对了一半：NonZero 确实被拒，但整图 SIGABRT 的主因是坑 4。）

  # 2'') 中间还有一步不能跳：pth → ONNX 出来直接喂 toolkit2 会 **SIGABRT**（无算子名）。
  # 原因是 Slice/Pad 的 ends/pads 走了计算链、ONNX 形状推不出来（944/1324 张量含符号维度）。
  # 本脚本的 rknn 子命令已自动跑常量折叠（onnxslim）；要跳过用 --no-simplify（只用于取证）。
  python -m onnxslim build/models/sfrnet_static.onnx build/models/sfrnet_sim.onnx

  # 3) 量化一致性比对（可选但强烈建议，SDD §6 风险 #7）
  python build/export_model.py verify --onnx build/models/sfrnet.onnx \
      --rknn build/models/sfrnet.rknn --calib build/calib/
"""

from __future__ import annotations

import argparse
import contextlib
import sys
from pathlib import Path

import numpy as np

# 从仓库根直接 `python build/export_model.py …` 时，sys.path[0] 是 build/ 而不是仓库根，
# 下面补上仓库根，否则 `from app.model_layer.registry …` 会报 ModuleNotFoundError（已实测踩到）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# SFRNet 输入规格
IN_SIZE = 512
MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)

# 预处理口径（实测踩坑，BOARD_BRINGUP §14.4）：rknn 的 mean/std 直接作用在喂进来的张量上，
# **不会**帮你 /255（config 里那行“range should be in [0,255]”警告说的就是这件事）。
# 契约 C1 要求 app 侧已经做完 /255 + ImageNet 归一化，所以这里烘**恒等**预处理，
# 让 .rknn 与 onnxruntime 吃完全同一份输入，两条后端的结果才可比。
MEAN_BAKED: list[float] = [0.0, 0.0, 0.0]
STD_BAKED: list[float] = [1.0, 1.0, 1.0]


# ──────────────────────────── pth → ONNX ────────────────────────────
def export_onnx(weights: str, out: str, static_frm: bool = True) -> int:
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

    ctx = contextlib.nullcontext()
    if static_frm:
        # 默认开：FRM 硬路由的 nonzero 会被 RKNN 拒收，且 trace 会把分支冻结在 dummy 上
        # （实测 14/60 真实切片语义错，见 BOARD_BRINGUP §13.3）。静态变体前向逐字等价。
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from static_frm_export import static_hard_routing
        ctx = static_hard_routing()
        print("[onnx] FRM 静态化已启用（one-hot 掩码求和，不依赖被 trace 冻结的分支）")
    with ctx:
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
def symbolic_dims(onnx_path: str) -> int:
    """数一下形状里还带符号维（unk__*）的张量个数——这就是 toolkit 会不会 abort 的先兆。"""
    import onnx

    m = onnx.shape_inference.infer_shapes(onnx.load(onnx_path), strict_mode=False)
    bad = 0
    for v in list(m.graph.value_info) + list(m.graph.output):
        dims = v.type.tensor_type.shape.dim
        if any(not d.HasField("dim_value") or d.dim_value <= 0 for d in dims):
            bad += 1
    return bad


def simplify(onnx_in: str, onnx_out: str) -> int:
    """常量折叠 + 形状固化（onnxslim，Rockchip 官方转换流程同样建议这一步）。

    不能省：实测跳过它，toolkit2 2.3.2 在 `load_onnx` 完成后直接 SIGABRT（returncode -6），
    不进 fold_constant、不报算子名；做完这一步后符号维度 944 → 8，build 一次过。
    """
    from onnxslim import slim

    slim(onnx_in, onnx_out)
    print(f"[simplify] {onnx_in} -> {onnx_out}，剩余符号维度张量 = "
          f"{symbolic_dims(onnx_out)}")
    return 0


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


def export_rknn(onnx: str, out: str, calib: str, target: str = "rk3576",
                quantize: bool = True, do_simplify: bool = True) -> int:
    try:
        from rknn.api import RKNN
    except ImportError:
        print("[rknn] ❌ 未安装 rknn-toolkit2（该步骤必须在 x86 Linux 开发机执行，"
              "Windows 下用 WSL/Docker；用 PyPI 的 2.3.2 即可，与 BOARD_BRINGUP §13.1 已判定的整链一致；"
              "记得钉 setuptools<81）")
        return 2

    if do_simplify:
        n_sym = symbolic_dims(onnx)
        if n_sym:
            sim = Path(out).with_suffix("").parent / (Path(onnx).stem + "_sim.onnx")
            print(f"[rknn] 发现 {n_sym} 个符号维度张量，先常量折叠 -> {sim}")
            simplify(onnx, str(sim))
            onnx = str(sim)
        else:
            print("[rknn] 形状已全静态，跳过常量折叠")

    rknn = RKNN(verbose=False)
    cfg = dict(mean_values=[MEAN_BAKED], std_values=[STD_BAKED],
               target_platform=target, quant_img_RGB2BGR=False)
    if quantize:
        cfg.update(quantized_dtype="w8a8", quantized_method="channel")
    if rknn.config(**cfg) != 0:
        return 1
    if rknn.load_onnx(model=onnx) != 0:
        print("[rknn] ❌ ONNX 加载失败（检查算子覆盖，SDD §6 风险 #2）")
        return 1
    if quantize:
        print("[rknn] 开始 int8 量化（首次约数分钟）…")
        ret = rknn.build(do_quantization=True, dataset=_write_calib_list(calib))
    else:
        print("[rknn] 未量化，出 fp16 模型（不需校准集；SDD §6 风险 #7 的回退路径）")
        ret = rknn.build(do_quantization=False)
    if ret != 0:
        print("[rknn] ❌ 构建失败")
        return 1
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    if rknn.export_rknn(out) != 0:
        print("[rknn] ❌ 导出失败")
        return 1
    print(f"[rknn] ✅ 导出完成: {out}")
    rknn.release()
    return 0


def _write_calib_list(calib_dir: str) -> str:
    """校准集列表。

    因为烘的是恒等预处理（MEAN_BAKED/STD_BAKED），toolkit 不能再拿 jpg 自己减均值——
    那样校准分布与真实推理输入不一致。这里用与推理同一套 `load_calib_images` 预处理，
    落成 float32 NCHW 的 .npy 再列进 txt（jpg 仍必须是真实屏摄图，SDD §6 风险 #7）。
    写在仓库内，不靠 /tmp（沙箱里常常只读，已踩过）。
    """
    dump = Path("build/probes/calib_npy")
    dump.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, x in enumerate(load_calib_images(calib_dir)):
        p = dump / f"calib_{i:03d}.npy"
        np.save(p, x)
        paths.append(str(p.resolve()))
    list_file = dump / "calib.txt"
    list_file.write_text("\n".join(paths), encoding="utf-8")
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
        # 实测（BOARD_BRINGUP §14.4）：.rknn 返的已经是 NCHW [1,1,H,W]，不能再 transpose；
        # 旧写法 `if shape[1]==1: transpose` 会把它摇成 [1,H,W,1]，静默错位。
        if got.ndim == 4 and got.shape[1] != 1 and got.shape[-1] == 1:
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
    po.add_argument("--legacy-trace-routing", action="store_true",
                    help="不改写 FRM 硬路由，保留 trace 把分支钉在 dummy 上的旧行为（产物不可转 RKNN）")
    pr = sub.add_parser("rknn"); pr.add_argument("--onnx", required=True); pr.add_argument("--out", required=True)
    pr.add_argument("--calib", default="build/calib"); pr.add_argument("--target", default="rk3576")
    pr.add_argument("--no-quant", action="store_true",
                    help="不量化，直接出 fp16（不需校准集；用于版本兼容性判定与精度上限参照）")
    pr.add_argument("--no-simplify", action="store_true",
                    help="跳过常量折叠（只用于取证：不折叠时 toolkit2 会直接 SIGABRT）")
    pv = sub.add_parser("verify"); pv.add_argument("--onnx", required=True); pv.add_argument("--rknn", required=True)
    pv.add_argument("--calib", default="build/calib")

    args = p.parse_args()
    if args.cmd == "onnx":
        return export_onnx(args.weights, args.out, static_frm=not args.legacy_trace_routing)
    if args.cmd == "rknn":
        return export_rknn(args.onnx, args.out, args.calib, args.target,
                           quantize=not args.no_quant, do_simplify=not args.no_simplify)
    return verify(args.onnx, args.rknn, args.calib)


if __name__ == "__main__":
    raise SystemExit(main())
