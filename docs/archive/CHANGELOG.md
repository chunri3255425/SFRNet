# 变更日志 (Changelog)

本文件记录项目的所有变更。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)。

---

## [Unreleased] - 2026-11-06（默认形态根本跑不出帧：适配器契约 + 错误恢复上限）

上一轮收尾时做全形态实跑，`python -m app.main`（不带任何参数，正是 README 承诺的板端形态）跑 12 s 得到 **8 299 254 字节日志、0 条 frame**。顺这条线挖出一串真缺陷，全部先取证再改，性能相关的取舍带数字。

### Fixed
- **`FakeCameraAdapter.grab()` 不收 `timeout_ms`**（Liskov 违背）：抽象基类与另外三个真实适配器都是 `grab(timeout_ms=3000)`，唯独假相机写成 `grab(self)`；引擎按 CONTROL_LOGIC 调用即 TypeError。开发机默认形态用的正是假相机 —— 裸跑一帧都出不来。
- **假相机的合成帧不是 HxWx3**：`np.where(stripe[:, :, None], 205, 50)` 里 `stripe` 只有 `(H,1)`，广播出来的“帧”是 `(H,1,1)`，到 `transpose(2,0,1)` 必炸（这条被上一条盖住，修完签名才露出来）。
- **启动自检信元数据不信像素**：STEP 6 只判 `data.size == 0`，于是 `(480,1,1)` 被报成「连通性自检通过: 640x480」。新增 `frame_shape_error()`（适配层），自检与每帧取帧后都按 `data.shape` 校，日志也改报真实像素尺寸。
- **未知异常既不退避也不升级（日志洪水的直接成因）**：`run_forever` 兜底分支先无条件打一行 ERROR，再交给 `_handle_error` 的 `else`（只置 DEGRADED）。CONTROL_LOGIC §6.1 早写明「未知异常：固定 2 s 退避、上限 3 次、达上限 FATAL、记完整堆栈」，代码没实现。补齐后同样的必炸路径从每秒数百行降到**恰好 3 条 ERROR**。
- **异常分类跑偏**：适配器抛的原生异常全被兜底成 unknown，C-10「重开摄像头」与 C-11「重载模型」永远走不到，重试上限也从 5/3 变成 3。`detect_once` 现在把 grab 的异常包成 `capture`、前向包成 `infer`、形状坏帧单独包成 `bad_frame`（重开摄像头和重载模型都救不了它）。
- **坏帧告警每帧一行**：`streak > 上限` 之后在全速循环里每帧刷一条 WARN。改为只在跨阈值那一次落日志。
- **`_fatal()` 里 `indicate()` 无兜底**：指示器抛异常会打断停机序列。两个真实实现都自行降级，但终端路径不该赌适配器守约。
- **FATAL 也返回退出码 0**：`run_forever` 跳出循环后总会走 `shutdown()`，状态名最后都是 SHUTDOWN，组合根分不出「正常收摊」与「恢复超限」。新增 `engine.fatal_reason`，`_run_loop` 据此返回 1。`moireguard.service` 是 `Restart=on-failure` 且 unit 注释明写「FATAL 由应用自身退出（退出码 1）」—— 在此之前，恢复超限的板子永远不会被拉回。
- `black_after` 的 docstring 谎称能验 C-12：全黑/全白判据代码里不存在（已 grep 确认逻辑层无一处）。改为写明它当前的真实作用；**是否补该判据留作待决**（它会改变交付布尔值，属决策面）。

### Changed
- `bootstrap()` 重跑时不再二次 `relay.start()`（新增 `_relay_started` 守卫）：未知异常恢复会重走启动序列，起第二个发送线程且旧连接无人回收。
- `run_forever` 的成功复位扩展到 `unknown`（与 capture/infer 同处清零）。

### 502 归因纠正（推翻上一轮写进本文件的结论）
上一轮 CHANGELOG 判定「同批 `urllib` 拿到的 502 未能复现，不予采信」。本轮该 502 在 `test_real_weights` 第 4 组复现，且机制查清：

```
no_proxy = *.local,172.31.*,172.26.*,172.17.*,10.*,192.168.*,127.*,localhost,<local>
urllib.request.proxy_bypass("127.0.0.1")  ->  False        # 实测
```

`urllib` 只把 `127.*` 当作 `*.xxx` 式后缀，**不匹配 IP 通配**，所以号称"已排除回环"的 `no_proxy` 对 `127.0.0.1` 无效，每个回环请求都先绕 `http://127.0.0.1:7897` 一跳。

> **本小节下面那句“502 与被测服务的鉴权逻辑无关”仍不完整，真正诱因见本节末尾
> 「401 可见性与 keep-alive 残体」：是我们自己的不排空 401 先把客户端弄成 broken
> pipe，代理只是把它翻译成了 502。代理确实在链路里（上面两条实测成立），但它不是根因。**

两条可复现证据说明 502 不可能出自我们的服务器：`grep "50[0-9]" app/adaptation/network/http_server.py`
只有 500 与 503，没有任何 502 出口；且 `server_version = "MoireGuard/0.1"`（同文件 118 行）是固定值，
代理返回的 502 带的是它自己的 `Server` 头。反向对照：空闲服务端、1 字节载荷，经代理连打 8 次全 401
—— 说明代理路径本身通。（当时据此说“那次 502 是代理侧偶发、诱因不查了”：方向对、体量错。
该实验用的是 1 字节 body，恰好落在套接字缓冲内，所以屏蔽了真诱因。）

- 处理：被测面（鉴权、状态码、SSE）断言全部改为 `ProxyHandler({})` 直连回环 —— `app/main.py` 的 `--selftest`、`test_real_weights` 第 4 组。`http.client`（第 5~7 组）本就不读环境变量代理，无需改。
- 判据强度不降：断言显式比 `e.code == 401`，代理污染只会让用例**失败**，不会被误读成通过。
- 元教训（写给后人，也写给改这条的我）：**"我试了一次没重现"不能否定"另一台机器上重现过"**。上一轮我只补测了"换端口返 200"就把 502 判死并写进正式记录，这是把一次抽样的运气当成结论。

### 401 可见性与 keep-alive 残体（把上面那条 502 追到底）
干净重跑真权重套件得 22/23，唯一失败就是「缺 token 被拒 — 502」——即上一轮被我判成
「未能复现、不予采信」的那一条。这回不靠抽样，直接做对照实验（各体量连打 6 次，全程
直连不经代理）：

| 未带 token 的请求体 | 修复前 | 修复后 |
|---|---|---|
| 空体 / 1 KB | 401 ×6 | 401 ×6 |
| **1 MB / 4 MB** | **URLError ×6（一个状态码都拿不到）** | **401 ×6** |
| 100 MB（超 `max_upload_mb`） | URLError | URLError（有意保留） |

机制不是代理，是 `_reject(401, ..., drain=False)` 自己的设计：`protocol_version = "HTTP/1.1"`
下先回响应不读体，体一旦大于套接字缓冲，客户端就在写入途中收到 FIN，拿到 broken pipe；
经代理时代理把这个上游中断翻译成 502——所以代理只是个报信人，不是制造者。

- 修：`_reject`/`_drain` 增加 `cap`，未鉴权路径排空上限压到 `max_upload_mb`（读取量不越过配置，
  但正常体量一定能看到 401）；超上限的声明体量仍不排空、直接断连。
- 修：新增 `handle_expect_100()`——鉴权不过就在传体前回 401、**不读一个字节**。这才是
  “不给身份不明者读数据”与“客户端要看得到状态码”的兼得点（不是 drain=False 那个旧取舍）。
- 修（同一类静默错失，顺着 grep 找到）：`/detect` 的两个 503 分支与 `do_GET` 的 401/404 也是
  不排空就回响应，全改为 `_reject`。实证：401 后在同一条 keep-alive 连接上发第二个请求，
  修前会读到残体错位，修后稳得 200（手机侧 okhttp / `requests.Session` 都复用连接，不是边缘场景）。
- 反证一个写进 docstring 的断言：“放行但不回 100”与“回 100”的实测对比为 **1.008 s vs
  0.048 s**（21×），所以授权分支必须交回 `super().handle_expect_100()`。

### 文献归档与安卓路线处置
厂商资料原先堆在仓库根目录（共 11 份 PDF：10 份 IDO 厂商文档 + 1 份论文），已归位：
`docs/vendor/purplepi-oh2/`（5 份：Linux 使用/开发手册、OpenHarmony 使用手册、开发板规格书、
上手指南）、`docs/vendor/som7609/`（3 份：S1/S2 核心板规格书、S1 硬件设计指南）、
`docs/literature/`（1 份论文，用 `git mv` 保留历史，三处引用已同改路径）。
共删 2 份：`IDO-Purple_Pi_OH2_Android使用手册.pdf`，以及 md5 与保留那份完全相同
（`9f8bb866…`）的重复下载 `Linux开发手册 (1).pdf`。现存厂商 8 份 / 26 MB，论文 1 份 / 21 MB。

> 安卓不在项目路线上，且不是“暂不支持”而是“整条依赖链不成立”：打包脚本落 `/opt` +
> systemd 单元、`rknn_runtime.py` 依赖 Python 的 `rknn-toolkit-lite2`、取帧走 V4L2 —— 在安卓
> 上全部要换成 init 服务 + NDK `librknnrt.so` + Camera HAL。所以不再收安卓手册，
> 后人看到这条就别再往回加。但出厂预装的安卓是**免费的设备取证机会**（`librknnrt.so` 版本定
> Toolkit2 对齐、dmesg 定 LPDDR 口径、cpufreq 定 A72/A53 基线），刷机前先拿完。

厂商 PDF 不入库。`.gitignore` 新增 `docs/vendor/purplepi-oh2/*`、`docs/vendor/som7609/*`
及对应的 `!…/.gitkeep` 例外（与权重目录同一约定；已用 `git check-ignore -v` 验证：8 份 PDF
全部被忽略、.gitkeep 与新增的 `docs/vendor/README.md` 不被忽略、仍会入库）。clone 后目录
结构保留，内容从厂商资料站自行下载。

新增 `docs/vendor/README.md`（入库）作为清单与出处索引：哪份手册讲什么、什么时候要翻、
去哪儿重新下载（厂商为触觉智能 Industio，官网 `www.industio.cn`，深层链接未逐个核实故不写）。
清单已双向核对磁盘：8 份文件全部在册，索引里没有磁盘上不存在的条目。

### 性能核对（先测后改）
| 改动 | 实测代价 |
|---|---|
| grab/infer 两处 `try/except` | 0：CPython 零成本异常，裸调用 44.4 ns vs 带块 41.9 ns（差异在噪声内），且每帧只设 2 次 |
| 每帧 `frame_shape_error()` | 三次 shape 比较，纳秒级；相对单帧 240 ms（640×480）~4.2 s（4K）可忽略 |
| 故障退避 2 s | 只在错误路径付，健康路径 0 |
| §5.3 全黑帧判据（**未实现，待决**） | 4K 全图 mean+std **97.6 ms**（占 4.2 s 的 2.3%）；1/8 抽样 **2.59 ms**（0.06%）。代价不是问题，问题是它会把「遮挡镜头」的输出从 `False` 改成「无有效结论」 |
| 未鉴权路径排空到 `max_upload_mb` | 只在错误路径付；带 `Expect` 的主流客户端根本不付（实测上传 0 字节、2 ms 得 401） |
| `handle_expect_100()` 本身 | 每请求一次 `_authorized()` 字符串比较，无额外 IO；不鉴权时反而省下一整个体的读取 |
| 授权上传不回 100 的代价（已避免） | 实测 1.008 s vs 0.048 s（21×）：`super().handle_expect_100()` 不可省 |

### 验证（开发机 RTX 5060 Ti，真实权重）
- `smoke_board_pipeline` **36/36**，耗时 3.7 s（新增第 7 组契约与恢复上限 11 条、第 8 组 HTTP
  错误响应 6 条；+1.5 s 全花在 6 MB 回环排空上，退避仍打桩不实等）
- `test_real_weights` **27/27**（第 4 组补体量梯度 401、keep-alive 复用、Expect 三条）
- `--selftest --port 8399` PASS（改为 `ProxyHandler({})` 直连后首次实跑，`/status`、`/healthz` 均 200）
- 裸跑对照：修复前 12 s / 8 299 254 字节 / 0 帧 → 修复后 rc=0、2 帧共 **511 字节**（`fake-000002/000003`，coverage=0.772624，240–396 ms/帧）
- 上一轮那条 README 承诺「以下每条都在开发机实测跑通」中的 `python -m app.main` 一行，到本轮才第一次真正成立

---

## [Unreleased] - 2026-11-06（接通广播出口：常驻循环 → SSE）

上轮合并后复核技术流程时发现：`IResultSink` 只在适配层声明、`HttpNetServer` 也实现了 `publish()`，但 `engine._publish()` 只把结果存进 `_last_valid` 从不外推——广播面是**画在图上没接线**的。本轮把它接通，并修掉验证过程中撞出的限帧路径缺陷。性能优先的取舍全部带实测数字。

### Added
- **常驻循环 → 广播出口真正接通**：`DetectionEngine(..., sinks=[...])` 与后置 `attach_sink()`（组合根要用它解开「服务要引擎做 `/status`、引擎要服务做广播」的引用环）。逐帧判定现在会构造 DTO 推给 SSE
- **新形态 `--serve-net --loop`**：常驻检测循环与网络出口同跑。`/status` 读真实状态机（新增 `DetectionEngine.engine_state()`），`/stream` 收实时帧。该形态**故意不提供 `/detect`**（返回 503 `E-NO-UPLOAD`）：接上传检测要再起一个推理实例，等于板端把权重与 NPU 上下文加载两遍
- 广播的 DTO 只带标量：`mask_png`/`prob_png` 恒为 `None`，需要图像的客户端仍走 `/detect` 的 `X-With-Mask` 显式 opt-in

### Changed
- `Decider.decide(logits, want_mean=False)`：`mean_prob` 这趟全数组归约**只在有人订阅时才算**（2.9Mpx 实测多付 2.43 ms/帧，占单帧 0.06%）；`Decision` / `DetectionResult` 相应新增字段
- 限帧与常驻合并为同一个循环体：`run_forever(req, max_frames=N)` 取代 `main.py` 里手写的 `for _ in range(N): detect_once()`
- `IFrameRelay.push` 不再引用逻辑层类型，改由适配层声明 `IFrameResult` 协议（只读 `has_moire`/`coverage`）。原先靠 `TYPE_CHECKING` 兜住、运行时不构成依赖，但源码确有向上引用；改后 SDD「适配层不得 import 逻辑层」这条红线字面成立

### Fixed
- **`--frames N` 限帧运行从不广播、也不复位重试计数**：`main.py` 自己手写 for 循环直调 `detect_once()`，绕过了 `run_forever` 的循环体（`_publish`、`_retry` 归零、`DEGRADED→READY` 恢复）。这条是验证新 SSE 时撞出来的——订阅 60 s 事件数为 0，才顺藤摸到限帧路径根本没进循环
- SDD §4.5 R1 措辞自相矛盾：标签写「**丢新不堵旧**」，同一句解释却写「新帧到来覆盖旧帧」（即丢旧保新）。代码语义明确是 latest-wins，已改对标签与理由
- SDD R1 引用的 `drop_policy="latest"` 在代码里并无此参数——它其实是 CONTROL_LOGIC §3.1 的**规格名**。已在两处补注：实现上不存在帧队列，到点直接 `grab()` 取当下最新帧，「latest」是默认且唯一行为
- SDD「文档声称 vs 实际状态」缺口表的「网络出口」一行随实况更新

### 验证（开发机 RTX 5060 Ti，真实权重 + 真实样张）
- `smoke_board_pipeline` **19/19**（新增第 6 组广播出口）、`test_real_weights` **17/17**（新增第 5 组循环+HTTP 同跑端到端）
- `--serve-net --loop --camera samples/moire_photo.jpg --frames 2`：SSE 实收 2 条，`source_id=file-000002/000003`、`coverage=0.997286`（与循环日志逐位一致）、`max_prob=0.944891`、`mean_prob=0.8493`；进程退出后端口关闭（`finally: server.stop()` 生效）
- `/healthz` 免鉴权 200；`/status` 报 `DETECTING`/`READY` 真实状态且 `sse_clients` 随订阅变化；`/detect` 返 503 `E-NO-UPLOAD`
- 扇出开销实测：`_publish` 0 sink = **0.25 µs/帧**（纯早退）、1 sink = 12 µs、3 sink = 29 µs
- 曾考虑的 sigmoid 降 float32 提速**经实测否决**：`decide()` 全程仅 32 ms/帧（4.2 s 帧时的 0.8%），省十几毫秒不足以补偿改动交付判据数值口径的风险
- 排障记录（避免后人重踩）：第一次探测时 `/stream` 收到 0 事件，同批 `urllib` 请求还拿到 502。0 事件是真缺陷，根因是上面那条 `--frames` 限帧路径根本没进循环体——已由第 5 组回归锁住。
  - **本条对 502 的原判（"未能复现、不予采信"）已被下一轮推翻，见该轮 "502 归因纠正"**。当时的"验证"只测了"换个端口就返 200"，据此宣布与本功能无关；但 `127.*` 这种写法 `urllib` 根本不认（`proxy_bypass("127.0.0.1")` 实测返 `False`），请求确实每次都经代理一跳，502 来自代理而非被测服务。教训：拿一次"没能重现"当"不成立"，和拿纸面印象当结论一样不可信。

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
