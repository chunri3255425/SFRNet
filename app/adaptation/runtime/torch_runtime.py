"""开发机 Torch 推理后端。

包装 moireguard.model_layer.registry 的加载逻辑（SFR_best.pth），
供 x86 开发机调试与回归使用；板端镜像不包含 PyTorch。
"""

from __future__ import annotations

import numpy as np
import torch

from app.adaptation.runtime.base import IN_SHAPE, IRuntimeAdapter, RuntimeLoadError


class TorchRuntime(IRuntimeAdapter):
    """PyTorch 后端（开发机）。"""

    def __init__(self, device: str = "auto"):
        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
        self._device = torch.device(device)
        self._model: torch.nn.Module | None = None

    def load(self, model_path: str, **kwargs) -> None:
        from moireguard.model_layer.registry import ModelRegistry
        from moireguard.logic_layer.exceptions import MoireError

        registry = ModelRegistry()
        try:
            core, _, _ = registry.create(
                model_id=kwargs.get("model_id", "sfrnet_uhd"),
                device=str(self._device),
            )
        except MoireError as e:
            raise RuntimeLoadError(str(e)) from e
        core.eval()
        self._model = core

    def infer(self, tensor: np.ndarray) -> np.ndarray:
        if self._model is None:
            raise RuntimeLoadError("模型未加载")
        with torch.no_grad():
            t = torch.from_numpy(tensor).to(self._device)
            out = self._model(t)
            return out.detach().cpu().numpy()

    def warmup(self) -> None:
        if self._model is None:
            raise RuntimeLoadError("模型未加载")
        dummy = torch.zeros(IN_SHAPE, device=self._device)
        with torch.no_grad():
            self._model(dummy)

    def unload(self) -> None:
        self._model = None
        if self._device.type == "cuda":
            torch.cuda.empty_cache()
