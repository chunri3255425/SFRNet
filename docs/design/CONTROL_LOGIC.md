# MoiréGuard 控制逻辑规范（可执行版）

> **文档定位**：本文不是架构说明，而是**可直接翻译成 `if/else` 的实现规格**。每条判定规则都给出：触发条件、判定表达式、默认阈值、超限后的动作、以及源码依据或实测依据。
>
> **核验基线**：仓库 `chunri3255425/SFRNet` @ `2d88d3c`，源码级通读 12 个 Python 文件，并对 `SlidingWindowEngine` / `PostProcessor` / `SFRNet` 前向做了 7 组实机探针（Python 3.14 + Torch 2.12.0+cpu）。
> 凡标 `[实测]` 者为本次实机运行结果；标 `[代码]` 者精确到 `文件:行号`；标 `[设计]` 者为 `SOFTWARE_DESIGN.md` 已声明但尚未编码的目标态。

---

## 0. 一页速查表（实现时贴在手边）

| 编号 | 决策点 | 判据 | 默认值 | 违反后果 |
|---|---|---|---|---|
| **C-01** | 是否接受该帧 | `min(H,W) ≥ 32` 且 `H%32==0` 且 `W%32==0` | — | 模型形状错误崩溃 |
| **C-02** | 走滑窗还是单次前向 | `H>512 or W>512` → 滑窗；否则单块+反射填充 | tile=512 | 小图会被 pad 到 512 |
| **C-03** | 分块步长 | `stride ≤ tile`；`stride` 建议 `tile/2` | 512 / 256 | stride>tile 直接抛异常 |
| **C-03b** | 短维不足的长图 | `min(H,W) < tile` **且** `max(H,W) > tile` → 须修 `plan()` 否则 IndexError | — | 崩溃 |
| **C-04** | 尾块是否贴边 | `last_pos + tile < H` → 追加 `H - tile` | 是 | 右/下边缘漏检 |
| **C-05** | 融合权重 | `cosine`（Hann 窗）或 `uniform` | cosine | 接缝或边角不可靠 |
| **C-06** | sigmoid 施加次数 | 全流程**恰好 1 次**，在 `PostProcessor.apply` | 1 | 置信度系统性偏移 |
| **C-07** | 像素级二值化 | `prob > threshold`（严格大于） | 0.5 | — |
| **C-08** | 面积级判定 | `coverage > coverage_threshold`（严格大于） | 0.01 | 孤立噪点误报 |
| **C-09** | 是否做形态学 | 默认关闭；细密纹严禁开启 | false | 3px 以下条纹被抹除 |
| **C-10** | 取帧失败重试 | 重开摄像头，间隔 2s，上限 5 次 | 5 | 达上限 → FATAL |
| **C-11** | 推理失败重试 | 释放+重载模型，上限 3 次 | 3 | 达上限 → FATAL |
| **C-12** | 坏帧跳过 | 连续 10 帧 | 10 | 转 DEGRADED 持续告警 |
| **C-13** | 单帧超时 | 取帧 3000ms + 推理 5000ms/块 | — | 该帧作废，不阻塞 |

---

## 1. 全局状态机（进程级）

### 1.1 状态定义

| 状态 | 进入条件 | 对外布尔输出 | 指示约定 |
|---|---|---|---|
| `BOOTING` | 进程启动 / 完整重初始化 | **无效**（禁止读取） | 忙（快闪） |
| `READY` | 启动序列全部通过 | **无效**（上一轮结果已消费完） | 常亮待机 |
| `DETECTING` | 收到一次检测请求 | **无效** | 忙（慢闪） |
| `DEGRADED` | 出现可恢复错误且未达上限 | **沿用最近一次有效结果** + 警告位 | 告警（双闪） |
| `FATAL` | 致命错误 / 恢复超限 | **固定 False** | 错误常亮 |
| `SHUTDOWN` | 收到停止信号 | 保持最后值 | 熄灭 |

### 1.2 状态迁移表（含触发条件与动作）

| # | 从 | 事件 | 到 | 动作 |
|---|---|---|---|---|
| T1 | `BOOTING` | 步骤 1–7 全部成功 | `READY` | 记录启动耗时，`log(INFO, boot_ok, elapsed)` |
| T2 | `BOOTING` | 配置解析失败 | `READY` | **非致命**：退回内置默认值，`log(WARN)` |
| T3 | `BOOTING` | 权重缺失 / 结构不匹配 | `FATAL` | `E-WEIGHT-NOT-FOUND` / `E-WEIGHT-MISMATCH`，指示常亮 |
| T4 | `BOOTING` | 摄像头 3 次重试仍失败 | `FATAL` | 记录 `camera_open_fail×3` |
| T5 | `BOOTING` | 连通性自检失败 | `DEGRADED` | 允许继续跑（摄像头可能预热中），第 1 帧仍走正常流程 |
| T6 | `READY` | 收到 `detect_once` | `DETECTING` | 建帧号，开始计时 |
| T7 | `DETECTING` | 单帧完成 | `READY` | 输出布尔 + 日志一行 |
| T8 | `DETECTING` | 取帧失败 | `DEGRADED` | 进 §5.1 恢复 |
| T9 | `DETECTING` | 推理失败/超时 | `DEGRADED` | 进 §5.2 恢复 |
| T10 | `DEGRADED` | 恢复动作成功 | `READY` | 恢复计数清零 |
| T11 | `DEGRADED` | 恢复计数达上限 | `FATAL` | 对应错误码常亮 |
| T12 | `READY`/`DEGRADED` | 收到 SIGTERM | `SHUTDOWN` | 进 §6 优雅退出 |
| T13 | `READY` | 连续 10 帧坏帧 | `DEGRADED` | 维持警告，不升级 FATAL |

> **重要实现约束**：`READY` 和 `DETECTING` 状态下，对外布尔量**必须标记为无效**（返回 `None` 或置 `valid=False`），而不是沿用上一帧的 `False`。否则下游无法区分"没检测"与"检测结果是否定的"——这是当前 `InferenceService` 缺失的能力，见 §7.3。

---

## 2. 启动序列（BOOTING）

严格按序执行，**每步要么成功要么明确降级，禁止静默跳过**。

```
STEP 1  读配置
  1.1 读 configs/default.yaml
      失败(文件不存在) → 用 AppConfig() 硬编码默认值，记 WARN
  1.2 读现场覆盖文件(若存在)
      解析失败 → 整体丢弃覆盖文件，保留 1.1 结果，记 WARN
      规则：覆盖文件是"全有或全无"，不做逐字段容错
  判定：配置可用 → 继续；完全失败 → 继续（默认值一定可用）

STEP 2  初始化日志
  创建日志句柄；失败 → 降级到 stdout，记 WARN（非致命）

STEP 3  平台探测 + 装配适配层
  推定 Linux / OpenHarmony → 选 CameraAdapter / RuntimeAdapter / DeviceIO
  探测异常 → FATAL（理论上不发生，发生了说明镜像损坏）

STEP 4  加载模型（含 warmup）
  4.1 registry.create(model_id, device)
      device=="auto" → cuda 可用取 cuda，否则 cpu        [代码 registry.py:99]
  4.2 权重文件存在性
      不存在 → FATAL / E-WEIGHT-NOT-FOUND              [代码 registry.py:121]
  4.3 load_state_dict(strict=True)
      RuntimeError → FATAL / E-WEIGHT-MISMATCH          [代码 registry.py:117-119]
  4.4 warmup：用 1×3×512×512 全零张量前向一次
      ★ 必须做：首次前向含 kernel 选择、内存池分配，耗时可达稳态的 3~10 倍
      失败 → FATAL（模型根本跑不动）
  4.5 记录 warmup 耗时作为 per-tile 超时基线的冷启动参考

STEP 5  打开摄像头
  5.1 open() 首次尝试
  5.2 失败 → 间隔 2000ms 重试，最多 3 次
  5.3 仍失败 → FATAL / camera_open_fail

STEP 6  连通性自检（取 1 帧，只验证管道通）
  丢弃该帧结果，不做判定
  超时 3000ms → DEGRADED（允许继续）

STEP 7  进入 READY，log(INFO, boot_ok, elapsed_ms)
```

**分级原则**：模型缺失、摄像头缺失、平台探测失败 = **致命**（镜像已固化这两样，缺了说明镜像坏或硬件坏，重启无用）；配置损坏 = **非致命**（默认值足以工作）。

---

## 3. 取帧与尺寸准入（每帧第一道闸）

### 3.1 取帧控制

```
frame = camera.grab(timeout_ms=3000)
  超时/异常 → CaptureError → §5.1
  返回帧为空 / 尺寸为 0 → BadFrame → §5.3
```

**采样频率控制**（`run_forever` 的节流）：

| 配置项 | 语义 | 取值建议 |
|---|---|---|
| `interval_ms = 0` | 连续全速，处理完立刻取下一帧 | 仅在算力充裕、追求最低延迟时用 |
| `interval_ms > 0` | 两帧**开始**时刻的最小间隔 | 板端建议 `max(200, 实测单帧耗时×1.2)` |
| `drop_policy = "latest"` | 间隔期间产生的帧全部丢弃，只留最新一帧 | **默认**。避免排队积压导致延迟越滚越大 |

> `drop_policy` 是**规格名，不是代码参数**：实现上不存在帧队列，`run_forever` 到点直接 `camera.grab()` 取当下最新帧，因此 "latest" 是不需要开关的默认且唯一行为（若将来真要 FIFO 队列，需在适配层新增缓冲并显式立项）。

> **为什么必须 `latest`**：摄像头以 30fps 产帧、单帧推理要 300ms 时，若用 FIFO 排队，第 N 帧处理完时队列里已积压 9 帧，端到端延迟单调增长直到内存耗尽。`latest` 策略把系统从"追帧"变为"跟最新"，代价是丢帧率上升——无人值守场景下这是正确取舍。

### 3.2 尺寸准入（**必须先做，否则模型崩溃**）

这是本项目最容易被忽略、且**必然导致运行时崩溃**的一条。

**规则 C-01**：

```
输入帧尺寸 (H, W) 必须满足：
  (a) H ≥ 32 且 W ≥ 32
  (b) H % 32 == 0 且 W % 32 == 0
任一不满足 → 拒绝该帧，返回 E-INPUT-SHAPE，进 §5.3

不满足时的处理策略（二选一，默认策略 A）：
  策略 A（推荐，零信息损失）：反射填充到最近的 32 的倍数，推理后裁回原尺寸
  策略 B（简单）：直接丢弃该帧并告警
```

**实测依据**（`[实测]`，逐边长扫描 1–300 + 抽查 300–640）：

| 输入边长 | 结果 |
|---|---|
| 1, 2, 3, 4, 8, 16, 24, 30 | **崩溃**：`AdaptiveAvgPool2d` 下采样后为 0 → `Input and output sizes should be greater than 0` |
| 31, 33, 65, 99, 127, 199, 255, 511, 513 | **崩溃**：`The size of tensor a (N) must match the size of tensor b (N-1)` |
| 32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448, 480, 512, 544, 576, 640 | **正常**，输出与输入同尺寸 |

**根因**：`upstream/sfr/FRM.py:109-128` 把特征图按 `H//2`、`W//2` 切成四个象限，再在 `FRM.py:135-138` 拼回去。当 `H` 为奇数时 `H//2 + (H - H//2) ≠ H` 的对称性被打破，拼接出错位 1px 的尺寸不匹配。而 `32` 是模型最深下采样倍率 `H/8` 的 4 倍（四象限再各下采样 8 倍），边长必须是 32 的倍数才能让四个象限在每一层都整除。

> **对现有实现的直接影响**：`SlidingWindowEngine.__call__` 在 `sliding_window.py:215-218` 只对 `H<512` 做反射填充，**不检查填充后是否满足 32 的倍数**，也不检查 `H>512` 时的整除性。当前 `plan()` 只在 `tile=512` 下工作，512 本身满足 32 倍数，所以滑窗主路径是安全的；但**小图分支**（`H` 或 `W` < 512）填充到 512 后虽然安全，**非滑窗直通路径**（即直接调 `core.forward`）会崩。必须在 `InferenceService.run` 第 3 步之后插入本闸门。

### 3.3 通道与位深准入

| 输入模式 | 处理 | 依据 |
|---|---|---|
| `L`（灰度） | `convert("RGB")` 复制三通道 | `builtin_adapter.py:104-106` |
| `RGBA` | 丢弃 alpha → `RGB` | `builtin_adapter.py:101-103` |
| `CMYK` | 转 `RGB` | `builtin_adapter.py:107-109` |
| `P`（调色板）| 走 `else` 分支 `convert("RGB")` | `builtin_adapter.py:110-111` |
| 16bit | `_detect_bit_depth` 识别，但**当前实现仍按 `/255.0` 归一化** | ⚠ 见 §7.2 缺陷 D-05 |

**准入门槛**：转换后张量必须通过 `assert_input_contract`（`contracts.py:204-225`）——4 维、batch=1、通道=3、float32、值域 [0,1]、无 NaN/Inf。任一不通过 → `E-CONTRACT-*` → 该帧作废，进 §5.3。

---

## 4. 分块决策树（核心采样逻辑）

### 4.1 三分支判定

```
输入：H, W（已通过 §3.2 准入，均为 32 的倍数）
参数：tile = config.inference.tile_size（默认 512）
      stride = config.inference.stride（默认 256）

IF H ≤ tile AND W ≤ tile:
    ── 分支 A：单块 + 反射填充
    1. pad_h = tile - H, pad_w = tile - W
    2. ★ 填充模式必须按 pad 量与边长比较后选择（实测约束）：
          mode = 'reflect'   if (pad_h < H and pad_w < W)
                 'replicate' otherwise
       PyTorch 的 reflect 要求 "Padding size should be less than the
       corresponding input dimension"，违反直接抛异常。
    3. image = F.pad(image, (0, pad_w, 0, pad_h), mode=mode)
    4. 前向 1 次
    5. 输出裁剪回 [0:H, 0:W]

ELIF H > tile AND W > tile:
    ── 分支 B：二维滑窗
    走 §4.3 的坐标生成 + 逐块推理 + §4.5 融合

ELSE:   # 一维 <= tile 且另一维 > tile —— ★ 当前实现会崩溃
    ── 分支 C：单维滑窗（必须先修 plan()，见下方警告）
    短的那一维固定坐标 [0]，长的那一维走 §4.3 坐标生成
    ★ 实测：plan(512, 2000) → 7 块，x ∈ {0,256,512,768,1024,1280,1488}，y = {0}
    ★ 注意 plan() 的判定是 `H<=tile AND W<=tile` 才单块，所以
      512×2000 不走分支 A，自然落到 rows=[0] + cols=[0,256,...,1488]
```

> ### ⚠ 分支 C 的现有实现缺陷（高危，实测复现）
>
> `plan()` 在 `sliding_window.py:106-124` 对每一维独立地跑 `while y + tile <= h` 循环。当**短维 < tile 且长维 > tile** 时，短维的循环体一次都不执行，`rows` 保持空列表，随后 `rows[-1]` 立即抛 `IndexError`。
>
> `[实测]` 崩溃矩阵：
>
> | 输入 | 结果 |
> |---|---|
> | 64×4096 | ❌ `IndexError: list index out of range` |
> | 256×2000 | ❌ 同上 |
> | 100×600 | ❌ 同上 |
> | 511×512 | ✅ OK（1 块）— 因 `512 <= 512` 走单块分支 |
> | 512×64 | ✅ OK（1 块）— 因 `64 <= 512` 走单块分支 |
> | 32×512 | ✅ OK（1 块） |
>
> **精确触发条件**：`min(H,W) < tile` **且** `max(H,W) > tile`（严格大于）。
>
> **修复**：在 `plan()` 的每维坐标生成后补空列表兜底：
>
> ```python
> def _axis(full, tile, stride):
>     if full <= tile:
>         return [0]                       # ← 关键：不足一维时补 0
>     coords, pos = [], 0
>     while pos + tile <= full:
>         coords.append(pos); pos += stride
>     if coords[-1] + tile < full:
>         coords.append(full - tile)
>     return sorted(set(coords))
> ```
>
> **规避方案（不改源码时）**：准入闸门把 `min(H,W) < tile` 的帧直接走分支 A（pad 到 tile×tile），不进入 `plan()`。代价是宽高比极端的画面（如 512×2000 的竖长截图）整帧被压缩到单块 512²，细节丢失。**推荐修复 `plan()` 而不是规避。**

> **正确性交叉验证** `[实测]`：修复后 `plan(512,2000)` 返回 7 块，x 坐标 `[0, 256, 512, 768, 1024, 1280, 1488]`，最后一块 x=1488 使 `1488+512=2000` 恰好贴右边——尾块回退逻辑正确。该计算本身无误，问题只出在"短维为空"的兜底缺失。

### 4.2 分支 A 的填充模式判据（实测边界）

`[实测]` 反射填充在 `pad ≥ 边长` 时直接抛异常：

| 原边长 | pad 量 | `reflect` | `replicate` |
|---:|---:|---|---|
| 400 | 112 | ✅ OK | ✅ |
| 300 | 212 | ✅ OK | ✅ |
| **255** | **257** | ❌ `Padding size should be less than the corresponding input dimension` | ✅ |
| **100** | **412** | ❌ 同上 | ✅ |
| **32** | **480** | ❌ 同上 | ✅ |

**分界点：`H < 256` 时 `pad_h = 512 - H > H`，reflect 必然失败。** 由于 §3.2 准入允许 `H ≥ 32`，区间 `[32, 255]` 全部落在危险区。因此分支 A 的实现**必须**写成：

```python
mode = "reflect" if (tile - H < H and tile - W < W) else "replicate"
image = F.pad(image, (0, tile - W, 0, tile - H), mode=mode)
```

`[实测]` `replicate` 在 `H=1, 2, 32, 100` 全部成功，可作无条件兜底。

### 4.3 坐标生成（可执行的精确算法）

```
def gen_coords(full, tile, stride) -> list[int]:
    assert stride <= tile, "stride 必须 <= tile"
    if full < tile:                    # 单维不足
        return [0]
    coords, pos = [], 0
    while pos + tile <= full:
        coords.append(pos)
        pos += stride
    if coords[-1] + tile < full:       # 尾块未贴边 → 强制回退
        coords.append(full - tile)
    return sorted(set(coords))         # 去重（回退位置可能与已有坐标重合）
```

**算法性质（已实测验证）**：

| 性质 | 验证结果 |
|---|---|
| 完全覆盖 | `[实测]` 9 组尺寸（512²、513×512、768²、1024²、2160×3840、1080×1920、300×400、400×512、1023²）覆盖率**全部 = 1.0000** |
| 尾块贴边 | `[实测]` H ∈ {512,513,767,768,769,1024,1025,1100,1300} 的 `last_y + 512 == H` **全部成立** |
| 相邻窗口最大间距 | `[实测]` tile/stride = 512/256 → 256；512/384 → 384；512/300 → 300；512/400 → 400，**均 ≤ stride** |

### 4.4 分块数量速查（tile=512, stride=256）`[实测]`

| 分辨率 | 场景 | 行数 | 列数 | 块数 | 耗时估算 @CPU 单块 1.2s |
|---|---|---:|---:|---:|---:|
| 512×512 | 训练裁剪尺寸 | 1 | 1 | **1** | ~1.2 s |
| 768×768 | 小窗屏摄 | 2 | 2 | **4** | ~4.8 s |
| 1024×1024 | 1MP | 3 | 3 | **9** | ~11 s |
| 1080×1920 | 手机全屏 | 4 | 7 | **28** | ~34 s |
| 2160×3840 | 4K UHD（论文基准） | 8 | 14 | **112** | ~134 s |

> **部署注意**：4K 单帧 112 块，在 CPU 上不可接受。必须走量化 + NPU（见 `SOFTWARE_DESIGN.md` §6 风险 #1）。本文档的参数选择不影响此结论，但如果板端必须做 4K，`stride` 从 256 提到 384 可把块数从 112 降到 **56**（代价是重叠从 50% 降到 25%，接缝风险上升，需实测确认）。

### 4.5 融合策略选择

| `fusion` | 权重 | 适用 | 风险 |
|---|---|---|---|
| `cosine`（默认） | 2D Hann 窗，中心 1.0、四角 1e-6 | 追求接缝平滑 | 角部像素只被单 tile 的角覆盖时，该 tile 的角置信度不可靠 |
| `uniform` | 全 1，等权平均 | 需要保真、可解释 | 块边界可能出现可见接缝 |

**融合公式（必须严格执行）**：

```
numerator[H,W]   = Σ_t ( logits_t ⊙ window_t )     # logits 域加权
denominator[H,W] = Σ_t ( window_t )
out = numerator / clamp(denominator, min=1e-8)
```

**三条硬约束**：

1. **在 logits 域累加**，绝不先 sigmoid。因 `σ(E[ℓ]) ≠ E[σ(ℓ)]`（`[代码] sliding_window.py:9-10` 已声明）。
2. **分母 clamp 下限 1e-8**（`[代码] sliding_window.py:183`）。`[实测]` 余弦窗四角权重 = 1.00e-06，768² 图像左上角分母 = 1.000e-06，远大于 1e-8，故**当前不会触发 clamp**，融合结果数值正确（`[实测]` 用常数 logits=2.0 做端到端，输出恒为 2.0000，误差 0.0000）。
3. **边角不可靠但当前无处理**。`[实测]` 当只让 2 个 tile 输出高置信时，图像角落 (0,0) 与 (0,511) 融合后概率 = 0.9997，而中心 = 0.9816——角部反而更"确定"，因为该处仅由单 tile 决定、没有其他 tile 拉低。**这是余弦窗的固有副作用**：图像四角仅有 1 个 tile 的角部覆盖，该 tile 在角部的感受野不完整（零填充边界效应）。

**建议实现（补丁级）**：融合后在 `denominator < 0.02` 的区域标注 `low_confidence` 掩膜，判定时对这些区域**不计入 coverage 分子也不计入分母**，或对 `has_moire` 结论附加 `warnings.append("border_low_conf_ratio=X%")`。判定阈值不应受边角伪影影响。

---

## 5. 判定逻辑（Decider）

### 5.1 三步判定公式（唯一正确顺序）

```
① logits → prob       prob = sigmoid(logits)                    ← 全流程唯一一次
② prob → mask         mask = (prob > threshold).to(uint8)       ← 严格大于
③ mask → bool         coverage = mask.sum() / mask.numel()
                      has_moire = coverage > coverage_threshold  ← 严格大于
```

**参数语义边界（易错点）**：

| 参数 | 作用域 | 默认 | 边界行为 | 依据 |
|---|---|---|---|---|
| `threshold` | **像素级** | 0.5 | `prob == 0.5` 时判为背景（因严格大于） | `postprocess.py:77` |
| `coverage_threshold` | **面积级** | 0.01 | `coverage == 0.01` 时判为无摩尔纹 | `postprocess.py:125` |

`[实测]`：全零 logits → coverage = 0.000000 → `has_moire = False`；全正大 logits → coverage = 1.0000 → `True`。当 `coverage_threshold = 0.01` 时，含义是"超过 1% 的像素被判为摩尔纹"才报警。

**为什么必须双门限**：单用像素级判据（任一像素超阈值即 True）对孤立噪点极度敏感；单用面积级判据则无法区分"大面积弱响应"与"小面积强响应"。`threshold` 先过滤置信度，`coverage_threshold` 再过滤规模，两级串联。

### 5.2 形态学的开关决策（默认必须关闭）

**规则 C-09**：`apply_morphology` 默认 `False`，且**不得**为"想去噪"而开启。

`[实测]` 用每 3px 一条竖线的合成掩膜（模拟细密摩尔纹）：

| 配置 | coverage |
|---|---|
| `apply_morphology = False` | **0.3438** |
| `apply_morphology = True`（kernel=3） | **0.0000** |

3×3 开运算（先腐蚀后膨胀，`postprocess.py:107-109`）会把宽度 < 3px 的条纹**整体抹除**，coverage 直接归零——而摩尔纹恰恰是高频细密纹理。**开启形态学等于对最典型的摩尔纹做静音。**

**允许开启的唯一场景**：已确认为"大面积块状假阳性"（如纯色墙面被误判），此时用 `kernel=3` 做形态学并结合人工复核。开启前必须用真实样本回归，确认 IoU 不下降。

### 5.3 坏帧的界定与跳过

**坏帧定义（满足任一即判坏帧）**：

| 条件 | 判定依据 |
|---|---|
| 解码失败 / 尺寸为 0 | 抛 `E-INPUT-DECODE` |
| 通道数 ∉ {1,3,4} | 适配器抛 `TypeError` |
| 契约校验失败 | `assert_input_contract` 抛 `E-CONTRACT-*` |
| 尺寸不满足 §3.2 | `E-INPUT-SHAPE` |
| 像素与元数据不符（`data.shape[:2] != (height, width)`）或不是 HxWx3 | `frame_shape_error()`（`adaptation/camera/base.py`），引擎每帧取帧后第一道闸调用 |
| **全黑帧**：`mean(张量) < 0.02` 且 `std < 0.01` | 补丁级判据（当前代码无此检查） |
| **全白过曝帧**：`mean > 0.98` 且 `std < 0.01` | 同上 |

> **为何把通道钉死为 3**：上表的 {1,3,4} 是「图像本身可用」的宽判据；而引擎→后端
> 之间是固定的 NCHW 3 通道契约（`_infer_padded` 直接 `transpose(2,0,1)`），灰度/RGBA
> 的归一化属适配层职责（V4L2 与文件源都已 `BGR2RGB`）。形状不合归坏帧，不归 capture
> 或 infer——重开摄像头和重载模型都救不了它。

**动作**：跳过该帧，`frame_skip_count += 1`，输出沿用上一有效值 + 警告位。连续 10 帧坏帧 → 转 `DEGRADED` 并持续告警（不升级 FATAL，因为可能只是镜头被遮挡）。

> **为什么全黑/全白要单列**：模型对全 0 输入输出接近全负 logits（sigmoid≈0），结果是 `has_moire=False`——**看起来像正常结论**。若下游依赖该布尔做安防决策，"镜头被挡"会被误读为"画面干净"，这是静默的假阴性。必须显式拦下。

---

## 6. 错误恢复（Supervisor）

### 6.1 恢复动作表

| 错误类 | 触发条件 | 恢复动作（按序） | 重试上限 | 退避 | 达上限 |
|---|---|---|---|---|---|
| 取帧失败 | `grab()` 超时/异常 | ① `camera.close()` ② 等 2000ms ③ `camera.open()` | **5** | 固定 2s | FATAL |
| 推理失败 | 前向抛异常 | ① `del` 所有张量 ② `cuda.empty_cache()` ③ `runtime.load()` ④ `warmup()` | **3** | 固定 2s | FATAL |
| 推理超时 | 单块 > 5000ms | 同"推理失败" | **3** | 固定 2s | FATAL |
| 坏帧 | §5.3 任一条 | 跳过，取下一帧 | **连续 10 帧** | 无 | DEGRADED 持续 |
| 未知异常 | 任意未捕获 Exception | ① 记完整堆栈 ② 完整重新初始化（回 BOOTING STEP 3） | **3** | 固定 2s | FATAL |

**关键设计选择**：

1. **固定间隔 2s，不用指数退避**。嵌入式场景下摄像头重开本身有硬件级延迟（USB 枚举、V4L2 buffer 分配通常 300–800ms），指数退避会让恢复时间不可预测。简单可靠优先。
2. **DEGRADED 期间沿用一个旧的 `True`**，不输出 `False`。否则下游会看到"有摩尔纹 → 无摩尔纹 → 有摩尔纹"的跳变。
3. **恢复成功后计数器清零**。计数是"连续失败"，不是累计失败。
4. **未知异常的"重启"边界**。表里的"回 BOOTING STEP 3"在实现中只能重跑 `Bootstrapper`
   （STEP 4 模型 → STEP 5 摄像头 → STEP 6 自检）：STEP 3 的适配器装配由组合根的
   `factory` 完成，引擎无法自我重造。所以 3 次仍失败即 FATAL，由组合根以**退出码 1**
   收摊，让 `Restart=on-failure` 去做一次真正的整机重启。
5. **每类错误的日志必须有界**。上限计数与退避同时承担这个职责：`interval_ms=0` 的
   全速循环里，一条无条件 ERROR 就是每秒数千行（实测 12 s 刷 8.3 MB），板端 flash
   寿命和日志可读性一起没。坏帧告警只在跨阈值那一次落日志。

### 6.2 恢复判定伪代码

```
def handle_error(err):
    match err.type:
        case CaptureError:
            n = self.retry.capture += 1
            if n > 5:  return FATAL("camera", n)
            sleep(2.0); camera.close(); camera.open()
            return DEGRADED if recovered else self.handle_error(err)   # 计数已加

        case InferError | InferTimeout:
            n = self.retry.infer += 1
            if n > 3:  return FATAL("runtime", n)
            free_tensors(); cuda_empty_cache(); runtime.load(); runtime.warmup()
            return DEGRADED

        case BadFrame:
            self.skip += 1
            if self.skip > 10:
                self.state = DEGRADED       # 只降级，不 FATAL
                log(WARN, "bad_frame_streak", self.skip)
            return SKIP_FRAME

        case _:
            n = self.retry.unknown += 1
            if n > 3:  return FATAL("unknown", n)
            log(ERROR, traceback.format_exc())
            return reinit_from(STEP_3)
```

### 6.3 超时预算表

| 环节 | 预算 | 来源 | 超时后果 |
|---|---|---|---|
| 取帧 | 3000 ms | `[设计] SOFTWARE_DESIGN §4.3.3` | `CaptureError` |
| 单块前向 | 5000 ms（板端按实测标定） | `[设计]` | `InferTimeout`，整帧作废 |
| warmup 前向 | 60000 ms | 本文补充 | FATAL（模型跑不动） |
| 单帧总耗时 | `request.timeout_ms` | 调用方指定 | `E-TIMEOUT`，帧作废 |

**单帧总预算的经验公式**：

```
timeout_ms = 2000 + num_tiles × per_tile_ms × 1.5

其中 num_tiles 由 §4.4 速查表得出，per_tile_ms 取 warmup 实测值的稳态
系数 1.5 是抖动余量（GC、内存碎片、温度降频）
```

例：1080×1920 的 28 块，稳态单块 80ms → `timeout = 2000 + 28×80×1.5 = 5360 ms`。

---

## 7. 配置变更生效矩阵

| 参数 | 变更方式 | 生效时机 | 需中断检测吗 | 需重载模型吗 |
|---|---|---|---|---|
| `threshold` | 热更新 | **下一帧** | 否 | 否 |
| `coverage_threshold` | 热更新 | **下一帧** | 否 | 否 |
| `apply_morphology` | 热更新 | 下一帧 | 否 | 否 |
| `interval_ms` | 热更新 | 下一轮循环 | 否 | 否 |
| `tile_size` / `stride` | 热更新 | 下一帧 | 否 | 否（但**必须重新校验 §4.1**）|
| `model_id` | 需重载 | 走恢复流程 | 短暂 | **是** |
| `device` | 需重载 | 走恢复流程 | 短暂 | **是** |
| `fusion` | 热更新 | 下一帧 | 否 | 否 |
| 摄像头分辨率/曝光 | 需重启进程 | — | 是 | 否 |

**校验规则**：热更新提交时立刻执行 `ConfigManager.validate()`（`config.py:100-154`），校验失败则**整体拒绝本次更新**并保留旧值，向调用方返回错误列表。不允许部分字段生效。

**`tile_size` 变更的额外校验（当前 `validate()` 缺失）**：

```
if tile_size % 32 != 0:
    reject("tile_size 必须是 32 的倍数，否则模型四象限拼接触发形状不匹配")
```

---

## 8. 常驻循环与优雅退出

### 8.1 循环骨架（可直接实现）

```
def run_forever(req, on_result):
    while True:
        # ① 停止信号（最高优先级）
        if self.stop_event.is_set():
            return self.shutdown()

        # ② 配置热更新（有版本号变化才动作）
        if self.config_version != self.consumed_version:
            if self.apply_hot_config():  self.consumed_version = self.config_version
            else: log(WARN, "hot_config_rejected")

        # ③ 节流
        if self._next_tick: sleep_until(self._next_tick)

        # ④ 单帧
        t0 = now()
        try:
            result = self.detect_once(req)
            self.publish(result)                    # 输出布尔 + 日志
            self.retry.reset_all()                  # 成功则清计数
            if self.state == DEGRADED: self.state = READY
        except MoireError as e:
            self.handle_error(e)                    # §6.2
        except Exception as e:
            self.handle_error(UnknownError(e))

        # ⑤ 下一拍
        if req.interval_ms > 0:
            self._next_tick = t0 + req.interval_ms
        else:
            self._next_tick = None
```

### 8.2 优雅退出（SHUTDOWN）

```
1. 置 stop_event，拒绝新帧
2. 等待当前帧完成，最多再等 1 个 timeout_ms
     超时 → 强制放弃该帧（记 WARN, "frame_dropped_on_shutdown"）
3. 按序释放：camera.close() → runtime.unload() → log.flush()
4. 指示熄灭；退出码：正常收摊 0，恢复超限（FATAL）1

异常路径：收到 SIGKILL 时不做任何清理（无持久状态，冷启动即可恢复）
```

**断电安全依据**：系统**不在运行中写任何关键状态文件**，所有配置只读，结果只写日志。因此重启后行为与冷启动完全一致，无需 WAL / 事务 / 恢复日志。

---

## 9. 验收用例（实现后必须逐条通过）

| # | 场景 | 输入 | 期望行为 | 验证方式 |
|---|---|---|---|---|
| V-01 | 标准 4K 帧 | 2160×3840 | 112 块，覆盖率 100%，无区块遗漏 | 检查 `plan()` 长度 = 112 |
| V-02 | 边界 513×512 | 513×512 | 2 块，y ∈ {0,1} | `[实测]` 已确认 |
| V-03 | 小图填充 | 300×400 | 1 次前向，tile=512，输出裁回 300×400 | `[实测]` 已确认 |
| V-04 | **非 32 倍数** | 511×513 | **拒绝或填充**，不得崩溃 | 当前**会崩**，需补闸门 |
| V-05 | **极小图** | 32×32 | 不得崩溃（pad 量 480 > 边长 32，须换 replicate） | `[实测]` reflect 必崩、replicate 可行 |
| V-05b | **填充临界** | 255×255 | pad=257 > 255，须自动切 replicate | `[实测]` 已确认分界点 |
| V-06 | 单维超限 | 512×2000 | 7 块，x 尾块 = 1488 | `[实测]` 已确认 |
| V-06b | **短维不足 + 长维超限** | 64×4096 | 不得崩溃（当前抛 `IndexError`） | `[实测]` 已复现，需修 `plan()` |
| V-07 | 常数 logits 融合 | 全 2.0 | 输出恒 2.0，误差 < 1e-4 | `[实测]` 误差 0.0000 |
| V-08 | 全零 logits | 全 0 | coverage = 0，has_moire = False | `[实测]` 已确认 |
| V-09 | 形态学开关 | 3px 条纹 | 关闭时 coverage≈0.34，开启时 =0 | `[实测]` 已确认 |
| V-10 | 阈值边界 | coverage 恰为 0.01 | has_moire = **False**（严格大于） | 单测断言 |
| V-11 | 取帧连续失败 5 次 | 模拟 camera 抛异常 | 第 5 次后进 FATAL，布尔固定 False | 注入故障 |
| V-12 | 坏帧连续 10 帧 | 全黑帧 | 转 DEGRADED，输出沿用旧值，不跳变 | 注入故障 |
| V-13 | 热更新非法值 | `tile_size = 500` | 整体拒绝，保留旧值，返回错误列表 | 单测断言 |
| V-14 | 优雅退出 | SIGTERM | 3 步释放完成，退出码 0 | 集成测试 |
| V-15 | 权重缺失 | 删除 pth | BOOTING → FATAL，错误码 `E-WEIGHT-NOT-FOUND` | 集成测试 |

### 9.1 参考实现已通过的端到端验证

按本文 §3.2 + §4.1 + §4.5 的规则写出一份参考实现（准入对齐 → 分支选择 → 余弦窗融合 → sigmoid），在**真实模型**（`build_SFR()`，随机初始化权重）上跑通全部 9 个分支组合：

| 输入 | 覆盖的规则 | 输出形状 | 概率范围 | 结果 |
|---|---|---|---|---|
| 64×64 | 分支 A + replicate（pad 480 > 64） | (1,1,64,64) | [0.450, 0.474] | ✅ |
| 100×128 | 分支 A + 对齐到 128×128 | (1,1,100,128) | [0.449, 0.475] | ✅ |
| 255×255 | 分支 A + replicate（临界） | (1,1,255,255) | [0.451, 0.476] | ✅ |
| 300×400 | 分支 A + 对齐 320×416 | (1,1,300,400) | [0.450, 0.476] | ✅ |
| 512×512 | 分支 A（无 pad） | (1,1,512,512) | [0.449, 0.476] | ✅ |
| 511×513 | 对齐 512×544 后走分支 C | (1,1,511,513) | [0.449, 0.476] | ✅ |
| **64×600** | **修复后的分支 C（原实现 IndexError）** | (1,1,64,600) | [0.450, 0.475] | ✅ |
| 512×600 | 分支 C 单维滑窗 | (1,1,512,600) | [0.450, 0.477] | ✅ |
| 600×800 | 分支 B 二维滑窗 | (1,1,600,800) | [0.449, 0.476] | ✅ |

**结论**：输出形状全部与输入严格一致（含裁回），概率集中在 0.45–0.48。随机权重下模型输出接近"全负偏置"，该数值无判别意义，仅用于证明控制流与形状变换正确。**若换成 `SFR_best.pth` 真实权重，同一管线即可产出有效掩膜。**

---

## 10. 现有实现的缺口清单（按修复优先级）

| # | 缺陷 | 位置 | 影响 | 严重度 | 修复动作 |
|---|---|---|---|---|---|
| D-01 | 无 32 倍数尺寸闸门 | `inference_service.py:136` 之后 | 非 32 倍数输入**必然崩溃** | 🔴 高 | 插入 §3.2 的 C-01 校验 + 填充 |
| D-01b | **`plan()` 短维为空列表崩溃** | `sliding_window.py:106-124`（`rows[-1]` / `cols[-1]`） | `min(H,W)<512 且 max(H,W)>512` 时抛 `IndexError`，如 64×4096、256×2000、100×600 | 🔴 高 | 每维坐标生成前加 `if full <= tile: return [0]` |
| D-02 | 反射填充未处理 pad ≥ 原尺寸 | `sliding_window.py:218` | `H<256` 时 reflect 抛异常，小图直接崩 | 🔴 高 | 按 §4.2 判据在 reflect/replicate 间切换 |
| D-03 | `plan()` 用 `H<=tile AND W<=tile` 判单块，但 `fuse()` 按实际尺寸裁剪 | `sliding_window.py:100, 168-176` | 单块分支下 `Rect` 宽高写死 512，靠 `min()` 兜底，逻辑混乱但结果正确 | 🟡 中 | 显式区分"规划尺寸"与"实际尺寸" |
| D-04 | `coverage` 只算整图，无 `low_confidence` 区排除 | `postprocess.py:84` | 边角伪影直接进判定 | 🟡 中 | 增加边角掩膜与 `warnings` |
| D-05 | 16bit 图仍按 `/255.0` 归一化 | `builtin_adapter.py:114` | 16bit 图整体过曝，判定失效 | 🟡 中 | 按 `bit_depth` 选除数 |
| D-06 | 无 warmup | `registry.py:150` 之后（`create` 返回处） | 首帧显著慢，易触发超时 | 🟡 中 | 加载后跑一次 512² 全零前向 |
| D-07 | `InferenceService` 无状态机、无重试、无超时 | `inference_service.py` 整体 | 一次失败即抛异常，无人值守不可用 | 🔴 高 | 外包一层 `DetectionEngine`（§6） |
| D-08 | `cancel()` 只置 flag，循环内不检查 | `inference_service.py:225-232` | 取消不生效，必须等整帧跑完 | 🟡 中 | 在 `on_progress` 里检查并 `raise CANCELLED` |
| D-09 | 无 `max_pixels` 实际拦截 | `config.py:46` 定义、仅 `_merge_config` 读取，`run()` 从不校验 | 超大图直接 OOM | 🟡 中 | `run()` 入口校验 `H*W > max_pixels` → `E-INPUT-TOO-LARGE` |
| D-10 | `stride` 允许等于 `tile`（无重叠） | `config.py:126` | 无重叠时接缝不可见，但融合退化为拼接 | 🟢 低 | 建议 `stride ≤ tile*0.75` 告警 |

---

## 11. 与上游脚本的关系（不要复用）

| 上游脚本 | 逻辑 | 是否可用于生产 | 原因 |
|---|---|---|---|
| `predict.py` 主路径 | 概率域累加 + 均匀权重 | ❌ | 与训练口径不一致（`σ(E)≠E(σ)`） |
| `predict.py` 小图路径 | 画布未随 pad 扩大 | ❌ | `[技术文档 §5.5.5]` 已确认崩溃 |
| `predict.py` 注释 | 称重叠 64px | ❌ | 实际 `512-256=256`，差 4 倍 |
| `train.py` 验证滑窗 | overlap=0.25（stride=384） | ⚠ | 与推理的 0.5 不一致，选出的 best 未必是 test 最优 |
| `train.py` `predict_sliding_window` | logits 域平均 | ✅ 逻辑对 | 但缺尾块去重，需换用 `SlidingWindowEngine.plan()` |

**结论**：生产路径只走 `moireguard/logic_layer/*`，`upstream/` 仅作为模型定义（`build_SFR`）的来源，**不调用任何上游推理函数**。

---

## 附录 A：单帧控制流（完整时序）

```
                    ┌──────────────┐
   请求 ──────────►│ 状态 == READY? │
                    └──────┬───────┘
                     否 ───┴──► 拒绝 E-BUSY
                     是
                      ▼
              ┌───────────────┐
              │ grab(3000ms)  │──失败──► §6.1 CaptureError 恢复
              └───────┬───────┘
                      ▼
              ┌───────────────┐
              │ 尺寸准入 C-01  │──失败──► 填充 或 丢弃(E-INPUT-SHAPE)
              │ H,W ≥32 且 %32│
              └───────┬───────┘
                      ▼
              ┌───────────────┐
              │ 适配器 → 张量  │──失败──► 丢弃(BadFrame)
              │ 契约校验       │
              └───────┬───────┘
                      ▼
              ┌───────────────┐
              │ 分块决策 §4.1  │
              └───┬───┬───┬───┘
            A(单块)│ B(2D)│ C(1D)
                  ▼   ▼   ▼
              ┌───────────────┐
              │ 逐块 forward   │──失败/超时──► §6.1 InferError
              │ 记录进度       │
              └───────┬───────┘
                      ▼
              ┌───────────────┐
              │ 融合(logits域) │
              └───────┬───────┘
                      ▼
              ┌───────────────┐
              │ sigmoid ×1     │
              │ >threshold     │
              │ coverage 统计  │
              │ >coverage_thr  │──► has_moire (bool)
              └───────┬───────┘
                      ▼
              ┌───────────────┐
              │ indicate(bool) │
              │ log 一行       │
              └───────┬───────┘
                      ▼
                 回到 READY
```

---

## 附录 B：日志单行格式

```
{fp|ft} t={iso8601} frame={seq:06d} state={S} has_moire={0|1|NA} \
coverage={f:.6f} thr={f:.3f} cthr={f:.3f} tiles={n} \
grab_ms={n} infer_ms={n} total_ms={n} device={cpu|cuda:N} \
warn={code[,code...]} retry={c|i|u}
```

- `has_moire=NA` 用于 `BOOTING`/`DETECTING`/坏帧三种"无有效结论"场景。
- `warn` 为逗号分隔的错误码，取自 `exceptions.py` 的 `ErrorCode`。
- 一行一帧，可被 `awk`/`jq` 直接解析。

---

*文档基线：仓库 @ `2d88d3c`｜实机：Python 3.14 + Torch 2.12.0+cpu｜维护：接口契约变更时同步更新 §0 速查表与 §9 验收用例*
