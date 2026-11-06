"""
推理服务 (Inference Service)

逻辑层的编排入口，对外提供唯一 API。

数据流:
  1. 参数校验
  2. 模型加载
  3. 输入适配
  4. 滑窗推理
  5. 输出适配
  6. 后处理
  7. 导出 (可选)
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional
import time
import threading

import torch
from PIL import Image

from app.model_layer.registry import ModelRegistry
from app.model_layer.contracts import DetectorOutput
from app.logic_layer.config import ConfigManager
from app.logic_layer.sliding_window import SlidingWindowEngine
from app.logic_layer.postprocess import PostProcessor, PostProcessResult
from app.logic_layer.export_service import ExportService, ExportOptions
from app.logic_layer.exceptions import MoireError, ErrorCode


@dataclass
class InferenceStats:
    """推理统计信息"""
    total_ms: float = 0.0
    per_tile_avg_ms: float = 0.0
    num_tiles: int = 0
    peak_memory_mb: float = 0.0
    device: str = ""


@dataclass
class InferenceRequest:
    """推理请求"""
    source: Any  # Path | Image.Image | str
    model_id: str = "sfrnet_uhd"
    tile_size: Optional[int] = None
    stride: Optional[int] = None
    threshold: float = 0.5
    coverage_threshold: float = 0.01
    device: str = "auto"
    apply_morphology: bool = False
    export: Optional[ExportOptions] = None


@dataclass
class InferenceResult:
    """推理结果"""
    source_id: str
    prob_map: torch.Tensor
    binary_mask: torch.Tensor
    coverage: float
    has_moire: bool
    stats: InferenceStats
    artifacts: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class InferenceService:
    """
    推理服务。
    
    编排整个推理流程。
    """
    
    def __init__(
        self,
        registry: ModelRegistry,
        config: ConfigManager,
    ):
        """
        初始化推理服务。
        
        Args:
            registry: 模型注册表
            config: 配置管理器
        """
        self._registry = registry
        self._config = config
        self._cancel_event = threading.Event()
    
    def run(
        self,
        request: InferenceRequest,
        on_progress: Optional[Callable[[int, int], None]] = None,
    ) -> InferenceResult:
        """
        执行推理。
        
        Args:
            request: 推理请求
            on_progress: 进度回调 (done, total)
        
        Returns:
            InferenceResult
        """
        start_time = time.time()
        self._cancel_event.clear()
        
        # 1. 参数校验
        params = {
            "tile_size": request.tile_size or self._config.config.inference.tile_size,
            "stride": request.stride or self._config.config.inference.stride,
            "threshold": request.threshold,
            "device": request.device,
        }
        errors = self._config.validate(params)
        if errors:
            raise MoireError(
                ErrorCode.MODEL_UNKNOWN,
                f"参数校验失败: {errors[0]['reason']}",
            )
        
        # 2. 模型加载
        tile_size = params["tile_size"]
        stride = params["stride"]
        
        core, input_adapter, output_adapter = self._registry.create(
            model_id=request.model_id,
            device=request.device,
        )
        
        # 3. 输入适配
        tensor, meta = input_adapter.from_external(request.source)
        device = next(core.parameters().__iter__()).device if hasattr(core, 'parameters') else torch.device(request.device if request.device != "auto" else ("cuda" if torch.cuda.is_available() else "cpu"))
        tensor = tensor.to(device)
        
        # 4. 滑窗推理
        engine = SlidingWindowEngine(
            tile_size=tile_size,
            stride=stride,
            fusion=self._config.config.inference.fusion,
        )
        
        # 记录峰值显存
        peak_memory = 0.0
        if device.type == "cuda":
            torch.cuda.reset_peak_memory_stats(device)
        
        # 执行推理
        raw_logits = engine(
            image=tensor,
            forward_fn=core.forward,
            device=device,
            on_progress=on_progress,
        )
        
        # 统计显存
        if device.type == "cuda":
            peak_memory = torch.cuda.max_memory_allocated(device) / 1024 / 1024
        
        # 5. 输出适配
        detector_output = output_adapter.to_external(raw_logits, meta)
        detector_output = output_adapter.resample_to_source(detector_output)
        
        # 6. 后处理
        postprocessor = PostProcessor(
            threshold=request.threshold,
            coverage_threshold=request.coverage_threshold,
            apply_morphology=request.apply_morphology,
        )
        post_result = postprocessor.apply(detector_output.logits)
        
        # 7. 导出 (可选)
        artifacts = []
        if request.export is not None:
            export_service = ExportService(request.export)
            artifacts = export_service.save(
                source_id=meta.source_id,
                prob_map=post_result.prob_map,
                binary_mask=post_result.binary_mask,
                coverage=post_result.coverage,
                has_moire=postprocessor.has_moire(post_result.coverage),
                meta=meta,
                stats=InferenceStats(
                    total_ms=(time.time() - start_time) * 1000,
                    num_tiles=len(engine.plan(meta.src_height, meta.src_width)),
                    peak_memory_mb=peak_memory,
                    device=str(device),
                ),
            )
        
        # 构建结果
        elapsed_ms = (time.time() - start_time) * 1000
        num_tiles = len(engine.plan(meta.src_height, meta.src_width))
        
        stats = InferenceStats(
            total_ms=elapsed_ms,
            per_tile_avg_ms=elapsed_ms / num_tiles if num_tiles > 0 else 0,
            num_tiles=num_tiles,
            peak_memory_mb=peak_memory,
            device=str(device),
        )
        
        result = InferenceResult(
            source_id=meta.source_id,
            prob_map=post_result.prob_map,
            binary_mask=post_result.binary_mask,
            coverage=post_result.coverage,
            has_moire=postprocessor.has_moire(post_result.coverage),
            stats=stats,
            artifacts=artifacts,
            warnings=[],
        )
        
        # 释放中间张量
        del tensor, raw_logits, detector_output
        if device.type == "cuda":
            torch.cuda.empty_cache()
        
        return result
    
    def cancel(self) -> None:
        """协作式取消"""
        self._cancel_event.set()
    
    @property
    def is_cancelled(self) -> bool:
        """是否已取消"""
        return self._cancel_event.is_set()
