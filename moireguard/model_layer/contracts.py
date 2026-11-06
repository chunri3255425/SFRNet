"""
模型层契约定义 (Contracts)

定义两个预留接口与数据契约:
  - IInputAdapter: 输入适配接口
  - IOutputAdapter: 输出适配接口
  - DetectorCore: 模型内核协议
  - DetectorOutput: 统一输出契约
  - InputMeta: 输入元信息
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from typing import Any, Callable, Protocol

import torch
import torch.nn.functional as F


# =============================================================================
# 数据契约
# =============================================================================

@dataclass(frozen=True)
class InputMeta:
    """
    输入的可追溯元信息。随张量一路传递，最终写进输出 JSON。
    
    Attributes:
        src_width: 原始像素宽
        src_height: 原始像素高
        channels: 输入原始通道数 (1/3/4)
        bit_depth: 位深 (8/16)
        color_space: 色彩空间 ("RGB" / "L" / "CMYK" / "RGBA")
        source_id: 文件名或任务 ID，用于日志与导出命名
        scale_x: adapter 内部若做缩放，记录横向比例
        scale_y: adapter 内部若做缩放，记录纵向比例
    """
    src_width: int
    src_height: int
    channels: int
    bit_depth: int
    color_space: str
    source_id: str
    scale_x: float = 1.0
    scale_y: float = 1.0


@dataclass(frozen=True)
class DetectorOutput:
    """
    模型层对外的唯一输出契约。逻辑层只认这个。
    
    Attributes:
        logits: [1, 1, h_out, w_out]，未过 sigmoid 的原始分数
        out_width: logits 的空间宽，可能 ≠ src_width
        out_height: logits 的空间高，可能 ≠ src_height
        meta: 透传的输入元信息
        extras: 模型私有附加信息 (如路由器类别分布)，可为空
    """
    logits: torch.Tensor
    out_width: int
    out_height: int
    meta: InputMeta
    extras: dict[str, Any]


@dataclass(frozen=True)
class ModelBundle:
    """
    注册表中的一个条目。三件套一起装配。
    
    Attributes:
        model_id: 模型唯一标识
        display_name: 显示名称
        core_factory: 创建 DetectorCore 的工厂函数
        input_adapter_factory: 创建 IInputAdapter 的工厂函数
        output_adapter_factory: 创建 IOutputAdapter 的工厂函数
        default_weights: 默认权重文件路径
        expected_tile_size: 该模型建议的 tile 尺寸 (SFRNet=512)
        expected_stride: 该模型建议的 stride 尺寸
    """
    model_id: str
    display_name: str
    core_factory: Callable[[], "DetectorCore"]
    input_adapter_factory: Callable[[], "IInputAdapter"]
    output_adapter_factory: Callable[[], "IOutputAdapter"]
    default_weights: str
    expected_tile_size: int
    expected_stride: int


# =============================================================================
# 接口定义
# =============================================================================

class IInputAdapter(ABC):
    """
    输入接口。
    
    实现者负责:
      1. 接受"外部的图像对象" (PIL.Image / numpy.ndarray / 文件路径 / 内存字节)
      2. 转换为模型可消费的 ImageTensor: [1,3,H,W]，float32，值域 [0,1]
      3. 返回 InputMeta 供回溯
    
    契约 (必须遵守，违反视为 bug):
      C1. 返回值 shape 必须是 [1, 3, H, W]，float32
      C2. 值域必须落在 [0.0, 1.0]，不得有 NaN/Inf
      C3. 色彩顺序必须是 RGB (不是 BGR)
      C4. 不得在此处做 tile 切分 (那是逻辑层的事)
      C5. 不得修改传入对象 (不可原地修改 ndarray)
    """
    
    name: str = "abstract"
    
    @abstractmethod
    def from_external(self, obj: Any) -> tuple[torch.Tensor, InputMeta]:
        """外部对象 → (ImageTensor, InputMeta)"""
        raise NotImplementedError
    
    @abstractmethod
    def to_external(self, tensor: torch.Tensor, meta: InputMeta) -> Any:
        """
        (可选逆变换) ImageTensor → 外部可显示对象 (PIL.Image)
        
        用途: 把滑窗融合后的张量还原成原始尺寸的可视化底图。
        若 adapter 未做缩放，实现应直接无损转换。
        """
        raise NotImplementedError
    
    def probe(self, obj: Any) -> dict[str, Any]:
        """
        轻量探测: 不解码全图，仅返回尺寸/模式等概要。
        
        用于新输入接入时快速展示信息，以及做前置校验
        (例如超大图预警)。默认实现返回空 dict，组件可覆盖。
        """
        return {}


class IOutputAdapter(ABC):
    """
    输出接口。
    
    实现者负责:
      1. 校验/规整模型原始输出 (可能是 tuple、dict、多尺度列表)
      2. 声明输出坐标系 (是否与输入同分辨率)
      3. 提供反变换，把低分辨率输出映射回与 src 同分辨率
    
    契约:
      O1. 返回的 logits 必须是未激活的原始分数 (不得先 sigmoid)
      O2. shape 必须是 [1, 1, h, w]，不允许多通道 (多类别请用 extras 传)
      O3. 不得在此处做阈值二值化
      O4. 若 out_* ≠ src_*，必须实现 resample_to_source()
    """
    
    name: str = "abstract"
    
    @abstractmethod
    def to_external(self, raw: Any, meta: InputMeta) -> DetectorOutput:
        """模型原始输出 → DetectorOutput"""
        raise NotImplementedError
    
    @abstractmethod
    def resample_to_source(self, out: DetectorOutput) -> DetectorOutput:
        """
        把 logits 双线性上采样到 (meta.src_height, meta.src_width)
        
        当 out_w == src_width 且 out_h == src_height 时应直接原样返回
        (零拷贝，避免无谓的插值引入数值噪声)。
        """
        raise NotImplementedError


# =============================================================================
# 模型内核协议
# =============================================================================

class DetectorCore(Protocol):
    """
    模型内核协议: 只要求能吃 tile、吐 raw logits。
    
    使用 Protocol 而非 ABC，因为 SFRNet 的 build_SFR() 返回的 nn.Module
    天然满足 forward/eval/to，无需为它写适配类继承。
    """
    
    def forward(self, tile: torch.Tensor) -> torch.Tensor:
        """前向推理: tile → raw logits"""
        ...
    
    def eval(self) -> None:
        """切换到评估模式"""
        ...
    
    def to(self, device: str) -> "DetectorCore":
        """迁移到指定设备"""
        ...


# =============================================================================
# 契约校验辅助函数
# =============================================================================

def assert_input_contract(tensor: torch.Tensor) -> None:
    """
    校验 IInputAdapter 的输出契约。
    
    Raises:
        ContractViolation: 违反契约时抛出
    """
    if tensor.dim() != 4:
        raise ContractViolation(f"E-CONTRACT-IN-DIM: expected 4D, got {tensor.dim()}D")
    if tensor.shape[0] != 1:
        raise ContractViolation(f"E-CONTRACT-IN-BATCH: expected batch=1, got {tensor.shape[0]}")
    if tensor.shape[1] != 3:
        raise ContractViolation(f"E-CONTRACT-IN-CHANNEL: expected channels=3, got {tensor.shape[1]}")
    if tensor.dtype != torch.float32:
        raise ContractViolation(f"E-CONTRACT-IN-DTYPE: expected float32, got {tensor.dtype}")
    if tensor.min() < 0.0 or tensor.max() > 1.0:
        raise ContractViolation(f"E-CONTRACT-IN-RANGE: values out of [0, 1]")
    if torch.isnan(tensor).any() or torch.isinf(tensor).any():
        raise ContractViolation("E-CONTRACT-IN-NAN: tensor contains NaN or Inf")


def assert_output_contract(output: DetectorOutput) -> None:
    """
    校验 IOutputAdapter 的输出契约。
    
    Raises:
        ContractViolation: 违反契约时抛出
    """
    logits = output.logits
    if logits.dim() != 4:
        raise ContractViolation(f"E-CONTRACT-OUT-DIM: expected 4D, got {logits.dim()}D")
    if logits.shape[0] != 1:
        raise ContractViolation(f"E-CONTRACT-OUT-BATCH: expected batch=1, got {logits.shape[0]}")
    if logits.shape[1] != 1:
        raise ContractViolation(f"E-CONTRACT-OUT-CHANNEL: expected channels=1, got {logits.shape[1]}")


class ContractViolation(Exception):
    """契约违反异常"""
    
    def __init__(self, code: str, message: str = ""):
        self.code = code
        self.message = message or code
        super().__init__(f"[{code}] {self.message}")
