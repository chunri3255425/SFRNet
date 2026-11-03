"""
逻辑层异常定义

定义统一的错误码与异常类。
"""

from enum import Enum


class ErrorCode(str, Enum):
    """错误码枚举"""
    
    # 权重相关
    WEIGHT_NOT_FOUND = "E-WEIGHT-NOT-FOUND"
    WEIGHT_MISMATCH = "E-WEIGHT-MISMATCH"
    
    # 模型相关
    MODEL_UNKNOWN = "E-MODEL-UNKNOWN"
    MODEL_OUTPUT = "E-MODEL-OUTPUT"
    
    # 输入相关
    INPUT_UNSUPPORTED = "E-INPUT-UNSUPPORTED"
    INPUT_TOO_LARGE = "E-INPUT-TOO-LARGE"
    INPUT_DECODE = "E-INPUT-DECODE"
    
    # 契约相关
    CONTRACT_IN_DIM = "E-CONTRACT-IN-DIM"
    CONTRACT_IN_BATCH = "E-CONTRACT-IN-BATCH"
    CONTRACT_IN_CHANNEL = "E-CONTRACT-IN-CHANNEL"
    CONTRACT_IN_DTYPE = "E-CONTRACT-IN-DTYPE"
    CONTRACT_IN_RANGE = "E-CONTRACT-IN-RANGE"
    CONTRACT_IN_NAN = "E-CONTRACT-IN-NAN"
    CONTRACT_OUT_CHANNEL = "E-CONTRACT-OUT-CHANNEL"
    
    # CUDA 相关
    CUDA_OOM = "E-CUDA-OOM"
    
    # 导出相关
    EXPORT_DENIED = "E-EXPORT-DENIED"
    
    # 取消
    CANCELLED = "E-CANCELLED"


class MoireError(Exception):
    """
    摩尔纹检测领域异常基类。
    
    Attributes:
        code: 错误码
        user_message: 面向用户的文案 (中文、可操作)
        detail: 详细技术信息 (写入日志)
    """
    
    def __init__(
        self,
        code: str | ErrorCode,
        user_message: str = "",
        detail: str = "",
    ):
        if isinstance(code, ErrorCode):
            code = code.value
        
        self.code = code
        self.user_message = user_message or self._default_message(code)
        self.detail = detail
        
        super().__init__(f"[{code}] {self.user_message}")
    
    @staticmethod
    def _default_message(code: str) -> str:
        """根据错误码返回默认用户文案"""
        messages = {
            "E-WEIGHT-NOT-FOUND": "未找到模型权重文件，请检查 assets/weights 目录",
            "E-WEIGHT-MISMATCH": "权重与模型结构不匹配，请确认模型版本",
            "E-MODEL-UNKNOWN": "未知模型，请检查模型 ID",
            "E-INPUT-UNSUPPORTED": "不支持的图片格式，请使用 PNG/JPG/TIFF",
            "E-INPUT-TOO-LARGE": "图片过大，建议先分块处理",
            "E-INPUT-DECODE": "图片解码失败，文件可能已损坏",
            "E-CUDA-OOM": "显存不足，可降低 tile 尺寸或切换到 CPU",
            "E-CANCELLED": "已取消",
            "E-EXPORT-DENIED": "导出目录不可写，请更换位置",
        }
        return messages.get(code, code)
