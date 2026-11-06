# MoiréGuard 文档中心

本目录包含软件设计、API 参考、使用指南等所有文档。

---

## 📁 目录结构

```
docs/
├── README.md                 # 本文档 (索引)
├── ARCHITECTURE.md           # 架构概览
├── SFRNet大创创意方向方案.md   # 大创应用落地方案
│
├── design/                   # 设计文档
│   ├── SOFTWARE_DESIGN.md    # 软件设计文档 (SDD v2.3) - 核心契约 + 唯一目录树
│   ├── CONTROL_LOGIC.md      # 控制逻辑可执行规格 (13 条判定规则)
│   └── TECHNICAL_DOC.md      # 技术文档 (含源码核验与实测)
│
├── api/README.md             # API 索引 (正文待补充)
│
├── guides/                   # 使用指南
│   ├── README.md             #   占位 (INSTALL/QUICKSTART/DEPLOYMENT 尚未撰写)
│   └── BOARD_BRINGUP.md      #   板端上电取证与烧录手册（RK3576 实测数据与出厂 SN/MAC 的唯一留存）
│
├── literature/               # 论文原文 (1 份 PDF，已入库)
│
├── vendor/                   # 厂商手册与规格书 (PDF 不入库，清单见 vendor/README.md)
│   ├── README.md             #   入库：哪份手册讲什么 + 重新下载出处
│   ├── purplepi-oh2/         #   整板 5 份：Linux 使用/开发手册、OpenHarmony 使用手册、
│   │                         #   开发板规格书、上手指南
│   └── som7609/              #   核心板 3 份：S1/S2 规格书、S1 硬件设计指南
│
└── archive/                  # 归档文档
    └── CHANGELOG.md          # 变更日志
```

---

## 📖 核心文档

### 设计文档

| 文档                                           | 说明                                                                     | 读者      |
| ---------------------------------------------- | ------------------------------------------------------------------------ | --------- |
| [SOFTWARE_DESIGN.md](design/SOFTWARE_DESIGN.md) | **软件设计文档 (SDD)**定义三层架构、两个预留接口、数据契约、错误码 | 全体开发  |
| [CONTROL_LOGIC.md](design/CONTROL_LOGIC.md)     | **控制逻辑可执行规格**13 条判定规则，逐条标 `[实测]/[代码]/[设计]` | 全体开发  |
| [TECHNICAL_DOC.md](design/TECHNICAL_DOC.md)     | **技术文档**含源码行级核验、实机实测数据、缺陷清单                 | 算法/开发 |

### 应用方案

| 文档                                                  | 说明                                         |
| ----------------------------------------------------- | -------------------------------------------- |
| [SFRNet大创创意方向方案.md](SFRNet大创创意方向方案.md) | 七个应用方向的详细分析、可行性评估、答辩话术 |

---

## 🏗️ 快速导航

### 我想了解...

**软件整体架构**
→ 阅读 [ARCHITECTURE.md](ARCHITECTURE.md)（⚠️ 桌面端旧版，已被 v2.3 取代，看头部差异表）

**镜像构建与模型转换**
→ 阅读 [../build/README.md](../build/README.md)

**模型层接口设计**
→ 阅读 [SOFTWARE_DESIGN.md §4.2](design/SOFTWARE_DESIGN.md) 两个预留接口

**推理流程细节**
→ 阅读 [SOFTWARE_DESIGN.md §4.3](design/SOFTWARE_DESIGN.md) 逻辑层设计

**上游代码问题**
→ 阅读 [TECHNICAL_DOC.md](design/TECHNICAL_DOC.md) 缺陷清单

**硬件手册与核心板规格**
→ 先读 [vendor/README.md](vendor/README.md) 的清单；PDF 本身不入库，需从厂商资料站下载。
   安卓路线不在项目计划上，故不再收安卓手册，缘由见 [CHANGELOG](archive/CHANGELOG.md)

**应用落地方案**
→ 阅读 [SFRNet大创创意方向方案.md](SFRNet大创创意方向方案.md)

---

## 📝 文档维护

### 更新原则

1. **契约变更必须走流程**：接口定义变更需更新 `SOFTWARE_DESIGN.md` 中的契约编号
2. **实测数据需标注**：所有性能数据标注 `[实机]` 或 `[论文]`
3. **缺陷需记录**：发现的问题添加到 `archive/CHANGELOG.md`

### 文档格式

- 使用 Markdown 格式
- 代码块标注语言类型
- 表格对齐整齐
- 中文与英文/数字间加空格

---

## 🔗 相关资源

- **项目 README**: [../README.md](../README.md)
- **上游仓库**: https://github.com/chunri3255425/SFRNet.git
- **论文**: [SFRNet (ESWA 2027)](literature/SFRNet--Ultra-high-definition-moir--pattern-_2027_Expert-Systems-with-Applic.pdf) — 目录 [literature/](literature/)

---

*最后更新: 2026-11-06（合并远端 app/ 体系后统一校正章节指针与文件清单）*
