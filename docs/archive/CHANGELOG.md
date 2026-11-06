# 变更日志 (Changelog)

本文件记录项目的所有变更。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)。

---

## [Unreleased] - 2026-11-06（合并 origin/main 的 `app/` 体系）

本地 ahead 1 / 远端 behind 4，双方各自实现了同一批抽象。合并策略：**以远端 `app/` 骨架为主干**（它符合 SDD §5 且有状态机与冒烟测试），**迁入本地的依赖倒置纪律与网络出口**，并修掉实测出的远端缺陷。合并全程 `--no-commit`，安全点留在分支 `backup/pre-merge`。

### Added
- 真实权重回归 `tests/test_real_weights.py`（13 项）：`TorchRuntime` 契约、`DetectionEngine` + 真实权重端到端、`Decider`/`PostProcessor` 边界同判、HTTP `/detect` + 401 + `X-With-Mask` opt-in。动机是冒烟测试 13 项全用 `FakeRuntime`，"后端接口与真模型对不上"这类缺陷一条也抓不到——合并时正好撞上
- `app/adaptation/base.py` 迁入 DTO（`DetectionResultDTO` / `DetectionEvent`）与反向注入协议（`IImageService` / `IDetectionEngine` / `IResultSink`）、`NetworkConfig` / `NetServerOptions`
- `configs/default.yaml` 新增 `runtime` 节（板端 rknn + `runtime.host` 开发机覆盖层），`camera` / `relay` 节入 `logic_layer/config.py` 类型化（`CameraConfig` / `RuntimeConfig` / `RelayConfig`）
- `app/main.py` 重写为**唯一组合根**（336 行）：`build_config` 四段合并（dataclass 默认 ← `configs/default.yaml` ← 平台持久配置 ← 命令行），四种形态 = 板端常驻循环 / `--detect` / `--serve-net` / `--selftest`
- `serve.py` 保留为 22 行兼容壳，参数原样转发，不再承接逻辑

### Changed
- **包名回到 `app/`**（与 11-05 那条 `app/ → moireguard/` 形成往返）：远端按 SDD §5 用 `app/` 落地，两套三层并存必然继续漂移，`moireguard/` 源码已全部 `git rm`
- 默认值真源从三处收敛到一处：`bootstrapper.DEFAULTS` 改为由 `config.AppConfig` 的 dataclass 导出（此前 `config.py` / `bootstrapper.py` / `configs/default.yaml` 各写一份且模型路径不同，开发机永远拿错路径）
- 适配层不再 import 逻辑层（`torch_runtime.py` 原借用 `logic_layer.exceptions`，违反 DIP 红线）
- 平台判定改为按"是否装了产品镜像"识别（`/etc/moireguard`、`/usr/share/moireguard`、`rknnlite` 可导入），开发机可用 `MOIREGUARD_PLATFORM` 强制；此前按 `sys.platform == "linux"` 判定，WSL 被误当板端去读 `model.rknn`
- `factory.make_runtime("auto")` 去掉 `rknn→onnx→torch` 软回退：选定即定死，缺失显式报 `E-BACKEND-UNAVAILABLE`（板端静默跑 CPU torch 会让"NPU 已就位"的结论失真）
- 目录树真源唯一化：SDD §5 为唯一一份，`docs/ARCHITECTURE.md` §6 与 `README.md` 改为指针
- `docs/api/README.md` 网络出口 4 端点、鉴权与掩膜 opt-in 行为入档（不再是纯占位）

### Fixed（远端缺陷，均有实测复现）
- `TorchRuntime` 把 `registry.create()` 返回的 `DetectorCore` 当 `nn.Module` 调用 → `'SFRNetDetector' object is not callable`，真实权重路径 100% 崩溃（改为 `core.forward(t)`）
- `load(model_path)` 丢弃入参不用 → 配置里的权重路径成了死字段（现真传递，并做绝对/工作目录/仓库根三级定位）
- `bootstrapper` 把任何加载异常一律标 `E-WEIGHT-NOT-FOUND` → 一次 `TypeError` 被报成"权重找不到"，直接带偏排障；现按 `FileNotFoundError` / 已带 `E-*` / 其余归 `E-RUNTIME-FAIL: 类型名` 分类
- `app/main.py` 的 `--model` 在 `bootstrap()` **之后**赋值 → 对首次加载是死标志；现经 `engine.bootstrap(overrides)` 在加载前生效
- `detect_once` 只用构造期的 `Decider()` → `req.threshold` / `req.coverage_threshold` 被静默忽略；现按请求重建
- `app/adaptation/__init__.py` 只 re-export `factory` 模块 → `ada.detect_platform` AttributeError，说明该入口从未跑起来过；现补齐工厂函数与抽象类导出
- 开发机日志全静默：`HostDeviceIO` 无 handler，`logging.lastResort` 只放行 WARNING 及以上，"跑完一声不响"比报错更难查；现挂 stdout handler，并置 `propagate = False`（修 `run_sim.py` 每条日志打两遍）
- `factory` 引用的模块名不存在（`camera/v4l2.py` 案例名、`FileCamera` 类名）→ `ModuleNotFoundError` 而非可读错误
- 存活探针 `/healthz` 被鉴权闸门拦住：配 `network.token` 后探针拿到 401（实测），等于让监控系统把健康服务判成"服务已死"。现 `/healthz` 免鉴权（不触发推理、不回诊断数据），`/status`、`/stream`、`/detect` 一律仍过 token。`--selftest` 因不配 token 照不到这条，已在 `tests/test_real_weights.py` 补两条断言（免鉴权 200 / 诊断面仍 401）
- `FileCamera` 的 `frame_id` 前缀 `sim-` 出现在产品形态日志里（`python -m app.main` 常驻也打 `frame=sim-000002`），与附录 B 的逐帧日志契约不符；改为按来源类型命名 `file-`（与 `fake-`、V4L2 的毫秒时间戳同口径）
- 文档事实：`build/README.md` 板型 RK3588S → **RK3576**（远端新增文件里的漏改，全库 RK3588 已清零）；`docs/README.md` 章节指针纠错（两个预留接口在 §4.2 非 §5，逻辑层在 §4.3 非 §6）与不存在的 `guides/*.md` 清单；SDD §5 树按现实重写并列出 5 条"文档声称 vs 实际状态"缺口（`supervisor.py`、`build/calib/`、`image_ohos/`、`ohos_io.py`、网络出口未立项）；`upstream/` 路径引用（`utils/` 已改名）；两处指向不存在的 `Supervisor` 的代码注释

### Removed
- 根目录 `SOFTWARE_DESIGN.md`：11-05 已移动到 `docs/design/`，但远端 4 个提交里的 `eccf5dc` 又把 v2.0→v2.3 的升级写回了这份**老副本**，导致两份 SDD 并存且根副本仍是 RK3588S 与已证伪的 1.5 GFLOPs 推算。逐块 diff 确认根副本无任何独有内容（7 处差异全是本地更正的旧态），故删除、保留 `docs/design/` 一份
- `.merge_scratch/`（合并期临时留存的 4 个旧文件副本）

### 验证（开发机 RTX 5060 Ti / py3.12 / torch 2.13+cu130）
- `tests/smoke_board_pipeline.py` **13/13**；`tests/test_real_weights.py` **13/13**
- 数值跨栈一致，三条独立路径互证同一张摩尔纹样张：板端常驻循环 `coverage=0.997286 / tiles=32`、`run_sim.py` 两帧 `0.997286`、`--detect` `0.997296`（与合并前 `serve.py --detect` 完全一致）；IDE 截图 `0.981504`
- `--selftest` PASS（`/status` 200、`/healthz` 200）；`--serve-net` 常驻进程实测：无 token 的 `/healthz` 200、`/status` 401、`/detect` 401，带 token 的 `/detect` 返 `coverage=0.997296` + `mask_png_b64`（与 `--detect` 同值）
- README 里列的 6 条命令本轮全部实跑一遍（`--detect` / 常驻循环 / `--serve-net` / `--selftest` / `run_sim.py` / `serve.py` 壳），不写未验证的命令

---

## [Unreleased] - 2026-11-05

### Added
- 创建 `docs/` 文档目录结构
- 添加 `ARCHITECTURE.md` 架构概览文档
- 添加 `CHANGELOG.md` 变更日志
- 创建 `moireguard/` 软件主体框架
  - 模型层: 接口契约 + 默认适配器 + 注册表
  - 逻辑层: 推理服务 + 滑窗引擎 + 后处理器 + 导出服务
  - 控制层: 占位 (待 M3 阶段实现)
- 创建 `configs/` 配置文件目录
- 移动权重文件到 `assets/weights/`
- 添加 `requirements.txt` 和 `pyproject.toml`
- 建立适配层 `moireguard/adaptation/`（v2.2 §4.1 接口先行）
  - `base.py`: `Frame` / `DetectionResultDTO` / `DetectionEvent` + `ICameraAdapter` / `IRuntimeAdapter` / `IDeviceIO` 抽象，以及反向注入的 `IImageService` / `IDetectionEngine` / `IResultSink` 协议（Protocol 而非 import，保证适配层不依赖逻辑层）
  - `factory.py`: 平台探测与装配，全项目唯一允许平台分支处；未实现的后端（onnxruntime / rknn）显式 `raise NotImplementedError`，不静默降级
  - 首组实现：`runtime/torch_runtime.py`（含 warmup 治理）、`camera/fake.py`（可注入故障）、`device_io/linux_io.py`
- 新增网络出口 `adaptation/network/http_server.py`（纯标准库 `ThreadingHTTPServer` + SSE）：`GET /healthz|/status|/stream`、`POST /detect`，Bearer 鉴权与体积闸门，`publish` 非阻塞丢慢客户端
- 新增组合根进程入口 `serve.py`（`--serve-net` / `--detect` / `--selftest`）；`configs/default.yaml` 新增 `network` 节（`enabled: false` 默认关闭，无头形态下网络为显式 opt-in 旁路）

### Changed
- 移动 `SOFTWARE_DESIGN.md` → `docs/design/`
- 移动 `TECHNICAL_DOC.md` → `docs/design/`
- 移动 `SFRNet大创创意方向方案.md` → `docs/`
- 移动 `SFR_best.pth` → `assets/weights/`
- 重命名 `app/` → `moireguard/` (与软件代号对齐)
- 重命名 `utils/` → `upstream/` (上游代码隔离，语义更清晰)
- 更新所有 Python import 路径引用
- 对齐 SOFTWARE_DESIGN v2.2 的无头镜像形态：`README.md` 定位/特性/目录树重写，`docs/ARCHITECTURE.md` 加“已被 v2.2 取代”差异表（不重写正文）
- `moireguard/logic_layer/__init__.py` 红线从“不做 UI 回调 / 不持有 Qt 对象”改为“不 import 任何平台 SDK / 不碰 state_dict / 不做平台判断”

### Removed
- 删除 `moireguard/control_layer/`（`__init__.py` + `widgets/` + `viewmodels/`，共 32 行纯占位，代码中零引用）—— v2.2 目标架构为适配层/模型层/逻辑层，无 UI 层
- 删除 `moireguard/resources/`（仅 `.gitkeep`，UI 时代资源占位，全库无引用）

### Fixed
- `registry.py:_register_builtins` 不再静默 `except ImportError: pass`（原注释“SFRNetDetector 尚未实现”与代码不符；真实危害是依赖缺失时退化成“注册表为空”，把环境问题伪装成配置问题）
- `predict.py:14` 权重路径 `./checkpoints/ins/SFR_best.pth` → `./assets/weights/SFR_best.pth`（M0 权重迁移后的遗留断链）
- `logic_layer/__init__.py` 改为 PEP 562 惰性导出：原先急切 re-export `InferenceService` 使“只导入配置/异常类”也拖起 torch，无 torch 环境下无法启动适配层；公开 API 不变
- `adaptation/network/http_server.py` 两处缺陷：`/status` 处理函数闭包误引用 Handler 实例的 `self`（改为捕获局部 `started_at` 与共享的 `shutdown_flag`，二者已在 `HttpNetServer.__init__` 预先初始化）、无效占位串 `"UN装配"` → `"UNASSEMBLED"`
- **体积闸门失效**（端到端实测才暴露）：`_read_body` 回 413 时不消费请求体，keep-alive 下残留字节被当成下一个请求；urllib 默认发 `Connection: close`，服务端先响应后关连接时仍在写的客户端看到的是 `Connection reset` 而非 413——实测 12 次超限上传只有 2 次拿到状态码。新增 `_reject(code, payload, drain=True)` 先按 Content-Length 分块排空（受 `_DRAIN_HARD_CAP` 512MB 约束）再回响应；顺序不能倒，先发后排仍剩一半失败。未鉴权的 401 传 `drain=False`，不为身份不明的发送方白读大块数据。修复后 urllib 与 http.client 各 12 次均为 12/12 拿到 413
- `docs/design/SOFTWARE_DESIGN.md` 目标硬件由误记的 **RK3588S** 更正为实板 **RK3576**（§1.1 硬件表、软回退行、§2 选型表与性能预估、Q1/Q6），并同步修正派生事实：CPU 改 4×A72@2.2GHz + 4×A53、软回退核改为 A53、NPU “三核”不作断言（公开资料口径不一致）、eMMC 5.1、ISP V3.9，新增 RGA 可卸载 4K 预处理的事实依据；保留 6 TOPS 推算但标注 CPU 代际下降需下调软回退与融合/后处理预算
- 接实购订单页进一步更正 §1.1：新增“视频输入（多路 AHD）/ 视频解码（4K@120）/ 网络（双千兆）”三行；内存行不再断言 LPDDR4X（商品主图标 LPDDR5，与系列规格页口径冲突，改为标注冲突并要上电用 `dmesg \| grep -i ddrc` 实测）；摄像头行记入已购 IMX415 模组；Q2 由开放项改为“基本已定”；性能预估段补“IMX415 本身是 4K 级，112 块是常态负载而非最坏情况”
- `docs/SFRNet大创创意方向方案.md` 4 处 RK3588 按实购板型与实价更正（L85 选型、L147/394 离线版与实拍描述、L435 成本表：OH2 4+64G 整板 ¥999、核心板+IMX415 ¥200，已各购 2 件）

### Changed
- 开发机（RTX 5060 Ti，torch 2.13+cu130，py3.12）首次跑通真实推理链，`SOFTWARE_DESIGN.md` 性能预估段按实测重写：**单块 512×512 实测 178.6 G-MAC ≈ 357 GFLOPs，本文早期假设的 1.5 GFLOPs 低估约 238 倍**；单块 163 ms（batch=8 仅 138 ms/块→算力/带宽受限；fp16 119 ms），4K 全图 112 块端到端 20.1 s（融合+两次 PNG 编码仅占 6%）；对 RK3576 推论为 120–200 ms/块、**4K 一帧 13–22 s**，“5–20 ms/块、0.6–2.2 s/帧”作废，当前架构板上做不到实时
- Q5 阈值项补充实测证据；新增 **Q9：SFR_best.pth 是否已退化**（优先级等同 Q7）；收敛方向按实测热点重排，结构化裁剪砍 `d3` 升为首位（最贵 6 层里 4 条在 `d3.*.safm` 与全分辨率 `c1.2`）

### Verified
- 端到端真实链路跑通：`serve.py --detect` 与 HTTP `POST /detect`（真权重）均返回 200，返 `coverage=0.9960 / max_prob=0.9037`，`X-With-Mask` 带回 4.6 KB 掩膜 PNG；`/status` 报 `engine_state=READY`；无/错 token 均 401；坏字节干净地返 `E-DETECT-FAILED`
- 权重加载确认生效：`strict=True` 未报错，且随机初始化 mean 0.438 vs 加载后 0.903（不同）；`registry.py:102` 已调 `eval()`，预处理与训练侧一致（均为 `ToTensor` + RGB，`builtin_adapter.py:114` 除 255）

### Known Issues
- 适配层已抽象但未完整装配：`logic_layer/{engine,bootstrapper,decider,supervisor}.py` 仍缺失，`serve.py` 的 `ServiceFacade` 只是协议适配器，缺口 D-07 保持开放
- `build/` 目录仍不存在，`pth → ONNX → .rknn` 转换链（Q7）未跑通；RKNN-Toolkit2 只发 x86_64 wheel 且 `cpXXX` 严格绑 Python 版本，需在开发机（建议 Docker Ubuntu22.04 + py3.10）完成，不在板上编译
- 网络层尚无单元用例（`tests/unit/test_net_http.py` 未写），`--selftest` 仅验端点/鉴权/生命周期
- 网络出口尚未在 `SOFTWARE_DESIGN.md` 立项：§1/§4.4 对外的承诺仍只有一个布尔量（`IDeviceIO.indicate`），HTTP/SSE 属代码先行能力，是否升为正式输出通道待与 Q4 一并定夺
- LPDDR5 / LPDDR4X 口径冲突未解（商品主图 vs 系列规格页），需上电实测后回填 §1.1；双千兆网口与 AHD 输入均未进入任何接口设计
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
