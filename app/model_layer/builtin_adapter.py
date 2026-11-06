"""
默认适配器实现 (Built-in Adapters)

提供 V1 唯一需要交付的默认实现:
  - RGBInputAdapter: 输入适配 (PIL.Image / np.ndarray / str → Tensor)
  - SFRNetOutputAdapter: SFRNet 输出适配
"""

from pathlib import Path
from typing import Any

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image
from dataclasses import replace

from app.model_layer.contracts import (
    IInputAdapter,
    IOutputAdapter,
    DetectorOutput,
    InputMeta,
    ContractViolation,
    assert_input_contract,
    assert_output_contract,
)


# =============================================================================
# 辅助函数
# =============================================================================

def _as_pil(obj: Any) -> Image.Image:
    """将各种输入类型统一转换为 PIL.Image"""
    if isinstance(obj, Image.Image):
        return obj
    if isinstance(obj, (str, Path)):
        return Image.open(obj)
    if isinstance(obj, np.ndarray):
        if obj.ndim == 2:
            return Image.fromarray(obj, mode="L")
        if obj.ndim == 3:
            if obj.shape[2] == 4:
                return Image.fromarray(obj, mode="RGBA")
            return Image.fromarray(obj, mode="RGB")
    raise TypeError(f"Unsupported input type: {type(obj)}")


def _detect_bit_depth(img: Image.Image) -> int:
    """检测图像位深"""
    mode = img.mode
    if mode in ("1", "L", "P"):
        return 8
    if mode in ("RGB", "RGBA", "CMYK"):
        if img.info.get("bits", 8) == 16:
            return 16
        return 8
    return 8


def _guess_source_id(obj: Any) -> str:
    """猜测输入来源 ID"""
    if isinstance(obj, (str, Path)):
        return Path(obj).name
    if isinstance(obj, Image.Image):
        return getattr(obj, "filename", "unknown")
    return "unknown"


# =============================================================================
# RGBInputAdapter
# =============================================================================

class RGBInputAdapter(IInputAdapter):
    """
    默认输入适配器: 接受 PIL.Image / np.ndarray / str(path)。
    
    处理链:
      解码 → 通道规整 (RGBA 丢 alpha / L 复制三通道 / CMYK 转 RGB)
      → 若为 16bit 则线性缩放到 [0,1]
      → ToTensor → unsqueeze(0)
    """
    
    name = "rgb_8bit"
    
    def from_external(self, obj: Any) -> tuple[torch.Tensor, InputMeta]:
        """外部对象 → (ImageTensor, InputMeta)"""
        img = _as_pil(obj)
        
        # 构建元信息
        meta = InputMeta(
            src_width=img.width,
            src_height=img.height,
            channels=len(img.getbands()),
            bit_depth=_detect_bit_depth(img),
            color_space=img.mode,
            source_id=_guess_source_id(obj),
        )
        
        # 通道规整: 强制 RGB
        if img.mode == "RGBA":
            # 丢弃 alpha 通道
            img = img.convert("RGB")
        elif img.mode == "L":
            # 灰度 → RGB (复制三通道)
            img = img.convert("RGB")
        elif img.mode == "CMYK":
            # CMYK → RGB
            img = img.convert("RGB")
        elif img.mode != "RGB":
            img = img.convert("RGB")
        
        # 转换为 Tensor: [1, 3, H, W], float32, [0, 1]
        arr = np.asarray(img, dtype=np.float32) / 255.0
        tensor = torch.from_numpy(arr).permute(2, 0, 1).unsqueeze(0).contiguous()
        
        # 契约校验
        assert_input_contract(tensor)
        
        return tensor, meta
    
    def to_external(self, tensor: torch.Tensor, meta: InputMeta) -> Image.Image:
        """ImageTensor → PIL.Image"""
        # [1, 3, H, W] → [H, W, 3]
        arr = tensor.squeeze(0).clamp(0, 1).mul(255).byte().permute(1, 2, 0).cpu().numpy()
        return Image.fromarray(arr, mode="RGB")
    
    def probe(self, obj: Any) -> dict[str, Any]:
        """轻量探测: 返回尺寸/模式概要"""
        try:
            if isinstance(obj, (str, Path)):
                with Image.open(obj) as img:
                    return {
                        "width": img.width,
                        "height": img.height,
                        "mode": img.mode,
                    }
            if isinstance(obj, Image.Image):
                return {
                    "width": obj.width,
                    "height": obj.height,
                    "mode": obj.mode,
                }
            if isinstance(obj, np.ndarray):
                return {
                    "width": obj.shape[1] if obj.ndim >= 2 else 0,
                    "height": obj.shape[0] if obj.ndim >= 1 else 0,
                    "channels": obj.shape[2] if obj.ndim == 3 else 1,
                }
        except Exception:
            pass
        return {}


# =============================================================================
# SFRNetOutputAdapter
# =============================================================================

class SFRNetOutputAdapter(IOutputAdapter):
    """
    SFRNet 默认输出适配器: 单通道同分辨率 logits。
    """
    
    name = "sfrnet_single_channel"
    
    def to_external(self, raw: Any, meta: InputMeta) -> DetectorOutput:
        """模型原始输出 → DetectorOutput"""
        # 处理可能的 tuple/list 输出
        if isinstance(raw, (tuple, list)):
            raw = raw[0]
        
        # 确保维度正确
        if raw.dim() == 3:
            raw = raw.unsqueeze(0)
        
        # 契约校验
        if raw.shape[1] != 1:
            raise ContractViolation(
                "E-CONTRACT-OUT-CHANNEL",
                f"Expected 1 channel, got {raw.shape[1]}"
            )
        
        return DetectorOutput(
            logits=raw,
            out_width=raw.shape[-1],
            out_height=raw.shape[-2],
            meta=meta,
            extras={},
        )
    
    def resample_to_source(self, out: DetectorOutput) -> DetectorOutput:
        """
        把 logits 双线性上采样到 (meta.src_height, meta.src_width)
        
        若输出尺寸已与输入一致，直接原样返回 (零拷贝)。
        """
        if (out.out_width, out.out_height) == (out.meta.src_width, out.meta.src_height):
            return out  # 零拷贝
        
        # 双线性上采样
        up = F.interpolate(
            out.logits,
            size=(out.meta.src_height, out.meta.src_width),
            mode="bilinear",
            align_corners=False,
        )
        
        return replace(
            out,
            logits=up,
            out_width=out.meta.src_width,
            out_height=out.meta.src_height,
        )
