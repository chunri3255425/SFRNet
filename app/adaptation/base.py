"""适配层基类接口。

适配层红线：
  - 不含任何判定逻辑
  - 不懂“摩尔纹”是什么
  - 不 import torch / PIL / v4l2 / rknn 等框架与平台包

生产者侧（平台差异）抽象按子包分文件：camera/、runtime/、relay/、device_io/。
本模块做三件事：
  ① 聚合再导出，上层一行 import；
  ② 承载跳子包共享的**传输契约**（DetectionResultDTO / DetectionEvent）；
  ③ 承载**消费者侧协议**（IImageService / IDetectionEngine / IResultSink）——
     网络出口、回传、落盘等消费者只依赖这些 Protocol，实现由组合根反向注入，
     因此适配层对逻辑层零感知（依赖倒置红线）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, runtime_checkable

from app.adaptation.camera.base import Frame, ICameraAdapter
from app.adaptation.runtime.base import IRuntimeAdapter
from app.adaptation.relay.base import IFrameRelay
from app.adaptation.device_io.base import IDeviceIO

# =============================================================================
# 传输契约：一次判定的可序列化快照（不含任何 torch / numpy 张量对象）
# =============================================================================


@dataclass(frozen=True)
class DetectionResultDTO:
    """
    由组合根从引擎结果构造。网络/存储等消费者只认这个 DTO，
    保证适配层不碍 state_dict、不碰张量运算。

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
# 消费者侧：能力协议（组合根反向注入，适配层对上层零感知）
# =============================================================================


@runtime_checkable
class IImageService(Protocol):
    """“给我一帧图，还我一个结果 DTO”的能力抽象。"""

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
                errors.append({"field": f"network.{key}", "reason": "未知字段",
                               "suggestion": "删除或改名"})
                continue
            setattr(cfg, key, value)
        if errors:
            raise ValueError(str(errors))
        if not (0 <= cfg.port <= 65535):
            raise ValueError(f"[network.port] 端口越界: {cfg.port}")
        if cfg.max_upload_mb <= 0:
            raise ValueError("max_upload_mb 必须为正")
        return cfg

    def validate(self, params: dict[str, Any]) -> list[dict[str, str]]:
        """热更新校验（与 ConfigManager.validate 同风格）：返回错误列表，空即通过。"""
        errors: list[dict[str, str]] = []
        port = params.get("port")
        if port is not None and not (0 <= int(port) <= 65535):
            errors.append({"field": "network.port", "reason": f"端口越界: {port}",
                           "suggestion": "0–65535"})
        mb = params.get("max_upload_mb")
        if mb is not None and int(mb) <= 0:
            errors.append({"field": "network.max_upload_mb", "reason": "必须为正",
                           "suggestion": "如 64"})
        return errors


@dataclass
class NetServerOptions:
    """HTTP 服务装配参数：依赖全部以抽象形式传入。"""
    image_service: Optional[IImageService] = None
    engine: Optional[IDetectionEngine] = None
    config: NetworkConfig = field(default_factory=NetworkConfig)


__all__ = [
    "Frame",
    "ICameraAdapter",
    "IRuntimeAdapter",
    "IFrameRelay",
    "IDeviceIO",
    "DetectionResultDTO",
    "DetectionEvent",
    "IImageService",
    "IDetectionEngine",
    "IResultSink",
    "NetworkConfig",
    "NetServerOptions",
]
