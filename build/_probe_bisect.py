"""整图前缀二分：把 sfrnet_static.onnx 按拓扑序截断成前 k 个节点，喂给 rknn build()，
找**第一个触发 SIGABRT 的前缀**，从而定位炸点算子 / 炸点子图。

背景：12 个单算子探针（_probe_ops.py）全部 exit 0 或给出明确 ValueError，
没有一个复现整图的 exit=134 abort ⇒ 炸点是整图组合才出现的，只能切图定位。

切法：前 k 个节点为新图，新图的输出 = 原本被 nodes[k:] 消费、且"依赖输入 + 浮点"的张量
（否则会被 toolkit 判成 `All outputs of model are constants` 而软失败，混淆结论）。

用法（abort 会杀进程，故每刀必须独立子进程；bisect 模式自己 spawn）：
    python build/_probe_bisect.py prep                      # 跑一次 shape inference 存中间件
    python build/_probe_bisect.py plan                      # 有多少刀可切
    python build/_probe_bisect.py try 662                   # 只切一刀，日志 build/probes/cut_662.log
    python build/_probe_bisect.py bisect                    # 二分找第一个 abort 的前缀
"""
import subprocess
import sys
from pathlib import Path

import onnx
from onnx import TensorProto, helper

SRC = Path("build/models/sfrnet_static.onnx")
OUT = Path("build/probes")
INFERRED = OUT / "_static_inferred.onnx"
FLOAT = {TensorProto.FLOAT, TensorProto.FLOAT16, TensorProto.DOUBLE}


# ───────────────────────── 准备 ─────────────────────────
def base_model() -> onnx.ModelProto:
    if not INFERRED.exists():
        raise SystemExit("先跑 `prep`（shape inference 只做一次）")
    return onnx.load(str(INFERRED))


def prep() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    m = onnx.load(str(SRC))
    m = onnx.shape_inference.infer_shapes(m, strict_mode=False)
    onnx.save(m, str(INFERRED))
    vi = len(m.graph.value_info)
    print(f"[prep] 推理出 {vi} 个中间张量的形状 -> {INFERRED}")
    return 0


# ───────────────────────── 一刀的信息 ─────────────────────────
class Cutter:
    """一次装载，反复切；把 dyn / 消费者 关系提前算好，避免每刀重扫全图。"""

    def __init__(self, model: onnx.ModelProto):
        self.m = model
        self.nodes = list(model.graph.node)
        self.n = len(self.nodes)
        self.vi = {v.name: v for v in list(model.graph.value_info) + list(model.graph.output)}
        self.init = {i.name for i in model.graph.initializer}
        self.consumers: dict[str, list[int]] = {}
        for idx, node in enumerate(self.nodes):
            for i in node.input:
                if i:
                    self.consumers.setdefault(i, []).append(idx)
        # 逐节点标出"依赖图输入"的输出（前向传播可达性）
        self.dyn: list[set[str]] = []
        live = {i.name for i in model.graph.input}
        for node in self.nodes:
            hit = any(i in live for i in node.input if i)
            outs = set(node.output) if hit else set()
            live |= outs
            self.dyn.append(outs)

    def cut_outputs(self, k: int) -> list[str]:
        """前 k 个节点里、被后半图消费、且是浮点动态张量的输出名。"""
        if k >= self.n:                       # 全图基线：没有下游，直接用原图输出
            return [o.name for o in self.m.graph.output
                    if o.name in self.vi
                    and self.vi[o.name].type.tensor_type.elem_type in FLOAT]
        names = []
        for idx in range(k):
            for o in self.dyn[idx]:
                v = self.vi.get(o)
                if v is None or o in self.init:
                    continue
                if v.type.tensor_type.elem_type not in FLOAT:
                    continue
                if any(c >= k for c in self.consumers.get(o, [])):
                    names.append(o)
        return names

    def write(self, k: int, dst: Path) -> int:
        names = self.cut_outputs(k)
        if not names:
            return 0
        kept = self.nodes[:k]
        used = {i for node in kept for i in node.input if i}
        graph = helper.make_graph(
            kept, "cut", list(self.m.graph.input),
            [self.vi[n] for n in names],
            [i for i in self.m.graph.initializer if i.name in used],
        )
        new = helper.make_model(graph, opset_imports=self.m.opset_import)
        new.ir_version = self.m.ir_version
        onnx.save(new, str(dst))
        return len(names)


# ───────────────────────── 评测 ─────────────────────────
def evaluate(k: int) -> int:
    c = Cutter(base_model())
    dst = OUT / f"cut_{k}.onnx"
    n_out = c.write(k, dst)
    if not n_out:
        print(f"[cut {k}] 这一刀没有可用的浮点边界输出")
        return 1
    print(f"[cut {k}] {k} 节点 / {n_out} 个边界输出")
    from rknn.api import RKNN
    r = RKNN(verbose=False)
    if r.config(target_platform="rk3576") != 0:
        return 1
    if r.load_onnx(model=str(dst)) != 0:
        print(f"[cut {k}] load_onnx FAIL")
        return 1
    if r.build(do_quantization=False) != 0:
        print(f"[cut {k}] build 软失败（非 abort）")
        return 1
    print(f"[cut {k}] OK")
    r.release()
    return 0


def valid_cuts(c: Cutter) -> list[int]:
    return [k for k in range(1, c.n + 1) if c.cut_outputs(k)]


def show(k: int, width: int = 6) -> int:
    """打出第 k 个节点（1-based）及其邻居，带属性、输入生产者、输出形状。"""
    c = Cutter(base_model())
    prod = {o: i for i, n in enumerate(c.nodes) for o in n.output}
    for i in range(max(0, k - width), min(c.n, k + width - 1)):
        n = c.nodes[i]
        mark = "<<" if i + 1 == k else "  "
        attrs = [(a.name, (a.i if a.type == a.INT else
                           a.s.decode() if a.type == a.STRING else
                           list(a.ints) if a.type == a.INTS else
                           list(a.floats) if a.type == a.FLOATS else f"tensor:{a.t.data_type}"))
                 for a in n.attribute]
        src = [f"{x}<-{c.nodes[prod[x]].op_type}#{prod[x]+1}" if x in prod else
               (f"{x}=init" if x in c.init else f"{x}=input") for x in n.input]
        dims = [(o, [d.dim_value or d.dim_param for d in c.vi[o].type.tensor_type.shape.dim])
                for o in n.output if o in c.vi]
        print(f"{mark} {i+1:4d} {n.op_type:16s} {n.name}\n"
              f"      in ={src}\n      out={[f'{o}{d}' for o, d in dims]}\n"
              f"      attr={attrs}")
    return 0


def plan() -> int:
    c = Cutter(base_model())
    hits = valid_cuts(c)
    print(f"[plan] 共 {c.n} 个节点，可切 {len(hits)} 刀，范围 {hits[0]}–{hits[-1]}")
    return 0


def spawn(k: int) -> int:
    p = subprocess.run([sys.executable, __file__, "try", str(k)],
                       capture_output=True, text=True)
    (OUT / f"cut_{k}.log").write_text(p.stdout + p.stderr, encoding="utf-8")
    return p.returncode


def aborted(code: int) -> bool:
    """SIGABRT 在 shell 里是 128+6=134，在 subprocess.returncode 里是 **-6**。
    一开始按 134 判，把整条二分基线误判成"没复现"（已踩过）。"""
    return code in (134, -6) or code < 0


def bisect() -> int:
    c = Cutter(base_model())
    hits = valid_cuts(c)
    print(f"[bisect] 基线：全图 k={c.n} 应 abort ...", flush=True)
    if not aborted(spawn(c.n)):
        print("[bisect] 全图没复现 abort，前提不成立（可能切图方式引入差异，看 cut_*.log）")
        return 1
    lo, hi, trace = 0, len(hits) - 1, []
    while lo <= hi:                     # 在"可切刀"序列上找第一个 abort 的位置
        mid = (lo + hi) // 2
        k = hits[mid]
        code = spawn(k)
        trace.append((k, code))
        print(f"[bisect] k={k} exit={code} {'ABORT' if aborted(code) else ''}", flush=True)
        if aborted(code):
            hi = mid - 1
        else:
            lo = mid + 1
    bad = [k for k, code in trace if aborted(code)]
    print(f"[bisect] 轨迹 {trace}")
    print(f"[bisect] 第一个 abort 前缀 = {min(bad) if bad else '未定位'}，"
          f"它之前的最后一刀安全前缀 = "
          f"{max([k for k, code in trace if not aborted(code)], default=0)}")
    return 0


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "help"
    if mode == "prep":
        raise SystemExit(prep())
    if mode == "plan":
        raise SystemExit(plan())
    if mode == "try":
        raise SystemExit(evaluate(int(sys.argv[2])))
    if mode == "show":
        raise SystemExit(show(int(sys.argv[2]), int(sys.argv[3]) if len(sys.argv) > 3 else 6))
    if mode == "bisect":
        raise SystemExit(bisect())
    print(__doc__)
