"""
逻辑层 (Logic Layer)

职责:
  - 编排推理流程: 切图 → 逐块调模型 → 融合 → 后处理 → 统计 → 导出
  - 管理配置与校验
  - 组织滑窗切分与融合

红线:
  ❌ 不 import 任何平台 SDK (v4l2 / onnx / ohos 只能出现在适配层)
  ❌ 不碰 state_dict
  ❌ 不做平台判断
"""

# 惰性导出 (PEP 562): inference_service / postprocess 等间接 import torch，
# 而配置、错误码等纯标准库成员不应因此被拖进重依赖（适配层与网络层
# 只读 config/exceptions，没装 torch 也必须可用）。公开名字保持不变。
_LAZY = {
    "InferenceService": "moireguard.logic_layer.inference_service",
    "InferenceRequest": "moireguard.logic_layer.inference_service",
    "InferenceResult": "moireguard.logic_layer.inference_service",
    "SlidingWindowEngine": "moireguard.logic_layer.sliding_window",
    "PostProcessor": "moireguard.logic_layer.postprocess",
    "ExportService": "moireguard.logic_layer.export_service",
    "ConfigManager": "moireguard.logic_layer.config",
    "MoireError": "moireguard.logic_layer.exceptions",
    "ErrorCode": "moireguard.logic_layer.exceptions",
}

__all__ = list(_LAZY)


def __getattr__(name: str):
    module_path = _LAZY.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    value = getattr(importlib.import_module(module_path), name)
    globals()[name] = value  # 只解析一次
    return value
