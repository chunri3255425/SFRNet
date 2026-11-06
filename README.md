# MoiréGuard - 摩尔纹视觉识别软件

基于 SFRNet 的超高清摩尔纹检测软件。交付形态为**带摄像头的开发板上无人值守常驻运行的服务**（上电自启、出错自恢复、断电安全），同时支持 Linux 与鸿蒙双系统。

> 🧭 **架构基准**：以 [docs/design/SOFTWARE_DESIGN.md](docs/design/SOFTWARE_DESIGN.md) **v2.3** 为准。目录结构只在该文档 §5 维护一份，本文与 `docs/ARCHITECTURE.md` 均改为指针。

> 📖 **完整文档**: 查看 [docs/](docs/README.md) 文档中心

## 目标硬件（已按实购订单核实）

| 项 | 事实 |
|---|---|
| 开发板 | 触觉智能 Purple Pi OH2，**RK3576**（4×A72 + 4×A53），4GB + 64GB |
| NPU | 6 TOPS INT8，推理后端锁定 RKNN；开发机侧用 PyTorch eager |
| 摄像头 | **Sony IMX415**，MIPI CSI-2，4K@30fps，经 RKISP + V4L2 |

> ⚠️ 早期文档误记为 RK3588S，已全局更正。涉及 CPU 代际与 NPU 微架构的性能推算需实板复核。

## 功能特性

- **板端常驻**：七步启动序列 + 六状态机（READY/DEGRADED/FATAL…），逐帧判定、出错重试、优雅退出
- **单帧检测**：一张图返回摩尔纹掩膜与布尔判定
- **结果导出**：二值掩膜 / 叠加图 / 概率热力图 / JSON 元数据
- **网络出口**：HTTP `/detect` + SSE，token 鉴权，掩膜按请求头 opt-in（默认关闭省带宽）
- **画面回传**：WiFi 旁路诊断面（TCP+JPEG，非阻塞 ≤5ms，默认关闭）
- **配置热更新**：阈值与滑窗参数带校验热更新，无需重启进程

## 快速开始

### 安装依赖

```bash
pip install -r requirements.txt          # 开发机；板端镜像不装 PyTorch，只装 rknn-toolkit-lite2
```

### 常用命令（以下每条都在开发机实测跑通）

```bash
python -m app.main --detect samples/moire_photo.jpg      # 单图判定后退出
python -m app.main                                       # 板端形态：bootstrap → 常驻循环
python -m app.main --serve-net --port 8340 --token xxx   # 网络出口常驻
python -m app.main --selftest --port 8399                # 不加载权重，仅验网络装配
python run_sim.py --source 0 --backend torch --show      # 开发机模拟板端（摄像头/视频/图片）
python serve.py --detect a.jpg                           # 兼容壳，等价于 python -m app.main
```

`--backend {auto,rknn,onnx,torch}` / `--model` / `--camera` / `--threshold` / `--coverage-threshold` / `--tile-size` / `--relay` 均可覆盖配置；`--model` 在首次加载**之前**生效。

### 库用法

```python
from app.logic_layer.config import ConfigManager
from app.logic_layer.inference_service import InferenceRequest, InferenceService
from app.model_layer import ModelRegistry

service = InferenceService(ModelRegistry(), ConfigManager())
result = service.run(InferenceRequest(source="path/to/image.jpg"))

print(f"覆盖率: {result.coverage:.2%}  存在摩尔纹: {result.has_moire}")
```

### 测试

```bash
python tests/smoke_board_pipeline.py    # 13 项：假适配器冒烟（不需要权重/torch）
python tests/test_real_weights.py       # 13 项：真实权重 + HTTP 出口回归（无 torch 或无样张则跳过）
```

## 项目结构

顶层一览，**完整目录树见 [SDD §5](docs/design/SOFTWARE_DESIGN.md)**：

```
SFRNet/
├── app/            # 软件主体：adaptation/ + model_layer/ + logic_layer/ + main.py（唯一组合根）
├── upstream/       # 上游算法代码（冻结，仅供 build_SFR 模型定义）
├── build/          # 镜像构建：pth→ONNX→RKNN、分层打包、systemd 单元
├── configs/        # default.yaml / models.yaml
├── assets/weights/ # SFR_best.pth（不入库）
├── tests/          # 冒烟 + 真实权重回归
└── docs/           # 📖 文档中心
```

## 文档导航

| 文档 | 说明 |
|------|------|
| [docs/README.md](docs/README.md) | 📚 文档中心索引 |
| [docs/design/SOFTWARE_DESIGN.md](docs/design/SOFTWARE_DESIGN.md) | 📋 软件设计文档 (SDD v2.3，架构基准 + 唯一目录树) |
| [docs/design/CONTROL_LOGIC.md](docs/design/CONTROL_LOGIC.md) | 🎛️ 控制逻辑可执行规格 |
| [docs/design/TECHNICAL_DOC.md](docs/design/TECHNICAL_DOC.md) | 🔧 技术文档 (源码核验) |
| [build/README.md](build/README.md) | 🏗️ 模型转换与镜像构建流程 |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | ⚠️ 旧版桌面端概览 (已被 v2.3 取代) |
| [docs/SFRNet大创创意方向方案.md](docs/SFRNet大创创意方向方案.md) | 💡 应用落地方案 |

## 技术规格

| 指标 | 数值 | 来源 |
|------|------|------|
| 参数量 | 7.80M | 结构统计 |
| 最大下采样 | H/8 | 结构统计 |
| 显存占用 | 0.92 GB (4K) | 开发机实测 |
| 单块算力 | 178.6 G-MAC ≈ 357 GFLOPs（512×512） | forward hook 实测 |
| 单块延迟 | 163 ms（RTX 5060 Ti，fp32，eager） | 开发机实测 |
| 4K 全图延迟 | 20.1 s / 112 块 | 开发机实测 |
| 板端 4K 预估 | 13–22 s / 帧 | 按 6 TOPS 与 30–50% 利用率推算，**待 M1 实测** |
| Precision / Recall | 0.6286 / 0.9942 | 上游论文口径（UHDMD 测试集） |

## 已知问题（未解，勿据此交付）

- **阈值恒判真**：默认 `threshold=0.5 / coverage_threshold=0.01` 下，纯黑/纯白/纯灰这类零内容图 coverage = 1.0000，即系统对任何输入都判"有摩尔纹"。权重确实生效（随机初始化 mean 0.438 vs 加载后 0.903），但输出存在系统性高偏置。SDD Q5/Q9 未解前，判定结果只能作相对比较，不可作交付判据。
- **板端非实时**：4K 一帧量级 13–22 s，需裁剪 `d3` / 降输入分辨率 / 加大 stride / RGA 卸载，均需实测验证。
- 鸿蒙侧仅 `camera/ohos.py` 占位，`device_io/ohos_io.py` 与 `build/image_ohos/` 未落地。

## 许可证

MIT License
