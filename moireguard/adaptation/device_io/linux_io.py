"""
Linux 设备 IO：配置落点 / 日志 / 布尔物理指示

对应 v2.2 平台差异对照表（Linux 列）：
  配置  /etc/moireguard/config.yaml（镜像内置）+ /var/lib/moireguard/（现场覆盖）
  日志  标准输出（systemd-journal 收集）/ journald-native / 文件
  指示  GPIO sysfs（板载 40-pin 排针），不可用时退化为日志行

GPIO 未配置或 sysfs 不可写时不报错——指示通道的具体选型仍是未决项 Q4，
此处先给可读写的默认路径，实板接线后改配置即可。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any, Optional

import yaml

from moireguard.adaptation.base import IDeviceIO

DEFAULT_ETC = Path("/etc/moireguard/config.yaml")
DEFAULT_VAR = Path("/var/lib/moireguard/config.yaml")
DEFAULT_GPIO = Path("/sys/class/gpio/gpio66/value")  # 例：ZeroPi/RK 系常用映射，实板需按接线改


class LinuxDeviceIO(IDeviceIO):
    """Linux（含 Purple Pi OH2）平台的 IDeviceIO 实现。"""

    def __init__(
        self,
        etc_path: Path | str = DEFAULT_ETC,
        var_path: Path | str = DEFAULT_VAR,
        log_path: Optional[Path | str] = None,
        gpio_path: Path | str = DEFAULT_GPIO,
    ):
        self.etc_path = Path(etc_path)
        self.var_path = Path(var_path)
        self.log_path = Path(log_path) if log_path else None
        self.gpio_path = Path(gpio_path)
        self._gpio_ready = False

    # ------------------------------------------------------------------ 配置
    def read_config(self) -> dict:
        """
        现场覆盖优先合并镜像内置：/var/lib 存在则覆盖 /etc 同名键。
        文件缺失/损坏一律返回已读到的部分——配置损坏是非致命的（v2.2 §2 决策）。
        """
        merged: dict[str, Any] = {}
        for path in (self.etc_path, self.var_path):
            if not path.exists():
                continue
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    merged.update(data)
            except Exception:
                # 解析失败：保留旧值继续跑，只记账不中断（C 规则：非致命）
                self.log("WARN", f"config unreadable: {path}")
        return merged

    # ------------------------------------------------------------------ 日志
    def log(self, level: str, msg: str) -> None:
        """单行格式 {ts} {LEVEL} {msg}，前后可附 key=value（附录 B 由调用方拼）。"""
        line = f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {level:<5s} {msg}"
        print(line, file=sys.stderr, flush=True)
        if self.log_path is not None:
            try:
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                with self.log_path.open("a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except Exception:
                pass  # 日志文件失败绝不反杀主流程

    # ------------------------------------------------------------------ 指示
    def indicate(self, has_moire: bool) -> None:
        """
        布尔结果物理呈现：优先 GPIO sysfs 拉平/拉高，失败退化为日志行。
        has_moire=True → 高电平（告警）；False → 低电平。
        """
        value = "1" if has_moire else "0"
        try:
            if not self._gpio_ready:
                if self.gpio_path.parent.exists():
                    self._gpio_ready = True
                else:
                    raise OSError("gpio not exported")
            self.gpio_path.write_text(value)
        except Exception:
            self.log("INFO", f"indicate has_moire={int(has_moire)} (gpio-unavailable)")

    # ------------------------------------------------------------------ 辅助
    def snapshot(self) -> dict:
        """当前适配层运行时快照，供 /status 等只读端点。"""
        return {
            "platform": "linux",
            "etc_config": str(self.etc_path) if self.etc_path.exists() else None,
            "var_config": str(self.var_path) if self.var_path.exists() else None,
            "log_file": str(self.log_path) if self.log_path else "stderr",
            "gpio_active": self._gpio_ready,
        }
