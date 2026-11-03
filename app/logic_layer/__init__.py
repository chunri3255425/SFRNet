"""
逻辑层 (Logic Layer)

职责:
  - 编排推理流程: 切图 → 逐块调模型 → 融合 → 后处理 → 统计 → 导出
  - 管理配置与校验
  - 组织滑窗切分与融合

红线:
  ❌ 不做 UI 回调
  ❌ 不持有 Qt 对象
  ❌ 不知道窗体的存在
"""

from app.logic_layer.inference_service import InferenceService, InferenceRequest, InferenceResult
from app.logic_layer.sliding_window import SlidingWindowEngine
from app.logic_layer.postprocess import PostProcessor
from app.logic_layer.export_service import ExportService
from app.logic_layer.config import ConfigManager
from app.logic_layer.exceptions import MoireError, ErrorCode

__all__ = [
    "InferenceService",
    "InferenceRequest",
    "InferenceResult",
    "SlidingWindowEngine",
    "PostProcessor",
    "ExportService",
    "ConfigManager",
    "MoireError",
    "ErrorCode",
]
