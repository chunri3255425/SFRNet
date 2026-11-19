"""YoloRuntime — Stage-1 YOLO 推理后端（多输出，独立于 IRuntimeAdapter）。

与 IRuntimeAdapter 的区别：SFRNet 是单输出 [1,1,H,W]；YOLO 裸主干是
三尺度 raw 特征图列表（SDD §4.1 A2：decode/NMS 不进 NPU）。
因此不强行套 IRuntimeAdapter 契约，本类只服务 YoloScreenGate。

  backend="rknnlite" 板端：RKNNLite，输入 NHWC float32 **0..255**
                            （归一化 /255 已在转换时烘进 .rknn，A1 口径）
  backend="onnx"     开发机：onnxruntime，输入 NCHW float32 **0..1**
                            （gate 侧负责除 255，见 normalized_input）

infer 返回 list[np.ndarray]，顺序与 yolo_postprocess.STRIDES 一一对应，
且**统一保证为 NCHW [1,65,h,w]**（与导出 ONNX 对齐）：板端实测 RKNNLite 已经
返回 NCHW，rknnlite 分支只做按 65 通道轴定位的幂等归一，不做盲猜 permute。

本模块**必须保持零 torch 依赖**（连顶层 import 都不允许，理由见 CHANNELS 上方注释），
因为它是板端产品路径唯一可用的 YOLO 推理入口。
"""

from __future__ import annotations

import numpy as np

YOLO_IN_SHAPE = (1, 3, 640, 640)
# 通道锚点 = 4 * REG_MAX + NC = 65，与 yolo_postprocess 模块头的约定同值。
# ⚠ 这里**刻意不写** `from app.model_layer.detectors.yolo_postprocess import REG_MAX`：
#   `app/model_layer/__init__.py`（→ contracts）与 `app/model_layer/detectors/__init__.py`
#   （→ sfrnet_detector）都在模块级硬 `import torch`，而板端按 SDD §1.2/§2 明确不装
#   PyTorch（“RKNN Runtime 是纯 C 库……无需在板上装 PyTorch”）。顶层一句跨层引用
#   就会让本模块在板上连 import 都失败——2026-11-16 实板踩过。
#   改动这两个常量时须同步 yolo_postprocess.py，tests/test_yolo_cascade.py 有对查。
REG_MAX = 16                        # DFL 每个坐标的离散 bin 数（YOLOv8 固定）
NC = 1                              # 本项目固定单类
CHANNELS = 4 * REG_MAX + NC         # 65：唯一可用于定位通道轴的锚点


class YoloRuntimeError(RuntimeError):
    """YOLO 后端加载/推理失败（gate 捕获后按 P3 降级，不致命）。"""


class YoloRuntime:
    def __init__(self, backend: str = "rknnlite"):
        if backend not in ("rknnlite", "onnx"):
            raise YoloRuntimeError(f"未知 YOLO 后端: {backend}")
        self._backend = backend
        self._session = None
        self._rknn = None
        self._input_name = ""

    def load(self, model_path: str) -> None:
        if self._backend == "onnx":
            try:
                import onnxruntime as ort
            except ImportError as e:
                raise YoloRuntimeError("未安装 onnxruntime") from e
            try:
                self._session = ort.InferenceSession(
                    model_path, providers=["CPUExecutionProvider"])
                self._input_name = self._session.get_inputs()[0].name
            except Exception as e:
                raise YoloRuntimeError(f"YOLO ONNX 加载失败: {e}") from e
            return
        # 板端 rknnlite
        try:
            from rknnlite.api import RKNNLite
        except ImportError as e:
            raise YoloRuntimeError("未安装 rknnlite（板端依赖 rknn-toolkit-lite2）") from e
        try:
            rknn = RKNNLite()
            if rknn.load_rknn(model_path) != 0:
                raise YoloRuntimeError(f"加载 {model_path} 失败")
            if rknn.init_runtime(core_mask=RKNNLite.NPU_CORE_0_1_2) != 0 \
                    and rknn.init_runtime() != 0:
                raise YoloRuntimeError("RKNNLite runtime 初始化失败")
            self._rknn = rknn
        except YoloRuntimeError:
            raise
        except Exception as e:
            raise YoloRuntimeError(f"RKNNLite 初始化异常: {e}") from e

    def infer(self, nchw: np.ndarray) -> list[np.ndarray]:
        """[1,3,640,640] float32 → [feat_s8, feat_s16, feat_s32] raw 特征图列表。"""
        if self._backend == "onnx":
            if self._session is None:
                raise YoloRuntimeError("模型未加载")
            return self._session.run(None, {self._input_name: nchw})
        if self._rknn is None:
            raise YoloRuntimeError("模型未加载")
        nhwc = np.ascontiguousarray(np.transpose(nchw, (0, 2, 3, 1)))
        outputs = self._rknn.inference(inputs=[nhwc])
        return [self._to_nchw(o) for o in outputs]

    @staticmethod
    def _to_nchw(o: np.ndarray) -> np.ndarray:
        """把单个 raw 特征图归一为 NCHW [1, CHANNELS, h, w]。

        按 65 通道轴唯一定位，不猜布局 —— 板端实测（2026-11-16）RKNNLite 对
        本模型返回的**已经是 NCHW**，旧写法用 `shape[-1] not in (65,)` 判断，
        对 [1,65,80,80] 因 80≠65 判真而误转成 [1,80,65,80]，decode_boxes 每帧
        抛 ValueError（SFRNet 侧 rknn_runtime.py 早有同条教训：实测已是 NCHW，
        不要再反向 permute）。
        """
        o = np.asarray(o)
        if o.ndim != 4:
            raise YoloRuntimeError(f"意外的 YOLO 输出维数: shape={o.shape}")
        if o.shape[1] == CHANNELS:
            return o.astype(np.float32)            # 已是 NCHW，原样交回
        if o.shape[3] == CHANNELS:                 # NHWC → 转回来
            return np.ascontiguousarray(
                np.transpose(o, (0, 3, 1, 2)), dtype=np.float32)
        raise YoloRuntimeError(
            f"无法在 shape={o.shape} 上定位 {CHANNELS} 通道轴")

    def warmup(self) -> None:
        zeros = np.zeros(YOLO_IN_SHAPE, dtype=np.float32)
        if self._backend == "onnx":
            self.infer(zeros)
        else:
            self._rknn.inference(inputs=[np.transpose(zeros, (0, 2, 3, 1))])

    def unload(self) -> None:
        if self._rknn is not None:
            self._rknn.release()
            self._rknn = None
        self._session = None
