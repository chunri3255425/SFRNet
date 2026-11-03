"""
模型层 (Model Layer)

职责:
  - 把外部图像规范成模型可消费的张量
  - 调用网络内核
  - 把原始输出规范成统一的 DetectorOutput

红线:
  ❌ 不做阈值判断
  ❌ 不做滑窗
  ❌ 不做文件 IO
  ❌ 不读配置
  ❌ 不打印日志
"""

from moireguard.model_layer.contracts import (
    IInputAdapter,
    IOutputAdapter,
    DetectorCore,
    DetectorOutput,
    InputMeta,
    ModelBundle,
)
from moireguard.model_layer.registry import ModelRegistry

__all__ = [
    "IInputAdapter",
    "IOutputAdapter",
    "DetectorCore",
    "DetectorOutput",
    "InputMeta",
    "ModelBundle",
    "ModelRegistry",
]
