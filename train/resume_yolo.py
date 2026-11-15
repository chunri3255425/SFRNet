# 断点续训：从 last.pt 恢复（训练中断后用）
from ultralytics import YOLO
YOLO(r"D:\VIEW\SFRNet\runs\yolo_screen\v1\weights\last.pt").train(resume=True)
