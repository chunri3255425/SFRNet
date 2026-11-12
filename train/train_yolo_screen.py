"""Stage-1 屏幕 YOLO 微调脚本（SDD v2.4.1 §4.2 / §8 M-A）。

研究侧脚本，不进镜像。ultralytics 只在开发机使用。

用法（SFRNet 仓库根目录）：
  python train/train_yolo_screen.py                     # 默认 YOLOv8n
  python train/train_yolo_screen.py --model yolo26n.pt  # Q10 备选一并验证
  python train/train_yolo_screen.py --val-only --weights runs/detect/train/weights/best.pt

验收（SDD §8 M-A）：val mAP@0.5 >= 0.9（单类任务）。
达不到先查 datasets/screen_detect/README.md 的标注口径是否被污染
（墨水屏/关机屏混入是首要嫌疑），不要先动超参。
"""

from __future__ import annotations

import argparse
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage-1 屏幕 YOLO 微调")
    parser.add_argument(
        "--model",
        default="yolov8n.pt",
        help="预训练权重（默认 yolov8n.pt；YOLO26n 为 Q10 备选，训练时一并验证）",
    )
    parser.add_argument("--data", default=str(REPO_ROOT / "datasets/screen_detect/data.yaml"))
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--batch", type=int, default=16)
    parser.add_argument("--device", default="0", help="开发机 GPU id；无卡用 cpu")
    parser.add_argument("--val-only", action="store_true", help="只跑验证不训练")
    parser.add_argument("--weights", default=None, help="--val-only 时的权重路径")
    args = parser.parse_args()

    from ultralytics import YOLO

    if args.val_only:
        if not args.weights:
            parser.error("--val-only 需要 --weights")
        model = YOLO(args.weights)
        metrics = model.val(data=args.data, imgsz=args.imgsz, device=args.device)
        print("\n==== M-A 验收标准：mAP@0.5 >= 0.9 ====")
        print(f"mAP@0.5     = {metrics.box.map50:.4f}")
        print(f"mAP@0.5:0.95= {metrics.box.map:.4f}")
        if metrics.box.map50 < 0.9:
            print("!! 未达标：先查标注口径（datasets/screen_detect/README.md），勿先调超参")
        return

    model = YOLO(args.model)
    model.train(
        data=args.data,
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        device=args.device,
        # 单类小数据集的合理超参；调参前先确认标注质量
        patience=30,
        seed=0,
        workers=8,
        project=str(REPO_ROOT / "runs" / "yolo_screen"),
        name="v1",
    )

    # 训练完自动跑一次 val，直接给出验收数字
    best = REPO_ROOT / "runs" / "yolo_screen" / "v1" / "weights" / "best.pt"
    model = YOLO(str(best))
    metrics = model.val(data=args.data, imgsz=args.imgsz, device=args.device)
    print("\n==== M-A 验收标准：mAP@0.5 >= 0.9 ====")
    print(f"mAP@0.5     = {metrics.box.map50:.4f}")
    print(f"best.pt 路径: {best}")
    print("下一步：python build/export_yolo.py onnx --weights <best.pt>")


if __name__ == "__main__":
    main()
