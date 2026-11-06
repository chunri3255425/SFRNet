"""IRuntimeAdapter — 把"跑一次前向"抽象出来，屏蔽 Torch / ONNX / RKNN 差异。

契约：
  - load(model_path) 加载模型，失败抛 RuntimeLoadError
  - infer(tensor) 输入 [1,3,H,W] float32，输出 [1,1,H,W] raw logits（未过 sigmoid）
  - warmup() 预热一次（首次前向含 kernel 选择/内存池分配，可达稳态 3~10 倍耗时）
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np

# 供各实现复用的形状常量
IN_SHAPE = (1, 3, 512, 512)


class RuntimeLoadError(RuntimeError):
    """模型加载失败（文件缺失 / 结构不匹配 / 后端不可用）。"""


class IRuntimeAdapter(ABC):
    """推理后端抽象接口。"""

    @abstractmethod
    def load(self, model_path: str, **kwargs) -> None: ...

    @abstractmethod
    def infer(self, tensor: "np.ndarray") -> np.ndarray:
        """[1,3,H,W] float32 NCHW → [1,1,H,W] float32 raw logits。"""
        ...

    @abstractmethod
    def warmup(self) -> None: ...

    @abstractmethod
    def unload(self) -> None: ...
