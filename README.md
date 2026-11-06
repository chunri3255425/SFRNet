# MoiréGuard - 摩尔纹视觉识别软件

基于 SFRNet 的超高清摩尔纹检测软件。交付形态为**带摄像头的开发板上无人值守常驻运行的服务**（上电自启、出错自恢复、断电安全），同时支持 Linux 与鸿蒙双系统。

> 🧭 **架构基准**：以 [docs/design/SOFTWARE_DESIGN.md](docs/design/SOFTWARE_DESIGN.md) v2.2 为准；本文与 `docs/ARCHITECTURE.md` 中的“桌面端/UI”描述已过期。

> 📖 **完整文档**: 查看 [docs/](docs/README.md) 文档中心

## 功能特性

- **单帧检测**: 一张图返回摩尔纹掩膜与布尔判定
- **批量处理**: 逐张处理目录并输出结果清单
- **结果导出**: 二值掩膜 / 叠加图 / 概率热力图 / JSON 元数据
- **配置热更新**: 阈值与滑窗参数带校验热更新，无需重启进程

## 快速开始

### 安装依赖

```bash
pip install -r requirements.txt
```

### 运行推理

```python
from moireguard.logic_layer import InferenceService, InferenceRequest
from moireguard.model_layer import ModelRegistry
from moireguard.logic_layer.config import ConfigManager

# 初始化
config = ConfigManager()
registry = ModelRegistry()
service = InferenceService(registry, config)

# 执行推理
request = InferenceRequest(source="path/to/image.jpg")
result = service.run(request)

print(f"覆盖率: {result.coverage:.2%}")
print(f"存在摩尔纹: {result.has_moire}")
```

## 项目结构

```
SFRNet/
├── moireguard/             # 软件主体
│   ├── model_layer/        # 模型层 (接口契约 + 适配器 + 注册表)
│   └── logic_layer/        # 逻辑层 (推理编排 / 滑窗 / 后处理 / 导出 / 配置)
│       # 适配层 adaptation/ 与状态机 engine/supervisor 尚未落地，见 SOFTWARE_DESIGN v2.2 §5 目标目录树
├── upstream/               # 上游算法代码 (冻结，仅供 build_SFR 模型定义)
├── configs/                # 配置文件
├── assets/weights/         # 模型权重 (不入库)
├── tests/                  # 测试
└── docs/                   # 📖 文档中心
    ├── README.md           # 文档索引
    ├── ARCHITECTURE.md     # 架构概览 (已被 v2.2 取代)
    └── design/             # 设计文档
        ├── SOFTWARE_DESIGN.md  # 软件设计文档 (SDD, 架构基准)
        ├── CONTROL_LOGIC.md    # 控制逻辑可执行规格 (13 条判定规则)
        └── TECHNICAL_DOC.md    # 技术文档
```

## 文档导航

| 文档 | 说明 |
|------|------|
| [docs/README.md](docs/README.md) | 📚 文档中心索引 |
| [docs/design/SOFTWARE_DESIGN.md](docs/design/SOFTWARE_DESIGN.md) | 📋 软件设计文档 (架构基准，当前有效) |
| [docs/design/CONTROL_LOGIC.md](docs/design/CONTROL_LOGIC.md) | 🎛️ 控制逻辑可执行规格 |
| [docs/design/TECHNICAL_DOC.md](docs/design/TECHNICAL_DOC.md) | 🔧 技术文档 (源码核验) |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | ⚠️ 旧版桌面端概览 (已被 v2.2 取代) |
| [docs/SFRNet大创创意方向方案.md](docs/SFRNet大创创意方向方案.md) | 💡 应用落地方案 |

## 技术规格

| 指标 | 数值 |
|------|------|
| 参数量 | 7.80M |
| 最大下采样 | H/8 |
| 显存占用 | 0.92 GB (4K) |
| Precision | 0.6286 |
| Recall | 0.9942 |

## 许可证

MIT License
