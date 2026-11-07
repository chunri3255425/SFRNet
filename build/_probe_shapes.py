"""全图符号形状普查：数一下哪些张量的 dim 是 unk__* / 字符串（形状推不出来），
并按"生产者算子 / 所在模块"聚类 —— 决定要针对性修几处。
"""
import sys
from collections import Counter
from pathlib import Path

import onnx

m = onnx.load(str(Path(sys.argv[1] if len(sys.argv) > 1 else
                   "build/models/sfrnet_static.onnx")))
m = onnx.shape_inference.infer_shapes(m, strict_mode=False)
vi = list(m.graph.value_info) + list(m.graph.output)
prod = {o: n for n in m.graph.node for o in n.output}

sym = []
for v in vi:
    dims = v.type.tensor_type.shape.dim
    if any(not d.HasField("dim_value") or d.dim_value <= 0 for d in dims):
        sym.append((v.name, [d.dim_value if d.HasField("dim_value") else d.dim_param
                             for d in dims],
                    prod[v.name].op_type if v.name in prod else "?",
                    prod[v.name].name if v.name in prod else "?"))

print(f"中间/输出张量共 {len(vi)} 个，其中形状含符号维度的 {len(sym)} 个")
print("\n按生产者算子聚类：")
for k, c in Counter(s[2] for s in sym).most_common():
    print(f"  {c:4d}  {k}")
print("\n按模块前缀聚类（取 node.name 的第一段）：")
for k, c in Counter(s[3].split("/")[1] if s[3].startswith("/") else s[3] for s in sym).most_common(10):
    print(f"  {c:4d}  {k}")
print("\n前 15 个明细：")
for name, dims, op, nn in sym[:15]:
    print(f"  {op:12s} {nn}\n      {name} -> {dims}")
