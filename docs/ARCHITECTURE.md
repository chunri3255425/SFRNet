# MoiréGuard 架构概览

本文档提供软件的整体架构视图，帮助开发者快速理解系统设计。

---

## 1. 系统定位

**MoiréGuard** 是一个基于 SFRNet 的桌面端摩尔纹检测工具，将上游科研代码解耦为可替换的模型层，在其上重建逻辑层与控制层。

| 维度 | 上游科研代码 | MoiréGuard |
|------|-------------|------------|
| 入口形态 | 改源码路径后 `python predict.py` | 双击 exe，选图即用 |
| 模型调用 | 硬编码路径 + 直接实例化 | 注册表 + 配置驱动 |
| 推理组织 | 脚本内联滑窗循环 | 独立 `InferenceService` |
| 参数暴露 | 常量写死 | 配置 + UI 热调 |
| 结果产物 | 单张二值 PNG | 掩膜 + 叠加 + 热力图 + JSON |

---

## 2. 三层架构

```
┌─────────────────────────────────────────────────────────────────┐
│                        控制层 Control Layer                       │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐ │
│  │ MainWindow  │  │  ViewModels │  │  QThreadPool + Workers  │ │
│  │  (PySide6)  │  │  (状态绑定)  │  │    (异步推理调度)        │ │
│  └─────────────┘  └─────────────┘  └─────────────────────────┘ │
│  ❌ 不直接 import torch  ❌ 不写推理算法  ❌ 不碰 state_dict     │
├─────────────────────────────────────────────────────────────────┤
│                        逻辑层 Logic Layer                        │
│  ┌───────────────┐  ┌────────────────┐  ┌──────────────────┐   │
│  │InferenceService│→│SlidingWindow   │→│ PostProcessor    │   │
│  │  (编排入口)    │  │  (切分+融合)    │  │ (sigmoid+二值化) │   │
│  └───────────────┘  └────────────────┘  └──────────────────┘   │
│  ┌───────────────┐  ┌────────────────┐  ┌──────────────────┐   │
│  │ ConfigManager │  │ ExportService  │  │   Exceptions     │   │
│  │  (配置校验)    │  │  (产物落盘)    │  │   (错误码体系)    │   │
│  └───────────────┘  └────────────────┘  └──────────────────┘   │
│  ❌ 不做 UI 回调  ❌ 不持有 Qt 对象  ❌ 不知道窗体的存在         │
├─────────────────────────────────────────────────────────────────┤
│                        模型层 Model Layer                        │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │              ① IInputAdapter (输入接口)                   │   │
│  │   from_external(): 任意图像 → [1,3,H,W] float32 [0,1]    │   │
│  │   to_external(): Tensor → PIL.Image                      │   │
│  └─────────────────────────────────────────────────────────┘   │
│                              ↓                                   │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │              DetectorCore (模型内核协议)                   │   │
│  │   forward(tile): [1,3,512,512] → [1,1,512,512] logits   │   │
│  │   实现: SFRNetDetector (包装上游 build_SFR)               │   │
│  └─────────────────────────────────────────────────────────┘   │
│                              ↓                                   │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │              ② IOutputAdapter (输出接口)                  │   │
│  │   to_external(): raw → DetectorOutput (logits)           │   │
│  │   resample_to_source(): 上采样还原到原图分辨率             │   │
│  └─────────────────────────────────────────────────────────┘   │
│  ❌ 不做阈值判断  ❌ 不做滑窗  ❌ 不做文件 IO  ❌ 不读配置       │
├─────────────────────────────────────────────────────────────────┤
│                        外部资源                                  │
│  ┌─────────────────┐  ┌─────────────────────────────────────┐ │
│  │  SFR_best.pth   │  │  utils/sfr/ (冻结的上游算法代码)      │ │
│  │  (7.8M 参数)     │  │  ├── sfrnet.py                      │ │
│  │                 │  │  ├── safm.py                          │ │
│  │                 │  │  └── FRM.py                           │ │
│  └─────────────────┘  └─────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

**依赖方向严格单向**: 控制层 → 逻辑层 → 模型层

---

## 3. 数据流

一次完整的推理流程：

```
用户点"开始"
  │
  ├─ 1. MainWindow 收集 UI 参数 → dict
  │
  ├─ 2. ViewModel.submit(params) → 投递到 QThreadPool
  │
  └─ 3. InferenceService.run(task)
       │
       ├─ 3.1  ConfigManager.validate(params)      # 参数校验
       │
       ├─ 3.2  ModelRegistry.create(model_id)       # 懒加载权重
       │        └─ 首次: torch.load(SFR_best.pth)
       │        └─ 后续: 复用单例
       │
       ├─ 3.3  IInputAdapter.from_external(image)   # 输入适配
       │        └─ 返回: (Tensor[1,3,H,W], InputMeta)
       │
       ├─ 3.4  SlidingWindowEngine.plan(H,W)        # 规划切分
       │        └─ 返回: List[Rect]
       │
       ├─ 3.5  for each rect:                       # 逐块推理
       │        └─ DetectorCore.forward(tile) → logits
       │
       ├─ 3.6  SlidingWindowEngine.fuse()           # 融合
       │        └─ 在 logits 域累加 (余弦窗加权)
       │
       ├─ 3.7  IOutputAdapter.to_external()         # 输出适配
       │        └─ 返回: DetectorOutput
       │
       ├─ 3.8  PostProcessor.apply()                # 后处理
       │        └─ ★ 全流程唯一 sigmoid
       │        └─ 二值化 + 统计覆盖率
       │
       ├─ 3.9  ExportService.save()                 # 导出产物
       │        └─ mask.png + overlay.png + prob.png + meta.json
       │
       └─ 3.10 emit finished(result) → UI 渲染
```

---

## 4. 核心模块职责

| 模块 | 文件 | 职责 |
|------|------|------|
| **契约定义** | `contracts.py` | 两个接口 + 数据契约 + 异常类 |
| **默认适配器** | `builtin_adapter.py` | RGBInputAdapter / SFRNetOutputAdapter |
| **模型注册表** | `registry.py` | 懒加载 + 单例 + 设备决策 |
| **推理服务** | `inference_service.py` | 编排入口，对外唯一 API |
| **滑窗引擎** | `sliding_window.py` | 切分规划 + 余弦窗融合 |
| **后处理器** | `postprocess.py` | sigmoid + 二值化 + 形态学 |
| **导出服务** | `export_service.py` | 产物落盘 + JSON 元数据 |
| **配置管理** | `config.py` | 加载 + 合并 + 校验 |

---

## 5. 关键设计决策

### 5.1 为什么输出统一为 logits？

上游代码存在融合域不一致的 bug：
- `train.py`: 累加 **logits** 再取平均
- `predict.py`: 累加 **sigmoid 后的概率** 再取平均

数学上 σ(E[ℓ]) ≠ E[σ(ℓ)]，导致同一模型在验证与推理时的重叠区置信度系统性不一致。

**解决方案**: 本设计把"融合"统一定义在 **logits 域**，sigmoid **只在 PostProcessor 里施加一次**。

### 5.2 为什么用余弦窗融合？

上游使用均匀权重 `1` 做平均，在块边界会形成接缝。

**解决方案**: 使用 2D Hann 窗，中心权重高、边缘权重低，重叠区平滑过渡。

### 5.3 为什么模型层不做阈值判断？

阈值判断是业务逻辑，不同场景可能需要不同的阈值。将阈值判断放在逻辑层，使模型层保持纯粹，便于替换。

---

## 6. 目录结构

```
SFRNet/
├── app/                          # 软件主体
│   ├── model_layer/              # 模型层
│   │   ├── contracts.py          # 接口契约
│   │   ├── builtin_adapter.py    # 默认实现
│   │   ├── registry.py           # 注册表
│   │   └── detectors/            # 具体模型封装
│   │
│   ├── logic_layer/              # 逻辑层
│   │   ├── inference_service.py  # 推理服务
│   │   ├── sliding_window.py     # 滑窗引擎
│   │   ├── postprocess.py        # 后处理器
│   │   ├── export_service.py     # 导出服务
│   │   ├── config.py             # 配置管理
│   │   └── exceptions.py         # 异常定义
│   │
│   └── control_layer/            # 控制层 (待实现)
│       ├── main_window.py
│       ├── widgets/
│       └── viewmodels/
│
├── utils/                        # 上游代码 (冻结)
│   └── sfr/
│
├── configs/                      # 配置文件
├── assets/weights/               # 模型权重
├── tests/                        # 测试
└── docs/                         # 文档
```

---

## 7. 扩展新模型的最小工作量

根据设计，新增一个模型只需：

1. 在 `app/model_layer/detectors/` 下新建类，实现 `forward(tile) → logits`
2. 若输入输出格式不同，另写两个 Adapter
3. 在 `configs/models.yaml` 里加一段配置
4. 在 `registry.py` 注册一行三元组

**逻辑层、控制层、UI 代码一行不改。**

---

*详见 [SOFTWARE_DESIGN.md](design/SOFTWARE_DESIGN.md)*
