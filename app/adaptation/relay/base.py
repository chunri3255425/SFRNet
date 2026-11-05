"""IFrameRelay — 把一帧画面经 WiFi 推给上位机。

旁路能力红线（SDD §4.5.2）：
  - push() 非阻塞，总耗时预算 ≤ 5 ms
  - 失败不致命，绝不阻塞检测循环
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.adaptation.camera.base import Frame
    from app.logic_layer.engine import DetectionResult


class IFrameRelay(ABC):
    """画面回传抽象接口。"""

    @abstractmethod
    def start(self, host: str, port: int, **kwargs) -> None: ...

    @abstractmethod
    def push(self, frame: "Frame", result: "DetectionResult | None" = None) -> bool:
        """非阻塞推送一帧。返回 False 表示本轮未发出（队列满/未连接/未启用）。"""
        ...

    @abstractmethod
    def stop(self) -> None: ...
