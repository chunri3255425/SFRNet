# MoiréGuard - 摩尔纹视觉识别软件

基于 SFRNet 的桌面端摩尔纹检测工具。

> 📖 **完整文档**: 查看 [docs/](docs/README.md) 文档中心

## 功能特性

- **单图推理**: 打开一张图，秒级返回掩膜
- **批量推理**: 选一个目录，逐张处理并列出结果
- **结果导出**: 二值掩膜 / 叠加图 / 概率热力图 / JSON 元数据
- **参数热调**: 阈值、滑窗参数实时调整，无需重启

## 快速开始

### 安装依赖

```bash
pip install -r requirements.txt
```

### 运行推理

```python
from app.logic_layer import InferenceService, InferenceRequest
from app.model_layer import ModelRegistry
from app.logic_layer.config import ConfigManager

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
├── app/                    # 软件主体
│   ├── model_layer/        # 模型层 (接口 + 适配器)
│   ├── logic_layer/        # 逻辑层 (推理编排)
│   └── control_layer/      # 控制层 (UI)
├── utils/                  # 上游算法代码 (冻结)
├── configs/                # 配置文件
├── assets/weights/         # 模型权重
├── tests/                  # 测试
└── docs/                   # 📖 文档中心
    ├── README.md           # 文档索引
    ├── ARCHITECTURE.md     # 架构概览
    └── design/             # 设计文档
        ├── SOFTWARE_DESIGN.md  # 软件设计文档 (SDD)
        └── TECHNICAL_DOC.md    # 技术文档
```

## 文档导航

| 文档 | 说明 |
|------|------|
| [docs/README.md](docs/README.md) | 📚 文档中心索引 |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 🏗️ 架构概览 |
| [docs/design/SOFTWARE_DESIGN.md](docs/design/SOFTWARE_DESIGN.md) | 📋 软件设计文档 (核心契约) |
| [docs/design/TECHNICAL_DOC.md](docs/design/TECHNICAL_DOC.md) | 🔧 技术文档 (源码核验) |
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
