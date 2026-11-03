"""
SFRNet 检测器实现

包装上游 build_SFR() 为 DetectorCore 协议。
"""

from typing import TYPE_CHECKING

import torch

if TYPE_CHECKING:
    from moireguard.model_layer.contracts import DetectorCore


class SFRNetDetector:
    """
    SFRNet 检测器封装。
    
    将上游 build_SFR() 包装为 DetectorCore 协议。
    """
    
    def __init__(self):
        from upstream.sfr.sfrnet import build_SFR
        self._model = build_SFR()
    
    def forward(self, tile: torch.Tensor) -> torch.Tensor:
        """前向推理: tile → raw logits"""
        return self._model(tile)
    
    def eval(self) -> None:
        """切换到评估模式"""
        self._model.eval()
    
    def to(self, device: str) -> "SFRNetDetector":
        """迁移到指定设备"""
        self._model.to(device)
        return self
    
    def load_state_dict(self, state_dict: dict, strict: bool = True) -> None:
        """加载权重"""
        self._model.load_state_dict(state_dict, strict=strict)
    
    def parameters(self):
        """返回模型参数"""
        return self._model.parameters()
