"""
模型注册表 (Model Registry)

实现懒加载 + 单例模式:
  - 首次 create(model_id) 才加载权重
  - 同 model_id 后续复用单例
  - 设备决策: auto → cuda 可用则用 cuda，否则 cpu
"""

from pathlib import Path
from typing import Optional

import torch

from moireguard.model_layer.contracts import ModelBundle, IInputAdapter, IOutputAdapter, DetectorCore
from moireguard.model_layer.builtin_adapter import RGBInputAdapter, SFRNetOutputAdapter


class ModelRegistry:
    """
    模型注册表。
    
    管理所有可用模型的注册、实例化与缓存。
    """
    
    def __init__(self, config: Optional[dict] = None):
        """
        初始化注册表。
        
        Args:
            config: 模型配置字典，来自 models.yaml
        """
        self._registry: dict[str, ModelBundle] = {}
        self._instances: dict[str, tuple[DetectorCore, IInputAdapter, IOutputAdapter]] = {}
        self._config = config or {}
        
        # 注册内置模型
        self._register_builtins()
    
    def _register_builtins(self):
        """注册内置模型"""
        # SFRNet UHD 模型
        try:
            from moireguard.model_layer.detectors.sfrnet_detector import SFRNetDetector
            self.register(ModelBundle(
                model_id="sfrnet_uhd",
                display_name="SFRNet (UHD Moiré)",
                core_factory=SFRNetDetector,
                input_adapter_factory=RGBInputAdapter,
                output_adapter_factory=SFRNetOutputAdapter,
                default_weights="assets/weights/SFR_best.pth",
                expected_tile_size=512,
                expected_stride=256,
            ))
        except ImportError:
            pass  # SFRNetDetector 尚未实现
    
    def register(self, bundle: ModelBundle) -> None:
        """注册一个模型"""
        self._registry[bundle.model_id] = bundle
    
    def list_models(self) -> list[str]:
        """列出所有已注册的模型 ID"""
        return list(self._registry.keys())
    
    def get_bundle(self, model_id: str) -> ModelBundle:
        """获取模型配置"""
        if model_id not in self._registry:
            available = ", ".join(self.list_models()) or "(none)"
            raise KeyError(f"E-MODEL-UNKNOWN: '{model_id}'. Available: {available}")
        return self._registry[model_id]
    
    def create(
        self,
        model_id: str,
        device: str = "auto",
        weights_path: Optional[str] = None,
    ) -> tuple[DetectorCore, IInputAdapter, IOutputAdapter]:
        """
        创建模型实例 (懒加载 + 单例)。
        
        Args:
            model_id: 模型 ID
            device: 设备 ("auto" / "cpu" / "cuda:0")
            weights_path: 权重文件路径 (可选，默认使用 bundle 中的路径)
        
        Returns:
            (core, input_adapter, output_adapter) 三元组
        """
        # 单例缓存
        cache_key = f"{model_id}:{device}"
        if cache_key in self._instances:
            return self._instances[cache_key]
        
        bundle = self.get_bundle(model_id)
        
        # 设备决策
        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
        
        # 创建核心模型
        core = bundle.core_factory()
        core.eval()
        core.to(device)
        
        # 加载权重
        weight_file = weights_path or bundle.default_weights
        weight_path = Path(weight_file)
        if not weight_path.exists():
            # 尝试相对于项目根目录
            project_root = Path(__file__).parent.parent.parent
            weight_path = project_root / weight_file
        
        if weight_path.exists():
            state_dict = torch.load(weight_path, map_location=device)
            try:
                core.load_state_dict(state_dict, strict=True)
            except RuntimeError as e:
                raise RuntimeError(f"E-WEIGHT-MISMATCH: {e}") from e
        else:
            raise FileNotFoundError(f"E-WEIGHT-NOT-FOUND: {weight_file}")
        
        # 创建适配器
        input_adapter = bundle.input_adapter_factory()
        output_adapter = bundle.output_adapter_factory()
        
        # 缓存
        self._instances[cache_key] = (core, input_adapter, output_adapter)
        
        return core, input_adapter, output_adapter
    
    def unload(self, model_id: str) -> None:
        """卸载模型实例，释放显存"""
        keys_to_remove = [k for k in self._instances if k.startswith(f"{model_id}:")]
        for key in keys_to_remove:
            del self._instances[key]
        torch.cuda.empty_cache()
