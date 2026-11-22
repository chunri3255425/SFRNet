"""
检测器模块 (Detectors)

包含各种 DetectorCore 的具体实现。

与上级包同一条导入纪律（BOARD_BRINGUP §19.2）：本 `__init__` 不得模块级
import 含 torch 的实现。`sfrnet` 要 torch，而板端不装 torch，
急加载会把同目录里纯 numpy 的 `decode` / `yolo` 一并拖死，
所以用 PEP 562 `__getattr__` 惰性导出。
"""

import importlib

__all__ = ["SFRNetDetector"]

_LAZY = {
    "SFRNetDetector": "app.model_layer.detectors.sfrnet",
}


def __getattr__(name: str):
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    obj = getattr(importlib.import_module(target), name)
    globals()[name] = obj
    return obj


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LAZY))
