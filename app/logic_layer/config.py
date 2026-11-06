"""
配置管理器 (Config Manager)

加载顺序:
  1. 本文件里的 dataclass 默认值 —— 全项目**唯一**的默认值真源
     （bootstrapper.DEFAULTS 由 AppConfig 导出，不再另写一份）
  2. configs/default.yaml (镜像内置默认) —— 已实现
  3. 平台侧持久化配置 (由适配层 IDeviceIO 决定落点: 板端 /etc + /var/lib 覆盖)
     —— 由组合根调 merge() 合入
  4. 运行时热更新覆盖 (update()，不需重启) —— 已实现
"""

from pathlib import Path
from typing import Any, Optional
from dataclasses import dataclass, field, fields, is_dataclass

import yaml


@dataclass
class InferenceConfig:
    """推理配置"""
    tile_size: int = 512
    stride: int = 256
    threshold: float = 0.5
    coverage_threshold: float = 0.01
    device: str = "auto"  # "auto" | "cpu" | "cuda:0"
    precision: str = "fp32"  # "fp32" | "fp16"
    fusion: str = "cosine"  # "uniform" | "cosine"
    apply_morphology: bool = False


@dataclass
class CameraConfig:
    """取帧配置（IMX415 @ MIPI CSI-2，4K@30fps）"""
    device: str = "/dev/video0"
    width: int = 3840
    height: int = 2160
    fps: int = 30


@dataclass
class RuntimeConfig:
    """推理后端与模型文件（镜像内置为板端 RKNN；开发机走 runtime.host 覆盖层）"""
    backend: str = "rknn"
    model_path: str = "/usr/share/moireguard/models/model.rknn"


@dataclass
class RelayConfig:
    """WiFi 画面回传（旁路诊断面，默认关，SDD §4.5.2）"""
    enabled: bool = False
    host: str = "192.168.1.100"
    port: int = 9000
    interval_frames: int = 6
    jpeg_quality: int = 75
    downscale: int = 1080
    overlay_result: bool = True


@dataclass
class ModelConfig:
    """模型配置"""
    active: str = "sfrnet_uhd"
    weights_path: str = "assets/weights/SFR_best.pth"
    tile_size: int = 512
    stride: int = 256
    device: str = "auto"
    precision: str = "fp32"


@dataclass
class AppConfig:
    """应用配置"""
    inference: InferenceConfig = field(default_factory=InferenceConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    camera: CameraConfig = field(default_factory=CameraConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    relay: RelayConfig = field(default_factory=RelayConfig)
    max_pixels: int = 150_000_000  # 1.5亿像素上限
    # 网络出口参数原样透传: 逻辑层不解释 network 节，避免反向依赖适配层
    # (依赖方向: 逻辑层 → 模型层 → 适配层)。类型化由组合根转成
    # adaptation.base.NetworkConfig.from_dict()。
    network: dict[str, Any] = field(default_factory=dict)


class ConfigManager:
    """
    配置管理器。
    
    负责配置的加载、合并、校验。
    """
    
    def __init__(self, project_root: Optional[Path] = None):
        """
        初始化配置管理器。
        
        Args:
            project_root: 项目根目录，默认为当前文件所在目录的祖父目录
        """
        if project_root is None:
            project_root = Path(__file__).parent.parent.parent
        
        self._project_root = project_root
        self._config = AppConfig()
        
        # 加载默认配置
        self._load_defaults()
    
    def _load_defaults(self):
        """加载内置默认配置"""
        default_config_path = self._project_root / "configs" / "default.yaml"
        if default_config_path.exists():
            with open(default_config_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
                self._merge_config(data)
    
    def merge(self, data: dict[str, Any]) -> None:
        """公开入口：把平台侧（IDeviceIO.read_config）或命令行覆盖合入。"""
        self._merge_config(data or {})

    def as_dict(self) -> dict[str, Any]:
        """转成 bootstrapper/engine 期望的 section dict。"""
        out: dict[str, Any] = {}
        for f in fields(self._config):
            value = getattr(self._config, f.name)
            if is_dataclass(value):
                out[f.name] = {k: getattr(value, k) for k in value.__dataclass_fields__}
            else:
                out[f.name] = value
        return out

    def _merge_config(self, data: dict[str, Any]):
        """合并配置数据：dataclass 小节逐字段合，未知字段忽略并告警。"""
        for f in fields(self._config):
            section = getattr(self._config, f.name)
            if not is_dataclass(section):
                continue
            incoming = data.get(f.name)
            if not isinstance(incoming, dict):
                continue
            for key, value in incoming.items():
                if key == "host" and f.name == "runtime":
                    continue        # 开发机覆盖层由 HostDeviceIO 提升，此处不照单全收
                if hasattr(section, key):
                    setattr(section, key, value)

        if "max_pixels" in data:
            self._config.max_pixels = data["max_pixels"]

        if isinstance(data.get("network"), dict):
            self._config.network = dict(data["network"])
    
    @property
    def config(self) -> AppConfig:
        """获取当前配置"""
        return self._config
    
    def validate(self, params: dict[str, Any]) -> list[dict[str, str]]:
        """
        校验参数。
        
        Args:
            params: 待校验的参数字典
        
        Returns:
            错误列表，每项包含 {"field": ..., "reason": ..., "suggestion": ...}
        """
        errors = []
        
        # tile_size 校验
        if "tile_size" in params:
            tile_size = params["tile_size"]
            if not isinstance(tile_size, int) or tile_size < 64 or tile_size > 4096:
                errors.append({
                    "field": "tile_size",
                    "reason": f"tile_size 必须在 [64, 4096] 范围内，当前值: {tile_size}",
                    "suggestion": "建议使用 512",
                })
        
        # stride 校验
        if "stride" in params:
            stride = params["stride"]
            tile_size = params.get("tile_size", self._config.inference.tile_size)
            if not isinstance(stride, int) or stride < 1 or stride > tile_size:
                errors.append({
                    "field": "stride",
                    "reason": f"stride 必须在 [1, tile_size({tile_size})] 范围内，当前值: {stride}",
                    "suggestion": f"建议使用 {tile_size // 2}",
                })
        
        # threshold 校验
        if "threshold" in params:
            threshold = params["threshold"]
            if not isinstance(threshold, (int, float)) or threshold < 0 or threshold > 1:
                errors.append({
                    "field": "threshold",
                    "reason": f"threshold 必须在 [0, 1] 范围内，当前值: {threshold}",
                    "suggestion": "建议使用 0.5",
                })
        
        # device 校验
        if "device" in params:
            device = params["device"]
            valid_devices = ["auto", "cpu"] + [f"cuda:{i}" for i in range(8)]
            if device not in valid_devices and not device.startswith("cuda"):
                errors.append({
                    "field": "device",
                    "reason": f"不支持的设备: {device}",
                    "suggestion": "使用 'auto', 'cpu', 或 'cuda:0'",
                })
        
        return errors
    
    def update(self, params: dict[str, Any]) -> None:
        """
        更新配置 (会话级覆盖)。
        
        Args:
            params: 要更新的参数
        """
        self._merge_config(params)
    
    def get_model_config(self) -> dict[str, Any]:
        """获取模型配置字典"""
        return {
            "active": self._config.model.active,
            "weights_path": self._config.model.weights_path,
            "tile_size": self._config.model.tile_size,
            "stride": self._config.model.stride,
            "device": self._config.model.device,
            "precision": self._config.model.precision,
        }
