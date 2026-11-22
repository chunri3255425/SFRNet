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

导入纪律（2026-11-16，BOARD_BRINGUP §19.2）：
  本包**不得在模块级 import torch**。板端按 SDD §1.2/§2 不装 PyTorch，而 Python
  导入任何子模块都要先执行各级包 __init__；一旦这里急加载 contracts/registry
  （两者都 import torch），纯 numpy 的 `detectors.decode` 与
  `detectors.yolo` 在板端也会连 import 都失败，Stage-1 门控直接进不了进程。
  所以下面用 PEP 562 `__getattr__` 惰性导出：用到谁才加载谁。
"""

import importlib

__all__ = [
    "IInputAdapter",
    "IOutputAdapter",
    "DetectorCore",
    "DetectorOutput",
    "InputMeta",
    "ModelBundle",
    "ModelRegistry",
]

# 符号 → 宿主模块。新增导出只改这张表，不要把 import 提回模块级。
_LAZY = {
    "IInputAdapter": "app.model_layer.contracts",
    "IOutputAdapter": "app.model_layer.contracts",
    "DetectorCore": "app.model_layer.contracts",
    "DetectorOutput": "app.model_layer.contracts",
    "InputMeta": "app.model_layer.contracts",
    "ModelBundle": "app.model_layer.contracts",
    "ModelRegistry": "app.model_layer.registry",
}


def __getattr__(name: str):
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    obj = getattr(importlib.import_module(target), name)
    globals()[name] = obj          # 缓存：后续走正常属性查找
    return obj


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LAZY))
