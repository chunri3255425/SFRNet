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
│   ├── SOFTWARE_DESIGN.md    # 软件设计文档 (SDD) - 核心契约
│   └── TECHNICAL_DOC.md      # 技术文档 (含源码核验与实测)
│
├── api/                      # API 文档 (待补充)
│
├── guides/                   # 使用指南 (待补充)
│   ├── INSTALL.md            # 安装指南
│   ├── QUICKSTART.md         # 快速开始
│   └── DEPLOYMENT.md         # 部署指南
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
| [TECHNICAL_DOC.md](design/TECHNICAL_DOC.md)     | **技术文档**含源码行级核验、实机实测数据、缺陷清单                 | 算法/开发 |

### 应用方案

| 文档                                                  | 说明                                         |
| ----------------------------------------------------- | -------------------------------------------- |
| [SFRNet大创创意方向方案.md](SFRNet大创创意方向方案.md) | 七个应用方向的详细分析、可行性评估、答辩话术 |

---

## 🏗️ 快速导航

### 我想了解...

**软件整体架构**
→ 阅读 [ARCHITECTURE.md](ARCHITECTURE.md)

**模型层接口设计**
→ 阅读 [SOFTWARE_DESIGN.md §5](design/SOFTWARE_DESIGN.md) 两个预留接口

**推理流程细节**
→ 阅读 [SOFTWARE_DESIGN.md §6](design/SOFTWARE_DESIGN.md) 逻辑层设计

**上游代码问题**
→ 阅读 [TECHNICAL_DOC.md](design/TECHNICAL_DOC.md) 缺陷清单

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
- **论文**: SFRNet--Ultra-high-definition-moiré-pattern-_2027_Expert-Systems-with-Applic.pdf

---

*最后更新: 2026-11-03*
