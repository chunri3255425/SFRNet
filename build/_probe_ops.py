"""算子隔离探针：把可疑算子各自编成一个单节点 ONNX，逐个喂给 rknn-toolkit2 走
config→load_onnx→build，看哪个把 fold_constant 打 abort。

用途：定位 sfrnet.onnx 在 build() 里 SIGABRT（无 Python 栈）的元凶算子。
每个算子必须**独立进程**跑（abort 会直接杀进程），故本脚本按 argv 只测一个 case。

    python build/_probe_ops.py make            # 生成所有探针 onnx（单个失败不影响其余）
    python build/_probe_ops.py run NonZero     # 测一个
    python build/_probe_ops.py all             # 逐个起子进程测完，最后汇总表
"""
import subprocess
import sys
from pathlib import Path

import torch
import torch.nn as nn

OUT = Path("build/probes")


class ConvOnly(nn.Module):          # 对照组：已知 NPU 友好
    def __init__(self):
        super().__init__()
        self.c = nn.Conv2d(3, 3, 3, padding=1)

    def forward(self, x):
        return self.c(x)


class HasNonZero(nn.Module):        # FRM: torch.nonzero(...) -> 动态输出形状
    def forward(self, x):
        return torch.nonzero(x[0, 0] > 0.5)


class HasArgMax(nn.Module):         # FRM: 路由决策取 argmax
    def forward(self, x):
        return torch.argmax(x, dim=1)


class HasScatterND(nn.Module):      # FRM: 按索引把特征写回
    # 注意 dummy 是 batch=1，索引不能越界（曾用 [0,2] 触发 IndexError 并连累后续探针导出）
    def forward(self, x):
        out = x.clone()
        out[torch.tensor([0, 0], device=x.device)] = 0.0
        return out


class HasRangeEqual(nn.Module):     # FRM: arange + == 组合出的掩码
    def forward(self, x):
        r = torch.arange(x.shape[0], device=x.device)
        return (r == 1).to(x.dtype)


import torch.nn.functional as F


class HasPadReflect(nn.Module):     # FRM: F.pad(x, (p,p,p,p), mode='reflect')
    def forward(self, x):
        return F.pad(x, (2, 2, 2, 2), mode='reflect')


class HasOneHot(nn.Module):         # 静态化后的 FRM: one_hot(argmax)
    def forward(self, x):
        return F.one_hot(torch.argmax(x, dim=1), 4).to(x.dtype)


class HasConstantOfShape(nn.Module):
    def forward(self, x):
        return torch.zeros_like(x) + 1.0


class HasShapeGather(nn.Module):    # safm.py 的 `if g_feat.shape[2:] != x_feat.shape[2:]` 同类形状链
    def forward(self, x):
        b, c, h, w = x.shape
        return x.reshape(b, c, h * w).reshape(b, c, h, w)


class HasResize(nn.Module):         # 全图 47 个 Resize
    def forward(self, x):
        return F.interpolate(x, scale_factor=2, mode='bilinear')


class HasGlobalPool(nn.Module):     # GatingNetwork: AdaptiveAvgPool2d(1)，全图 34 个
    def forward(self, x):
        return F.adaptive_avg_pool2d(x, 1)


class HasSoftmaxGemm(nn.Module):    # router 尾部：Linear + softmax
    def __init__(self):
        super().__init__()
        self.fc = nn.Linear(192, 4)

    def forward(self, x):
        return F.softmax(self.fc(x.flatten(1)), dim=1)


CASES = {
    "Conv": ConvOnly,
    "NonZero": HasNonZero,
    "ArgMax": HasArgMax,
    "ScatterND": HasScatterND,
    "RangeEqual": HasRangeEqual,
    "PadReflect": HasPadReflect,
    "OneHot": HasOneHot,
    "ConstantOfShape": HasConstantOfShape,
    "ShapeGather": HasShapeGather,
    "Resize": HasResize,
    "GlobalPool": HasGlobalPool,
    "SoftmaxGemm": HasSoftmaxGemm,
}


def make() -> int:
    """逐个导出；某个 case 抛异常只记一行，不能让后面的 case 全被跳过（实测踩过）。"""
    OUT.mkdir(parents=True, exist_ok=True)
    ok, bad = [], []
    for name, cls in CASES.items():
        try:
            m = cls().eval()
            torch.onnx.export(m, torch.randn(1, 3, 8, 8), str(OUT / f"{name}.onnx"),
                              input_names=["input"], output_names=["out"],
                              opset_version=17, dynamo=False)
            ok.append(name)
        except Exception as e:                                    # noqa: BLE001
            bad.append(f"{name}: {type(e).__name__}: {e}")
    print(f"[make] 成功 {len(ok)}/{len(CASES)}: {','.join(ok)}")
    for line in bad:
        print(f"[make] 失败 {line}")
    return 0 if not bad else 1


def run(name: str) -> int:
    from rknn.api import RKNN
    net = CASES[name]().eval()
    x = torch.randn(1, 3, 8, 8)
    ref = net(x).detach().numpy()
    r = RKNN(verbose=False)
    if r.config(target_platform="rk3576") != 0:
        print(f"[{name}] config FAIL")
        return 1
    if r.load_onnx(model=str(OUT / f"{name}.onnx")) != 0:
        print(f"[{name}] load_onnx FAIL  <-- 解析层就不支持")
        return 1
    if r.build(do_quantization=False) != 0:
        print(f"[{name}] build FAIL  <-- 图优化/编译层不支持")
        return 1
    print(f"[{name}] OK (ref shape {ref.shape})")
    r.release()
    return 0


def run_all() -> int:
    """abort 会直接杀进程，所以每个 case 起独立子进程；这里只收 exit code 和尾部日志。"""
    for name in CASES:
        if not (OUT / f"{name}.onnx").exists():
            print(f"### {name} 探针 onnx 不存在（跳过）")
            continue
        p = subprocess.run([sys.executable, __file__, "run", name],
                           capture_output=True, text=True)
        log = (p.stdout + p.stderr).strip().splitlines()
        tail = next((l for l in reversed(log) if l.strip()), "")
        print(f"### {name} exit={p.returncode} | {tail[:120]}")
        (OUT / f"run_{name}.log").write_text(p.stdout + p.stderr, encoding="utf-8")
    return 0


if __name__ == "__main__":
    if sys.argv[1] == "make":
        raise SystemExit(make())
    if sys.argv[1] == "all":
        raise SystemExit(run_all())
    raise SystemExit(run(sys.argv[2]))
