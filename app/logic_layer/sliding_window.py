"""
滑窗引擎 (Sliding Window Engine)

负责:
  - 切分: 将大图切成重叠的小块 (tiles)
  - 融合: 将小块结果融合成整图，使用余弦窗消除接缝

核心设计:
  - 在 logits 域累加求平均 (不先 sigmoid)
  - 使用余弦窗作为重叠区权重
"""

from dataclasses import dataclass
from typing import Callable, Optional

import torch
import torch.nn.functional as F
import math


@dataclass
class Rect:
    """矩形区域"""
    x: int
    y: int
    width: int
    height: int
    
    @property
    def slice(self) -> tuple[slice, slice]:
        """返回可用于索引的 slice 对象"""
        return (slice(self.y, self.y + self.height), slice(self.x, self.x + self.width))


class SlidingWindowEngine:
    """
    滑窗引擎。
    
    管理切分与融合逻辑。
    """
    
    def __init__(
        self,
        tile_size: int = 512,
        stride: int = 256,
        fusion: str = "cosine",  # "uniform" | "cosine"
    ):
        """
        初始化滑窗引擎。
        
        Args:
            tile_size: 小块尺寸
            stride: 步长 (必须 <= tile_size 才有重叠)
            fusion: 融合方式 ("uniform" | "cosine")
        """
        if stride > tile_size:
            raise ValueError(f"stride ({stride}) must be <= tile_size ({tile_size})")
        
        self.tile_size = tile_size
        self.stride = stride
        self.fusion = fusion
        
        # 预计算余弦窗
        self._cosine_window: Optional[torch.Tensor] = None
        if fusion == "cosine":
            self._cosine_window = self._make_cosine_window(tile_size)
    
    @staticmethod
    def _make_cosine_window(size: int) -> torch.Tensor:
        """
        创建 2D 余弦窗 (Hann window)。
        
        中心权重高，边缘权重低，用于消除接缝。
        """
        # 1D Hann window
        hann_1d = torch.hann_window(size, periodic=False)
        # 2D outer product
        window_2d = hann_1d.unsqueeze(1) * hann_1d.unsqueeze(0)
        # 确保最小值不为 0 (避免除零)
        window_2d = window_2d.clamp(min=1e-6)
        # 归一化到 [0, 1]
        window_2d = window_2d / window_2d.max()
        return window_2d
    
    def plan(self, h: int, w: int) -> list[Rect]:
        """
        规划切分方案。
        
        Args:
            h: 图像高度
            w: 图像宽度
        
        Returns:
            矩形区域列表
        """
        tile = self.tile_size
        stride = self.stride
        
        # 小图处理: 单块 + 反射填充
        if h <= tile and w <= tile:
            return [Rect(0, 0, tile, tile)]
        
        rects = []
        
        # 计算行列数
        rows = []
        y = 0
        while y + tile <= h:
            rows.append(y)
            y += stride
        # 尾部回退贴边
        if rows[-1] + tile < h:
            rows.append(h - tile)
        # 去重
        rows = sorted(set(rows))
        
        cols = []
        x = 0
        while x + tile <= w:
            cols.append(x)
            x += stride
        if cols[-1] + tile < w:
            cols.append(w - tile)
        cols = sorted(set(cols))
        
        # 生成所有矩形
        for y in rows:
            for x in cols:
                rects.append(Rect(x, y, tile, tile))
        
        return rects
    
    def fuse(
        self,
        tiles: list[torch.Tensor],
        rects: list[Rect],
        h: int,
        w: int,
        device: torch.device,
    ) -> torch.Tensor:
        """
        融合小块结果为整图。
        
        Args:
            tiles: 小块 logits 列表，每项 shape [1, 1, tile, tile]
            rects: 对应的矩形区域列表
            h: 原图高度
            w: 原图宽度
            device: 设备
        
        Returns:
            整图 logits [1, 1, h, w]
        """
        # 初始化缓冲
        numerator = torch.zeros(1, 1, h, w, device=device, dtype=torch.float32)
        denominator = torch.zeros(1, 1, h, w, device=device, dtype=torch.float32)
        
        # 获取权重窗
        if self.fusion == "cosine" and self._cosine_window is not None:
            window = self._cosine_window.to(device)
        else:
            window = torch.ones(self.tile_size, self.tile_size, device=device)
        
        # 累加
        for tile, rect in zip(tiles, rects):
            # tile: [1, 1, tile_size, tile_size]
            # 获取实际要写入的区域 (处理边界)
            y_slice = slice(rect.y, min(rect.y + rect.height, h))
            x_slice = slice(rect.x, min(rect.x + rect.width, w))
            
            # 计算 tile 中要使用的部分
            tile_y_end = y_slice.stop - y_slice.start
            tile_x_end = x_slice.stop - x_slice.start
            
            tile_crop = tile[:, :, :tile_y_end, :tile_x_end]
            window_crop = window[:tile_y_end, :tile_x_end]
            
            # 累加 (logits 域)
            numerator[:, :, y_slice, x_slice] += tile_crop * window_crop.unsqueeze(0).unsqueeze(0)
            denominator[:, :, y_slice, x_slice] += window_crop.unsqueeze(0).unsqueeze(0)
        
        # 归一化 (避免除零)
        denominator = denominator.clamp(min=1e-8)
        result = numerator / denominator
        
        return result
    
    def __call__(
        self,
        image: torch.Tensor,
        forward_fn: Callable[[torch.Tensor], torch.Tensor],
        device: torch.device,
        on_progress: Optional[Callable[[int, int], None]] = None,
    ) -> torch.Tensor:
        """
        执行滑窗推理。
        
        Args:
            image: 输入图像 [1, 3, H, W]
            forward_fn: 前向推理函数
            device: 设备
            on_progress: 进度回调 (done, total)
        
        Returns:
            整图 logits [1, 1, H, W]
        """
        _, _, h, w = image.shape
        tile_size = self.tile_size
        
        # 规划
        rects = self.plan(h, w)
        total = len(rects)
        
        # 小图处理: 需要 padding
        if h < tile_size or w < tile_size:
            pad_h = max(0, tile_size - h)
            pad_w = max(0, tile_size - w)
            image = F.pad(image, (0, pad_w, 0, pad_h), mode='reflect')
        
        # 逐块推理
        tiles = []
        for i, rect in enumerate(rects):
            # 裁剪
            tile = image[:, :, rect.y:rect.y + tile_size, rect.x:rect.x + tile_size]
            
            # 推理
            with torch.no_grad():
                logits = forward_fn(tile)
            
            tiles.append(logits)
            
            # 进度回调
            if on_progress is not None:
                on_progress(i + 1, total)
        
        # 融合
        result = self.fuse(tiles, rects, h, w, device)
        
        return result
