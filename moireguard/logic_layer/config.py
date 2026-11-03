"""
配置管理器 (Config Manager)

加载顺序:
  1. configs/default.yaml (内置默认)
  2. 用户配置 %APPDATA%/MoireGuard/config.yaml
  3. 本次会话 UI 覆盖
"""

from pathlib import Path
from typing import Any, Optional
from dataclasses import dataclass, field

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
    max_pixels: int = 150_000_000  # 1.5亿像素上限


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
    
    def _merge_config(self, data: dict[str, Any]):
        """合并配置数据"""
        if "inference" in data:
            for key, value in data["inference"].items():
                if hasattr(self._config.inference, key):
                    setattr(self._config.inference, key, value)
        
        if "model" in data:
            for key, value in data["model"].items():
                if hasattr(self._config.model, key):
                    setattr(self._config.model, key, value)
        
        if "max_pixels" in data:
            self._config.max_pixels = data["max_pixels"]
    
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
