"""适配层 — 屏蔽平台差异（摄像头 / 推理后端 / 画面回传 / 设备 IO）。"""

from app.adaptation import factory

__all__ = ["factory"]
