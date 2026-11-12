"""IDeviceIO — 配置、日志、指示。两套系统的路径与外设约定统一在此。

v2.4.1：indicate 接受三态字符串（screen_detected / has_moire / no_moire）
或布尔（兼容旧调用，如 FATAL 路径 indicate(False)）。物理映射（灯码）属
SDD Q4 剩余项，当前约定：正向态（has_moire / screen_detected）→ 高电平，
no_moire → 低电平。用 result_to_bool() 统一归约，各实现不得各自解释。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

# 三态中的"正向"结论（GPIO/LED 点亮）
POSITIVE_STATES = ("has_moire", "screen_detected")


def result_to_bool(result: "str | bool") -> bool:
    """三态/布尔 → 物理电平归约。所有 indicate 实现共用，不许各写一份。"""
    if isinstance(result, str):
        return result in POSITIVE_STATES
    return bool(result)


class IDeviceIO(ABC):
    """设备 IO 抽象接口。"""

    @abstractmethod
    def read_config(self) -> dict: ...

    @abstractmethod
    def log(self, level: str, msg: str) -> None: ...

    @abstractmethod
    def indicate(self, result: "str | bool") -> None:
        """三态结果的物理呈现：GPIO 电平 / LED / 屏幕角标 / 串口输出。

        result ∈ {screen_detected, has_moire, no_moire}（SDD v2.4.1 方案 B），
        也接受 bool 兼容旧调用。电平归约见 result_to_bool()。
        """
        ...
