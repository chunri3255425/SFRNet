"""Stage-1 YOLO 导出流水线：yolo .pt → 裸主干 ONNX → .rknn（int8 + 框级一致性比对）。

与 build/export_model.py（SFRNet 分割模型导出）并列，遵循同一套纪律（SDD §4.1 A2/A3）：

  ⚠ 第一坑（SDD 风险 #14）：ultralytics 原生 model.export() 的 ONNX 含 decode+NMS，
    RKNN 对 TopK/NonMaxSuppression 动态 shape 支持差。本脚本**裁掉后处理只导主干**：
    三个尺度的 raw 特征图直接出 ONNX，decode+NMS 用
    app/model_layer/detectors/yolo_postprocess.py（同一份 numpy 代码）在 CPU 侧完成。

用法（SFRNet 仓库根目录）：
  # 1) .pt → 裸主干 ONNX（开发机，需 torch + ultralytics）
  python build/export_yolo.py onnx --weights runs/yolo_screen/v1/weights/best.pt \
      --out build/models/yolo_screen.onnx

  # 2) ONNX → RKNN int8（rknn-toolkit2 环境，板型 RK3576；与 export_model.py 同环境）
  python build/export_yolo.py rknn --onnx build/models/yolo_screen.onnx \
      --out build/models/yolo_screen.rknn --calib build/calib_yolo/ --target rk3576

  # 2') fp16 参照（不量化，精度上限参照用）
  python build/export_yolo.py rknn --onnx build/models/yolo_screen.onnx \
      --out build/models/yolo_screen_fp16.rknn --no-quant

  # 3) 框级一致性比对（Q12 验收线）
  python build/export_yolo.py verify --onnx build/models/yolo_screen.onnx \
      --rknn build/models/yolo_screen.rknn --images <实拍图目录>

  验收（SDD §8 M-B）：ONNX 参照与 RKNN 输出逐框 IoU >= 0.95；
  conf 相对误差 > 5% 或召回下降 > 2% → 回退 fp16（Q12）。

⚠ 所有性能/精度数字在实测前标注 [推算]，实测后回填并加 [实机]。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "app" / "model_layer" / "detectors"))

from yolo_postprocess import STRIDES, decode_boxes, letterbox  # noqa: E402

INPUT_SIZE = 640
NC = 1  # 单类 screen


# ---------------------------------------------------------------------------
# 子命令 1：onnx —— 裸主干导出
# ---------------------------------------------------------------------------

def cmd_onnx(args: argparse.Namespace) -> None:
    import torch
    from ultralytics import YOLO

    yolo = YOLO(args.weights)
    det_model = yolo.model.eval()  # DetectionModel

    class _RawHead(torch.nn.Module):
        """裁掉 Detect 头的 decode 后处理，只返回三尺度 raw 特征图。"""

        def __init__(self, model):
            super().__init__()
            self.model = model.model  # DetectionModel 的层列表

        def forward(self, x):
            outs = []
            y = x
            for m in self.model:
                if hasattr(m, "f"):  # C2f/Concat 的索引包装
                    y = m(y)
                else:
                    y = m(y)
                if hasattr(m, "i") and m.i in getattr(self, "_head_idx", ()):
                    outs.append(y)
            return outs

        @staticmethod
        def _find_head_idx(model):
            # Detect 头是最后一个带 dfl 属性的模块
            for i, m in enumerate(model.model):
                if hasattr(m, "dfl"):
                    return (i,)
            raise RuntimeError("未找到 Detect 头（该权重不是 YOLOv8/YOLO26 检测模型？）")

    raw = _RawHead(det_model)
    raw._head_idx = _RawHead._find_head_idx(det_model)
    raw = raw.float().eval()

    dummy = torch.zeros(1, 3, INPUT_SIZE, INPUT_SIZE)
    outs = raw(dummy)
    strides = tuple(INPUT_SIZE // o.shape[-1] for o in outs)
    if strides != STRIDES:
        print(f"⚠ 实际输出 stride {strides} 与默认 {STRIDES} 不一致，"
              f"请同步修改 yolo_postprocess.STRIDES")
        sys.exit(1)

    torch.onnx.export(
        raw, dummy, args.out,
        input_names=["images"],
        output_names=[f"feat_s{s}" for s in STRIDES],
        opset_version=12,
        do_constant_folding=True,
    )
    print(f"✅ 裸主干 ONNX 已导出: {args.out}")
    print("   输出为三尺度 raw 特征图（无 decode/NMS），后处理见 yolo_postprocess.decode_boxes")
    print("   注意：中间可能仍需 onnxslim/simplify 修 shape（SFRNet 导出的 §13.2 坑同样适用）")


# ---------------------------------------------------------------------------
# 子命令 2：rknn —— 转换 + int8 量化（可选）
# ---------------------------------------------------------------------------

def cmd_rknn(args: argparse.Namespace) -> None:
    from rknn.api import RKNN  # x86 开发机，rknn-toolkit2 2.3.2

    rknn = RKNN(verbose=True)
    rknn.config(
        mean_values=[[0, 0, 0]],
        std_values=[[255, 255, 255]],  # 归一化口径：/255 烘进转换（A1，非恒等）
        target_platform="rk3576",
    )
    if rknn.load_onnx(model=args.onnx) != 0:
        sys.exit("E-LOAD-ONNX: 加载失败（若报 shape/算子错，先跑 onnxslim）")
    if rknn.build(do_quantization=not args.no_quant, dataset=args.calib) != 0:
        sys.exit("E-BUILD: 转换失败（int8 需校准集 jpg 列表文件）")
    if rknn.export_rknn(args.out) != 0:
        sys.exit("E-EXPORT: 导出失败")
    print(f"✅ RKNN 已导出: {args.out}（{'int8' if not args.no_quant else 'fp16'}）")


# ---------------------------------------------------------------------------
# 子命令 3：verify —— 框级一致性比对（Q12 验收）
# ---------------------------------------------------------------------------

def _onnx_infer(onnx_path: Path, img: np.ndarray) -> list[np.ndarray]:
    import onnxruntime as ort

    sess = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
    blob = (img / 255.0).astype(np.float32).transpose(2, 0, 1)[None]
    outs = sess.run(None, {"images": blob})
    return outs


def _rknn_infer(rknn_path: Path, img: np.ndarray):
    """开发机 RKNN 推理（x86 模拟器模式 init_runtime(target=None)）。

    最终验收仍以板端实测为准（SDD 纪律：模拟器数字只是中间参照）。
    """
    from rknn.api import RKNN

    sess = RKNN(verbose=False)
    sess.load_rknn(str(rknn_path))
    sess.init_runtime()  # x86 模拟器
    blob = (img / 255.0).astype(np.float32).transpose(2, 0, 1)[None]
    return sess.inference(inputs=[blob])


def _match_boxes(ref: np.ndarray, test: np.ndarray, iou_thres: float = 0.5):
    """贪心 IoU 匹配：ref/test 均为 [N,5] (cx,cy,w,h,conf)。返回 (matched_iou, matched_conf_err)。"""
    if len(ref) == 0 or len(test) == 0:
        return [], []
    r = ref[np.argsort(-ref[:, 4])]
    t = test[np.argsort(-test[:, 4])]
    matched_iou, matched_err, used = [], [], set()
    for rb in r:
        best_iou, best_j = 0.0, -1
        for j, tb in enumerate(t):
            if j in used:
                continue
            ax1, ay1, ax2, ay2 = rb[0]-rb[2]/2, rb[1]-rb[3]/2, rb[0]+rb[2]/2, rb[1]+rb[3]/2
            bx1, by1, bx2, by2 = tb[0]-tb[2]/2, tb[1]-tb[3]/2, tb[0]+tb[2]/2, tb[1]+tb[3]/2
            inter = max(0, min(ax2, bx2)-max(ax1, bx1)) * max(0, min(ay2, by2)-max(ay1, by1))
            union = rb[2]*rb[3] + tb[2]*tb[3] - inter
            iou = inter / (union + 1e-9)
            if iou > best_iou:
                best_iou, best_j = iou, j
        if best_j >= 0 and best_iou >= iou_thres:
            used.add(best_j)
            matched_iou.append(best_iou)
            matched_err.append(abs(t[best_j][4] - rb[4]) / max(rb[4], 1e-9))
        else:
            matched_iou.append(0.0)  # 参照框没对上 → 记漏
    return matched_iou, matched_err


def cmd_verify(args: argparse.Namespace) -> None:
    """逐图：ONNX 主干 + numpy 后处理 vs RKNN 主干 + 同一份后处理，逐框比对（Q12）。"""
    images = sorted(p for p in Path(args.images).iterdir()
                    if p.suffix.lower() in {".jpg", ".png", ".jpeg"})
    if not images:
        sys.exit("E-NO-IMG: 图片目录为空")

    import cv2

    all_iou, all_err, ref_total, test_total = [], [], 0, 0
    for p in images:
        img = cv2.imread(str(p))[:, :, ::-1]  # BGR→RGB
        lb, _, _, _ = letterbox(img, INPUT_SIZE)
        ref = decode_boxes(_onnx_infer(Path(args.onnx), lb), nc=NC)
        test = decode_boxes(_rknn_infer(Path(args.rknn), lb), nc=NC)
        ious, errs = _match_boxes(ref, test)
        all_iou.extend(ious)
        all_err.extend(errs)
        ref_total += len(ref)
        test_total += len(test)
        print(f"  {p.name}: ref框={len(ref)} rknn框={len(test)}")

    iou_arr = np.array(all_iou)
    err_arr = np.array(all_err)
    good = (iou_arr >= 0.95).mean() if len(iou_arr) else 0.0
    mean_iou = iou_arr.mean() if len(iou_arr) else 0.0
    mean_err = err_arr.mean() if len(err_arr) else 0.0
    recall_drop = 1.0 - (test_total / max(ref_total, 1))
    print("\n==== M-B / Q12 框级一致性验收 ====")
    print(f"匹配框平均 IoU        = {mean_iou:.4f}   （逐框 IoU>=0.95 占比 {good:.1%}）")
    print(f"conf 平均相对误差     = {mean_err:.4f}")
    print(f"框数比 rknn/ref       = {test_total}/{ref_total}（召回下降 {recall_drop:.1%}）")
    verdict = good >= 0.95 and mean_err <= 0.05 and recall_drop <= 0.02
    print("✅ 通过 int8 验收" if verdict else "❌ 未达标 → 按 Q12 回退 fp16 重转")
    print("⚠ 本结果为 x86 模拟器参照；板端实测数字 [实机] 后回填 SDD §3.3 预算表。")


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage-1 YOLO 导出流水线")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("onnx", help=".pt → 裸主干 ONNX")
    p1.add_argument("--weights", required=True)
    p1.add_argument("--out", default=str(REPO_ROOT / "build/models/yolo_screen.onnx"))

    p2 = sub.add_parser("rknn", help="ONNX → RKNN（int8/fp16）")
    p2.add_argument("--onnx", required=True)
    p2.add_argument("--out", default=str(REPO_ROOT / "build/models/yolo_screen.rknn"))
    p2.add_argument("--calib", default=str(REPO_ROOT / "build/calib_yolo/calib.txt"))
    p2.add_argument("--no-quant", action="store_true", help="fp16（精度参照）")
    p2.add_argument("--target", default="rk3576")

    p3 = sub.add_parser("verify", help="框级一致性比对（Q12 验收）")
    p3.add_argument("--onnx", required=True)
    p3.add_argument("--rknn", required=True)
    p3.add_argument("--images", required=True)

    args = parser.parse_args()
    {"onnx": cmd_onnx, "rknn": cmd_rknn, "verify": cmd_verify}[args.cmd](args)


if __name__ == "__main__":
    main()
