"""IDeviceIO — 配置、日志、指示。两套系统的路径与外设约定统一在此。"""

from __future__ import annotations

from abc import ABC, abstractmethod


class IDeviceIO(ABC):
    """设备 IO 抽象接口。"""

    @abstractmethod
    def read_config(self) -> dict: ...

    @abstractmethod
    def log(self, level: str, msg: str) -> None: ...

    @abstractmethod
    def indicate(self, has_moire: bool) -> None:
        """布尔结果的物理呈现：GPIO 电平 / LED / 屏幕角标 / 串口输出。"""
        ...
