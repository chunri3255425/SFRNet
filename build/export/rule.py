"""把 CPU 上串行的摩尔纹判别规则编译成 NPU 图（零训练、零参数、权重全部解析生成）。

产物：`build/models/rule_v5.onnx`（开发机 onnxruntime 用）与 `rule_v5.rknn`（板端 NPU 用）。
两者由同一份 `spec()` 生成，判据常量只有一个真源（本文件顶部）。

用法
----
    python build/export/rule.py onnx  --out build/models/rule_v5.onnx
    python build/export/rule.py rknn  --onnx build/models/rule_v5.onnx \
        --out build/models/rule_v5.rknn --target rk3576
    python build/export/rule.py ops   --onnx build/models/rule_v5.onnx    # 只看算子落点

为什么图长这个样子（三条都是实测结论，不是设计偏好）
----------------------------------------------------
1. **可分离 DCT 用两个 1×1 Conv 表达**。ONNX 里没有 DCT 算子，NPU 也只肯跑卷积。
   把图像的行当成通道轴 —— 输入张量取 (1, C=512, H=1, W=512)，一个 1×1 Conv 就是在
   通道轴上左乘一个 512×512 矩阵；配一次 Transpose 再乘一次，得到 Z = A·x·B，
   即"先加窗后做 DCT"的二维可分离变换。代价：两个 512³ 的 Conv（268 M MAC/片）。
2. **中间量必须全部落进 fp16 可表示范围**。toolkit2 2.3.2 的 float_dtype 只接受
   float16/bfloat16/tfloat32；后两个在本模型上报 `unsupported tensor dtype in Mul/Add`
   后进程直接段错误（exit=139），fp32 则压根不给选。而真图带内峰功率跨
   5.4e-5..1.16e7（11 个数量级），fp16 上限 65504、可用动态范围只有约 1e9：
   **任何固定尺度都无解**（把最大值压进去，有用的小值就掉进非规格化区）。
   所以图内做自动增益：g = 全带峰 + eps，两个子带都除以 g，于是一路 ≤ 1。
   峰/均值这类比值本身尺度不变，判据一个字没改，只是阈值换算到了归一化域。
3. **不算倒数**。`Div(常数, 数据)` 会被 toolkit 折成 Reciprocal 并扔到 CPU（实测）。
   门因此开在反方向上：`cr1 > L  ⟺  ρ/cr1 < ρ/L`，用 `Sub(lo, x) → Div(常数) → Clip`。
   顺带把内盘/环带比也写成 pkl/pkh 而不是 pkh/pkl。

三道门（都是软与，线性过渡段宽度 2×）
------------------------------------
  p1 环带单峰度  ρ·meanh/g  → 挡宽带噪声（合成自检里 noise 的 cr1=21）
  p2 内盘/环带峰 pkl/g      → 挡锐边与截图（edges 的 cr2=0.93）
  p3 绝对带内峰 g-AMP       → 挡"什么都没有"：AGC 之后全平/全暗块会因 0/0 兜底
                              变成 crest 拉满，实机遮镜头、纯黑帧就是这种输入
常量由 494 张 / 50731 块标定（**in-sample**，负样本是数字截图而非实拍无屏）：
  C=0.01 整图二分类 92.71%，召回 88.0%，误报 0.0%；把两道结构门关掉可到 94.94%，
  但那时 C=0 误报 100%，判据就退化成"高频能量超阈"，不再是周期性判据——所以宁可少 2 个点。
  另：把片均分口径换成引擎实际的 Hann 融合+像素覆盖率口径，同一组常量是 91.70%。

编译结果（实测）：50 个计算算子、CPU 落点 0 个；周期估算 DDR 521,449 + NPU 531,632。
板端实测单片中位 7.54 ms / 165 片全帧 1840 ms（4K）；rknn 2.89 MB（权重 2 张
512×512 fp16 占大头）。

算子融合已试到哪里为止（三项否证 + 一项采纳，全部板上实测）：
  - 采纳：「先乘掩码再平方」→「先平方再乘掩码」（dct²·m ≡ (dct·m)²），52→50 算子，
    全帧 1982→1840 ms（0.928×）。单项 N×N 逐元素算子实价约 0.43 ms/片，比按
    「元素-算子对 0.59 ns」估的 0.15 ms 贵得多。
  - 否证：GlobalMaxPool 两级池化 → ReduceMax(axes=[1,2,3])。算子数 52→34，但
    toolkit 把两个 ReduceMax 都扔到 CPU（`cpu: ['7 ReduceMax','21 ReduceMax']`），
    全帧 1657→3738 ms（2.26× 慢）。**两级池化 + 换轴不是没想到 ReduceMax，
    是只有这么干才能落 NPU**。
  - 否证：Transpose → Reshape。池化前的换轴输入是 (1,N,1,1)/(1,1,1,N)，H=W=1，
    转置退化成形状改写，理论上零拷贝；但 toolkit 又归并回 Transpose，算子数不变。
  - 否证：MatMul 替 Conv+Transpose。转换后算子表/周期与 Conv 版一模一样（MatMul
    被认成 1×1 Conv），却多出 6.6e-06 的数值差，无收益只有风险。
  - 否证：pwu = pwh + pwl（bun 与两子带不重叠，恒等成立，可再省 1 个 N×N 算子）。
    中位 0.982× 但全帧轮间极差跑到 473 ms（27%），长尾不稳定，不采纳。
  - 封死：Div → Mul(1/g)。`Div(常数, 数据)` 会被折成 Reciprocal 并落 CPU（早期实测）。
所以剩下的大头不在算子数，而在长尾（均值−中位 ≈ 4.2 ms/片，占全帧 1/3）。
"""

from __future__ import annotations

import argparse
import contextlib
import os
import re
import sys
import tempfile
from collections import Counter
from pathlib import Path

import numpy as np
import onnx
from onnx import TensorProto, helper, numpy_helper

N = 512                                   # 图定死的块边长；也是 runtime 的准入条件
Z_SHIFT = 8                               # Z 整体 ÷2^8，两侧权重各 ÷2^4（2 的幂，fp16 精确无舍入）
EPS_G = 1e-3                              # 增益分母兜底：取成规格化数，防 fp16 把 1e-6 冲成 0 后 0/0

F_LO, F_SPLIT, F_HI = 0.05, 0.20, 0.46    # 归一化频率：内盘 [0.05,0.20)，环带 [0.20,0.46]
CR1_LO, CR1_HI = 250.0, 500.0             # p1 环带单峰度阈（过渡段 [LO,HI]）
CR2_LO, CR2_HI = 3.0, 6.0                 # p2 内盘/环带峰比阈
AMP_RAW = 64.0                            # p3 绝对门：0-255 灰度域的带内峰功率下限
RAMP = 2.0                                # 过渡段宽度 = LO 的 1/RAMP（与标定脚本同式）

# 算子表行：`| 0  Conv FLOAT16 NPU ...` —— (.*)$ 必须配 re.M，否则只匹配最后一行，
# 解析失败会被伪装成"没有 CPU 算子"（这个坑踩过一次，见 opdump 的教训）。
ROW = re.compile(r"\] (\d+) +([A-Za-z0-9_]+) +([A-Z0-9_]+) +(NPU|CPU) +(\S.*?)$", re.M)


# =============================================================================
# 解析生成的权重与阈值
# =============================================================================

def dct_matrix(n: int = N) -> np.ndarray:
    """归一化 DCT-II 矩阵 D：Z = D·x 对一维信号。row0 是 DC，系数差 √(1/2)。"""
    j = np.arange(n)[None, :]
    k = np.arange(n)[:, None]
    D = np.cos(np.pi * (2 * j + 1) * k / (2.0 * n))
    D *= np.sqrt(2.0 / n)
    D[0] *= 1 / np.sqrt(2.0)
    return D.astype(np.float32)


def band(n: int, lo: float, hi: float) -> np.ndarray:
    """径向频带掩码（周期性信号的量尺是 |k|，不是 k）。"""
    a = np.arange(n, dtype=np.float32)
    r = np.hypot(a[:, None], a[None, :]) / (2.0 * n)
    return (r >= lo) & (r <= hi)


def weights() -> tuple[np.ndarray, np.ndarray]:
    """两个 1×1 Conv 的等效矩阵，已含 2^-4 尺度；与 spec() 完全同源。

    A = C·diag(w) 作用在行轴，B = diag(w)·Cᵀ 作用在列轴。Aᵀ = B 让整个图在**数学上**
    对输入转置不变（float64 参考与 onnx 实跑都复算过，同一片 prob 差 0）。但板端
    toolkit 的 NHWC 输入重排下实测**可区分**（同一片 prob 0.0 → 0.5），所以喂法仍
    必须与开发机一致，不能拿“反正不变”去省那次转置（BOARD_BRINGUP §20.4）。
    """
    C = dct_matrix(N)
    w = np.hanning(N).astype(np.float32)
    s = np.float32(2.0 ** (-Z_SHIFT / 2.0))
    return (C * w[None, :]) * s, (C.T * w[:, None]) * s


def consts() -> tuple[float, tuple[float, float], tuple[float, float], float]:
    """编译进图的四个常量：环带占比 ρ、p1 门 (lo,宽)、p2 门 (lo,宽)、p3 门 (阈,宽)。

    ρ = 环带元素数 / 总元素数：图里用两次 GlobalAveragePool 求均值（ReduceSum 会
    溢出 fp16：sumh 上界 2.77e8），所以进门的量是 ρ/cr1 而不是 cr1。
    p3 的阈/宽都取 AMP/2^Z_SHIFT，等价于 `Clip((g/t3) - 1)`，过渡段是 [AMP/2, AMP]。
    """
    m_hi = band(N, F_SPLIT, F_HI)
    rho = float(m_hi.sum()) / float(N * N)
    return (rho,
            (rho / CR1_LO, rho / CR1_LO - rho / (CR1_LO * RAMP)),
            (1.0 / CR2_LO, 1.0 / CR2_LO - 1.0 / (CR2_LO * RAMP)),
            (AMP_RAW / 2.0 ** Z_SHIFT,) * 2)


# =============================================================================
# 图
# =============================================================================

def spec() -> tuple[list, list]:
    A, B = weights()
    m_hi = band(N, F_SPLIT, F_HI)
    m_lo = band(N, F_LO, F_SPLIT)
    m_un = m_hi | m_lo
    rho, g1, g2, g3 = consts()

    def w4(name, m):
        return numpy_helper.from_array(m[:, :, None, None].astype(np.float32), name)

    def sc(name, v):
        return numpy_helper.from_array(np.array(v, np.float32), name)

    def m4(name, mask):
        return numpy_helper.from_array(mask.astype(np.float32)[None, :, None, :], name)

    inits = [w4("W1", A), w4("W2", B.T),
             m4("bun", m_un), m4("bhi", m_hi), m4("blo", m_lo),
             sc("eps_g", EPS_G),
             sc("lo1", g1[0]), sc("w1", g1[1]),
             sc("lo2", g2[0]), sc("w2", g2[1]),
             sc("t3", g3[0]), sc("w3", g3[1]),
             sc("zlo", 0.0), sc("zhi", 1.0)]

    def tp(src, dst):
        # 池化只扫 H,W 两轴；换轴再池一次才能覆盖 C 轴（Transpose 实测落 NPU）
        return helper.make_node("Transpose", [src], [dst], perm=[0, 3, 2, 1])

    nodes = [
        helper.make_node("Conv", ["X", "W1"], ["y1"]),
        tp("y1", "y2"),
        helper.make_node("Conv", ["y2", "W2"], ["y3"]),
        tp("y3", "dct"),
        # ── 功率谱只算一次，三个带掩码各乘一次：dct²·m ≡ (dct·m)²（掩码是 0/1）。
        # 先前是「先乘掩码再平方」，同一个 dct 要平方三遍，白花 2 个 N×N 逐元素算子。
        # 板端实测（/root/mg/board_rule_v6r.py，165 片 × 4 轮轮换次序）：算子 52→50，
        # 单片中位 7.79→7.54 ms、全帧 1982→1840 ms（0.928×），四轮全帧无一重叠。
        helper.make_node("Mul", ["dct", "dct"], ["pw"]),
        # ── 增益：union 带（两条子带的并）的峰 ⇒ 除完两个子带都 ≤ 1
        helper.make_node("Mul", ["pw", "bun"], ["pwu"]),
        helper.make_node("GlobalMaxPool", ["pwu"], ["u1"]),
        tp("u1", "u2"),
        helper.make_node("GlobalMaxPool", ["u2"], ["u3"]),
        helper.make_node("Add", ["u3", "eps_g"], ["g"]),
        # ── p1 用：环带归一化后的均值 = ρ/cr1（越小越像单峰）
        helper.make_node("Mul", ["pw", "bhi"], ["pwh"]),
        helper.make_node("Div", ["pwh", "g"], ["anh"]),
        helper.make_node("GlobalAveragePool", ["anh"], ["h1"]),
        tp("h1", "h2"),
        helper.make_node("GlobalAveragePool", ["h2"], ["cr1"]),
        # ── p2 用：内盘归一化后的峰 = pkl/pkh = 1/cr2
        helper.make_node("Mul", ["pw", "blo"], ["pwl"]),
        helper.make_node("Div", ["pwl", "g"], ["anl"]),
        helper.make_node("GlobalMaxPool", ["anl"], ["l1"]),
        tp("l1", "l2"),
        helper.make_node("GlobalMaxPool", ["l2"], ["cr2"]),
        # ── 三道软与门（方向都是"小于阈才开"，所以写成 lo - x）
        helper.make_node("Sub", ["lo1", "cr1"], ["s1a"]),
        helper.make_node("Div", ["s1a", "w1"], ["s1b"]),
        helper.make_node("Clip", ["s1b", "zlo", "zhi"], ["p1"]),
        helper.make_node("Sub", ["lo2", "cr2"], ["s2a"]),
        helper.make_node("Div", ["s2a", "w2"], ["s2b"]),
        helper.make_node("Clip", ["s2b", "zlo", "zhi"], ["p2"]),
        helper.make_node("Sub", ["g", "t3"], ["s3a"]),
        helper.make_node("Div", ["s3a", "w3"], ["s3b"]),
        helper.make_node("Clip", ["s3b", "zlo", "zhi"], ["p3"]),
        helper.make_node("Mul", ["p1", "p2"], ["q12"]),
        helper.make_node("Mul", ["q12", "p3"], ["cout"]),
    ]
    return nodes, inits


def graph(nodes, inits, extra=()) -> onnx.ModelProto:
    """extra: [(张量名, 形状)]，把中间量声明成输出，仅供取证/对数用。"""
    xin = helper.make_tensor_value_info("X", TensorProto.FLOAT, [1, N, 1, N])
    yout = helper.make_tensor_value_info("cout", TensorProto.FLOAT, [1, 1, 1, 1])
    outs = [yout] + [helper.make_tensor_value_info(n, TensorProto.FLOAT, list(s)) for n, s in extra]
    g = helper.make_graph(nodes, "rule_v5", [xin], outs, inits)
    m = helper.make_model(g, opset_imports=[helper.make_opsetid("", 17)])
    m.ir_version = 8
    onnx.checker.check_model(m)
    return m


# =============================================================================
# 取证：中间量的真实数值（对数/回归用，不参与板端路径）
# =============================================================================

def reference(tile: np.ndarray) -> dict[str, float]:
    """按节点顺序直译一遍图（float64）。用来证明图就是我以为的那个图。

    图的轴布局是 X=(1, C=行频率索引, H=1, W=列频率索引)，所以
      y1 = A·x（Conv 在通道轴上左乘） → Transpose → Bᵀ·y2ᵀ… → dct = A·x·B
    """
    A, B = weights()
    A, B = A.astype(np.float64), B.astype(np.float64)
    x = tile.astype(np.float64)
    y1 = A @ x
    dct = (B.T @ y1.T).T
    m_hi = band(N, F_SPLIT, F_HI).astype(np.float64)
    m_lo = band(N, F_LO, F_SPLIT).astype(np.float64)
    m_un = m_hi + m_lo - m_hi * m_lo
    pw = dct * dct
    g = float((pw * m_un).max()) + EPS_G
    cr1 = float((pw * m_hi).sum() / N / N / g)          # = ρ/cr1
    cr2 = float((pw * m_lo).max() / g)                  # = 1/cr2
    rho, (lo1, w1), (lo2, w2), (t3, w3) = consts()
    p = (np.clip((lo1 - cr1) / w1, 0, 1) * np.clip((lo2 - cr2) / w2, 0, 1)
         * np.clip((g - t3) / w3, 0, 1))
    return {"prob": float(p), "cr1": cr1, "cr2": cr2, "g": g}


# =============================================================================
# RKNN 转换
# =============================================================================

@contextlib.contextmanager
def _stdout_to_file():
    """RKNN 的 verbose 日志是 C 层直接写 fd 1，Python 侧 capture 不住（实测）。"""
    tmp = tempfile.NamedTemporaryFile("w+", delete=False, suffix=".log")
    saved = os.dup(1)
    sys.stdout.flush()
    os.dup2(tmp.fileno(), 1)
    try:
        yield tmp
    finally:
        sys.stdout.flush()
        os.fsync(tmp.fileno())
        os.dup2(saved, 1)
        os.close(saved)


def to_rknn(onnx_path: str, out_path: str, target: str = "rk3576") -> dict:
    """ONNX → RKNN（fp16，不做 int8 量化：判据是阈值附近的比较，量化噪声直接改结论）。"""
    from rknn.api import RKNN
    report: dict = {"build": -1, "cpu": [], "ops": {}, "log": ""}
    with _stdout_to_file() as tmp:
        rknn = RKNN(verbose=True)
        rknn.config(target_platform=target)
        if rknn.load_onnx(model=onnx_path) != 0:
            report["error"] = "load_onnx 失败"
            return report
        report["build"] = rknn.build(do_quantization=False)
        if report["build"] == 0:
            rknn.export_rknn(out_path)
        rknn.release()
        os.fsync(tmp.fileno())
        report["log"] = open(tmp.name, errors="ignore").read()
    rows = [(int(i), t, dt, tg) for i, t, dt, tg, _ in ROW.findall(report["log"])]
    calc = [r for r in rows if r[1] not in ("InputOperator", "OutputOperator")]
    report["n_op"] = len(calc)
    report["cpu"] = [f"{i} {t}" for i, t, _dt, tg in calc if tg == "CPU"]
    report["ops"] = dict(Counter(t for _i, t, _dt, _tg in calc))
    report["dtype"] = sorted({dt for _i, _t, dt, _tg in calc})
    tri = re.findall(r"\] +\d+ +[A-Za-z0-9_]+ +[A-Z0-9_]+ +(?:NPU|CPU) +\S* *\S* +(\d+)/(\d+)/(\d+)",
                     report["log"])
    report["cycles"] = {"ddr": sum(int(a) for a, _b, _c in tri),
                        "npu": sum(int(b) for _a, b, _c in tri)}
    return report


# =============================================================================
# CLI
# =============================================================================

def main(argv=None) -> int:
    p = argparse.ArgumentParser("build/export/rule.py")
    sub = p.add_subparsers(dest="cmd", required=True)

    a1 = sub.add_parser("onnx", help="生成 ONNX")
    a1.add_argument("--out", default="build/models/rule_v5.onnx")

    a2 = sub.add_parser("rknn", help="ONNX → RKNN（板端 NPU）")
    a2.add_argument("--onnx", default="build/models/rule_v5.onnx")
    a2.add_argument("--out", default="build/models/rule_v5.rknn")
    a2.add_argument("--target", default="rk3576")

    a3 = sub.add_parser("ops", help="只看算子落点与周期估算")
    a3.add_argument("--onnx", default="build/models/rule_v5.onnx")
    a3.add_argument("--out", default="build/models/rule_v5.rknn")
    a3.add_argument("--target", default="rk3576")

    a4 = sub.add_parser("selfcheck", help="随机块 + 合成光栅块，打印图内各量")
    a4.add_argument("--onnx", default="build/models/rule_v5.onnx")

    args = p.parse_args(argv)
    for attr in ("out", "onnx"):
        if hasattr(args, attr):
            Path(getattr(args, attr)).parent.mkdir(parents=True, exist_ok=True)

    if args.cmd == "onnx":
        nodes, inits = spec()
        onnx.save(graph(nodes, inits), args.out)
        rho, g1, g2, g3 = consts()
        print(f"写出 {args.out}")
        print(f"  rho={rho:.4f}  p1 门在均值域 lo={g1[0]:.5f} 宽={g1[1]:.5f}"
              f" ｜ p2 门 lo={g2[0]:.4f} 宽={g2[1]:.4f} ｜ p3 阈={g3[0]:.5f}")
        return 0

    if args.cmd in ("rknn", "ops"):
        if not Path(args.onnx).exists():
            nodes, inits = spec()
            onnx.save(graph(nodes, inits), args.onnx)
        r = to_rknn(args.onnx, args.out, args.target)
        if r["build"] != 0:
            print(f"build={r['build']} {r.get('error', '')}")
            return 1
        print(f"build={r['build']} 计算算子{r['n_op']}个 CPU落点{len(r['cpu'])}个")
        print(f"  张量 dtype: {r['dtype']}")
        print(f"  类型: {r['ops']}")
        for c in r["cpu"]:
            print(f"  [CPU] {c}")
        print(f"  周期合计 DDR={r['cycles']['ddr']:,} NPU={r['cycles']['npu']:,}")
        if Path(args.out).exists():
            print(f"  产物 {args.out} {os.path.getsize(args.out):,} B")
        return 0 if not r["cpu"] else 1

    # selfcheck：不依赖 rknn，只用 onnxruntime 对 reference()
    import onnxruntime as ort
    sess = ort.InferenceSession(args.onnx, providers=["CPUExecutionProvider"])
    rng = np.random.default_rng(0)
    a = np.arange(N, dtype=np.float64)
    # 轴向光栅的 DCT bin k ≈ 2·f·N，归一化半径 r = k/(2N) = f：取 f=0.30 正落在环带 [0.20,0.46]
    f0 = 0.30
    prof = 128 + 100 * np.cos(2 * np.pi * f0 * a)          # (N,) 沿列方向振荡
    cases = {
        "均匀噪声": rng.uniform(0, 255, (N, N)),
        "全黑": np.zeros((N, N)),
        "纯光栅(环带)": np.tile(prof[None, :], (N, 1)),     # (N,N) 每行同一条光栅
    }
    for name, t in cases.items():
        got = float(sess.run(None, {"X": t[None, :, None, :].astype(np.float32)})[0].ravel()[0])
        ref = reference(t.astype(np.float32))
        print(f"  {name:>12s} 图={got:.4f} 参考={ref['prob']:.4f} "
              f"cr1={ref['cr1']:.4e} cr2={ref['cr2']:.4f} g={ref['g']:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
