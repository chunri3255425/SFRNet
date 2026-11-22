"""IRuntimeAdapter — 把"跑一次前向"抽象出来，屏蔽 Torch / ONNX / RKNN 差异。

契约：
  - load(model_path) 加载模型，失败抛 RuntimeLoadError
  - infer(tensor) 输入 [1,3,H,W] float32，输出 [1,1,H,W] raw logits（未过 sigmoid）
  - warmup() 预热一次（首次前向含 kernel 选择/内存池分配，可达稳态 3~10 倍耗时）

两个能力标记把"引擎怎么备料"从硬编码变成声明（默认值就是上面那三条老契约，
所以 SFRNet 后端一行都不用改）：
  - wants_gray=True     引擎改为整图 cvtColor 一次，交来 [H,W] uint8（0-255 域）。
                        省掉每片 3 通道的 float32 往返 —— 判据只看亮度时，那三趟
                        拷贝全是白干的。
  - scalar_output=True  每片只出一个标量。引擎据此把融合搬到片网格上，
                        4K float64 的 num/den 与 8.4M 次 exp 一起消失；
                        代价是 coverage 口径从"像素占比"变成"片计数占比"，
                        C 的含义按片数量子化（实测四种口径见 .scratch/coverage_metrics.py，
                        本机过程件、不入库；结论已写进 docs/design/CONTROL_LOGIC.md）。

另有一个可选入口（有默认实现，不实现也能跑）：
  - infer_tiles(patches, guard=None)  片网格整批推理。默认逐片调 infer，等价于
                        老的串行循环；片网格后端可以覆盖它把片分给多路执行器。
                        guard 是每片前调一次的回调，抛异常即中止（引擎拿它做停机闸）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np

# 供各实现复用的形状常量
IN_SHAPE = (1, 3, 512, 512)


class RuntimeLoadError(RuntimeError):
    """模型加载失败（文件缺失 / 结构不匹配 / 后端不可用）。"""


class IRuntimeAdapter(ABC):
    """推理后端抽象接口。"""

    # 能力标记：引擎按声明备料，不按实现类名猜（猜类名等于把适配层焊回逻辑层）。
    wants_gray: bool = False
    scalar_output: bool = False

    @abstractmethod
    def load(self, model_path: str, **kwargs) -> None: ...

    @abstractmethod
    def infer(self, tensor: "np.ndarray") -> np.ndarray:
        """[1,3,H,W] float32 NCHW → [1,1,H,W] float32 raw logits。

        wants_gray / scalar_output 打开时形状跟着变（[H,W] uint8 入、
        [1,1,1,1] 出），但**语义不变**：出的仍是 logits，sigmoid 只在 Decider 做。
        """
        ...

    @abstractmethod
    def warmup(self) -> None: ...

    def infer_tiles(self, patches, guard=None):
        """片网格整批推理：返回与 patches 一一对应的 infer 结果。

        默认实现就是逐片调（与引擎以前的 for 循环完全等价，所以不实现这个
        入口的后端行为不变）。片网格后端可覆盖它，把片分给多路执行器。

        guard: 可选回调，每片前调一次；它抛异常即中止，异常原样抛给调用侧。
        """
        out = []
        for p in patches:
            if guard is not None:
                guard()
            out.append(self.infer(p))
        return out

    @abstractmethod
    def unload(self) -> None: ...
