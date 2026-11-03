# 变更日志 (Changelog)

本文件记录项目的所有变更。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)。

---

## [Unreleased] - 2026-11-03

### Added
- 创建 `docs/` 文档目录结构
- 添加 `ARCHITECTURE.md` 架构概览文档
- 添加 `CHANGELOG.md` 变更日志
- 创建 `app/` 软件主体框架
  - 模型层: 接口契约 + 默认适配器 + 注册表
  - 逻辑层: 推理服务 + 滑窗引擎 + 后处理器 + 导出服务
  - 控制层: 占位 (待 M3 阶段实现)
- 创建 `configs/` 配置文件目录
- 移动权重文件到 `assets/weights/`
- 添加 `requirements.txt` 和 `pyproject.toml`

### Changed
- 移动 `SOFTWARE_DESIGN.md` → `docs/design/`
- 移动 `TECHNICAL_DOC.md` → `docs/design/`
- 移动 `SFRNet大创创意方向方案.md` → `docs/`
- 移动 `SFR_best.pth` → `assets/weights/`

### Fixed
- (待记录)

### Known Issues
- 上游 `train.py:24` 的 `autocast(device_type='cuda:1')` 硬编码问题
- 上游 `predict.py:42-43` 小图推理崩溃问题
- 上游 FRM Router 100% 坍缩到 branch_1

---

## [1.0.0] - 2026-11-03

### 初始版本
- 基于 SFRNet 论文代码建立软件框架
- 完成 M0-M2 里程碑 (骨架 + 推理链 + 导出)
- 待完成: M3 (UI) / M4 (打包) / M5 (测试收尾)

---

*记录规范:*
- *每个版本按 Added / Changed / Deprecated / Removed / Fixed / Security 分类*
- *日期格式: YYYY-MM-DD*
- *重大变更需标注版本号*
