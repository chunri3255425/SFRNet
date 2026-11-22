"""开发机 Torch eager 后端（板端镜像不含 PyTorch）。

契约（见 base.py）：numpy in / numpy out，输出 raw logits，全流程不在这里过 sigmoid。

三条修复说明（合并时的实测缺陷）：
  1. `load(model_path)` 必须真正使用 model_path —— 早期实现把参数丢了，
     配置里的权重路径成了死字段。
  2. `registry.create()` 返回的是 DetectorCore 协议对象（SFRNetDetector），
     **不是 nn.Module**，直接 `core(t)` 会 TypeError；统一走 `core.forward(t)`。
  3. 本模块不得 import logic_layer —— 适配层对上层零感知。registry 抛的是
     FileNotFoundError / RuntimeError（自带 E-* 前缀），无需借用上层异常类型。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from app.adaptation.runtime.base import IN_SHAPE, IRuntimeAdapter, RuntimeLoadError

REPO_ROOT = Path(__file__).resolve().parents[2]


class TorchRuntime(IRuntimeAdapter):
    """PyTorch 后端（开发机 / 回归测试）。"""

    def __init__(self, device: str = "auto"):
        self._device_str = device
        self._core = None
        self._model_path: str = ""

    # ── 内部 ─────────────────────────────────
    @staticmethod
    def _torch():
        """延迟 import：板端镜像没有 torch，模块加载本身不该失败。"""
        try:
            import torch
        except ImportError as e:
            raise RuntimeLoadError(f"torch 不可用: {e}") from e
        return torch

    def _resolve(self, model_path: str) -> str:
        """绝对路径 → 相对工作目录 → 相对仓库根，三级定位。"""
        p = Path(model_path)
        if p.is_absolute() or p.exists():
            return str(p)
        rooted = REPO_ROOT / model_path
        return str(rooted) if rooted.exists() else str(p)

    # ── IRuntimeAdapter ──────────────────────
    def load(self, model_path: str, **kwargs) -> None:
        torch = self._torch()
        device = self._device_str
        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"

        path = self._resolve(model_path)
        if not Path(path).exists():
            raise RuntimeLoadError(f"E-WEIGHT-NOT-FOUND: {path}")

        from app.model_layer.registry import ModelRegistry

        try:
            core, _, _ = ModelRegistry().create(
                model_id=kwargs.get("model_id", "sfrnet_uhd"),
                device=device,
                weights_path=path,          # ★ 真正把手上的权重路径交给模型层
            )
        except FileNotFoundError as e:
            raise RuntimeLoadError(f"E-WEIGHT-NOT-FOUND: {e}") from e
        except RuntimeError as e:
            # 结构不匹配 / 键缺失：与"文件找不到"是两种故障，不要混标
            raise RuntimeLoadError(f"E-WEIGHT-MISMATCH: {e}") from e
        core.eval()
        self._core = core
        self._device_str = device
        self._model_path = path

    def infer(self, tensor: np.ndarray) -> np.ndarray:
        torch = self._torch()
        if self._core is None:
            raise RuntimeLoadError("E-RUNTIME-NOT-LOADED: 先调用 load()")
        with torch.no_grad():
            out = self._core.forward(torch.from_numpy(np.ascontiguousarray(tensor))
                                     .to(torch.device(self._device_str)))
        return out.detach().cpu().float().numpy()      # [1,1,H,W] raw logits

    def warmup(self) -> None:
        torch = self._torch()
        if self._core is None:
            raise RuntimeLoadError("E-RUNTIME-NOT-LOADED: 先调用 load()")
        # 首次前向含 kernel 选择/内存池分配，可达稳态 3~10 倍耗时（治理 D-06）
        self.infer(np.zeros(IN_SHAPE, dtype=np.float32))

    def unload(self) -> None:
        self._core = None
        torch = self._torch()
        if self._device_str.startswith("cuda"):
            torch.cuda.empty_cache()
