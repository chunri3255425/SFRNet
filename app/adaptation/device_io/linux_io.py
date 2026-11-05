"""Linux 设备 IO 实现。

- 配置：/etc/moireguard/（镜像内置）+ /var/lib/moireguard/（现场覆盖）
- 日志：/var/log/moireguard/（logrotate 滚动）；开发环境降级 stdout
- 指示：GPIO sysfs（40-pin），无权限时降级仅日志
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from app.adaptation.device_io.base import IDeviceIO

CFG_DIR = Path("/etc/moireguard")
OVERRIDE_CFG = Path("/var/lib/moireguard/override.yaml")
LOG_DIR = Path("/var/log/moireguard")
GPIO_BASE = Path("/sys/class/gpio")


class LinuxDeviceIO(IDeviceIO):
    """Linux 系统的配置 / 日志 / GPIO 指示实现。"""

    def __init__(self, gpio_pin: int | None = None):
        self._gpio_pin = gpio_pin
        self._gpio_ready = False
        LOG_DIR.mkdir(parents=True, exist_ok=True) if os.access(LOG_DIR.parent, os.W_OK) else None
        self._logger = self._setup_logger()

    def _setup_logger(self) -> logging.Logger:
        logger = logging.getLogger("moireguard")
        logger.setLevel(logging.INFO)
        if not logger.handlers:
            fmt = logging.Formatter(
                "{asctime} {levelname} {message}", style="{", datefmt="%Y-%m-%dT%H:%M:%S"
            )
            log_file = LOG_DIR / "moireguard.log"
            try:
                handler: logging.Handler = logging.FileHandler(log_file)
            except OSError:
                handler = logging.StreamHandler()   # 降级 stdout（非致命）
            handler.setFormatter(fmt)
            logger.addHandler(handler)
        return logger

    # ── 配置 ─────────────────────────────────
    def read_config(self) -> dict:
        base = {}
        default_cfg = CFG_DIR / "default.yaml"
        try:
            if default_cfg.exists():
                import yaml
                base = yaml.safe_load(default_cfg.read_text(encoding="utf-8")) or {}
        except Exception:
            self.log("WARN", f"默认配置解析失败，退回内置默认值: {default_cfg}")
        try:
            if OVERRIDE_CFG.exists():
                import yaml
                override = yaml.safe_load(OVERRIDE_CFG.read_text(encoding="utf-8")) or {}
                base.update(override)   # 全有或全无：解析失败整体丢弃
        except Exception:
            self.log("WARN", f"现场覆盖配置解析失败，整体丢弃: {OVERRIDE_CFG}")
        return base

    # ── 日志 ─────────────────────────────────
    def log(self, level: str, msg: str) -> None:
        self._logger.log(getattr(logging, level.upper(), logging.INFO), msg)

    # ── 布尔指示（GPIO sysfs）─────────────────
    def indicate(self, has_moire: bool) -> None:
        if not self._gpio_ready:
            self._gpio_ready = self._setup_gpio()
        if self._gpio_ready and self._gpio_pin is not None:
            try:
                (GPIO_BASE / f"gpio{self._gpio_pin}" / "value").write_text(
                    "1" if has_moire else "0"
                )
                return
            except OSError as e:
                self.log("WARN", f"GPIO 写入失败: {e}")
                self._gpio_ready = False
        # 降级：仅日志输出布尔
        self.log("INFO", f"has_moire={has_moire}")

    def _setup_gpio(self) -> bool:
        if self._gpio_pin is None:
            return False
        try:
            export = GPIO_BASE / "export"
            if not (GPIO_BASE / f"gpio{self._gpio_pin}").exists():
                export.write_text(str(self._gpio_pin))
            (GPIO_BASE / f"gpio{self._gpio_pin}" / "direction").write_text("out")
            return True
        except OSError as e:
            self.log("WARN", f"GPIO 初始化失败，指示降级为日志: {e}")
            return False


class HostDeviceIO(IDeviceIO):
    """开发机实现：stdout 日志 + JSON 配置（unittest 用）。"""

    def __init__(self, config: dict | None = None):
        self._config = config or {}
        self._logger = logging.getLogger("moireguard.host")

    def read_config(self) -> dict:
        return json.loads(json.dumps(self._config))

    def log(self, level: str, msg: str) -> None:
        self._logger.log(getattr(logging, level.upper(), logging.INFO), msg)

    def indicate(self, has_moire: bool) -> None:
        self.log("INFO", f"has_moire={has_moire}")
