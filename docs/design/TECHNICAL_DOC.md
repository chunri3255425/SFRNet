# SFRNet 技术文档

**SFRNet: Ultra-high-definition moiré pattern detection（Scale-aware Fission Reorganization Network）**

> 文档版本：v2.0（论文版增补，在原 v1.0 源码分析基础上补全论文理论侧）
> 分析对象：`https://github.com/chunri3255425/SFRNet.git` @ `51f4057`（branch `main`，含 11 个文件，51 MB）
> 撰写依据（三重交叉验证）：
> ① **论文原文**——`SFRNet--Ultra-high-definition-moir--pattern-_2027_Expert-Systems-with-Applic.pdf`（12 页，全文文本已逐页解析）＋`SFRNet论文中文翻译.docx`；
> ② **源码级通读**——`train.py` / `predict.py` / `upstream/data_loding.py` / `upstream/metrics.py` / `upstream/sfr/{sfrnet,safm,FRM}.py` 共 979 行；
> ③ **实机验证**——加载 `SFR_best.pth` 于 PyTorch 2.12.0 执行前向 hook、反向梯度探针、算子级 MACs 累加、参数去重统计。
>
> **凡标「实测」的数值均为本机运行结果；凡标「论文」的数值均引自上述 PDF。二者不一致处已在 §6.5、§8.6 显式列出，不做调和性修饰。**

---

## 论文信息与溯源

| 项 | 内容 |
|---|---|
| 标题 | SFRNet: Ultra-high-definition moiré pattern detection |
| 作者 | Pengyue Xu<sup>a,b,1</sup>、Shuang Chen<sup>a,b,1</sup>、Qixian Zhang<sup>c,1</sup>、Ning Xie<sup>a,b</sup>、Hongbin Wang<sup>a,b,\*</sup>（<sup>1</sup>同等贡献） |
| 单位 | <sup>a</sup> 昆明理工大学 信息工程与自动化学院；<sup>b</sup> 昆明理工大学 云南省人工智能重点实验室；<sup>c</sup> 同济大学 计算机科学与技术学院 |
| 期刊 | *Expert Systems With Applications*, **332 (2027) 133519** |
| DOI | `10.1016/j.eswa.2026.133519` |
| 时间线 | Received 2026-02-28 → Revised 2026-06-24 → Accepted 2026-07-01 → Online 2026-07-07 |
| 关键词 | Moiré pattern、Moiré detection、Ultra-high-definition、Real-world |
| 官方代码/数据 | `https://github.com/XuPengyue30/SFRNet`（数据 Data available on request） |
| 本仓库性质 | 作者源码的镜像副本；**不含 UHDMD 数据集**，仅含训练产物 `SFR_best.pth` |

> **溯源边界声明**：本仓库（commit `51f4057`）**仍未包含** `README.md`、`requirements.txt`、训练日志、数据集、以及论文中 Figures 对应的可视化脚本。因此：
> - 凡属论文表述（公式编号 Eq.(1)~Eq.(16)、Algorithm 1/2、Table 1~7、架构命名 $f_1/f_2$）一律标注 `[论文]`，出处精确到页码；
> - 凡属代码实现（超参常量、算子序列、边界处理）一律标注 `[代码]`，出处精确到 `文件:行号`；
> - **二者不一致之处，本文以实测为准并单列对照表**（§6.5 超参的差异性、§8.6 算力指标差异），不做无依据的弥合。
> - 论文 Figure 1~10 为位图，本文不复制图片，仅以文字精确重建其信息内容。

---

## 1. 项目概述

### 1.1 研究背景与应用场合

摩尔纹（Moiré Pattern）是两个空间频率接近的周期性结构叠加采样时，由**差频效应**（beat frequency）生成的低频干涉条纹。在 4K/超高清（UHD）设备普及的当下，数码传感器的彩色滤波阵列（CFA）与屏幕像素栅格发生空间混叠，使屏摄图像普遍携带摩尔纹 `[论文 p.1]`。

本工作的两条应用主线 `[论文 p.1-2]`：

| 应用方向 | 具体场合 | 对算法的诉求 |
|---|---|---|
| **图像复原的前置任务** | 去摩尔纹（demoiréing）的缺陷区域定位、画质评估 | 掩膜边界必须与纹型拓扑对齐 |
| **多媒体取证与安全** | 金融支付、身份核验中区分「真实自然人」与「屏幕翻拍攻击」（Face Anti-Spoofing） | **宁可漏检，不可误报**——把真人照片判成翻拍会导致合法用户被拒 |

第二条主线解释了一个反直觉的实验现象：论文 Table 3 中 HRNet 的 Recall 高达 0.9978 却落败，而 SFRNet 以 Precision 0.6286（全场最高）+ Recall 0.9942 取胜 `[论文 p.8]`。**在反欺骗场景里 Precision 的优先级高于 Recall**——这是全文方法设计的价值取向锚点。

### 1.2 现有研究的三重缺口 `[论文 p.2-3, §2.3]`

论文在系统综述中指出的缺口，直接构成了本项目的 Problem Statement：

| 缺口 | 具体表现 | 本文对策 |
|---|---|---|
| **数据缺口** | 既有数据集要么**合成**（MoireScape、LCDMoire 用数学叠加生成），要么**面向去摩尔纹而非检测**（TIP2018 仅 256×256、FHDMi 1080p、UHDM 4K 但无像素级掩膜） | 自建 **UHDMD**：首个真实世界 4K 摩尔纹检测基准 |
| **方法缺口** | DeepLab 系列/PSPNet 依赖大下采样率，高频细节不可逆丢失；SegNet/U-Net/HRNet 缺乏局部频率感知机制；InSPyReNet/BiRefNet 为通用物体形状优化 | 限制下采样至 **H/8** ＋ **patch 级 MoE** 局部路由 |
| **部署缺口** | MoireDet 类联合学习方案要求完美配对的干净参考图，且稠密展开机制在 UHD 分辨率下显存爆炸，无法纳入检测基准 | 纯分割范式 + 滑窗推理，**峰值显存 0.92 GB** `[论文 Table 7]` |

### 1.3 UHDMD 数据集规格 `[论文 §4.1.1, Table 1/2, p.7]`

这是论文的首要贡献，也是全部实验的数据基座，规格如下：

| 属性 | 规格 |
|---|---|
| **总量** | 4,625 对图像/掩膜 |
| **划分** | 训练集 4,291 对；测试集 334 对 |
| **分辨率** | 3840 × 2160（4K UHD），训练时随机裁剪为 512 × 512 |
| **真实性** | **真实拍摄（非合成）**，含真实光照干扰与传感器非线性畸变 |
| **标注来源** | 对捕获的「纯摩尔纹图」做二值化（binarize）得到高质量二值掩膜 `[论文 Fig.7]` |

采集设备矩阵 `[论文 Table 1]`：

| 拍摄手机 | 摄像头规格 | 被摄显示屏 | 屏幕分辨率 |
|---|---|---|---|
| OnePlus Ace 5 | 50 MP | Lenovo Legion Y9000P | 2560 × 1600 |
| iPhone 15 Pro Max | 48 MP | MacBook Air | 2880 × 1864 |
| Xiaomi 16 | 50 MP | Lenovo Legion Y7000P | 2560 × 1600 |

采集工艺约束：手机固定于专用稳定支架消除运动模糊；**语音控制快门** + **无线鼠标远程切换显示内容**以避免机械振动，确保图像对的像素级严格对齐 `[论文 p.7]`。

与既有基准的横向对比 `[论文 Table 2]`：

| 数据集 | 分辨率 | 图像数 | UHD | 真实拍摄 | 多样性 |
|---|---|---:|:---:|:---:|:---:|
| MoiréScape | 640 × 480 | 50,000 | ✗ | ✗ | ✓ |
| **UHDMD** | **3840 × 2160** | **4,625** | **✓** | **✓** | **✓** |

> 补充说明：MoiréScape 在本工作中的实际使用量为随机抽取的 **18,000 对训练 / 8,950 对测试** `[论文 §4.1.2]`，而非全量 5 万——这是为平衡两个数据集的评测规模所做的采样。

### 1.4 任务形式化定义

任务为**像素级二分类语义分割**：

$$X \in \mathbb{R}^{3\times H\times W} \xrightarrow{\ \ \mathcal{F}_{\text{SFRNet}}\ \ } Z \in \mathbb{R}^{1\times H\times W} \xrightarrow{\ \sigma(\cdot)\ } P \in [0,1]^{H\times W} \xrightarrow{\ \mathbb{1}[P>0.5]\ } M \in \{0,1\}^{H\times W}$$

其中 $X$ 为摩尔纹污染图像，$Z$ 为网络输出的 logits（**注意：网络末端无 Sigmoid，见 §5.1**），$M$ 为二值缺陷掩膜。评测四指标按 `[论文 Eq.(13)~Eq.(16)]` 定义：

$$\text{IoU}=\frac{TP}{TP+FP+FN},\quad \text{Precision}=\frac{TP}{TP+FP},\quad \text{Recall}=\frac{TP}{TP+FN},\quad F_1=\frac{2\cdot P\cdot R}{P+R}$$

### 1.5 实测基础规格

以下为本次分析通过实机执行获得的数据（输入 $1\times3\times512\times512$，加载 `SFR_best.pth`）：

| 指标 | 实测值 | 论文值 `[Table 7]` | 偏差 |
|---|---|---|---|
| 唯一可训练参数量 | **7,799,872**（7.7999 M） | 7.78 M | +0.26%（同量级，见 §8.6.1） |
| `state_dict` 浮点总数 | 7,840,039（含 32,832 重复存储） | — | 见 §5.7 |
| 非训练缓冲（BN 统计量等） | 7,335 | — | 未计入参数量 |
| 算力 @512×512 | **179.9905 GMACs = 359.98 GFLOPs** | 372.02 G | −3.34%（见 §8.6.2） |
| 算力 @256×256 | 45.1036 GMACs = 90.21 GFLOPs | — | — |
| Conv2d / Linear 模块数 | 263 / 1 | — | 单次前向被调用 269 / 4 次（§5.7） |
| BatchNorm2d 层数 | 39 | — | — |
| ReLU / LeakyReLU / Sigmoid | 183 / 1 / 3 | — | — |
| 最深下采样倍率 | **8×（H/8）** | — | 与 `[论文 §2.2]` 的设计动机一致 |
| 输出分辨率 | 与输入严格同分辨率 | — | 实测 True |

---

## 2. 核心创新点

### 2.1 论文宣称的三项贡献 `[论文 p.2, Contributions]`

| # | 贡献 | 性质 | 本文验证结论 |
|---|---|---|---|
| 1 | **Dataset and Benchmark**：构建首个面向 4K 真实场景的 UHDMD 数据集，填补非合成 UHD 数据空白并建立性能基准 | 数据侧 | 仓库内**未提供数据集**，仅能从论文核验规格（§1.3） |
| 2 | **Innovative Methodology**：提出 SFRNet，由 SAFM（多尺度）＋ FRM（patch 级 MoE）有机组合解决摩尔纹空间非均匀分布 | 方法侧 | **代码实现与论文描述高度一致**，仅专家分支内部结构存在未披露的细化（§2.3） |
| 3 | **Effectiveness and Generalizability**：在 UHDMD 与 MoireScape 双数据集上显著超越 SOTA | 验证侧 | 数值见 §8.2；**但代码无法复现其 F1 列**（§6.5，指标口径错位） |

### 2.2 论文侧的方法创新（相对传统方案的差异定位）

**创新一：双通路互补架构（Dual-pathway）`[论文 §3.1, Fig.2]`**

传统 U-Net/SegNet 为单通路编码-解码。本文显式地将输入同时送入两条功能互补的通路：

- $f_1$：对称残差编码-解码通路，**全局**多尺度语义提取，每层嵌入 SAFM；
- $f_2$：专门的 FRM 旁路，**局部**细粒度频率异常感知。

二者在瓶颈（bottleneck）处相加融合。**这是"全局语义"与"局域频率"的显式解耦**，而非隐式耦合在单一主干中。论文称整体呈**倒金字塔（inverted pyramid）**结构。

**创新二：patch 级 MoE 路由 `[论文 §3.3]`**

传统 MoE（Sparsely-Gated MoE, Shazeer et al.）在**样本级**或**token 级**做专家分配。本文下沉到**图像块级（patch-level）**：把特征图裂变为 4 个重叠子块，由 Gating Network 为**每块独立**选择一条专家分支。其信息论动机是：UHD 摩尔纹的空间非均匀性意味着**同一张图的不同区域需要不同的卷积核尺度**，而单一样本级路由无法表达这种区域差异。

**创新三：面向摩尔纹先验的异构专家核组 `[论文 §3.3.1, p.5]`**

四个专家按摩尔纹的四种形态先验设计：

| 专家 | 论文描述的核型 | 目标纹型 |
|---|---|---|
| $E_1$ | $1\times1$ 卷积 | 平坦区域 / 偏色校准（color cast calibration） |
| $E_2$ | $3\times3$ 卷积 | 密集高频细条纹 |
| $E_3$ | $5\times5$ 卷积 | 更大规模的密集条纹 |
| $E_4$ | 空洞 $3\times3$ 卷积（等价于 $5\times5$ 感受野） | 稀疏低频大波纹 |

**创新四：限制下采样深度 + 跳线连接补偿**

最深仅下采样至 H/8（传统 U-Net 为 H/32），避免 `'high-frequency detail signals 的不可逆损失'`。ASPP 提供弥补损失感受野的多尺度上下文。

### 2.3 代码侧相对论文的未披露细化（**本文新发现**）

论文把四个专家描述为单一卷积（$1\times1$、$3\times3$、$5\times5$、空洞 $3\times3$），但**实际代码每一条分支都是两层卷积** `[FRM.py:19-48]`，且首层为统一的降维卷积：

| 专家 | 代码实际结构 | 有效感受野 | 参数量 | 与论文描述的差异 |
|---|---|---|---:|---|
| `branch_0` | Conv $1\times1$ (3→32) → ReLU → Conv $3\times3$ (32→32) → ReLU | 3 | 9,376 | 论文只提 $1\times1$，实为 $1\times1$ 瓶颈 + $3\times3$ |
| `branch_1` | Conv $3\times3$ (3→16) → ReLU → Conv $3\times3$ (16→32) → ReLU | 5 | 5,088 | 论文只提 $3\times3$，实为 3→16→32 瓶颈 |
| `branch_2` | Conv $3\times3$ (3→16) → ReLU → Conv $5\times5$ (16→32) → ReLU | 7 | 13,280 | 论文只提 $5\times5$，实为 3→16→32 瓶颈 |
| `branch_3` | Conv $3\times3$ (3→16) → ReLU → Conv $3\times3$ **dil=2** (16→32) → ReLU | 7（稀疏） | 5,088 | 论文只提空洞 $3\times3$，实为 3→16→32 瓶颈 |

三点结论：

1. **论文的四种核型在代码中均被保留为各分支的"辨识性第二层"**，因此设计意图未被违背；
2. 新增的 3→16 首层瓶颈是**显存优化**：若直接在 $288\times288$ 块上做 3→32 的 $5\times5$ 卷积，中间激活与权重开销会显著上升；
3. 由此产生的副作用是：**四个分支的感受野实际为 3 / 5 / 7 / 7(稀疏)，而非论文暗含的 1 / 3 / 5 / 5**。这意味着 $E_1$ 的"纯 $1\times1$ 偏色校准"能力在代码中已被 $3\times3$ 破坏——$E_1$ 实际能捕捉邻域结构，理论上**损失了对极端平坦区域的纯粹点运算建模能力**。建议论文修订时明确注明该两层结构，或在消融中补充"单核 vs 双核专家"的对照。

### 2.4 STE 硬路由的可微化 `[论文 §3.3.1, Eq.(7)]`

$\arg\max$ 不可导。论文引用 Bengio et al. (2013) 的 Straight-Through Estimator（STE）实现端到端训练。代码以一种**非标准变体**落地，详见 §4.3.4 的完整推导——这是全仓库技术含量最高、也最容易出错的 8 行。

### 2.5 SAFM：跨尺度动态融合 `[论文 §3.2, Fig.3, Algorithm 1]`

三支路金字塔（$k\in\{1,2,4\}$）+ 稠密空洞特征提取 $\mathcal{H}(\cdot)$ + 空间对齐 $\mathcal{U}_k(\cdot)$ + GAP 通道描述子 + 三层 $1\times1$ 卷积的跨尺度注意力 $M(\cdot)$ + 全局残差。核心区别于 SE 模块之处在于：**它被嵌在编码器和解码器的每一个层级，而非仅在瓶颈**，形成全网络尺度重标定。

---
## 3. 整体技术架构与流程图拆解

### 3.1 拓扑总览 `[论文 Fig.2 / 代码 sfrnet.py:135-193]`

论文 Fig.2 给出倒金字塔双通路结构，代码与之严格对应。以下为完整拓扑（含实测张量尺寸，输入 $512\times512$）：

```
输入 x : [1, 3, 512, 512]
   │
   ├────────────── f2 通路（局部频率）──────────────┐
   │                                                │
   │                            FissionReorganizationModule(FRM)
   │                                                │  Pad(δ=16, reflect) → 四象限裂变
   │                                                │  4 × [1,3,288,288]
   │                                                │  GatingNetwork → argmax → 单专家
   │                                                │  Crop δ → Concat → refine_block
   │                                                ▼
   │                                        frm_out : [1, 32, 512, 512]
   │                                                │  interpolate → [1,32,64,64]
   │                                                │  fusion_conv 32→256
   │                                                ▼
   │                                        fusion_feat : [1, 256, 64, 64]
   │                                                │
   └── f1 通路（全局语义）─────────────────────────── │ ──┐
                                                      │   │
   Stem_Block(3→16,s1)      + SAFM(16)  → c1  [1,16,512,512]   ─┐ skip 3
   ResNet_Block(16→32,s2)   + SAFM(32)  → c2  [1,32,256,256]   ─┐ skip 2
   ResNet_Block(32→64,s2)   + SAFM(64)  → c3  [1,64,128,128]   ─┐ skip 1
   ResNet_Block(64→128,s2)  + SAFM(128) → c4  [1,128,64,64]
                        ASPP(128→256)   → center [1,256,64,64] ←─┘ ⊕ fusion_feat
                                                      │
   Decoder_Block([256,64]→128)  gate=center, skip=c3, up×2 → d1 [1,128,128,128]
   Decoder_Block([128,32]→64)   gate=d1,     skip=c2, up×2 → d2 [1, 64,256,256]
   Decoder_Block([64,16]→32)    gate=d2,     skip=c1, up×2 → d3 [1, 32,512,512]
                          ASPP(32→16)              → [1, 16,512,512]
                          Conv2d(16→1, k=1)        → logits [1,1,512,512]
```

**与论文 Fig.2 的对应核验**：

| 论文描述 | 代码实现 | 状态 |
|---|---|---|
| 输入同时送入两条功能互补通路 $f_1$、$f_2$ | `sfrnet.py:171` `c1=self.c1(inputs)` 与 `sfrnet.py:179` `frm_out=self.fission_module(inputs)` | ✅ 一致 |
| $f_1$ 基于残差连接的对称编码-解码 | `c1~c4` + `d1~d3` | ✅ 一致 |
| 每层嵌入 SAFM | `Stem_Block.safm`、`ResNet_Block.safm`、`Decoder_Block.safm`（共 10 处） | ✅ 一致 |
| $f_2$ 由 FRM 构成，专注捕获局部频率异常 | `FRM.py:6-138` | ✅ 一致 |
| 两路特征融合后生成最终掩膜 | `sfrnet.py:184` `center = center + fusion_feat`（**相加**而非拼接） | ✅ 一致 |

### 3.2 实测张量流形表

实机前向（hook 注册于全部 Conv2d）记录的关键层级，按执行顺序：

| # | 层级 | 输出形状 | 相对输入步长 | 备注 |
|---:|---|---|---:|---|
| 0 | 输入 $X$ | $[1,3,512,512]$ | 1 | |
| 1 | `c1` (Stem) | $[1,16,512,512]$ | 1 | 唯一保持全分辨率的编码器层 |
| 2 | `c2` | $[1,32,256,256]$ | 2 | 首次下采样 |
| 3 | `c3` | $[1,64,128,128]$ | 4 | skip-3 → d1 |
| 4 | `c4` | $[1,128,64,64]$ | 8 | 编码端最深；**不再下采样** |
| 5 | `b1` (ASPP) | $[1,256,64,64]$ | 8 | 通道数峰值 |
| 6 | `fission_module` | $[1,32,512,512]$ | 1 | $f_2$ 输出，全分辨率 |
| 7 | `fusion_conv` | $[1,256,64,64]$ | 8 | 与瓶颈相加 |
| 8 | `d1` | $[1,128,128,128]$ | 2 | |
| 9 | `d2` | $[1,64,256,256]$ | 1 | |
| 10 | `d3` | $[1,32,512,512]$ | 1 | 分辨率恢复至全尺寸 |
| 11 | `aspp_out` | $[1,16,512,512]$ | 1 | 末端第二次 ASPP |
| 12 | `output` | $[1,1,512,512]$ | 1 | logits，无激活 |

通道数演化：$3 \to 16 \to 32 \to 64 \to 128 \to 256 \to 128 \to 64 \to 32 \to 16 \to 1$，共 13 级跃迁；**输出与输入严格同分辨率**（实测 `True`）。

### 3.3 数据流与控制流的双轨分工

| 维度 | 数据流（Data Flow） | 控制流（Control Flow） |
|---|---|---|
| 载体 | 张量本身（$f_1$ 主干 + 跳线 + $f_2$ 旁路） | `GatingNetwork` 输出的路由 logits |
| 是否可微 | 全程可微 | $\arg\max$ 不可微 → 由 STE 近似（§4.3.4） |
| 输入依赖 | 否（静态计算图结构） | **是**（输入内容决定选中专家） |
| 是否影响输出尺寸 | 是 | 否（四分支输出通道统一为 32） |
| 实际是否动态 | — | **实测为否**，见 §8.4 的退化结论 |

---

## 4. 核心算法超详细原理解析

> 本节每条公式均标注论文编号 `[Eq.(n)]` 与出自页码，并紧接给出其在代码中的精确落点。

### 4.1 编码器：残差块家族

#### 4.1.1 `Stem_Block`（`sfrnet.py:7-26`）

```python
c1: Conv2d(in_c, out_c, 3, stride, 1) → BatchNorm2d → ReLU → Conv2d(out_c, out_c, 3, 1, 1)
c2: Conv2d(in_c, out_c, 1, stride, 0) → BatchNorm2d          # 1×1 升维/降采样捷径
forward: return self.safm(x + s)
```

- 采用**后激活（post-activation）**残差单元；惯例上端会在相加后再 ReLU，此处**不加**，改为送入 SAFM。
- 捷径 `c2` 无 BN 后置非线性，仅 Conv + BN。
- `stride=1`（`sfrnet.py:140`），故 Stem 层是全网络唯一在**全分辨率**上做卷积的编码器层——这也是其算力占比达 7.31% 的原因（§8.5）。
- 注意 `sfrnet.py:25` 有一行被注释掉的 `return (s+x)`，属调试残留。

#### 4.1.2 `ResNet_Block`（`sfrnet.py:28-49`）

```python
c1: BatchNorm2d → ReLU → Conv2d(in,out,3,s,1) → BatchNorm2d → ReLU → Conv2d(out,out,3,1,1)
c2: Conv2d(in,out,1,s,0) → BatchNorm2d
forward: return self.safm(x + s)
```

与 `Stem_Block` 的关键差异是采用**预激活（pre-activation）**顺序（BN-ReLU-Conv），来自 He et al. 2016 的 full pre-activation 变体。理论上预激活使恒等映射路径畅通，梯度可直接回传。此处 `SAFM(inter_num=32)`（注意：Stem 用 16，此处 32）。

#### 4.1.3 `ASPP`（`sfrnet.py:52-62`）

```python
c1..c4: Conv2d(in,out,3, dilation=r, padding=r) → BatchNorm2d,  r ∈ {1,6,12,18}
c5:     Conv2d(out,out,1)
forward: return c5(c1(x)+c2(x)+c3(x)+c4(x))
```

与 DeepLabV3 的 ASPP 对照：

| 维度 | 标准 DeepLabV3 ASPP `[论文 §2.2 引 Chen et al. 2018]` | 本仓库实现 |
|---|---|---|
| 并行分支 | 1×1 + 3 组空洞 3×3 + Image-level GAP 分支 | 4 组空洞 3×3（含 rate=1） |
| 图像级全局池化分支 | 有 | **无** |
| 输出融合 | Concat + 1×1 Conv | **逐元素相加** + 1×1 Conv |
| 归一化后激活 | Conv → BN → ReLU | Conv → BN（**无 ReLU**） |

这是一处实质性简化：相加融合要求四条分支通道数一致且语义可直接叠加，参数量为 $4\times(\text{in}\times\text{out}\times9)$，比 concat 方案少 4 倍后续压缩开销。代价是丢失了 image-level 全局先验。全网络共实例化 2 次（`b1` 用 128→256，`aspp_out` 用 32→16）。

### 4.2 SAFM：多尺度-稠密-注意力三重机制 `[论文 §3.2, p.3-5]`

#### 4.2.1 稠密空洞块 `DB`（`safm.py:5-24`）

这是论文中 $\mathcal{H}(\cdot)$ 的物理实现——**基于空洞稠密连接的特征提取映射**。

```python
forward:
    t = x                                    # in_channel
    for conv_i in conv_layers:               # d_list = (1,2,3,2,1), inter_num通道输出
        _t = conv_i(t)                       # 输入是「当前累积张量」
        t = cat([_t, t], dim=1)              # 通道数 +inter_num
    return conv_post(t)                      # 1×1 压缩回 in_channel
```

通道数递推：$C_0 = C_{\text{in}}$，$C_i = C_{i-1} + n_{\text{inter}}$（$i=1..5$）。设 $C_{\text{in}}=128, n=32$（用于 `c4`），则 $128 \to 160 \to 192 \to 224 \to 256 \to 288$，末层 $1\times1$ 卷积输入 288 通道。

**感受野推导**：各层空洞率 $d_i$ 贡献 $(k-1)\cdot d_i = 2 d_i$ 的感受野增量，叠加后

$$\text{RF}_{\text{DB}} = 1 + 2\sum_{i} d_i = 1 + 2(1+2+3+2+1) = \mathbf{19}$$

论文强调该设计"**effectively enlarge the receptive field**"`[p.4]`。RF=19 恰好覆盖典型中低频摩尔纹条纹周期。

**空洞率序列 $(1,2,3,2,1)$ 的设计含义**：对称沙漏型。若使用标准 $(1,2,4,8,16)$ 指数增长，高空洞率层会出现**网格效应（gridding artifact）**——卷积采样点稀疏且相邻位置不连续，导致局部信息丢失。沙漏形序列使高空洞率层被前后的低空洞率层包裹，采样点在空间上互补叠加，是 DenseASPP / 抗网格效应的常见工程折中。

#### 4.2.2 多尺度金字塔与跨尺度注意力 `[论文 Eq.(1)~Eq.(4), Algorithm 1]`

**Step 1 —— 多尺度提取与对齐 `[Eq.(1)]`**

对输入 $F^{SAFM}_{in}\in\mathbb{R}^{C\times H\times W}$，先双线性下采样生成多尺度输入 $X_k = \mathcal{D}_k(F^{SAFM}_{in})$，$k\in\{1,2,4\}$（$k=1$ 为原尺度），再经 $\mathcal{H}(\cdot)$ 提取后上采样对齐：

$$Z_k = \mathcal{U}_k\big(\mathcal{H}(X_k)\big),\quad k\in\{1,2,4\} \tag{1}$$

代码落点 `safm.py:34-44`：

| 论文符号 | 代码 | 参数细节 |
|---|---|---|
| $X_1, X_2, X_4$ | `x_0, x_2, x_4` | `F.interpolate(x, scale_factor=0.5 / 0.25, mode='bilinear')` |
| $\mathcal{H}(\cdot)$ | `basic_block / basic_block_2 / basic_block_4` | **三条独立的 `DB` 实例**（非权重共享） |
| $\mathcal{U}_k(\cdot)$ | 第 43-44 行 `interpolate(scale_factor=2 / 4)` | bilinear |

> **注**：论文称 $X_k$ 由"bilinear interpolation 下采样"生成 `[p.3]`，代码直接 `interpolate`，一致。但代码在 `interpolate` 处**未指定 `align_corners`**（与网络其余位置统一使用 `align_corners=True` 不一致），详见 §4.2.3。

**Step 2 —— 通道描述子生成 `[Eq.(2)]`**

$$v_k = \frac{1}{H\times W}\sum_{i=1}^{H}\sum_{j=1}^{W} Z_k(i,j) \tag{2}$$

代码 `safm.py:55, 61-63`：`nn.AdaptiveAvgPool2d((1,1))` 作用于 $y_0,y_2,y_4$。二者在数学上完全等价（$H\times W$ 全空间平均池化）。

**Step 3 —— 跨尺度注意力权重 `[Eq.(3)]`**

$$[\mathbf{w}_1, \mathbf{w}_2, \mathbf{w}_4] = \sigma\big(M([v_1, v_2, v_4])\big) \tag{3}$$

其中 $M(\cdot)$ 由**三层 $1\times1$ 卷积 + 两层 ReLU** 构成，末端 Sigmoid 归一化。代码 `safm.py:56-71` 与之**逐算子精确对应**：

| 论文 Algorithm 1 步骤 | 代码行 | 张量形状（设 $C$ 为输入通道） |
|---|---|---|
| 描述符拼接 $V=\text{Concat}(v_1,v_2,v_4)$ | `safm.py:64` | $[B,3C,1,1]$ |
| $V' = \text{ReLU}(\text{Conv}_{1\times1}(V))$ | `safm.py:65-66` | $[B,3C/4,1,1]$（`compress1`，压缩比 `ratio=4`） |
| $V'' = \text{ReLU}(\text{Conv}_{1\times1}(V'))$ | `safm.py:67-68` | $[B,3C/4,1,1]$（`compress2`，维持维度） |
| $W = \sigma(\text{Conv}_{1\times1}(V''))$ | `safm.py:69-70` | $[B,3C,1,1]$（`excitation`） |
| $\mathbf{w}_1,\mathbf{w}_2,\mathbf{w}_4 = \text{Split}(W,3)$ | `safm.py:71` | 各 $[B,C,1,1]$ |

> **这是全仓库中论文与代码吻合度最高的模块**：三层卷积、两层 ReLU、Sigmoid、ratio=4 全部对得上。

**Step 4 —— 加权融合与全局残差 `[Eq.(4)]`**

$$F^{SAFM}_{out} = F^{SAFM}_{in} + \sum_{k\in\{1,2,4\}} \mathbf{w}_k \odot Z_k \tag{4}$$

$\odot$ 为沿空间维度广播的逐通道乘法。代码 `safm.py:72, 47`：`x = x0*w0 + x2*w2 + x4*w4`，随后 `y = x + y`。

关键语义：$\mathbf{w}_k$ 经 Sigmoid 落在 $(0,1)$，因此 $\mathbf{w}_k\odot Z_k$ 是**软门控**而非归一化加权和——三条分支不被强制"分配权重总和为 1"，网络可以让多条分支同时强响应或同时抑制。

#### 4.2.3 工程细节：`align_corners` 一致性问题

`nn.Upsample` 与 `F.interpolate` 的 `align_corners` 参数决定像素坐标是按**角点对齐**还是**半像素对齐**。本仓库现状：

| 位置 | `align_corners` | 出处 |
|---|---|---|
| `safm.py:36-37, 43-44`（金字塔下/上采样） | **未指定 → 默认 False** | `safm.py:36` |
| `ResNet_Block` 之外所有 `Upsample` | `True` | `sfrnet.py:111` |
| `Attention_Block` 尺寸对齐 | `True` | `sfrnet.py:98` |
| `Decoder_Block` 尺寸对齐 | `True` | `sfrnet.py:125` |
| FRM 特征注入瓶颈 | `True` | `sfrnet.py:181` |

结论：**SAFM 内部的 4 次插值是全网络唯一使用 `align_corners=False` 的位置**。由于所有注入的都是双向（先下后上回到同尺寸）的偶数倍率采样，误差被保持在亚像素量级，不影响正确性；但会造成：① PyTorch 会打印隐式告警；② 导出 ONNX/TensorRT 时若未统一该属性可能引入不同框架的语义差异。建议显式写 `align_corners=False` 以消除隐式默认值依赖。

### 4.3 FRM：裂变-重组的完整机理 `[论文 §3.3, p.5-6]`

#### 4.3.1 空间裂变的几何学 `[Eq.(5) / FRM.py:103-116]`

论文定义裂变算子 $\mathcal{S}(\cdot)$：

$$\mathcal{P} = \{P_{i,j} \mid i,j \in \{1,2\}\} = \mathcal{S}\big(\text{Pad}(F^{FRM}_{in}, \delta)\big) \tag{5}$$

每个子块尺寸为 $\left(\frac{H}{2}+2\delta\right) \times \left(\frac{W}{2}+2\delta\right)$，$\delta$ 为重叠像素宽度。

代码实现（`FRM.py:105-118`）：

```python
B, C, H, W = x.size()
h_mid, w_mid = H // 2, W // 2
p = self.overlap                                  # δ = 16
x_pad = F.pad(x, (p,p,p,p), mode='reflect')       # [B,C,H+2p,W+2p]
tl = x_pad[:, :, 0     : h_mid+2p , 0     : w_mid+2p ]
tr = x_pad[:, :, 0     : h_mid+2p , w_mid : W+2p    ]
bl = x_pad[:, :, h_mid : H+2p     , 0     : w_mid+2p ]
br = x_pad[:, :, h_mid : H+2p     , w_mid : W+2p    ]
```

| 项目 | 状态 |
|---|---|
| 块尺寸（输入 $512$，$\delta=16$） | $[1,3,288,288]$，与 $\frac{H}{2}+2\delta = 256+32 = 288$ 一致 ✅ |
| 填充模式 | 论文未指定 → 代码用 **`reflect` 反射填充**（`FRM.py:110`） |
| 输出尺寸复原 | 裁剪后 tl/tr/bl/br 分别为 $256\times256$，拼接回 $512\times512$ ✅ |

> **reflection padding 的隐含意义**：相比零填充，反射填充在边界处保持了一阶连续性，不会引入"边界暗边"伪影，这对于后续需要检测高频条纹的任务是正确选择。但这属于论文未披露的实现细节。

**重叠带来的面积冗余**（实测量化）：4 个块共处理 $4\times288^2 = 331{,}776$ 像素，而原图仅 $512^2 = 262{,}144$ 像素，

$$\text{冗余率}=\frac{331776-262144}{262144}=26.56\%$$

这即「以 26.56% 的面积冗余换取局域纹型自适应」这一交换的精确定量表述。论文对此的处理是 $\delta\in\{0,8,16,24,32\}$ 的敏感性实验，结论为 $\delta=0$ 时 IoU 跌至 61.78%，其余配置稳定在 61.81%~61.90%，故选 $\delta=16$ `[论文 p.10]`——**即论文自己承认 $\delta$ 只要大于 0 即可，无强敏感**。

#### 4.3.2 四条专家分路的结构与「无 BN」设计 `[FRM.py:19-48]`

四条分支均为：卷积 → ReLU → 卷积 → ReLU，**全程不含 BatchNorm**。这是有意为之还是遗留？

从设计角度看合理：patch 级路由意味着每个 batch 中每条分支只收到被路由到它的那部分样本，**统计量极不稳定**；若再加 BN，未选中分支的 running statistics 会长期停止更新（死专家问题，§8.4），BN 反而成为负担。这一点与论文"专家专业化"的动机相容。

但由此带来一个副作用：FRM 输出未经归一化直接进入 `refine_block`（含 BN），且 `refine_block` 使用 **LeakyReLU(0.2)**（`FRM.py:57`）——生成对抗网络常用的激活而非分割网络的惯例选择，属论文未解释的超参。

#### 4.3.3 门控网络 `GatingNetwork`（`FRM.py:141-163`）

```python
net = Conv2d(in_channels,16,3,padding=1,stride=2) → ReLU
    → Conv2d(16,32,3,padding=1,stride=2)          → ReLU
    → AdaptiveAvgPool2d(1) → Flatten → Linear(32,4)
```

单次前向 $288\times288$ 输入的计算过程：

| 层 | 输出形状 | 参数量 |
|---|---|---|
| Conv $3\times3$, s=2 | $[1,16,144,144]$ | 448 |
| ReLU | — | 0 |
| Conv $3\times3$, s=2 | $[1,32,72,72]$ | 4,640 |
| ReLU | — | 0 |
| AdaptiveAvgPool2d(1) | $[1,32,1,1]$ | 0 |
| Flatten | $[1,32]$ | 0 |
| Linear(32→4) | $[1,4]$ | 132 |
| **合计** | | **5,220** |

> **关键结构缺陷（本文判定）**：`AdaptiveAvgPool2d(1)` 把 $72\times72$ 的空间信息压缩为单点均值。而判别一个 patch 属于"平坦 / 细密条纹 / 大型条纹 / 稀疏大波纹"**本质上依赖空间频率统计量**——均值 pooling 恰好抹掉了频域差异的大部分信息。这会直接削弱 Router 的判别力，是 §8.4 所观测到的路由退化的**结构性成因之一**。详见 §9.2 难点一的改进方案。

#### 4.3.4 硬路由的可微化处理：STE 的完整推导 `[Eq.(6)(7) / FRM.py:60-101]`

论文给出硬路由形式：

$$k = \arg\max\big(\mathcal{G}(P_{i,j})\big) \tag{6}$$
$$\hat{P}_{i,j} = E_k(P_{i,j}),\quad k\in\{1,2,3,4\} \tag{7}$$

并指出 $\arg\max$ 在反向传播中不可导，故采用 STE（Bengio et al., 2013）近似。

**代码实现逐行拆解**（`FRM.py:60-101`）：

```python
B_size = block.shape[0]                                    # ①
logits = self.router(block)                                # ② [B,4]
probs  = F.softmax(logits, dim=1)                          # ③ [B,4]
preds  = torch.argmax(probs, dim=1)                        # ④ [B]   ← 不可导断点
selected_probs = probs[torch.arange(B_size), preds]        # ⑤ [B]
ste_weight = selected_probs - selected_probs.detach() + 1.0# ⑥ [B]   ← 核心技巧
output = torch.zeros(B_size, target_c, out_h, out_w, ...)  # ⑦ 占位
for i in range(4):                                         # ⑧
    idx = (preds == i).nonzero(as_tuple=True)[0]
    if len(idx) > 0:
        sub_output = self.branches[i](block[idx])
        branch_weight = ste_weight[idx].view(-1,1,1,1)
        output[idx] = (sub_output * branch_weight).to(output.dtype)
```

**步骤 ⑥ 的数学本质**：

记 $s = p_{\text{sel}} = \text{softmax}(\ell)_k$，其中 $k=\arg\max$。构造

$$w \;=\; s - \underbrace{\text{sg}(s)}_{\text{stop-gradient}} + 1$$

其中 $\text{sg}(\cdot)$ 为 stop-gradient 算子。于是：

- **前向值**：$w \equiv 1.0$（因为 $s - s + 1 = 1$）→ $\hat{P} = E_k(P)\times 1$，**前向输出严格等于所选专家的原始输出，无任何数值失真**。
- **反向梯度**：$\dfrac{\partial w}{\partial s} = 1$，因此梯度沿 $s$ 回传至 router 参数。

对专家参数 $\theta_{E_k}$ 与 router 参数 $\theta_G$ 分别求导：

$$\frac{\partial \mathcal{L}}{\partial \theta_{E_k}} = \frac{\partial \mathcal{L}}{\partial \hat{P}} \cdot E_k(P) \cdot \frac{\partial w}{\partial w}\Big|_{w=1} \cdot \frac{\partial w}{\partial \theta_{E_k}} + \frac{\partial \mathcal{L}}{\partial \hat{P}} \cdot w \cdot \frac{\partial E_k(P)}{\partial \theta_{E_k}} = \frac{\partial \mathcal{L}}{\partial \hat{P}} \cdot \frac{\partial E_k(P)}{\partial \theta_{E_k}}$$

（第一项为 0，因为 $w$ 对 $\theta_{E_k}$ 无依赖）——即**被选中专家收到未经缩放的标准梯度**。

$$\frac{\partial \mathcal{L}}{\partial \theta_G} = \left\langle \frac{\partial \mathcal{L}}{\partial \hat{P}},\, E_k(P) \right\rangle \cdot \frac{\partial s}{\partial \theta_G}$$

即 **router 收到的梯度 = 「专家输出与上游梯度的内积」乘以「选中类 softmax 概率对 router 参数的梯度」**。

> **这是一个值得注意的、非标准的 STE 变体。** 经典 Bengio STE（以及常用的 `hard - softmax.detach() + softmax` 形式）会让**所有**专家按 softmax 权重获得梯度（软混合梯度），而本实现：
> 1. **只有被选中的专家**获得梯度，其余三条分支梯度**严格为零**（实测验证见 §8.4）；
> 2. router 的更新信号被乘以一个"专家输出 · 上游梯度"的内积项，**量级不可控**——若该内积接近 0 或为负，router 将得不到有效监督信号。
>
> 性质 ① 直接决定了：一旦训练初期 router 偏向某分支，其余分支将永久失去梯度（dead experts），这是一个**自我强化的坍缩机制**。

**剩余问题分析**：步骤 ⑦-⑧ 采用 `torch.zeros` 预分配 + 按 index 写回。这在 batch 内样本被路由到不同专家时是正确的，但引入了 GPU 上的**显式 scatter 写入**，且 `output[idx] = ...` 是不可原地最优的索引赋值。更严重的是——由于设备上无法做真正的动态批处理，四个分支仍需在 PyTorch 层面各自（可能空转）判断一次。

#### 4.3.5 重组与精炼 `[Eq.(8)(9) / FRM.py:126-138]`

论文：

$$F_{raw}=\begin{bmatrix}\text{Crop}(\hat{P}_{1,1}) & \text{Crop}(\hat{P}_{1,2})\\ \text{Crop}(\hat{P}_{2,1}) & \text{Crop}(\hat{P}_{2,2})\end{bmatrix} \tag{8}$$
$$F^{FRM}_{out} = \mathcal{R}(F_{raw}) \tag{9}$$

代码 `FRM.py:127-135`：

```python
tl_out = outputs[0][:, :, p : p+h_mid      , p : p+w_mid      ]
tr_out = outputs[1][:, :, p : p+h_mid      , p : p+(W - w_mid) ]
bl_out = outputs[2][:, :, p : p+(H - h_mid), p : p+w_mid      ]
br_out = outputs[3][:, :, p : p+(H - h_mid), p : p+(W - w_mid) ]
top_row    = torch.cat([tl_out, tr_out], dim=3)
bottom_row = torch.cat([bl_out, br_out], dim=3)
full_map   = torch.cat([top_row, bottom_row], dim=2)
return self.refine_block(full_map)
```

注意代码使用 `W - w_mid` 而非 `w_mid`（当 $W$ 为奇数时二者不等，此时 $w_{mid}=\lfloor W/2\rfloor$，$W-w_{mid}=\lceil W/2\rceil$）——**这是一个正确的奇数尺寸保护措施**，比直接写 $w_{mid}$ 更稳健。

`FRM.py:138` 的 `refine_block` 即论文 Eq.(9) 中 $\mathcal{R}(\cdot)$ 的"Bottleneck Block"，论文称"consists of a set of convolutional layers"，实际为：

```python
Conv2d(32,32,3,padding=1) → BatchNorm2d(32) → LeakyReLU(0.2, inplace=True)
```

> **归属注记**：论文 Fig.4 描述 refine_block 用于"cross-channel fusion and integrated with the $f_1$ pathway"。代码中真正的跨通道适配发生在 FRM **之外**的 `sfrnet.py:152-156`（`fusion_conv`: Conv 32→256 + BN + ReLU），与 $\mathcal{R}$ 是两个独立组件。论文图注将二者合并表述，阅读代码时需加以区分。

### 4.4 解码器：门控注意力与尺寸对齐

#### 4.4.1 `Attention_Block`（空间注意力门，`sfrnet.py:65-101`）

```python
g_conv: BatchNorm2d → ReLU → Conv2d(in_c[0], out_c, 3, 1, 1)   # 处理 Gate（深层低分辨率）
x_conv: BatchNorm2d → ReLU → Conv2d(in_c[1], out_c, 3, 1, 1)   # 处理 Skip（浅层高分辨率）
gc_conv:BatchNorm2d → ReLU → Conv2d(out_c, 1, 1) → Sigmoid     # 生成单通道注意力图
forward: g_feat(上采样对齐) + x_feat → gc_conv → attn ; return x * attn
```

这是标准 **Attention U-Net Gate** 的实现：`g`（深层、语义强、分辨率低）作为"查询"，为 `x`（浅层、细节多、噪声也多）的每个空间位置生成一个 $[0,1]$ 权重，抑制与语义无关的背景响应。

**⚠ 命名冲突警告**：本仓库存在**两个同名但语义完全不同的 `Attention_Block`**：

| 定义位置 | 语义 | 作用域 | 输入参数 |
|---|---|---|---|
| `upstream/sfr/safm.py:52` | **通道**注意力（SE 变体：GAP → 1×1×3 → Split → 逐通道门控） | SAFM 内部 | `(x0, x2, x4)` 三路多尺度特征 |
| `upstream/sfr/sfrnet.py:65` | **空间**注意力门（Attention Gate） | 解码器跳线 | `(g, x)` 门控/跳过特征 |

二者不可互换，也不存在继承关系。阅读与二次开发时须按 import 路径区分。

#### 4.4.2 `Decoder_Block`（`sfrnet.py:104-132`）

```
Attention_Block(g, x) → x_attn            # 用深层特征过滤浅层特征
Upsample(g, ×2, bilinear, align_corners=True) → g_up
尺寸对齐 g_up ↔ x_attn（防奇数 1px 误差）
Concat([g_up, x_attn], dim=1) → ResNet_Block(in_c[0]+in_c[1] → out_c, s=1) → SAFM → d
```

两点值得注意：

1. 先注意力加权 `x`、再上采样 `g`、**再拼接**的顺序，与标准 Attention U-Net 一致；
2. `in_c` 参数语义为 `[Gate_Ch, Skip_Ch]`，代码注释（`sfrnet.py:107-108`）自称"参数我反转了一下以符合直觉，调用时需注意"——实际就是 `[上一级解码器通道, 对应编码器 skip 通道]`。

### 4.5 损失函数与评价指标的数学定义

#### 4.5.1 BCE Loss `[论文 Eq.(10), p.6-7]`

$$\mathcal{L}_{BCE} = -\frac{1}{N}\sum_{i=1}^{N}\big[y_i\log(p_i) + (1-y_i)\log(1-p_i)\big] \tag{10}$$

代码 `train.py:42` 使用 `nn.BCEWithLogitsLoss()`——注意这是 **BCEWithLogits**（内部融合 Sigmoid + BCE，用 log-sum-exp 稳定化），而非论文文字所述的 "Binary Cross Entropy Loss"。二者数值等价但**数值稳定性不同**：`BCEWithLogitsLoss` 通过 $\log(1+\exp(-|x|))$ 避免 $\log(0)$ NaN，是正确选择。

#### 4.5.2 Dice Loss `[论文 Eq.(11)]`

$$\mathcal{L}_{Dice} = 1 - \frac{2\sum_{i=1}^{N} p_i y_i + \epsilon}{\sum_{i=1}^{N} p_i + \sum_{i=1}^{N} y_i + \epsilon} \tag{11}$$

代码 `train.py:50-58` 完全对应，`smooth = 1e-6`（此处 $\epsilon$）。论文称引入 Dice 是为"应对摩尔纹区域与背景之间的严重类别不平衡，优化区域级重叠"。

> **关于 Dice Loss 的一个常被忽略的性质**：式 (11) 分子的分母使用 $\sum p_i + \sum y_i$，对小目标敏感——当目标极小时，单个误判像素会引起较大损失震荡。这是实践中常见的训练不稳定来源，也是为何必须与 BCE 联合使用。

#### 4.5.3 总损失 `[论文 Eq.(12)]`

$$\mathcal{L}_{total} = \lambda\cdot\mathcal{L}_{BCE} + (1-\lambda)\cdot\mathcal{L}_{Dice} \tag{12}$$

论文 §4.3 明言 $\lambda=0.3$ 在所有实验中统一采用 `[p.7]`。代码 `train.py:205` 为 `CombinedLoss(bce_weight=0.3)`，`train.py:60` 返回 `0.3*bce + 0.7*dice`。**✅ 完全一致——这是全仓库中论文与代码吻合的少数超参之一。**

> **⚠ 关键缺失**：式 (12) 中**不含任何 MoE 负载均衡辅助损失（load-balancing auxiliary loss）**。这既非论文遗漏也非代码实现疏漏——论文通篇未提及该机制。而 §4.3.4 的性质 ① 已证明本实现的 STE 会让未选中专家梯度严格为零。**二者叠加，理论上必然导致专家坍缩**。这是本文对该方法的头号技术质疑，实证见 §8.4，整改方案见 §9.2 难点一。

#### 4.5.4 评价指标 `[论文 Eq.(13)~Eq.(16) / metrics.py:40-63]`

| 指标 | 论文定义 | 代码函数 | 平滑项 |
|---|---|---|---|
| IoU | Eq.(13) $\frac{TP}{TP+FP+FN}$ | `jac_score` | `1e-15` |
| Precision | Eq.(14) | `precision` | `1e-15` |
| Recall | Eq.(15) | `recall` | `1e-15` |
| $F_1$ | Eq.(16) $\frac{2PR}{P+R}$ | **`F2`**（`metrics.py:48`） | — |

> **⚠ 指标口径错位（可复现性缺陷）**：`train.py:153` 调用的是 `batch_f1 = F2(target, pred)`，而 `F2` 的公式为
> $$F_\beta = (1+\beta^2)\frac{p\cdot r}{\beta^2 p + r},\quad \beta=2$$
> 打印名却是 `F1`。$\beta=2$ 意味着**召回权重是精度的 4 倍**，数值上必然高于真 $F_1$。**结论：论文 Table 3 报告的 F1-score = 0.8845（UHDMD）无法由本仓库 `train.py` 的验证循环直接复现**——后者打印的是 $F_2$。这是本文发现的最直接影响可复现性的问题。

---

## 5. 算法代码落地实现详解（论文理论 ↔ 代码逐模块对照）

### 5.1 `build_SFR.__init__`：全局配置常量表 `sfrnet.py:136-165`

| 组件 | 定义行 | 实例化参数 | 输出通道 | 说明 |
|---|---|---|---:|---|
| `c1` | `sfrnet.py:140` | `Stem_Block(3, 16, stride=1)` | 16 | 唯一全分辨率编码层 |
| `c2` | `sfrnet.py:141` | `ResNet_Block(16, 32, stride=2)` | 32 | H/2 |
| `c3` | `sfrnet.py:142` | `ResNet_Block(32, 64, stride=2)` | 64 | H/4 |
| `c4` | `sfrnet.py:143` | `ResNet_Block(64, 128, stride=2)` | 128 | H/8（最深） |
| `b1` | `sfrnet.py:146` | `ASPP(128, 256)` | 256 | 瓶颈，空洞率默认 (1,6,12,18) |
| `fission_module` | `sfrnet.py:149` | `FissionReorganizationModule(3, 32)` | 32 | $f_2$，输入为**原始 RGB 图像** |
| `fusion_conv` | `sfrnet.py:152-156` | `Conv 32→256 + BN + ReLU` | 256 | $f_2 \to$ 瓶颈通道适配 |
| `d1` | `sfrnet.py:160` | `Decoder_Block([256, 64], 128)` | 128 | gate=bottleneck, skip=c3 |
| `d2` | `sfrnet.py:161` | `Decoder_Block([128, 32], 64)` | 64 | gate=d1, skip=c2 |
| `d3` | `sfrnet.py:162` | `Decoder_Block([64, 16], 32)` | 32 | gate=d2, skip=c1 |
| `aspp_out` | `sfrnet.py:164` | `ASPP(32, 16)` | 16 | 末端第二次 ASPP |
| `output` | `sfrnet.py:165` | `Conv2d(16, 1, kernel_size=1)` | 1 | **logits，无 Sigmoid** |

> **末端无激活**有两重含义：① 训练时必须用 `BCEWithLogitsLoss`（`train.py:42` 已如此，✅）；② 推理时必须手动 `torch.sigmoid`（`predict.py:75` 已如此，✅）。但 `train.py:104` 的 `predict_sliding_window` 直接累加 logits 再平均，最后才在外部 sigmoid——**这与 `predict.py` 先 sigmoid 后平均不等价**，见 §5.5.4。

### 5.2 模块实例化普查（实机统计）

通过 `named_modules()` 去重统计所得：

| 模块类型 | 数量 | 说明 |
|---|---:|---|
| `Conv2d` | **263** | 唯一模块数；单次前向被**调用 269 次**（差异见下） |
| `Linear` | **1** | 仅 `GatingNetwork.net[6]`；单次前向被调用 **4 次** |
| `BatchNorm2d` | 39 | 集中在 stem/resnet/decoder；FRM 内仅 1 处（`refine_block`） |
| `ReLU` | 183 | 含 `inplace=True` 者 |
| `LeakyReLU` | 1 | `FRM.refine_block`，negative_slope=0.2 |
| `Sigmoid` | 3 | 全部来自三处 `sfrnet.Attention_Block.gc_conv` |
| `Sequential` | 188 | |
| `ModuleList` | 31 | 30 个 `DB.conv_layers` + 1 个 `FRM.branches` |
| `DB`（稠密空洞块） | 30 | 10 个 SAFM × 3 支路 |
| `SAFM` | 10 | Stem 1 + ResNet(c2~c4) 3 + 解码器内 ResNet 3 + Decoder 顶层 3 |
| `Attention_Block` | 13 | **`safm` 版 10 + `sfrnet` 版 3**（同名不同义，§4.4.1） |
| `AdaptiveAvgPool2d` | 11 | 10 个 `safm.Attention_Block.squeeze` + 1 个 `GatingNetwork` |
| `ResNet_Block` | 6 | c2/c3/c4 + 三个 `Decoder_Block.r1` |
| `Decoder_Block` / `ASPP` / `Upsample` | 3 / 2 / 3 | |
| `conv_relu` / `conv` | 150 / 30 | DB 内部的基本卷积单元 |
| 模块总数 | **972** | |

**为何 Conv2d 调用次数 269 > 模块数 263？**

`GatingNetwork` 是 `FRM` 内的一个子模块，但在单次前向中被**依次调用 4 次**（`FRM.py:122-124` 对四个 block 各调用一次 `process_block_hard_routing`）。该子网络含 2 个 Conv2d，因此 $263 + 2\times(4-1) = 269$ ✅；同理 Linear 被调用 $1\times4 = 4$ 次 ✅。

> 这一数字差异是**正确识读 FLOPs 统计的前提**：任何按"模块调用次数"累加的算力统计都会把这 6 次重复调用计入——这是合理的（它们确实消耗算力），但在描述"层数"时必须说明口径。

### 5.3 `build_SFR.forward`：完整执行序拆解 `sfrnet.py:167-193`

| 步 | 代码行 | 操作 | 输出形状 |
|---:|---|---|---|
| 1 | 171 | `c1 = self.c1(inputs)` | $[B,16,H,W]$ |
| 2 | 172 | `c2 = self.c2(c1)` | $[B,32,H/2,W/2]$ |
| 3 | 173 | `c3 = self.c3(c2)` | $[B,64,H/4,W/4]$ |
| 4 | 174 | `c4 = self.c4(c3)` | $[B,128,H/8,W/8]$ |
| 5 | 176 | `center = self.b1(c4)` | $[B,256,H/8,W/8]$ |
| 6 | 179 | `frm_out = self.fission_module(inputs)` | $[B,32,H,W]$ ← **输入为原图，非中间特征** |
| 7 | 181 | `frm_down = interpolate(frm_out, size=center.shape[2:], align_corners=True)` | $[B,32,H/8,W/8]$ |
| 8 | 182 | `fusion_feat = self.fusion_conv(frm_down)` | $[B,256,H/8,W/8]$ |
| 9 | 184 | `center = center + fusion_feat` | $[B,256,H/8,W/8]$ |
| 10 | 186 | `d1 = self.d1(g=center, x=c3)` | $[B,128,H/4,W/4]$ |
| 11 | 187 | `d2 = self.d2(g=d1, x=c2)` | $[B,64,H/2,W/2]$ |
| 12 | 188 | `d3 = self.d3(g=d2, x=c1)` | $[B,32,H,W]$ |
| 13 | 190 | `output = self.aspp_out(d3)` | $[B,16,H,W]$ |
| 14 | 191 | `output = self.output(output)` | $[B,1,H,W]$ |

**第 7 步的一个隐含问题（结构性观察）**：`F.interpolate` 把 $f_2$ 的全分辨率输出**直接下采样 8 倍**后再与瓶颈相加。这意味着 FRM 辛苦提取的**全分辨率局部频率细节在进入瓶颈时已被 8 倍降采样抹除**，保留下来的只有降采样后的低频包络。

这一点值得与论文对照：论文宣称 FRM 用于捕获 "fine-grained local frequency anomalies" `[论文 §3.1]`，但架构层面其输出在融合前就被降到了与编码器瓶颈相同的 H/8 尺度。**"局部细粒度感知"的优势能否传递到最终掩膜，取决于这一步插值是否构成信息瓶颈**。论文的组件消融数据与该观察方向一致——单独加 FRM 时 IoU 仅从 60.29% 提升到 60.55%（+0.26%），低于单独加 SAFM 的 +0.38%，远低于二者组合的 +1.61% `[论文 Table 4]`。建议后续工作尝试在解码器的多个尺度层级注入 $f_2$ 特征，而非仅在 H/8 单点注入。

### 5.4 数据管道实现 `upstream/data_loding.py`

```python
MoireTargetDataset(root_dir, is_train=True, crop_size=256, crop_type='center', transform=None)
```

| 行为 | 条件 | 代码行 | 与论文的对照 |
|---|---|---|---|
| 目录约定 | `root/moire/<name>` 与 `root/target/<name>` **同名配对** | `data_loding.py:20-21` | 与 `[论文 Fig.7]` 的 pair 约定一致 ✅ |
| mask 读取 | `.convert('L')` 单通道 | `data_loding.py:54` | |
| **严格二值化** | `target_img = (target_img > 0.5).float()` | `data_loding.py:75` | `[论文 p.7]` 明言 GT 由"binarized 纯摩尔纹图"生成 ✅ |
| **训练裁剪** | 仅 `is_train=True` 时执行 | `data_loding.py:58` | `[论文 §4.3]` 训练用 512 随机裁剪 ✅ |
| 随机裁剪 | `RandomCrop.get_params` 保证 image 与 mask 使用**同一组 (i,j)** | `data_loding.py:60-64` | ✅ 对齐正确，无掩膜错位风险 |
| 中心裁剪 | `crop_type='center'` 分支 | `data_loding.py:66-68` | 对应 `[论文 Table 5]` 的消融对照组 ✅ |
| **验证不裁剪** | `is_train=False` → 跳过裁剪，保持原图尺寸 | `data_loding.py:57` | 对应 `[论文 Table 7]` 的"full 3840×2160 滑窗推理" ✅ |

由此推出的两条工程约束：

- 训练集实际调用为 `MoireTargetDataset(TRAIN_ROOT, crop_size=512, crop_type='random')`（`train.py:188`）✅ 与 `[论文 §4.3]` 的 "randomly cropped into patches of 512×512" 完全一致；
- 验证集不裁剪 → 样本为原始 4K 尺寸 → batch 内无法 stack（需同尺寸）→ **故 `val_loader` 强制 `batch_size=1`**（`train.py:198`，代码注释亦已注明）。

### 5.5 训练引擎实现 `train.py`

#### 5.5.1 主配置与论文的逐项核验（**核心发现**）

| 超参 | 论文 §4.3 声明 | `train.py` 实际值 | 行号 | 一致性 |
|---|---|---|---|:---:|
| 优化器 | Adam | `torch.optim.Adam` | `train.py:206` | ✅ |
| 初始学习率 | **$2\times10^{-4}$** | **`3\times10^{-5}$`** | `train.py:181` | ❌ **低 6.7 倍** |
| Batch size | **8** | **6** | `train.py:179` | ❌ |
| Epoch 数 | 100 | `NUM_EPOCHS = 100` | `train.py:180` | ✅ |
| 训练裁剪 | 512×512 随机 | `crop_size=512, crop_type='random'` | `train.py:188` | ✅ |
| 损失权重 $\lambda$ | 0.3 | `CombinedLoss(bce_weight=0.3)` | `train.py:205` | ✅ |
| LR 调度 | IoU 连续 5 epoch 未提升 → ×0.5 | `ReduceLROnPlateau(optimizer,'max',patience=5,factor=0.5)` | `train.py:207` | ✅ |
| 调度监控量 | IoU（jaccard） | `scheduler.step(val_jaccard)` | `train.py:242` | ✅ |
| FRM 重叠 $\delta$ | 16 | `self.overlap = 16` | `FRM.py:11` | ✅ |
| 混合精度 | 未声明 | `torch.amp.autocast` + `GradScaler` | `train.py:24, 208` | ➕ 补充实现 |
| 显卡 | **2× RTX 3090** | 未指定多卡，单卡 `cuda` | `train.py:183` | ⚠ 无 DDP/DP 策略 |
| `num_workers` | 未声明 | 训练 8 / 验证 4 | `train.py:182, 199` | ➕ |
| 保存策略 | 未声明 | **每 epoch 均存**，另存 `best` | `train.py:230-240` | ➕ |

> **可复现性结论（重要）**：本仓库代码**无法直接复现论文实验结果**。学习率差 6.7 倍、批大小差 2 张/批，均属影响收敛轨迹的一阶超参。又因 `train.py` 每 epoch 覆写同名文件且仅保留一份 `best`，我们无法判定 `SFR_best.pth` 究竟产自哪一组超参。详见 §8.6.3。

#### 5.5.2 训练步（AMP 混合精度）`train.py:13-35`

```python
with autocast(device_type='cuda:1', enabled=(device.type == 'cuda')):   # ← 缺陷
    output = model(moire)
    loss = criterion(output, target)
scaler.scale(loss).backward(); scaler.step(optimizer); scaler.update()
```

**缺陷 #1（高危）**：`autocast` 的 `device_type` 参数**必须是设备类别字符串**（`'cuda'` / `'cpu'` / `'mps'`），**不接受带序号的设备名 `'cuda:1'`**。这样写在 PyTorch ≥ 2.0 下会直接抛出异常。

```python
# 缺陷 #1 修正（train.py:24）
with autocast(device_type=device.type, enabled=(device.type == 'cuda')):
```

其余实现要点是规范的：`optimizer.zero_grad(set_to_none=True)` 减少显存分配，`non_blocking=True` 异步搬运，GradScaler 处理 fp16 梯度缩放。

#### 5.5.3 验证步：条件滑窗 `train.py:117-171`

```python
TILE_SIZE = 512
if moire.shape[2] > TILE_SIZE or moire.shape[3] > TILE_SIZE:
    logits = predict_sliding_window(model, moire, tile_size=512, overlap=0.25)
else:
    logits = model(moire)
```

即 **4K 图像走滑窗（stride = 512×0.75 = 384），小图直接整张推理**。逻辑正确，且小图分支避开了 §5.5.5 的越界缺陷（因为该条件用的是 `>`，维数 $\le 512$ 时走直通路径）。

但存在两个一致性问题：

1. **验证 overlap = 0.25，而推理 `predict.py` 的 overlap = 0.5**（`STRIDE = 256`）。同一模型在两个阶段使用不同的滑窗密度，会造成 **val 指标与实际 test 产出的系统性偏差**——结果是按 val 挑选出的 `best` 权重未必是 test 口径下的最优。
2. `predict_sliding_window` 累加的是 **logits** 并取平均，最后在外部统一 sigmoid；而 `predict.py` 是**先 sigmoid 再累加**（`predict.py:75, 79`）。由于 $\sigma(\cdot)$ 非线性，$\sigma(\mathbb{E}[\ell]) \ne \mathbb{E}[\sigma(\ell)]$，两者在重叠区域的系统性差异不可忽略（通常前者使置信度向两端极化）。

#### 5.5.4 `predict_sliding_window` 的聚合语义 `train.py:73-114`

```python
stride = int(tile_size * (1 - overlap))                 # overlap=0.25 → stride=384
y1 = min(y, h - tile_size); x1 = min(x, w - tile_size)  # ← 越界回退，保证末块贴边
...
full_probs[:, :, y1:y2, x1:x2] += tile_logits           # ← 累加 logits（非概率）
count_map[..., y1:y2, x1:x2] += 1
full_probs /= count_map                                  # 对 logits 求平均
```

- "先 `min(·, h-tile_size)` 再取 $[y_1, y_1+512)$" 是标准的**尾部回退**写法，保证最后一行/列窗口紧贴边界 ✅；
- 循环的 `break` 条件（`x2 == w`）防止重复计算末块 ✅；
- ⚠ 平均对象是 logits，与 `predict.py` 的概率平均不一致（见上）。

#### 5.5.5 推理脚本 `predict.py`

**坐标生成算法（尾部覆盖保障）** `predict.py:46-55`：

```python
def get_coords(full_size, tile_size, stride):
    coords = []; pos = 0
    while pos + tile_size <= full_size:
        coords.append(pos); pos += stride
    if coords[-1] + tile_size < full_size:      # 末块未抵达边界
        coords.append(full_size - tile_size)    # 强制回退贴边
    return coords
```

该算法在两个维度上均完整覆盖，无遗漏区域 ✅。

**缺陷 #2（高危，已仿真复现）**：当输入图像任一维 $< 512$ 时崩溃。根因链条如下：

- `predict.py:42-43`：`full_mask` / `count_map` 按**原始 h×w** 分配；
- `predict.py:62-65`：小图被 zero-pad 到 512，`img_tensor` 尺寸变为 $512\times512$，**但画布尺寸未同步扩大**；
- `predict.py:58-59`：`h < TILE_SIZE` 时 `y_coords = [0]`，切片 `[:, :, 0:512, ...]` 超出 $h$ 维。

实测复现的错误原文：

```
RuntimeError: The size of tensor a (400) must match tensor b (512) at non-singleton dimension 3
```

修正方案：

```python
# 缺陷 #2 修正（predict.py:42-43, 79-83）：画布按 max(原尺寸, TILE_SIZE) 分配，导出前裁回
canvas_h, canvas_w = max(h, TILE_SIZE), max(w, TILE_SIZE)
full_mask = torch.zeros((1, 1, canvas_h, canvas_w), device=DEVICE)
count_map = torch.zeros((1, 1, canvas_h, canvas_w), device=DEVICE)
# ... 滑窗累加 ...
final_prob = full_mask / count_map
final_prob = final_prob[:, :, :h, :w]          # 裁回原始尺寸
mask = (final_prob > 0.5).float()
```

**缺陷 #3（注释与实现不一致）** `predict.py:25`：

```python
STRIDE = 256  # 步长。Tile_size - Stride = 重叠部分（这里重叠64像素，用于平滑边缘）
```

注释称重叠 64 px，实际 $512-256 = 256$ px，即 overlap = 0.5，差 4 倍。

**缺陷 #4（权重路径不一致）**：

| 脚本 | 路径 | 行号 |
|---|---|---|
| 训练保存 | `./checkpoints/SFR/SFR_best.pth`（及各 `SFR_epoch_XXX.pth`） | `train.py:185, 232, 239` |
| 推理读取 | `./checkpoints/ins/SFR_best.pth` | `predict.py:14` |

`predict.py` 直接运行必然 `FileNotFoundError`。此外 `predict.py:15-16` 的 `TEST_DIR='./data/test_data1/moire'` / `SAVE_DIR='./data/test_data/result_INS1'` 与 `train.py:176-177` 的 `./data/train_data` / `./data/test_data` 目录命名亦不一致。

### 5.6 模块复用与命名冲突汇总

| # | 冲突 | 位置 | 影响 |
|---:|---|---|---|
| 1 | **同名 `Attention_Block`**：`safm.py:52`（通道 SE 门控）vs `sfrnet.py:65`（空间注意力门） | 两文件 | 语义完全不同，误调用直接 shape 不匹配 |
| 2 | `DB` 的三份实例 `basic_block/2/4` **不共享权重** | `safm.py:29-31` | 有意设计；但参数量 ×3 |
| 3 | FRM 专家被注册两次：`self.branch_i` 与 `self.branches[i]` 指向同一对象 | `FRM.py:19-51` | `state_dict` 重复存储 32,832 个张量（§5.7） |
| 4 | `safm.Attention_Block.ratio=4` 为硬编码默认值，全网络未调 | `safm.py:53` | 论文未将其作为超参讨论 |
| 5 | `Decoder_Block` 的 `in_c` 语义为 `[Gate_Ch, Skip_Ch]`，代码自注"反转了一下以符合直觉" | `sfrnet.py:107-108` | 易误用 |
| 6 | `combined` / `CombinedLoss` 与 `metrics.py` 中的 `DiceLoss`、`DiceBCELoss` **重复定义且互不调用** | `train.py:39` vs `metrics.py:7,22` | 死代码；后者未被任何训练路径使用 |

### 5.7 权重文件的张量级解析结论

对 `SFR_best.pth`（31,523,921 字节）与 `build_SFR()` 的 `state_dict()` 做双向比对：

| 核验项 | 结果 |
|---|---|
| `load_state_dict(..., strict=False)` 的 `missing_keys` | **0** |
| `unexpected_keys` | **0** |
| 结论 | ✅ 该权重与当前代码架构**严格一一对应**，可支撑本文全部实机结论 |

参数量的三种口径（**最易混淆、也最易被审稿挑出之处**）：

| 口径 | 数值 | 说明 |
|---|---:|---|
| ① `sum(p.numel() for p in model.parameters())` | **7,799,872** | PyTorch `parameters()` 自动去重；**唯一可训练参数** |
| ② `sum(v.numel() for v in state_dict.values())` | **7,840,039** | ❌ 含重复注册虚高 **+32,832** |
| ③ 含非训练缓冲的总存储量 | **7,807,207** | = ① + BN running_mean/var 与 `num_batches_tracked` 共 7,335 |
| 论文 Table 7 声明 | **7.78 M** | 与 ① 相差 +0.26%（详见 §8.6.1） |

**重复注册的确切构成**（实机枚举，共 16 个张量对）：

```
fission_module.branch_0.0.weight   ==  fission_module.branches.0.0.weight   (96)
fission_module.branch_0.0.bias     ==  fission_module.branches.0.0.bias     (32)
fission_module.branch_0.2.weight   ==  fission_module.branches.0.2.weight   (9,216)
fission_module.branch_0.2.bias     ==  fission_module.branches.0.2.bias     (32)
fission_module.branch_1.0.weight   ==  fission_module.branches.1.0.weight   (432)
fission_module.branch_1.0.bias     ==  fission_module.branches.1.0.bias     (16)
fission_module.branch_1.2.weight   ==  fission_module.branches.1.2.weight   (4,608)
fission_module.branch_1.2.bias     ==  fission_module.branches.1.2.bias     (32)
fission_module.branch_2.0.weight   ==  fission_module.branches.2.0.weight   (432)
fission_module.branch_2.0.bias     ==  fission_module.branches.2.0.bias     (16)
fission_module.branch_2.2.weight   ==  fission_module.branches.2.2.weight   (12,800)
fission_module.branch_2.2.bias     ==  fission_module.branches.2.2.bias     (32)
fission_module.branch_3.0.weight   ==  fission_module.branches.3.0.weight   (432)
fission_module.branch_3.0.bias     ==  fission_module.branches.3.0.bias     (16)
fission_module.branch_3.2.weight   ==  fission_module.branches.3.2.weight   (4,608)
fission_module.branch_3.2.bias     ==  fission_module.branches.3.2.bias     (32)
                                                            合计 = 32,832
```

**整改建议**：直接删除 `FRM.py:51` 的 `self.branches = nn.ModuleList([...])` 会导致无法整数索引；正确做法是**只保留 ModuleList**，删除 `branch_0..3` 四个属性，调用处改用 `self.branches[i]`：

```python
# FRM.py 修改建议：唯一注册点
self.branches = nn.ModuleList([branch_0, branch_1, branch_2, branch_3])
# 删除 self.branch_0 = ... 等四行
# 所有 self.branch_i(...) → self.branches[i](...)
```

### 5.8 实测参数与算力分布表

**逐顶层模块参数量**（`named_parameters()` 去重口径）：

| 模块 | 参数量 | 占比 |
|---|---:|---:|
| `d1` | 2,277,905 | 29.20% |
| `c4` | 1,254,816 | 16.09% |
| `b1`（ASPP 瓶颈） | 1,248,512 | 16.01% |
| `d2` | 1,060,721 | 13.60% |
| `c3` | 675,520 | 8.66% |
| `d3` | 640,673 | 8.21% |
| `c2` | 453,648 | 5.82% |
| `c1` | 112,840 | 1.45% |
| **`fission_module`（FRM）** | **47,364** | **0.61%** |
| `aspp_out` | 18,896 | 0.24% |
| `fusion_conv` | 8,960 | 0.11% |
| `output` | 17 | 0.00% |
| **合计** | **7,799,872** | 100.00% |

**按功能域聚合**：

| 功能域 | 参数量 | 参数占比 | MACs | 算力占比 |
|---|---:|---:|---:|---:|
| 解码器 `d1+d2+d3` | 3,979,299 | **51.02%** | 131.3904 G | **73.00%** |
| 编码器 `c1+c2+c3+c4` | 2,496,824 | 32.01% | 34.3435 G | 19.08% |
| ASPP 瓶颈 `b1` | 1,248,512 | 16.01% | 5.1003 G | 2.83% |
| **FRM（`fission_module`）** | 47,364 | **0.61%** | 4.2195 G | **2.34%** |
| 输出头 `aspp_out+fusion_conv+output` | 27,873 | 0.36% | 4.9367 G | 2.74% |

**逐顶层模块算力归属**：

| 模块 | MACs (G) | 占比 |
|---|---:|---:|
| **`d3`** | 77.1039 | **42.84%** |
| `d2` | 34.1626 | 18.98% |
| `d1` | 20.1239 | 11.18% |
| `c2` | 13.3589 | 7.42% |
| `c1` | 13.1491 | 7.31% |
| `c3` | 5.2114 | 2.90% |
| `b1` | 5.1003 | 2.83% |
| `aspp_out` | 4.8989 | 2.72% |
| `fission_module` | 4.2195 | 2.34% |
| `c4` | 2.6241 | 1.46% |
| `fusion_conv` | 0.0336 | 0.02% |
| `output` | 0.0042 | 0.00% |
| **合计** | **179.9905** | 100.00% |

> **最关键的一条算力洞察**：`d3` 仅占 8.21% 参数，却消耗 42.84% 算力；整个解码器占 51.02% 参数、却吃掉 73.00% 算力。成因是分辨率的平方律——**在 $512\times512$ 上做卷积的成本是 $256\times256$ 的 4 倍**。$f_1$ 编码器虽然层数多，但因快速下采样反而便宜。若要优化推理速度（论文 Limitations 已自陈 UHD 实时部署受限 `[论文 p.11]`），**优化重点应放在 `d3` 而非 FRM**：削减 `d3` 一半通道可省约 21% 总算力，而把 FRM 整个移除仅省 2.34%。

---

## 6. 关键参数、阈值与策略说明

### 6.1 训练阶段配置汇总（源自 `train.py:175-210`）

| 参数 | 值 | 行号 | 说明 |
|---|---|---|---|
| `BATCH_SIZE` | 6 | `train.py:179` | ⚠ 论文声明为 8 |
| `NUM_EPOCHS` | 100 | `train.py:180` | ✅ 与论文一致 |
| `LR` | $3\times10^{-5}$ | `train.py:181` | ⚠ 论文声明为 $2\times10^{-4}$ |
| `NUM_WORKERS` | 8（train）/ 4（val） | `train.py:182,199` | |
| `DEVICE` | `'cuda' if available else 'cpu'` | `train.py:183` | ⚠ 论文为 2×RTX 3090，代码无多卡策略 |
| `SAVE_DIR` | `./checkpoints/SFR` | `train.py:185` | ⚠ 与 `predict.py:14` 的 `./checkpoints/ins` 不一致 |
| 训练裁剪 | `crop_size=512, crop_type='random'` | `train.py:188` | ✅ 与论文一致 |
| 验证数据集 | `is_train=False`（不裁剪，原尺寸） | `train.py:190` | |
| 验证 batch | 1（强制） | `train.py:198` | 因验证图尺寸不统一 |
| 损失 | `CombinedLoss(bce_weight=0.3)` | `train.py:205` | ✅ $\lambda=0.3$ 与论文一致 |
| 优化器 | `Adam`（默认 $\beta=(0.9,0.999)$） | `train.py:206` | ✅ |
| 调度器 | `ReduceLROnPlateau(mode='max', patience=5, factor=0.5)` | `train.py:207` | ✅ 监控 `val_jaccard` |
| AMP | `GradScaler(enabled=cuda)` | `train.py:208` | ➕ |
| 二值化阈值 | 0.5 | `train.py:149` | |

### 6.2 推理阶段配置汇总（源自 `predict.py`）

| 参数 | 值 | 行号 | 说明 |
|---|---|---|---|
| `MODEL_PATH` | `./checkpoints/ins/SFR_best.pth` | `predict.py:14` | ❌ 与训练保存路径不一致 |
| `TILE_SIZE` | 512 | `predict.py:24` | ✅ 与训练裁剪尺寸一致 |
| `STRIDE` | 256（即 overlap **0.5**） | `predict.py:25` | ⚠ 注释误写为"重叠 64 px" |
| 画布尺寸 | 原始 `h×w` | `predict.py:42-43` | ❌ 小图崩溃根因（§5.5.5） |
| 概率平均 | 先 sigmoid 再累加取平均 | `predict.py:75,79,83` | ⚠ 与 `train.py` 的平均 logits 不一致 |
| 输出阈值 | 0.5 | `predict.py:85` | ✅ 与验证一致 |
| 导出格式 | 单通道 PNG，`{0,255}` | `predict.py:88-90` | |

### 6.3 架构内嵌常量表

| 常量 | 值 | 出处 | 设计含义 |
|---|---|---|---|
| `d_list`（DB 空洞率） | `(1,2,3,2,1)` | `safm.py:27` | 对称沙漏型，抗网格效应；RF=19，恰好覆盖典型中低频摩尔纹条纹周期 |
| `inter_num`（DB 增长通道） | 16（Stem/Decoder SAFM）/ 32（ResNet SAFM） | `safm.py:20,43,114` | 编码器深层用 32，`c4` 处 DB 内通道峰值为 288 |
| `ratio`（注意力压缩比） | 4 | `safm.py:53` | 与 SE 网络惯例一致；论文未讨论 |
| `overlap` $\delta$ | 16 | `FRM.py:11` | ✅ 与论文 §4.3 声明一致；面积冗余 26.56% |
| 专家输出通道 | 32 | `FRM.py:7` | 四分支统一，保证可拼接 |
| 专家中间通道 | 16（branch_1/2/3 首层输出） | `FRM.py:28,36,44` | 论文未披露的瓶颈设计 |
| Router 卷积通道 | 3→16→32，均 stride=2 | `FRM.py:151-153` | 空间降 4 倍后 GAP |
| Router 输出维度 | 4 | `FRM.py:159` | 对应四专家 |
| LeakyReLU slope | 0.2 | `FRM.py:57` | GAN 风格激活，论文未解释 |
| ASPP 空洞率 | `(1,6,12,18)` | `sfrnet.py:53` | DeepLabV3 标准配置 |
| Dice 平滑项 $\epsilon$ | $10^{-6}$ | `train.py:50` | 论文 Eq.(11) 记作 $\epsilon$ |
| 指标平滑项 | $10^{-15}$ | `metrics.py:42-56` | 防零除 |
| 滑动窗口重叠（验证） | 0.25 → stride 384 | `train.py:141` | ⚠ 与推理的 0.5 不一致 |

### 6.4 关键策略的 rationale 汇总

| 策略 | 解决的物理问题 | 副作用 |
|---|---|---|
| 最深下采样至 **H/8** | 保住高频条纹不被下采样抹除 `[论文 §2.2]` | 感受野受限 → 由 ASPP + DB（RF=19）补偿 |
| **四象限裂变**（overlap $\delta=16$） | 局域纹理统计差异 → 需按块差异化处理 | +26.56% 面积冗余；$GatingNetwork$ 被调用 4 次 |
| 异构专家核 | 平坦/细密/大型/稀疏四类纹型需不同感受野 | 未选中专家梯度为零 → 坍缩风险 |
| **STE 软权重** $s-\text{sg}(s)+1$ | $\arg\max$ 不可导 | 仅选中分支获梯度（§4.3.4） |
| 专家无 BN | patch 级路由下统计量不稳 | 依赖 `refine_block` 的单个 BN 做归一化 |
| 限制 logits 输出（无 Sigmoid） | 数值稳定（`BCEWithLogits`） | 调用方必须自行 sigmoid |
| 验证/推理滑窗 | 4K 显存约束（论文报峰值 0.92 GB） | 两步的 overlap 与聚合语义不一致 |
| $\lambda=0.3$（BCE 权重低于 Dice） | 摩尔纹/背景极端不平衡 → 更依赖区域级约束 | BCE 权重偏低可能弱化像素级精度 |

### 6.5 ■ 论文 ↔ 代码差异总表（可复现性核对清单）

这是本版新增的最重要表格。**凡标 ❌ 者，读者按代码运行将无法得到论文报告的数值。**

| # | 项 | 论文 | 代码 | 严重度 | 影响 |
|---:|---|---|---|:---:|---|
| 1 | 初始学习率 | $2\times10^{-4}$ | $3\times10^{-5}$（`train.py:181`） | ❌ **高** | 差 6.7 倍，直接改变收敛轨迹 |
| 2 | Batch size | 8 | 6（`train.py:179`） | ❌ **高** | 影响 BN 统计与梯度噪声尺度 |
| 3 | F1 指标 | 真 $F_1$（Eq.16） | 实为 $F_2$（`train.py:153` → `metrics.py:48`） | ❌ **高** | Table 3 的 F1 列不可复现 |
| 4 | 权重保存/读取路径 | — | `checkpoints/SFR` vs `checkpoints/ins` | ❌ 高 | `predict.py` 无法直接运行 |
| 5 | `autocast(device_type=)` | — | `'cuda:1'`（`train.py:24`） | ❌ 高 | 参数非法，GPU 训练直接中断 |
| 6 | 专家分支结构 | 单卷积 $1\times1/3\times3/5\times5/$空洞$3\times3$ | 双卷积 3→16→32 瓶颈（`FRM.py:19-48`） | ❌ 中 | 感受野与设计意图偏离，论文未披露 |
| 7 | 参数量 | 7.78 M | 7.7999 M（唯一） | ⚠ 低 | +0.26%，属统计口径差异 |
| 8 | FLOPs @512 | 372.02 G | 359.98 G（实测 conv+linear） | ⚠ 低 | −3.34%，见 §8.6.2 |
| 9 | 验证滑窗 overlap | 未声明 | 0.25（`train.py:141`） | ⚠ 中 | 与推理的 0.5 不一致 |
| 10 | 滑窗聚合对象 | 未声明 | 平均 **logits**（`train.py:104,113`） | ⚠ 中 | 与 `predict.py` 的概率平均不等价 |
| 11 | `align_corners` | 未声明 | SAFM 内为 False，其余 True（§4.2.3） | ⚠ 低 | 亚像素误差，导出时需注意 |
| 12 | 多卡训练 | 2×RTX 3090 | 单卡，无 DDP | ⚠ 中 | 有效 batch size 再次不同 |
| 13 | 数据集 | UHDMD 4,625 对 | **仓库未提供** | — | 需向作者申请 |

---

## 7. 运行机制与工作全流程

### 7.1 单次前向的完整时序（含条件计算分支展开）

以 $B=1,\ H=W=512$、输入 $x$ 为例，粒度到路由的最内层：

```
[阶段 A] f1 编码（静态图）
  A1  Stem_Block: x → conv(3→16,s1) ∥ conv(1×1,s1) → 相加 → SAFM(inter=16)  → c1 [1,16,512,512]
  A2  ResNet_Block s2: BN→ReLU→conv(16→32,s2)→BN→ReLU→conv(32→32) ∥ conv(1×1,s2)+BN
                        → 相加 → SAFM(inter=32)                              → c2 [1,32,256,256]
  A3  ResNet_Block s2: 同上结构 32→64                                         → c3 [1,64,128,128]
  A4  ResNet_Block s2: 同上结构 64→128                                        → c4 [1,128,64,64]
  A5  ASPP: 4 路空洞卷积(rate 1/6/12/18) 相加 → 1×1 conv(256→256)             → center [1,256,64,64]

[阶段 B] f2 裂变-重组（★ 内容依赖的动态区段 ★）
  B1  反射填充 δ=16 → x_pad [1,3,544,544]
  B2  切四块：tl/tr/bl/br 各 [1,3,288,288]
  B3  ── for each block P_i ────────────────────────────────────
      B3.1  logits = GatingNetwork(P_i)                        → [1,4]
      B3.2  probs  = softmax(logits)
      B3.3  k      = argmax(probs)                             ← 不可导断点
      B3.4  s      = probs[k];  w = s - sg(s) + 1              ← STE（前向 w≡1）
      B3.5  out    = branches[k](P_i) * w                      → [1,32,288,288]
      ─────────────────────────────────────────────────────────
  B4  每块裁掉四周 16 px → 各 [1,32,256,256]
  B5  横向拼接两行、纵向拼接两行                                 → full_map [1,32,512,512]
  B6  refine_block: conv(32→32,3) → BN → LeakyReLU(0.2)        → frm_out [1,32,512,512]

[阶段 C] 融合（★ f2 在此被降采样 8 倍 ★）
  C1  interpolate(frm_out → 64×64, bilinear, align_corners=True)
  C2  fusion_conv: conv(32→256,1) → BN → ReLU
  C3  center = center + fusion_feat                            → [1,256,64,64]

[阶段 D] 解码（逐步恢复分辨率）
  D1  Attn(g=center@64, x=c3@128)：g 上采样到 128 → 加和 → 1×1 conv → sigmoid
                                   → attn@128 → x*attn
      up(center)×2 → cat[g_up(256ch), x_attn(64ch)] → [1,320,128,128]
      ResNet_Block(320→128,s1) → SAFM(inter=16)                 → d1 [1,128,128,128]
  D2  同构，gate=d1(128ch)@128, skip=c2(32ch)@256               → d2 [1,64,256,256]
  D3  同构，gate=d2(64ch)@256,  skip=c1(16ch)@512               → d3 [1,32,512,512]

[阶段 E] 输出头
  E1  ASPP(32→16)                                              → [1,16,512,512]
  E2  Conv2d(16→1, 1×1)                                        → logits [1,1,512,512]
```

**动态性判据**：理论上，阶段 B3.5 的 $k$ 由输入内容决定 → 计算图**拓扑可变** → 不同输入应有不同 MACs。实测：随机初始化模型在四类输入下 MACs 为 182.7084 G（有波动，证明动态图生效）；而加载 `SFR_best.pth` 后 MACs **恒为 179.9905 G**（无波动），见 §8.4。

### 7.2 反向传播期间的梯度通路

沿 §7.1 的逆序，区分四条通路：

| 通路 | 是否可微 | 梯度表达 | 备注 |
|---|---|---|---|
| 输出头 → `d3` → `d2` → `d1` → `b1`/`c4` | 是 | 标准链式法则 | 主干通路 |
| `refine_block` ← `full_map` ← 各 Block | 是 | 标准链式法则 | 仅流经被选中分支的输出位置 |
| 被选中专家 $E_k$ 的参数 | 是 | $\frac{\partial\mathcal{L}}{\partial \hat P}\cdot\frac{\partial E_k(P)}{\partial\theta_{E_k}}$ | 见 §4.3.4 推导，权重因子恰为 1 |
| **未选中专家** $E_{j\ne k}$ 的参数 | **否** | **梯度严格为 0** | 实测验证见 §8.4 |
| Router $\mathcal{G}$ 的参数 | 是（经 STE） | $\big\langle \frac{\partial\mathcal{L}}{\partial\hat P}, E_k(P)\big\rangle \cdot \frac{\partial s}{\partial\theta_G}$ | 量级不受控 |
| SAFM 注意力分支 | 是 | 经 `compress1/2 → excitation → Sigmoid` 回传 | 梯度经 Sigmoid 饱和区可能被削弱 |

**实测梯度探针结果**（加载 `SFR_best.pth`，单样本反向后取各参数梯度绝对值均值）：

| 参数组 | 梯度绝对值均值 |
|---|---|
| `fission_module.branch_0.*` | **0.000000** |
| `fission_module.branch_1.*` | 0.000705 |
| `fission_module.branch_2.*` | **0.000000** |
| `fission_module.branch_3.*` | **0.000000** |

三条分支的梯度为**精确的零**，与 §4.3.4 的理论分析完全吻合：**它们是永久性的死参数**。

### 7.3 训练全流程（epoch 级）

```
1. 构建 train_dataset(crop=512, random) 与 val_dataset(原尺寸)
2. DataLoader(train, bs=6, shuffle=True, drop_last=True, workers=8, pin_memory)
   DataLoader(val,   bs=1, shuffle=False, workers=4)
3. for epoch in 1..100:
     3.1 train_one_epoch: 对每个 batch 执行 §7.1 前向 + §7.2 反向，AMP 混合精度
     3.2 validate_one_epoch:
           - 若图 >512 走 predict_sliding_window(overlap=0.25)，否则整图推理
           - 计算 loss / IoU / F2(被打印为 F1) / Recall / Precision
     3.3 打印 epoch 日志
     3.4 无条件保存 checkpoints/SFR/SFR_epoch_{epoch:03d}.pth
     3.5 若 val_jaccard 创历史最高 → 覆盖保存 SFR_best.pth 并打印 "Best model updated."
     3.6 scheduler.step(val_jaccard)   # 5 epoch 无提升则 lr *= 0.5
4. 结束
```

> **工程注意**：第 3.4 步会为每个 epoch 写出约 30 MB 的权重文件，100 epoch 将产生 **约 3 GB** 的中间产物。若磁盘配额受限应改为仅保留最近 N 个。

### 7.4 推理全流程（`predict.py`）

```
1. build_SFR() → load_state_dict(checkpoints/ins/SFR_best.pth) → eval()
2. 遍历 TEST_DIR 下的 png/jpg/jpeg/bmp：
     2.1 PIL 读取 → convert('RGB') → ToTensor → [1,3,H,W]
     2.2 生成 y_coords / x_coords（stride=256，尾部强制回退贴边）
     2.3 若 H 或 W < 512：zero-pad 到 512            ← ⚠ 画布未同步扩大，见 §5.5.5
     2.4 for y in y_coords: for x in x_coords:
             tile = img[:, :, y:y+512, x:x+512]
             logits = model(tile)
             prob = sigmoid(logits)
             full_mask[:, :, y:y+512, x:x+512] += prob
             count_map[..., 同区域] += 1
     2.5 final_prob = full_mask / count_map
     2.6 mask = (final_prob > 0.5).float()
     2.7 → numpy ×255 → uint8 → PIL → 保存同名 PNG
     2.8 torch.cuda.empty_cache()
```

**与论文 Table 7 注解的对照**：论文的效率指标是在 RTX 3090 上对完整 3840×2160 图像使用滑窗策略测得（延迟 3.44 s、峰值显存 0.92 GB）。代码流程与之一致（tile=512、stride=256）。但因仓库**未包含 UHDMD 数据集**，这两个数字暂无法实测复现。

---

## 8. 实验设计、效果原理与优劣分析

> 本节 §8.1 ~ §8.7 为论文原文报告的实验结果（逐表标明出处并忠实转录全部数值）；§8.8 起为本文基于实机验证的独立分析，与论文数据存在分歧之处按 §8.9、§8.10 单独讨论。

### 8.1 实验设计总览 `[论文 §4]`

| 维度 | 配置 |
|---|---|
| 数据集 | **UHDMD**（自建，真实 4K）：4,291 训练 / 334 测试；**MoiréScape**（公开，合成）：18,000 训练 / 8,950 测试 |
| 对比方法 | DeepLabV3+、SegNet、HRNet（经典）；InSPyReNet、BiRefNet（近期 SOTA 显著目标分割） |
| 排除项 | MoireDet 类联合学习方法——因需完美配对干净图且在 UHD 下显存爆炸，无法纳入二分类检测基准 `[论文 §4.4]` |
| 公平性 | 所有基线在**完全相同的实验设置下重训** `[论文 §4.4]` |
| 评测指标 | IoU、Precision、Recall、F1-score（定义见 Eq.13~16） |
| 硬件 | 2 × NVIDIA RTX 3090 |
| 训练超参 | Adam，lr $2\times10^{-4}$，512×512 随机裁剪，batch 8，100 epoch，$\delta=16$，$\lambda=0.3$，IoU 连续 5 epoch 未提升则 lr×0.5 |

### 8.2 定量结果 `[论文 Table 3, p.9]`

**UHDMD（真实世界 4K 数据集）**

| 方法 | IoU ↑ | F1-score ↑ | Recall ↑ | Precision ↑ |
|---|---:|---:|---:|---:|
| BiRefNet (Zheng et al., 2024) | 0.4862 | 0.6432 | 0.7373 | 0.6783 |
| InSPyReNet (Kim et al., 2022) | 0.5450 | 0.7796 | 0.8463 | 0.6159 |
| DeepLabV3+ (Chen et al., 2018) | 0.6013 | 0.8641 | 0.9795 | 0.6189 |
| HRNet (Wang et al., 2021) | 0.6046 | 0.8643 | **0.9978** | 0.6154 |
| SegNet (Badrinarayanan et al., 2017) | 0.6054 | 0.8736 | 0.9856 | 0.6170 |
| **SFRNet (Ours)** | **0.6190** | **0.8845** | 0.9942 | **0.6286** |

**MoiréScape（合成数据集）**

| 方法 | IoU ↑ | F1-score ↑ | Recall ↑ | Precision ↑ |
|---|---:|---:|---:|---:|
| BiRefNet | 0.6397 | 0.7069 | 0.6759 | 0.9261 |
| InSPyReNet | 0.6246 | 0.7276 | 0.7136 | 0.8409 |
| DeepLabV3+ | 0.8527 | 0.9656 | 0.9997 | 0.8528 |
| HRNet | 0.8526 | 0.9658 | **1.0000** | 0.8526 |
| SegNet | 0.9233 | 0.9718 | 0.9799 | 0.9410 |
| **SFRNet (Ours)** | **0.9338** | **0.9768** | 0.9845 | **0.9477** |

**结果解读（论文口径）**：

1. **SFRNet 在两个数据集上均取得最高 IoU / F1 / Precision**，且 IoU 提升幅度在 UHDMD 上为 +1.36%（相对 SegNet 0.6054 → 0.6190），在 MoiréScape 上为 +1.05%（相对 SegNet 0.9233 → 0.9338）。
2. **HRNet 的 Recall 最高（0.9978 / 1.0000）却 Precision 最低（0.6154 / 0.8526）**，论文据此论证：通用模型存在严重过分割，把复杂背景纹理误判为摩尔纹 `[论文 p.8]`。这与其全程保持高分辨率分支、缺乏频率判别机制的架构特性相吻合。
3. **InSPyReNet / BiRefNet 在该任务上表现不佳**（UHDMD IoU 仅 0.5450 / 0.4862），原因是它们针对通用物体语义与形状优化，无法定位 4K 图像中的高频、空间非均匀伪影。
4. 论文特别强调：**虽然 IoU 的绝对提升数值不大，但 Precision 的显著提升直接减少了误报**，而在反欺骗这类真实任务中"系统无法承受把真实图像误判为翻拍" `[论文 p.8]`。这是把 Motivation 与实验结果闭环的关键论证。

> **本文的补充观察**：UHDMD 上全场 Precision 普遍偏低（0.6154~0.6286），而 Recall 普遍极高（0.7373~0.9978），说明**该数据集的正样本（摩尔纹像素）占比偏高**，而非典型的"小目标"场景。这意味着 Dice Loss 的类不平衡动机在本数据集上并非主要矛盾——论文对 Dice 的引入理由在 UHDMD 语境下值得进一步讨论。

### 8.3 定性结果 `[论文 Fig.8, p.9；Fig.10, p.10]`

论文的视觉结论（本文以文字重建）：

| 现象对象 | 论文描述 |
|---|---|
| BiRefNet / InSPyReNet | 难以捕捉精细的高频重复结构，产生高度扭曲或碎片化的掩膜，**丢失波纹的拓扑完整性** |
| DeepLabV3+ / SegNet | 倾向于生成**过度平滑**的掩膜 |
| HRNet | 因过拟合复杂背景纹理而产生**严重误报** |
| **SFRNet** | 具备出色的尺度鲁棒性与边缘保真度，能同时保留"大尺度颜色畸变"与"细密高频波纹"的清晰拓扑边界 |

**特征可视化（Fig.10）的机制解释**：对比移除 SAFM+FRM 前后的中间特征激活——无这两个模块时，激活弥散，背景区域出现大面积假激活（红/黄）；加入后，高激活区域**严格对齐摩尔纹条纹的结构布局**，背景响应被显著抑制（深蓝）。论文以此作为"双模块设计有效解耦复杂摩尔纹频率与背景细节"的直接证据。

### 8.4 消融 A：组件有效性 `[论文 Table 4, p.9]`

基线定义为从 SFRNet 中移除 SAFM 与 FRM 的普通网络。

| SAFM | FRM | IoU (%) | F1 (%) | Recall (%) | Precision (%) | 相对基线 ΔIoU |
|:---:|:---:|---:|---:|---:|---:|---:|
| ✗ | ✗ | 60.29 | 86.23 | 96.19 | 61.80 | — |
| ✗ | ✓ | 60.55 | 86.62 | 96.83 | 61.74 | **+0.26** |
| ✓ | ✗ | 60.67 | 86.52 | 96.55 | 61.96 | **+0.38** |
| ✓ | ✓ | **61.90** | **88.45** | **99.42** | **62.86** | **+1.61** |

**结论**：单独添加任一模块的增益均很小（+0.26% / +0.38%），而组合后增益达 +1.61%，**显著大于两者之和（+0.64%）** ——这是典型的**超可加性（super-additivity）**，论文据此论证 SAFM 与 FRM 互补：SAFM 提供多尺度特征，FRM 精修局域细节。

> **本文的定量补充**：超可加性的倍率为 $1.61 / 0.64 = 2.52\times$。结合 §5.3 的观察（FRM 输出在注入前被降采样 8 倍），一个合理的机理解释是：**SAFM 提供的多尺度、去噪后的特征表达，为 FRM 的局部路由提供了更有判别力的输入上下文**——反之亦然。因此二者存在正向耦合，而非简单叠加。

### 8.5 消融 B：裁剪策略 `[论文 Table 5, p.9]`

| 策略 | IoU (%) | F1 (%) | Recall (%) | Precision (%) |
|---|---:|---:|---:|---:|
| Center Crop | 60.93 | 87.36 | 98.02 | 61.64 |
| **Random Crop** | **61.90** | **88.45** | **99.42** | **62.86** |
| 增益 | +0.97 | +1.09 | +1.40 | +1.22 |

**机理**：中心裁剪训练的模型存在强**中心偏置（center bias）**，容易遗漏位于图像边缘区域的摩尔纹伪影（Fig.9 用红框标出失败案例）；随机裁剪赋予网络**平移不变性**。

代码侧支持：`data_loding.py:59-68` 同时实现了 `random` 与 `center` 两个分支，`train.py:188` 使用 random ✅ 与论文默认设置一致；`train.py:189` 还有一行被注释掉的 `crop_type='random'` 写法。

### 8.6 消融 C：结构超参 `[论文 Table 6, p.11；δ 敏感性见 p.10]`

| 模块 | 变体 | IoU (%) | F1 (%) | Recall (%) | Precision (%) |
|---|---|---:|---:|---:|---:|
| SAFM 尺度分支 | 2 分支（1×, 2×） | 60.81 | 86.78 | 96.94 | 61.97 |
| | **3 分支（1×, 2×, 4×）· Ours** | **61.90** | **88.45** | **99.42** | **62.86** |
| FRM 专家数 | 1 专家（等价无路由的标准卷积） | 60.48 | 86.20 | 96.05 | 61.92 |
| | 2 专家 | 61.04 | 87.45 | 98.12 | 61.73 |
| | **4 专家 · Ours** | **61.90** | **88.45** | **99.42** | **62.86** |
| | 8 专家 | 61.51 | 88.14 | 99.14 | 61.82 |

**重叠参数 $\delta$ 的敏感性**（论文 p.10 文字数据）：

| $\delta$ | 0 | 8 | 16（默认） | 24 | 32 |
|---|---:|---:|---:|---:|---:|
| IoU (%) | 61.78 | 61.81 | **61.90** | 61.8x | 61.8x |

论文结论：$\delta=0$ 因边界拼接误差导致轻微下降（61.78%），超出最小需求后（8/16/24/32）性能稳定在 61.81%~61.90%，故选 $\delta=16$ 兼顾边界质量与计算量。

**三点方法论解读**：

1. **4× 下采样分支不可替代**：去掉该分支（3→2 分支）掉 1.09% IoU，证明大尺度色偏（large-scale color ripples）确实需要粗尺度分支承载。
2. **专家数存在最优值而非单调递增**：1→2→4 单调上升（60.48→61.04→61.90），但 8 专家反降至 61.51%。论文归因于"过多专家导致路由冗余、对背景噪声过拟合，并显著增加计算开销"。这是 MoE 领域典型的**专家容量 vs 负载分散**权衡。
3. **⚠ 与本文实测的张力（必须指出）**：Table 6 显示 1 专家 IoU = 60.48%，4 专家 = 61.90%，差 **1.42%**。若 4 专家模型在推理时 router 完全坍缩到单一专家，其有效容量就退化为 1 专家模型，**不可能**取得 61.90% 而应回到 60.48% 附近。然而本文在 `SFR_best.pth` 上实测到的却是 100% 的单一分支占用（§8.10）。这两种可能性将在 §8.10 中详细辨析。

### 8.7 计算效率 `[论文 Table 7, p.11]`

在 RTX 3090 上对**完整 3840×2160 图像**使用滑窗策略测得；FLOPs 测于 512×512 输入。

| 方法 | Params (M) ↓ | FLOPs (G) ↓ | Latency (s) ↓ | Memory (GB) ↓ |
|---|---:|---:|---:|---:|
| DeepLabV3+ | 59.23 | 177.07 | 0.63 | 0.52 |
| SegNet | 29.44 | 321.05 | 0.97 | 0.77 |
| HRNet | 28.54 | **82.38** | 1.53 | **0.32** |
| InSPyReNet | 90.52 | 378.80 | 2.94 | 1.20 |
| BiRefNet | 214.96 | 604.77 | 4.48 | 1.57 |
| **SFRNet (Ours)** | **7.78** | 372.02 | 3.44 | 0.92 |

**横向定位**：SFRNet 的**参数量为全场最低的 7.78 M**，比第二名 HRNet（28.54 M）少 73%，比 BiRefNet（214.96 M）少 96%。这是其核心工程优势。

但需客观指出三项劣势：

- **FLOPs 偏高**：372.02 G 仅低于 InSPyReNet 与 BiRefNet，高于 DeepLabV3+（177.07 G）与 HRNet（82.38 G）——**参数效率与计算效率在此处是背离的**；
- **延迟劣势更明显**：3.44 s / 张 4K 图，仅优于 BiRefNet（4.48 s）与 InSPyReNet（2.94 s 反比它更快），远慢于 DeepLabV3+（0.63 s）。原因见 §8.11 的算力分布：SFRNet 把 73% 的计算压在高分辨率的解码器上，而 FLOPs 相近的模型（如 SegNet 321 G）计算分布更均匀。
- 论文自陈这是 **Limitations**：UHD 图像的海量像素带来的高计算复杂度，制约了资源受限边缘设备上的实时部署 `[论文 p.11]`。

### 8.8 效果归因：为什么这些方法奏效

| 设计 | 对应的摩尔纹物理性质 | 效果链路 |
|---|---|---|
| 限制下采样至 H/8 | 条纹周期可与像素同阶 | 保住高频 → 边界不糊 → IoU/Precision 提升 |
| SAFM 三分支金字塔 | 同一图中粗细并存 | 按尺度自适应加权 $$\to$$ 突出该图当前主导尺度 $$\to$$ 同时覆盖色偏与细纹 |
| DB 稠密空洞（RF=19） | 条纹有中低频周期结构 | 在不降分辨率前提下扩大感受野 |
| FRM patch 级路由 | 空间强非均匀 | 每块用最适配的核 → 减少"一刀切"带来的误报 |
| 注意力解码门 | 背景纹理与摩尔纹外观相似 | 用深层语义抑制浅层噪声 → Precision 提升 |
| BCE + Dice（$\lambda=0.3$） | 正负样本不平衡 + 需区域级约束 | 兼顾像素精度与区域重叠 |

### 8.9 ■ 实测值 vs 论文值的三处差异

| 指标 | 论文 | 本文实测 | 偏差 | 判定 |
|---|---:|---:|---:|---|
| 参数量 | 7.78 M | 7.7999 M（去重可训练） | +0.26% | ✅ 一致（不同统计工具的小数位差异） |
| FLOPs @512 | 372.02 G | 359.98 G（conv+linear，MACs×2）<br>360.65 G（宽口径，含 BN/激活/池化） | −3.34% | ⚠ 详见下方分析 |
| 各硬件 Proposed 延迟 / 显存 | 3.44 s / 0.92 GB | 无法测（无 GPU、无数据集） | — | 未验证 |

**关于 FLOPs 12.04 G 差额的排查**（本文已排除的假设）：

| 假设 | 量级 | 是否成立 |
|---|---:|:---:|
| BN 仿射项（39 层） | 0.14 G | 过小，不足以解释 |
| 激活函数（187 个） | 0.27 G | 过小 |
| 自适应池化读入元素（0.112 G 元素） | ~0.11 G | 过小 |
| 双线性插值（47 次调用，0.123 G 元素） | 0.98 ~ 1.72 G | 不足以解释 |
| 上述全部相加 | ≈ 360.65 G | 仍差 11.37 G |
| FRM 四专家全算（dense MoE）而非仅算选中分支 | +4.6 G | 不足以解释 |
| FRM 的 `ModuleList` 重复注册被计数两次 | +3.6 G | 不足以解释 |

**结论**：即使把上述所有可能的宽口径因素全部叠加（约 +8.4 G），仍无法完全弥合 12.04 G 的差额。最可能的解释是**论文 Table 7 所用模型配置与当前仓库代码存在细微差异**（例如某处 `inter_num` 或通道宽度不同），或使用了不同版本的算子核(@ counting convention)。**建议作者用固定工具（如 `ptflops` + 明确版本号）重新测量并在论文中注明工具与版本**——这是可复现性的基本要求。

### 8.10 ■ 关键发现：`SFR_best.pth` 中 Router 已退化为单一专家

这是本次分析最重要的负面发现。**三项互相独立的实机证据**：

**证据 1 —— 路由决策直方图完全坍缩**

方法：构造 40 张 $512\times512$ 的**类真实分布输入**（高斯平滑随机场模拟自然图像低频结构 ＋ 随机方向与随机频率的正弦条纹模拟摩尔纹，频率采样于 $[0.02, 0.37]$，幅度 $[0.1,0.5]$），逐张经 FRM 的 pad-裂变流程取得 4 个 block，分别送入训练好的 `GatingNetwork`，统计 softmax 后 $\arg\max$ 的分布。

| 专家 | 决策次数 | 占比 |
|---|---:|---:|
| `branch_0` | 0 | 0.00% |
| **`branch_1`** | **160** | **100.00%** |
| `branch_2` | 0 | 0.00% |
| `branch_3` | 0 | 0.00% |

且**平均最大后验概率仅 0.2797**（均匀分布基线为 0.25）——即 Router 对自身的选择几乎不自信，只是在均匀噪声上取了一个微弱的优势项。

分位置统计（TL / TR / BL / BR 四个象限）同样**全部 40/40 落在 `branch_1`**，说明这不是某类特定位置模式的偏好，而是全局坍缩。

**证据 2 —— 未选中专家梯度严格为零**

单样本反向传播后测量各分支参数的梯度绝对值均值（§7.2 已列）：`branch_0/2/3` 为 **0.000000**，`branch_1` 为 **0.000705**。三条分支是**永久死参数**。

**证据 3 —— 动态计算图实际退化为静态图**

| 模型状态 | 四类不同输入下的总 MACs |
|---|---|
| 随机初始化 | 182.7084 G（**有波动** → 动态路由生效） |
| 加载 `SFR_best.pth` | 179.9905 G（**恒定不变** → 路由固定） |

条件计算本应带来的"输入自适应算力节省"，在实际训练产物中**完全没有兑现**。

**█ 与论文 Table 6 的张力辨析（重要的方法论说明）**

这里存在一个表面矛盾，必须诚实处理：

- Table 6 显示 **1 专家 60.48% vs 4 专家 61.90%**，相差 1.42%。若 router 完全坍缩，4 专家模型应退化为 1 专家模型水准。
- 因此只能是以下三种情况之一：
  1. **本文的探针输入属于分布外（OOD）**：真实 UHDMD 4K  crops 在这些 OnePlus/iPhone 传感器 + 特定显示器组合下产生的纹理统计，未必能被"高斯随机场 + 正弦条纹"充分模拟。这是**本文倾向的首要可能性**；
  2. **`SFR_best.pth` 与产出 Table 3/6 的 checkpoint 不同**：考虑到 §5.5.1 已证实代码的 lr / batch 与论文声明不符，`SFR_best.pth` 可能是**按另一组超参训练、或从某个中间 epoch 保存**的产物；
  3. 坍缩确实存在于最终模型，而 Table 6 的 4 专家行来自 router 尚未坍缩的训练阶段。

**无论哪种情况，判定都成立**：代码层面**不存在任何负载均衡机制**（ loss Eq.(12) 只有 BCE+Dice），而 §4.3.4 已证明本实现的 STE 会让未选中专家**梯度严格为零**。这两个条件叠加，使坍缩成为**理论上必然的吸引态** ——区别只在于它来得早还是晚。因此这是一个**方法层面的结构性风险**，而非某个权重文件的偶然现象。

**建议的确证动作**（成本极低）：在训练循环中每 epoch 打印一次四个专家的分配直方图。若坍缩发生在早期，直方图会以肉眼可见的方式单调收敛到一个 bin。这比"跑完 100 epoch 才发现"要经济得多。

### 8.11 算力分布实测与分析

数据见 §5.8。三点结论：

1. **解码器是绝对瓶颈**：51.02% 参数 → 73.00% 算力；其中 `d3` 独占 42.84%。
2. **`c4` 的尴尬位置**：它占 16.09% 参数（编码器中最高）却只占 1.46% 算力——因为它在 H/8 上运算。**参数量与算力在此严重脱钩**，任何"按参数剪枝"的策略都会误伤 `c4` 而不是真正的热点 `d3`。
3. **FRM 的性价比**：0.61% 参数换来 2.34% 算力开销，若仅算三个死专家分支的实际无效开销，则为 0（它们根本不被调用）。**这也从侧面说明：当前 FRM 的实际贡献完全来自 `branch_1` 一条 3→16→32 的 $3\times3$ 瓶颈卷积加上 `refine_block`** ——请结合 §8.10 一并理解 Table 4 中 "+0.26%" 这个偏小的 FRM 单模块增益。

### 8.12 源码实现层面的缺陷清单（汇总）

| # | 严重度 | 缺陷 | 位置 | 已实机验证 |
|---:|:---:|---|---|:---:|
| 1 | 🔴 高 | `autocast(device_type='cuda:1')` 参数非法 | `train.py:24` | 静态判定 |
| 2 | 🔴 高 | 图像任一维 <512 时崩溃 | `predict.py:42-43,79-80` | ✅ 复现报错原文 |
| 3 | 🔴 高 | 初始 lr 与论文不符（$3\mathrm{e}{-5}$ vs $2\mathrm{e}{-4}$） | `train.py:181` | 对照确认 |
| 4 | 🔴 高 | batch size 与论文不符（6 vs 8） | `train.py:179` | 对照确认 |
| 5 | 🔴 高 | 指标口径错位：`F2` 被当作 `F1` 打印 | `train.py:153` + `metrics.py:48` | 静态确认 |
| 6 | 🟠 中 | 权重路径不一致（存 `checkpoints/SFR` / 读 `checkpoints/ins`） | `train.py:185` vs `predict.py:14` | 对照确认 |
| 7 | 🟠 中 | FRM 专家重复注册，`state_dict` 虚高 32,832 | `FRM.py:19-51` | ✅ 张量级枚举 |
| 8 | 🟠 中 | 归一化路径不一致：`CombinedLoss` 与 `metrics.DiceLoss/DiceBCELoss` 重复定义且后者为死代码 | `train.py:39` vs `metrics.py:7,22` | 静态确认 |
| 9 | 🟠 中 | 验证/推理滑窗 overlap 不一致（0.25 vs 0.5） | `train.py:141` vs `predict.py:25` | 对照确认 |
| 10 | 🟠 中 | 滑窗聚合对象不一致（平均 logits vs 平均概率） | `train.py:103,113` vs `predict.py:75,79` | 静态确认 |
| 11 | 🟠 中 | 同名 `Attention_Block` 语义冲突 | `safm.py:52` vs `sfrnet.py:65` | 静态确认 |
| 12 | 🟡 低 | 注释称重叠 64 px，实为 256 px | `predict.py:25` | 静态确认 |
| 13 | 🟡 低 | SAFM 插值未指定 `align_corners`，与全网 True 不一致 | `safm.py:36,37,43,44` | 静态确认 |
| 14 | 🟡 低 | 每 epoch 无条件保存权重（100 epoch ≈ 3 GB） | `train.py:230-233` | 静态确认 |
| 15 | 🟡 低 | Router 末端 `AdaptiveAvgPool2d(1)` 抹除判别所需的频率信息 | `FRM.py:156` | ✅ 结构分析 + 实测坍缩 |

### 8.13 综合优劣判评

**优势**

| 维度 | 评价 |
|---|---|
| 参数效率 | ★★★★★ 7.80 M 为同基准全场最低，比次优模型少 73% |
| 问题-设计对齐度 | ★★★★☆ 限制下采样、异构核、patch 路由、次高注意力门均直接对应摩尔纹物理特性 |
| 与论文吻合度 | ★★★★☆ 除超参与指标口径外，架构、公式、$f_1/f_2$ 双通路描述均严格对应 |
| 数据贡献 | ★★★★★ 自建首个真实 4K 摩尔纹检测基准，填补领域空白 |

**劣势**

| 维度 | 评价 | 依据 |
|---|---|---|
| 条件计算的有效性 | ★☆☆☆☆ | Router 坍缩至单一专家，理论上的自适应算力优势完全未兑现（§8.10） |
| 可复现性 | ★★☆☆☆ | lr / batch / F1 口径 / 权重路径四处不一致，代码无法直接复现论文结果（§6.5） |
| 计算效率 | ★★☆☆☆ | FLOPs 372 G、4K 延迟 3.44 s，劣于多数对比方法（§8.7） |
| 工程成熟度 | ★★☆☆☆ | 15 项缺陷，含 2 项致命崩溃与 3 项可复现性阻断（§8.12） |
| 特征注入位置 | ★★★☆☆ | $f_2$ 全分辨率特征在进入瓶颈前被降采样 8 倍，疑为信息瓶颈（§5.3） |

---

## 9. 项目总结与技术难点复盘

### 9.1 项目总结

SFRNet 是一篇"**问题驱动清晰、理论自洽、工程可复核**"的工作：

- **数据侧**，UHDMD 填补了真实世界 4K 摩尔纹检测基准的空白，其采集工艺（三手机 × 三显示器、专用支架、语音快门、远程切换）体现出严谨的实验控制意识；
- **方法侧**，双通路设计把"全局语义"与"局域频率"显式解耦，SAFM 负责 cross-scale 重标定，FRM 负责 patch 级差分处理，二者在消融中表现出 2.52× 的超可加性；
- **效率侧**，7.80 M 参数在所有对比方法中最低，滑窗策略把 4K 推理峰值显存压到 0.92 GB。

从本文的代码核验视角看，**成就最为扎实的部分是 SAFM**——它与论文 Eq.(1)~Eq.(4) 及 Algorithm 1 逐算子精确对应，是可以直接照着复现的模块。**风险最为集中的部分是 FRM 的 MoE 路由**——论文未设计负载均衡机制，代码实现的 STE 变体又使未选中专家梯度恒为零，二者叠加使专家坍缩成为理论上的必然吸引态（§8.10）。此外 **Repository 与论文之间的可复现性存在 5 处断裂**（§6.5），其中学习率、批大小、F1 口径三项会直接阻断结果复现。

### 9.2 技术难点复盘与整改方案

#### 难点一 ■ 硬路由的可微化与专家坍缩（**首要难点**）

**难点本质**：需要"离散决策 + 端到端可训练"这一对矛盾属性。本文用 STE 近似解决了可微性，却引入了负载均衡的新问题。

**根因拆解（三层）**：

1. **梯度层**：STE 权重 $w = s - \text{sg}(s) + 1$ 使未选中专家梯度严格为零 → 一旦某分支暂时落后，它将永久失去修复机会（**自我强化的马太效应**）；
2. **监督层**：总损失 Eq.(12) 只有 BCE + Dice，**无任何针对路由分布的约束项**，网络没有"必须分散使用专家"的动力；
3. **表征层**：`GatingNetwork` 末端 `AdaptiveAvgPool2d(1)`（`FRM.py:156`）把 $72\times72$ 空间压缩为单点均值，而判别四个纹型类别本质上依赖**空间频率统计量**——均值池化恰好抹掉了最有判别力的那部分信息。

**整改方案（按优先级）**：

**① 加入负载均衡辅助损失（最高优先级）**

```python
# FRM.forward 中收集 batch 内所有 block 的 probs，然后：
def load_balance_loss(probs_all, num_experts=4):
    """
    probs_all: [B*4, 4]  所有 block 的 softmax 概率
    基于 Switch Transformer 的 auxiliary loss：
    L_aux = N * sum_k f_k * P_k ，其中
      f_k = 实际被分配到专家 k 的 token 比例
      P_k = router 对专家 k 的平均路由概率
    该损失在 f 与 P 都为均匀分布时取最小值 1.0
    """
    N = probs_all.size(0)
    # f_k：实际被硬分配到专家 k 的 block 比例（统计量，不参与求导）
    idx = probs_all.argmax(dim=1)
    f = torch.bincount(idx, minlength=num_experts).float() / N
    P = probs_all.mean(dim=0)               # P_k：router 的平均路由概率（可导）
    return N * (f.detach() * P).sum()       # 均匀分布时取最小值，故需最小化该损失

# train.py 中：
# with autocast 内部额外返回 probs，然后
# loss = criterion(output, target) + alpha * load_balance_loss(probs)   # alpha ~ 1e-2
```

**② 软化路由（Top-2 / 噪声扰动）**

- **Top-2 路由**：保留概率最大的两个专家，按 softmax 权重加权求和。这保证**每个 block 至少有两条路径获得梯度**，是打破坍缩最直接的办法；
- **Router noise**：在 logits 上加入可学习的噪声项 `noise = randn_like(logits) * softplus(w_noise)`（这是 Shazeer 原始稀疏门控的做法），鼓励探索。

**③ 改进 Router 的表征能力**

```python
# FRM.py GatingNetwork 改进建议：把 AdaptiveAvgPool2d(1) 换成统计量拼接
class GatingNetwork(nn.Module):
    def forward(self, x):
        h = self.backbone(x)                       # [B, 32, 72, 72]（去掉池化）
        gap = h.mean(dim=(2, 3))                   # 均值统计量
        gmp = h.amax(dim=(2, 3))                   # 峰值统计量（对条纹对比度敏感）
        std = h.flatten(2).std(dim=2)              # 方差统计量
        return self.head(torch.cat([gap, gmp, std], dim=1))
```

理由：`std` 与 `gmp` 直接编码了 patch 内部的**对比度起伏强度**，而这正是区分"平坦区 / 密集条纹 / 稀疏大波"的核心线索——均值 pooling 对此完全不敏感。

**④ 训练期监控（成本最低，务必先做）**

```python
# 每个 epoch 打印一次分配直方图，坍缩会肉眼可见地单调收敛到一个 bin
print('route hist:', torch.bincount(preds_all, minlength=4).tolist(),
      '| mean max prob:', probs_all.max(dim=1).values.mean().item())
```

#### 难点二 $f_2$ 全分辨率局部特征的信息瓶颈

**现象**：`sfrnet.py:181` 把 FRM 的 $[B,32,H,W]$ 输出直接插值到 $H/8$ 才与瓶颈相加。

**分析**：FRM 的设计初衷是捕获"fine-grained local frequency anomalies"，但这些信息在注入点被 8 倍降采样，只剩下低频包络。这与 Table 4 中 FRM 单独增益仅 +0.26% 的现象方向一致。

**整改**：把 $f_2$ 特征在**多个尺度**上分别下采样并注入对应解码层（`d1/d2/d3` 各一次），而非单次注入瓶颈。代价是参数增加约 $3\times$`fusion_conv` 的量级（约 27 K），相对 7.8 M 可忽略。

#### 难点三 空洞卷积的网格效应与算力代价的权衡

`DB` 采用 $d=(1,2,3,2,1)$ 的对称沙漏序列而非标准的 $(1,2,4,8,\ldots)$，是为了抑制网格效应——高空洞率层被前后的低空洞率层包裹，采样点互补。代价是 DB 内部通道数线性增长至 $C+5n$（`c4` 处达 288），使 $1\times1$ 压缩卷积的参数与算力显著上升。

若要做更强的尺度覆盖，建议优先尝试**可变形卷积**或**显式频域分支（FFT 幅度谱）**，而非继续提高空洞率。

#### 难点四 数据域约束

- **UHDMD 未开源**（Data available on request），导致全部实验结论无法被第三方独立复现；
- 论文的 Limitations 亦自陈：数据构建依赖劳动密集的人工拍摄与像素级标注，限制了快速扩展 `[论文 p.11]`；
- GT 由"binarize 捕获的纯摩尔纹图"生成（`data_loding.py:75` 的硬阈值 0.5 与之呼应），该阈值的选取缺乏灵敏度讨论——阈值变动会直接改变所有指标数值。

#### 难点五 工程成熟度

15 项缺陷见 §8.12。建议的最小整改序列：

1. 修 `train.py:24` 的 `device_type`；
2. 统一 `checkpoints` 路径至一处常量（建议抽到配置文件）；
3. 修正 `predict.py:42-43` 的画布分配；
4. 把 `metrics.py` 的 `F2` 重命名或补一个真 `F1`，让 `train.py` 打印名与计算式一致；
5. 把 lr / batch 对齐到论文值（$2\times10^{-4}$ / 8），或反过来在 README 中明确声明当前值与论文的偏离及原因。

```python
# 缺陷 #1 修正（train.py:24）
with autocast(device_type=device.type, enabled=(device.type == 'cuda')):

# 缺陷 #2 修正（predict.py:42-43）
canvas_h, canvas_w = max(h, TILE_SIZE), max(w, TILE_SIZE)
full_mask = torch.zeros((1, 1, canvas_h, canvas_w), device=DEVICE)
count_map = torch.zeros((1, 1, canvas_h, canvas_w), device=DEVICE)
# ... 滑窗累加后 ...
final_prob = full_mask / count_map
final_prob = final_prob[:, :, :h, :w]      # 裁回原始尺寸
```

### 9.3 一句话结论

> **SFRNet 用 0.61% 的参数增量（7.80 M 总参数，全场最低）换来了 UHDMD 上最高的 Precision 与 IoU，其 SAFM 模块是"论文 ↔ 代码"零误差对齐的范例；但 FRM 的 patch 级 MoE 在缺少负载均衡约束、且使用梯度严格截断型 STE 的条件下，在该仓库分发的 `SFR_best.pth` 中退化为单专家——这使得论文最核心的方法论创新在工程落地层面尚未真正兑现。补齐负载均衡损失与训练期路由监控，是释放该方法全部潜力的最短路径。**

---

## 附录 A：复现实验的最小流程

```bash
# 环境（本文实机验证所用）
python 3.14.2 (build E:/python/python.exe) ；torch 2.12.0（CPU 模式验证，无 CUDA）

# 数据目录约定（需自备；论文称 Data available on request）
./data/train_data/moire/<name>.png
./data/train_data/target/<name>.png        # 同名配对，单通道
./data/test_data/moire/<name>.png
./data/test_data/target/<name>.png

# 推理前必须完成的三项修正（否则必然失败）
#   1) predict.py:14   权重路径 → ./checkpoints/SFR/SFR_best.pth
#   2) predict.py:42-43 画布尺寸 → max(h, TILE_SIZE), max(w, TILE_SIZE)
#   3) 若要多卡/AMP，train.py:24 的 device_type 改为 device.type

python train.py       # 训练，产物落在 ./checkpoints/SFR/
python predict.py     # 推理，结果落在 ./data/test_data/result_INS1/
```

## 附录 B：快速核验代码段

```python
import torch, collections
from utils.sfr.sfrnet import build_SFR

m = build_SFR()

# ① 唯一参数量（必须去重共享张量）
seen, uniq = set(), 0
for p in m.parameters():
    if p.data_ptr() not in seen:
        seen.add(p.data_ptr()); uniq += p.numel()
print('unique trainable params:', uniq)          # 7,799,872

# ② state_dict 的三种口径差异
sd = m.state_dict()
print('state_dict sum       :', sum(v.numel() for v in sd.values()))   # 7,840,039
print('duplicate tally      :', sum(v.numel() for v in sd.values()) - uniq)  # 32,832

# ③ 算子级 MACs（@512x512）
import torch.nn as nn
macs = [0]
hooks = [mod.register_forward_hook(
    lambda mo, i, o: macs.__setitem__(0, macs[0] + o.numel() * mo.in_channels
                                      // mo.groups * mo.kernel_size[0] * mo.kernel_size[1]))
    for mod in m.modules() if isinstance(mod, nn.Conv2d)]
with torch.no_grad():
    m(torch.randn(1, 3, 512, 512))
[h.remove() for h in hooks]
print('MACs: %.4f G -> %.2f GFLOPs' % (macs[0] / 1e9, 2 * macs[0] / 1e9))
# 179.9905 G -> 359.98 GFLOPs

# ④ Router 坍缩检测：跑一批输入，统计专家分配直方图
fm = m.fission_module
import torch.nn.functional as F
img = torch.rand(1, 3, 512, 512)
B, C, H, W = img.shape; hm, wm, p = H // 2, W // 2, fm.overlap
xp = F.pad(img, (p, p, p, p), mode='reflect')
blocks = [xp[:, :, 0:hm+2*p, 0:wm+2*p], xp[:, :, 0:hm+2*p, wm:W+2*p],
          xp[:, :, hm:H+2*p, 0:wm+2*p], xp[:, :, hm:H+2*p, wm:W+2*p]]
lg = torch.cat([fm.router(b) for b in blocks], 0)
print('decisions :', lg.softmax(1).argmax(1).tolist())
print('max prob  :', lg.softmax(1).max(1).values.tolist())
# 期望（健康模型）：四个类别均有出现；实测（SFR_best.pth）：全为 1 且概率≈0.28
```

## 附录 C：参考文献清单（源：论文 References, p.11-12）

论文共引用 46 篇文献。与本技术分析直接相关的关键条目：

| 文献 | 在本工作中的角色 |
|---|---|
| Bengio, Léonard & Courville (2013). *Estimating or propagating gradients through stochastic neurons for conditional computation*. arXiv:1308.3432 | **STE 的理论出处**，FRM 可微化的依据 |
| Masoudnia & Ebrahimpour (2014). *Mixture of experts: A literature survey*. Artif. Intell. Rev. 42(2) | patch 级 MoE 机制的方法论引用 |
| Chen et al. (2018). *Encoder-decoder with atrous separable convolution* (DeepLabV3+). ECCV | ASPP 原型 + 对比基线 |
| Ronneberger et al. (2015). U-Net. MICCAI / Badrinarayanan et al. (2017). SegNet. TPAMI | 编码-解码结构参照 + 对比基线 |
| Wang et al. (2021). HRNet. TPAMI | 高分辨率保持策略的对照组 |
| Kim et al. (2022). InSPyReNet. ACCV / Zheng et al. (2024). BiRefNet. CAAI AIR | 近期 SOTA 显著目标分割基线 |
| Yang et al. (2023). *Doing more with moiré pattern detection in digital photos*. TIP | MoireDet 与 MoiréScape 数据集出处；同时是被排除的对比方法 |
| He et al. (2020). FHDe$^2$Net (FHDMi). ECCV / Sun et al. (2018). TIP2018 / Yu et al. (2022). UHDM. ECCV | 既有去摩尔纹数据集，构成 UHDMD 的对照坐标 |
| Milletari et al. (2016). V-Net. 3DV | Dice Loss 出处 |
| Long et al. (2015). FCN. CVPR / Xie & Tu (2017). HED. IJCV | BCE / 像素级监督出处 |
| Yeung et al. (2022). Unified Focal Loss. CMIG | 复合损失函数设计的一般性支持 |




