"""IFrameRelay — 把一帧画面经 WiFi 推给上位机。

旁路能力红线（SDD §4.5.2）：
  - push() 非阻塞，总耗时预算 ≤ 5 ms
  - 失败不致命，绝不阻塞检测循环
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from app.adaptation.camera.base import Frame


class IFrameResult(Protocol):
    """回传角标所需的最小只读视图。

    画面回传实际上只读 `has_moire` 与 `coverage` 两个字段，所以本层不
    import 逻辑层的 DetectionResult（SDD 引用约束：适配层不得 import 逻辑层），
    而是声明一个结构性协议——逻辑层的结果天然满足它，且不引入任何运行时开销。
    """

    has_moire: bool
    coverage: float


class IFrameRelay(ABC):
    """画面回传抽象接口。"""

    @abstractmethod
    def start(self, host: str, port: int, **kwargs) -> None: ...

    @abstractmethod
    def push(self, frame: "Frame", result: "IFrameResult | None" = None) -> bool:
        """非阻塞推送一帧。返回 False 表示本轮未发出（队列满/未连接/未启用）。"""
        ...

    @abstractmethod
    def stop(self) -> None: ...
