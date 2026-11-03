"""
导出服务 (Export Service)

负责将推理结果落盘:
  - 二值掩膜 PNG
  - 叠加图 PNG
  - 概率热力图 PNG
  - 元数据 JSON
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
import json

import torch
import numpy as np
from PIL import Image


@dataclass
class ExportOptions:
    """导出选项"""
    output_dir: Path
    save_mask: bool = True
    save_overlay: bool = True
    save_prob_map: bool = True
    save_json: bool = True
    overlay_alpha: float = 0.45
    colormap: str = "jet"  # "jet" | "viridis"


class ExportService:
    """
    导出服务。
    
    将推理结果保存为文件。
    """
    
    def __init__(self, options: ExportOptions):
        """
        初始化导出服务。
        
        Args:
            options: 导出选项
        """
        self._options = options
        options.output_dir.mkdir(parents=True, exist_ok=True)
    
    def save(
        self,
        source_id: str,
        prob_map: torch.Tensor,
        binary_mask: torch.Tensor,
        coverage: float,
        has_moire: bool,
        meta: Any,
        stats: Any,
    ) -> list[Path]:
        """
        保存导出文件。
        
        Args:
            source_id: 源文件 ID
            prob_map: 概率图 [1, 1, H, W]
            binary_mask: 二值掩膜 [1, 1, H, W]
            coverage: 覆盖率
            has_moire: 是否存在摩尔纹
            meta: 输入元信息
            stats: 推理统计
        
        Returns:
            保存的文件路径列表
        """
        artifacts = []
        stem = Path(source_id).stem
        
        # 1. 二值掩膜
        if self._options.save_mask:
            mask_path = self._options.output_dir / f"{stem}__mask.png"
            self._save_mask(binary_mask, mask_path)
            artifacts.append(mask_path)
        
        # 2. 概率热力图
        if self._options.save_prob_map:
            prob_path = self._options.output_dir / f"{stem}__prob.png"
            self._save_prob_map(prob_map, prob_path)
            artifacts.append(prob_path)
        
        # 3. 元数据 JSON
        if self._options.save_json:
            json_path = self._options.output_dir / f"{stem}__meta.json"
            self._save_json(
                json_path=json_path,
                source_id=source_id,
                coverage=coverage,
                has_moire=has_moire,
                meta=meta,
                stats=stats,
                artifacts=artifacts,
            )
            artifacts.append(json_path)
        
        return artifacts
    
    @staticmethod
    def _save_mask(mask: torch.Tensor, path: Path) -> None:
        """保存二值掩膜"""
        # [1, 1, H, W] → [H, W]
        mask_np = mask.squeeze().cpu().numpy()
        mask_np = (mask_np * 255).astype(np.uint8)
        Image.fromarray(mask_np, mode="L").save(path)
    
    @staticmethod
    def _save_prob_map(prob_map: torch.Tensor, path: Path, colormap: str = "jet") -> None:
        """保存概率热力图"""
        # [1, 1, H, W] → [H, W]
        prob_np = prob_map.squeeze().cpu().numpy()
        
        # 应用 colormap
        try:
            import matplotlib.pyplot as plt
            cmap = plt.get_cmap(colormap)
            colored = cmap(prob_np)[:, :, :3]  # 去掉 alpha
            colored = (colored * 255).astype(np.uint8)
        except ImportError:
            # 无 matplotlib，使用灰度
            colored = (prob_np * 255).astype(np.uint8)
        
        Image.fromarray(colored, mode="RGB").save(path)
    
    @staticmethod
    def _save_overlay(
        image: Image.Image,
        mask: torch.Tensor,
        path: Path,
        alpha: float = 0.45,
    ) -> None:
        """保存叠加图"""
        # 将掩膜转为 RGBA (红色半透明)
        mask_np = mask.squeeze().cpu().numpy()
        overlay = np.array(image.convert("RGBA"))
        
        # 红色通道
        red_overlay = np.zeros_like(overlay)
        red_overlay[:, :, 0] = 255  # R
        red_overlay[:, :, 3] = (mask_np * 255 * alpha).astype(np.uint8)  # A
        
        # 混合
        result = Image.alpha_composite(
            Image.fromarray(overlay),
            Image.fromarray(red_overlay)
        )
        result.convert("RGB").save(path)
    
    def _save_json(
        self,
        json_path: Path,
        source_id: str,
        coverage: float,
        has_moire: bool,
        meta: Any,
        stats: Any,
        artifacts: list[Path],
    ) -> None:
        """保存元数据 JSON"""
        data = {
            "schema_version": "1.0",
            "source": {
                "id": source_id,
                "width": meta.src_width,
                "height": meta.src_height,
                "channels": meta.channels,
                "bit_depth": meta.bit_depth,
                "color_space": meta.color_space,
            },
            "result": {
                "coverage": coverage,
                "has_moire": has_moire,
            },
            "inference": {
                "elapsed_ms": stats.total_ms,
                "num_tiles": stats.num_tiles,
                "device": stats.device,
                "peak_memory_mb": stats.peak_memory_mb,
            },
            "artifacts": [str(p) for p in artifacts],
            "software": {
                "name": "MoireGuard",
                "version": "1.0.0",
            },
        }
        
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
