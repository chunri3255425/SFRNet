"""
Torch 推理后端（开发机调试路径）

包装上游 build_SFR + SFR_best.pth 为 IRuntimeAdapter。
本文件是适配层中唯一允许 import torch 的位置（对齐 v2.2 引用约束的
精神：平台/后端差异只允许出现在 adaptation 与 build 之下）。

注意：这里只做"加载权重 + 单次前向"，不做滑窗、不做判定——那些属于
逻辑层（sliding_window / decider）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import torch

from moireguard.adaptation.base import IRuntimeAdapter


class TorchRuntimeAdapter(IRuntimeAdapter):
    """
    Torch eager 后端。

    Attributes:
        device: "cpu" / "cuda" / "cuda:0" 等
        model: 加载后的 nn.Module（eval 态）
    """

    def __init__(self, device: str = "cpu"):
        self.device = torch.device(device)
        self.model: Any = None
        self._model_path: str = ""

    def load(self, model_path: str, **kwargs) -> None:
        """
        加载权重并切 eval 态。

        Args:
            model_path: .pth 权重文件路径（state_dict）
            kwargs:
                builder: 模型工厂，默认上游 build_SFR
                strict: state_dict 加载是否严格，默认 True
        """
        path = Path(model_path)
        if not path.exists():
            raise FileNotFoundError(f"E-WEIGHT-NOT-FOUND: {model_path}")

        builder = kwargs.get("builder")
        if builder is None:
            from upstream.sfr.sfrnet import build_SFR
            builder = build_SFR

        strict = bool(kwargs.get("strict", True))
        model = builder()
        state_dict = torch.load(path, map_location=self.device)
        try:
            model.load_state_dict(state_dict, strict=strict)
        except RuntimeError as e:
            raise RuntimeError(f"E-WEIGHT-MISMATCH: {e}") from e
        model.eval().to(self.device)
        self.model = model
        self._model_path = str(path)

    def infer(self, tensor: Any) -> Any:
        """一次前向：输入 [1,3,H,W]，输出 [1,1,H,W] raw logits（未过 sigmoid）。"""
        if self.model is None:
            raise RuntimeError("E-RUNTIME-NOT-LOADED: 先调用 load()")
        with torch.no_grad():
            return self.model(tensor.to(self.device))

    def warmup(self) -> None:
        """全零 512² 前向一次，消除首帧开销（治理缺口 D-06）。"""
        if self.model is None:
            raise RuntimeError("E-RUNTIME-NOT-LOADED: 先调用 load()")
        probe = torch.zeros(1, 3, 512, 512, device=self.device)
        with torch.no_grad():
            self.model(probe)
