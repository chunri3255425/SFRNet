"""
适配层抽象接口总定义（SOFTWARE_DESIGN v2.2 §4.1）

四个平台差异抽象 + 两个消费侧协议：

  ┌─ 生产者侧（平台差异）─────────────────────────────┐
  │ ICameraAdapter   取一帧      V4L2 / 鸿蒙相机 Kit / 内存假源 │
  │ IRuntimeAdapter  跑一次前向  ONNX Runtime / NPU SDK / Torch │
  │ IDeviceIO        配置/日志/物理指示                 Linux / 鸿蒙 │
  └───────────────────────────────────────────────────────┘

  ┌─ 消费者侧（网络出口等，反向注入）─────────────────┐
  │ IImageService     "给我图，还我结果" 的能力抽象     │
  │ IDetectionEngine  "我现在能不能干活" 的状态抽象     │
  │ IResultSink       "把一次判定结果广播出去" 的出口   │
  └───────────────────────────────────────────────────────┘

红线：本模块只允许标准库与 numpy 类型注解，不得出现 torch/PIL/v4l2/ohos，
也不得 import logic_layer——上层能力一律由装配处（factory/组合根）以协议
形式注入，实现依赖倒置。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, runtime_checkable

import numpy as np

# =============================================================================
# 数据契约
# =============================================================================


@dataclass(frozen=True)
class Frame:
    """
    一帧原始图像。字段顺序与 v2.2 §4.1.1 约定一致：
    Frame(width, height, channels, timestamp, data)。

    Attributes:
        width: 像素宽
        height: 像素高
        channels: 1 / 3 / 4；None 时由 data 形状自动推断
        timestamp: 采集时刻（epoch 秒）
        data: uint8 数组，形状 (H, W, C) 或 (H, W)
        source_id: 帧名/文件标识，用于日志追溯
    """
    width: int
    height: int
    channels: Optional[int]
    timestamp: float
    data: np.ndarray
    source_id: str = ""

    def __post_init__(self) -> None:
        if self.channels is None:
            object.__setattr__(
                self, "channels",
                self.data.shape[2] if self.data.ndim == 3 else 1,
            )


@dataclass(frozen=True)
class DetectionResultDTO:
    """
    一次检测的可序列化结果快照（不含任何 torch 对象）。

    由组合根从 InferenceResult 构造；网络/存储等消费者只认这个 DTO，
    保证适配层不碰 state_dict、不碰张量运算。

    Attributes:
        source_id: 输入标识（文件名/帧号/上传原名）
        has_moire: 布尔判定（coverage > coverage_threshold）
        coverage: 摩尔纹面积占比 [0, 1]
        max_prob: 全图最大概率值 [0, 1]
        mean_prob: 全图均值概率
        elapsed_ms: 本帧推理耗时（毫秒）
        mask_png: 二值掩膜编码为 PNG 的字节；None 表示不携带
        prob_png: 概率图编码为 PNG 的字节；None 表示不携带
        warnings: 非致命提示（降级沿用旧值、边角伪影等）
        ts: 结果产生时刻（epoch 秒）
    """
    source_id: str
    has_moire: bool
    coverage: float
    max_prob: float
    mean_prob: float
    elapsed_ms: float
    mask_png: Optional[bytes] = None
    prob_png: Optional[bytes] = None
    warnings: tuple[str, ...] = ()
    ts: float = 0.0

    def summary(self) -> dict[str, Any]:
        """不含图像字段的轻量摘要，可直接进 JSON / 日志单行。"""
        return {
            "source_id": self.source_id,
            "has_moire": self.has_moire,
            "coverage": round(self.coverage, 6),
            "max_prob": round(self.max_prob, 6),
            "mean_prob": round(self.mean_prob, 6),
            "elapsed_ms": round(self.elapsed_ms, 1),
            "warnings": list(self.warnings),
            "ts": self.ts,
        }


@dataclass(frozen=True)
class DetectionEvent:
    """
    单帧控制流的旁路观测记录（对应附录 B 的日志单行格式）。
    纯记录用途，不参与判定；无持久状态，断电不丢任何东西。
    """
    seq: int
    ts: float
    grab_ms: float
    infer_ms: float
    total_ms: float
    tiles: int
    state: str
    result: Optional[DetectionResultDTO] = None
    error_code: str = ""


# =============================================================================
# 生产者侧：三个平台差异抽象（v2.2 §4.1.1 ~ §4.1.3 逐字段对齐）
# =============================================================================


class ICameraAdapter(ABC):
    """把"取一帧"这件事抽象出来，屏蔽 V4L2 / 鸿蒙相机 Kit 的差异。"""

    @abstractmethod
    def open(self, **kwargs) -> None: ...

    @abstractmethod
    def grab(self) -> Frame:
        """阻塞取一帧。失败应抛异常，由 Supervisor 按 C-10 重试。"""
        ...

    @abstractmethod
    def close(self) -> None: ...


class IRuntimeAdapter(ABC):
    """
    把"跑一次前向"抽象出来，屏蔽 ONNX Runtime / NPU SDK 的差异。

    镜像内固化的是量化模型文件；开发机调试时亦可用 Torch 后端。
    """

    @abstractmethod
    def load(self, model_path: str, **kwargs) -> None: ...

    @abstractmethod
    def infer(self, tensor: Any) -> Any:
        """输入 [1,3,H,W]，输出 [1,1,H,W] raw logits（未过 sigmoid）。"""
        ...

    @abstractmethod
    def warmup(self) -> None:
        """加载后跑一次全零前向，消除首帧 JIT/内存池开销（治理 D-06）。"""
        ...


class IDeviceIO(ABC):
    """配置、日志、临时目录、布尔结果的物理指示。两套系统的路径与外设约定不同，统一在此。"""

    @abstractmethod
    def read_config(self) -> dict:
        """读取平台侧现场覆盖配置；无持久配置返回空 dict（非致命）。"""
        ...

    @abstractmethod
    def log(self, level: str, msg: str) -> None: ...

    @abstractmethod
    def indicate(self, has_moire: bool) -> None:
        """布尔结果的物理呈现：GPIO 电平 / LED / 屏幕角标 / 串口输出。"""
        ...


# =============================================================================
# 消费者侧：能力协议（组合根反向注入，适配层对上游零感知）
# =============================================================================


@runtime_checkable
class IImageService(Protocol):
    """
    "给我一帧图，还我一个结果 DTO" 的能力抽象。

    网络出口（HTTP/gRPC/…）、批量落盘、演示缓存等消费者只依赖本协议；
    真正的实现住在组合根（daemon / factory 装配处），适配层不 import 逻辑层。
    """

    def detect_bytes(self, data: bytes, name: str = "") -> DetectionResultDTO:
        """接收原始图像字节（PNG/JPEG 等编码），返回结果 DTO。"""
        ...

    def detect_file(self, path: str) -> DetectionResultDTO:
        """接收本机可读的文件路径，返回结果 DTO。"""
        ...


@runtime_checkable
class IDetectionEngine(Protocol):
    """检测主体当前状态的只读抽象，供 /status 类端点与状态机旁路观测。"""

    def engine_state(self) -> str:
        """返回全局状态机状态名（BOOTING/READY/DETECTING/DEGRADED/FATAL…）。"""
        ...


@runtime_checkable
class IResultSink(Protocol):
    """判定结果的广播出口。HTTP-SSE、串口帧、MQTT 皆为它的实现。"""

    def publish(self, result: DetectionResultDTO) -> None: ...


# =============================================================================
# 网络出口配置（configs/default.yaml 的 network 节）
# =============================================================================


@dataclass
class NetworkConfig:
    """
    板端网络出口参数。默认关闭——无头产品形态下网络是显式 opt-in 的旁路。

    Attributes:
        enabled: 是否启动网络服务
        host: 绑定地址；板端建议 127.0.0.1 或产线内网段
        port: 监听端口
        token: Bearer 令牌；空串 = 不鉴权（仅限开发机）
        allow_paths: 是否允许 JSON 请求用本机文件路径引用图像
        max_upload_mb: 单张上传体积上限（保护 4GB 内存预算）
        sse_heartbeat_s: SSE 心跳间隔秒
    """
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 8340
    token: str = ""
    allow_paths: bool = False
    max_upload_mb: int = 64
    sse_heartbeat_s: float = 15.0

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "NetworkConfig":
        """从 yaml 节构造；非法值直接拒绝，由调用方决定退回策略。"""
        cfg = cls()
        if not data:
            return cfg
        errors: list[dict[str, str]] = []
        for key, value in data.items():
            if not hasattr(cfg, key):
                errors.append({"field": f"network.{key}", "reason": "未知字段", "suggestion": "删除或改名"})
                continue
            setattr(cfg, key, value)
        if errors:
            raise ValueError(str(errors))
        if not (0 <= cfg.port <= 65535):
            raise ValueError(f"[{'network.port'}] 端口越界: {cfg.port}")
        if cfg.max_upload_mb <= 0:
            raise ValueError("max_upload_mb 必须为正")
        return cfg

    def validate(self, params: dict[str, Any]) -> list[dict[str, str]]:
        """热更新校验（与 ConfigManager.validate 同风格）：返回错误列表，空即通过。"""
        errors = []
        port = params.get("port")
        if port is not None and not (0 <= int(port) <= 65535):
            errors.append({"field": "network.port", "reason": f"端口越界: {port}", "suggestion": "0–65535"})
        mb = params.get("max_upload_mb")
        if mb is not None and int(mb) <= 0:
            errors.append({"field": "network.max_upload_mb", "reason": "必须为正", "suggestion": "如 64"})
        return errors


@dataclass
class NetServerOptions:
    """HTTP 服务装配参数：依赖全部以抽象形式传入。"""
    image_service: Optional[IImageService] = None
    engine: Optional[IDetectionEngine] = None
    config: NetworkConfig = field(default_factory=NetworkConfig)
