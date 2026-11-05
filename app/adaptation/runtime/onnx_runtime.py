"""ONNX Runtime 推理后端（软回退路径：NPU 驱动异常时 A55 CPU 上仍可低速运行）。"""

from __future__ import annotations

import numpy as np

from app.adaptation.runtime.base import IN_SHAPE, IRuntimeAdapter, RuntimeLoadError


class OnnxRuntime(IRuntimeAdapter):
    """onnxruntime 后端。"""

    def __init__(self):
        self._session = None
        self._input_name = ""

    def load(self, model_path: str, **kwargs) -> None:
        try:
            import onnxruntime as ort
        except ImportError as e:
            raise RuntimeLoadError("未安装 onnxruntime（板端软回退依赖）") from e
        try:
            so = ort.SessionOptions()
            so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            providers = kwargs.get(
                "providers",
                ["CPUExecutionProvider"],
            )
            self._session = ort.InferenceSession(model_path, so, providers=providers)
            self._input_name = self._session.get_inputs()[0].name
        except Exception as e:
            raise RuntimeLoadError(f"ONNX 模型加载失败: {e}") from e

    def infer(self, tensor: np.ndarray) -> np.ndarray:
        if self._session is None:
            raise RuntimeLoadError("模型未加载")
        out = self._session.run(None, {self._input_name: tensor})
        return out[0]

    def warmup(self) -> None:
        if self._session is None:
            raise RuntimeLoadError("模型未加载")
        self._session.run(None, {self._input_name: np.zeros(IN_SHAPE, dtype=np.float32)})

    def unload(self) -> None:
        self._session = None
