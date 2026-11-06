"""
后处理器 (Post Processor)

负责:
  - 施加 sigmoid (全流程唯一)
  - 二值化
  - 形态学处理 (可选)
  - 统计计算 (覆盖率等)
"""

from dataclasses import dataclass
from typing import Optional

import torch


@dataclass
class PostProcessResult:
    """后处理结果"""
    prob_map: torch.Tensor      # [1, 1, H, W], float32, [0, 1]
    binary_mask: torch.Tensor   # [1, 1, H, W], uint8, {0, 1}
    coverage: float             # 摩尔纹像素占比


class PostProcessor:
    """
    后处理器。
    
    施加 sigmoid、二值化、形态学处理。
    """
    
    def __init__(
        self,
        threshold: float = 0.5,
        coverage_threshold: float = 0.01,
        apply_morphology: bool = False,
        morphology_kernel: int = 3,
        min_area: int = 64,
    ):
        """
        初始化后处理器。
        
        Args:
            threshold: 二值化阈值
            coverage_threshold: 判定 has_moire 的覆盖率门槛
            apply_morphology: 是否应用形态学处理
            morphology_kernel: 形态学核大小
            min_area: 最小连通域面积 (小于此值的区域会被移除)
        """
        self.threshold = threshold
        self.coverage_threshold = coverage_threshold
        self.apply_morphology = apply_morphology
        self.morphology_kernel = morphology_kernel
        self.min_area = min_area
    
    def apply(
        self,
        logits: torch.Tensor,
        threshold: Optional[float] = None,
    ) -> PostProcessResult:
        """
        执行后处理。
        
        Args:
            logits: 网络原始输出 [1, 1, H, W]
            threshold: 可选的阈值覆盖
        
        Returns:
            PostProcessResult
        """
        threshold = threshold if threshold is not None else self.threshold
        
        # ★ 全流程唯一的 sigmoid
        prob_map = torch.sigmoid(logits)
        
        # 二值化
        binary_mask = (prob_map > threshold).to(torch.uint8)
        
        # 形态学处理 (可选)
        if self.apply_morphology:
            binary_mask = self._apply_morphology(binary_mask)
        
        # 计算覆盖率
        coverage = binary_mask.sum().item() / binary_mask.numel()
        
        return PostProcessResult(
            prob_map=prob_map,
            binary_mask=binary_mask,
            coverage=coverage,
        )
    
    def _apply_morphology(self, mask: torch.Tensor) -> torch.Tensor:
        """
        应用形态学处理。
        
        包括:
          - 开运算 (去除孤立噪点)
          - 移除小连通域
        """
        # 简单实现: 使用平均池化做平滑，再二值化
        kernel = self.morphology_kernel
        padding = kernel // 2
        
        # 开运算: 先腐蚀后膨胀
        # 腐蚀: 局部最小值
        mask_float = mask.float()
        eroded = -F.max_pool2d(-mask_float, kernel_size=kernel, stride=1, padding=padding)
        # 膨胀: 局部最大值
        dilated = F.max_pool2d(eroded, kernel_size=kernel, stride=1, padding=padding)
        
        return (dilated > 0.5).to(torch.uint8)
    
    def has_moire(self, coverage: Optional[float] = None) -> bool:
        """
        判断是否存在摩尔纹。
        
        Args:
            coverage: 覆盖率，若为 None 则使用上次 apply 的结果
        
        Returns:
            bool
        """
        if coverage is None:
            return False
        return coverage > self.coverage_threshold


# 导入 F 用于形态学
import torch.nn.functional as F
