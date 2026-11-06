"""
推理后端适配层实现

按 v2.2 §4.1.2 的 IRuntimeAdapter 提供多后端：
  torch_runtime.py  开发机调试（唯一允许 import torch 的实现）
  onnx_runtime.py   （待建：ONNX Runtime）
  rknn_runtime.py   （待建：RKNN NPU，见 Q7 转换链）
"""
