"""文档机械自检：围栏成对 / 表格列数一致 / § 交叉引用有落点 / 常见错字。

不改文件，只报问题。改完文档必跑（本项目的纪律：结论要能在代码侧 grep 到）。
"""

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
DOCS = ["docs/guides/BOARD_BRINGUP.md", "docs/design/SOFTWARE_DESIGN.md",
        "docs/design/TECHNICAL_DOC.md", "docs/design/CONTROL_LOGIC.md",
        "docs/archive/CHANGELOG.md", "docs/ARCHITECTURE.md", "build/README.md", "README.md",
        "docs/dataset/README.md"]
TYPO = ["呰", "跱", "跳后端", "面住", "阀值", "清算子", "风隙", "单算子探子", "呾",
        "兑底", "锅甲", "甲给", "跱后", "跳设备", "跳后端对照", "包拫", "最后一朵"]      # 均为本项目文档里真实写错过的字

problems: list[str] = []
present = []

for rel in DOCS:
    p = ROOT / rel
    if not p.exists():
        continue
    present.append(rel)
    lines = p.read_text(encoding="utf-8").split("\n")

    # 1) 代码围栏成对
    fence = sum(1 for l in lines if l.startswith("```"))
    if fence % 2:
        problems.append(f"[围栏] {rel}: {fence} 个 ```，奇数")

    # 2) 表格列数一致（跳过围栏内部）
    infence = False
    i = 0
    while i < len(lines):
        if lines[i].startswith("```"):
            infence = not infence
            i += 1
            continue
        if infence or not lines[i].startswith("|"):
            i += 1
            continue
        blk = []
        while i < len(lines) and lines[i].startswith("|"):
            blk.append((i + 1, lines[i].count("|")))
            i += 1
        counts = {}
        for ln, c in blk:
            counts.setdefault(c, []).append(ln)
        if len(counts) > 1:
            detail = "; ".join(f"{c}列@行{v[0]}等{len(v)}行" for c, v in sorted(counts.items()))
            problems.append(f"[表格] {rel} 行{blk[0][0]} 起：列数不齐 → {detail}")

    # 3) 错字
    for t in TYPO:
        for n, l in enumerate(lines, 1):
            if t in l:
                problems.append(f"[错字] {rel}:{n} 含“{t}”：{l.strip()[:70]}")

# 4) §x.y 交叉引用必须有落点（只在 BOARD_BRINGUP / SDD 之间查）
heads = set()
for rel in present:
    for l in (ROOT / rel).read_text(encoding="utf-8").split("\n"):
        m = re.match(r"^#{2,3}\s+(\d+(?:\.\d+)?)", l)
        if m:
            heads.add((rel.split("/")[-1], m.group(1)))
allnums = {n for _, n in heads}
for rel in present:
    for n, l in enumerate((ROOT / rel).read_text(encoding="utf-8").split("\n"), 1):
        for ref in re.findall(r"§(\d+\.\d+)", l):
            if ref not in allnums:
                problems.append(f"[断引用] {rel}:{n} 引用 §{ref}，但全部文档里没这个编号")

print(f"扫描 {len(present)} 份文档：{present}")
if problems:
    for x in problems:
        print("  " + x)
    sys.exit(1)
print("✅ 围栏 / 表格 / 错字 / §引用 全部通过")
