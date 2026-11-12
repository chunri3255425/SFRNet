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

from app.adaptation.device_io.base import IDeviceIO, result_to_bool

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
        logger.propagate = False      # 自带 sink，不再向 root 传，避免与调用方 basicConfig 双打
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

    # ── 三态指示（GPIO sysfs；归约见 base.result_to_bool）──
    def indicate(self, result) -> None:
        positive = result_to_bool(result)
        if not self._gpio_ready:
            self._gpio_ready = self._setup_gpio()
        if self._gpio_ready and self._gpio_pin is not None:
            try:
                (GPIO_BASE / f"gpio{self._gpio_pin}" / "value").write_text(
                    "1" if positive else "0"
                )
                return
            except OSError as e:
                self.log("WARN", f"GPIO 写入失败: {e}")
                self._gpio_ready = False
        # 降级：仅日志输出三态原文（灯码细分属 SDD Q4 剩余项）
        self.log("INFO", f"result={result}")

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
    """开发机实现：stdout 日志 + 仓库内 configs/default.yaml。

    配置真源只有一个：镜像里是 /etc/moireguard/default.yaml（由
    build/package_app.py 从 configs/default.yaml 安装），开发机就直接读
    configs/default.yaml。早期版本只读注入的 dict + bootstrapper 里的 DEFAULTS，
    导致开发机永远拿 DEFAULTS 的 model.rknn，yaml 里的权重路径不生效。
    """

    REPO_CFG = Path(__file__).resolve().parents[3] / "configs" / "default.yaml"

    def __init__(self, config: dict | None = None):
        self._logger = self._setup_logger()
        self._config = config if config is not None else self._load_repo_config()

    @staticmethod
    def _setup_logger() -> logging.Logger:
        """stdout 日志。

        必须自己挂 handler：开发机若不配，logging 的 lastResort 只放行
        WARNING 及以上，BOOTING/逐帧结果全部静默——“跑完一声不响”比报错更难查。

        同时关掉 propagate：自己已经有 sink 了，再往 root 传一份就会被
        调用方的 basicConfig 重复输出一次（run_sim.py 实测每条日志打两遍）。
        """
        logger = logging.getLogger("moireguard.host")
        logger.setLevel(logging.INFO)
        logger.propagate = False
        if not logger.handlers:
            h = logging.StreamHandler()
            h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s",
                                             datefmt="%Y-%m-%dT%H:%M:%S"))
            logger.addHandler(h)
        return logger

    def _load_repo_config(self) -> dict:
        """读 configs/default.yaml，并把 runtime.host 覆盖层提到 runtime 顶层。"""
        try:
            import yaml
            data = yaml.safe_load(self.REPO_CFG.read_text(encoding="utf-8")) or {}
        except FileNotFoundError:
            return {}
        except Exception:
            self.log("WARN", f"configs 解析失败，退回内置默认值: {self.REPO_CFG}")
            return {}
        runtime = data.get("runtime")
        if isinstance(runtime, dict) and isinstance(runtime.get("host"), dict):
            host = dict(runtime["host"])          # 开发机后端与权重路径
            runtime.pop("host")
            runtime.update(host)
        return data

    def read_config(self) -> dict:
        return json.loads(json.dumps(self._config))

    def log(self, level: str, msg: str) -> None:
        self._logger.log(getattr(logging, level.upper(), logging.INFO), msg)

    def indicate(self, result) -> None:
        self.log("INFO", f"result={result} positive={result_to_bool(result)}")
