"""板端运行包打包：app/ + 模型 + 配置 → 免依赖独立运行包。

产物直接叠加进镜像根文件系统（build/image_linux/ 的 buildroot/Yocto recipe
或离线 .deb 均消费本目录产物）。

用法：
  python build/package_app.py --model build/models/sfrnet.rknn --out build/dist/
"""

from __future__ import annotations

import argparse
import shutil
import tarfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# 镜像内的落盘位置（SDD §4.1 平台差异对照表）
DEST = {
    "app": Path("/opt/moireguard/"),
    "model": Path("/usr/share/moireguard/models/"),
    "config": Path("/etc/moireguard/"),
}


def package(model: str, out: str) -> int:
    model_path = Path(model)
    if not model_path.exists():
        print(f"[pkg] ❌ 模型不存在: {model_path}（先跑 build/export_model.py）")
        return 1

    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    stage = out_dir / "stage"
    if stage.exists():
        shutil.rmtree(stage)

    # ── 阶段 1：按镜像路径组装 stage/ ──
    (stage / "opt" / "moireguard").mkdir(parents=True)
    (stage / "usr" / "share" / "moireguard" / "models").mkdir(parents=True)
    (stage / "etc" / "moireguard").mkdir(parents=True)
    (stage / "var" / "lib" / "moireguard").mkdir(parents=True)
    (stage / "var" / "log" / "moireguard").mkdir(parents=True)

    # 应用本体（三层已全部收敛到 app/ 单一包，upstream 仅需 sfr 结构定义）
    for pkg in ("app", "upstream", "configs"):
        src = REPO / pkg
        dst = stage / "opt" / "moireguard" / pkg
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))

    # 模型
    shutil.copy2(model_path, stage / "usr" / "share" / "moireguard" / "models" / "model.rknn")

    # 配置（镜像内置默认值）
    shutil.copy2(REPO / "configs" / "default.yaml", stage / "etc" / "moireguard" / "default.yaml")

    # systemd 服务单元
    (stage / "etc" / "systemd" / "system").mkdir(parents=True)
    shutil.copy2(REPO / "build" / "image_linux" / "moireguard.service",
                 stage / "etc" / "systemd" / "system" / "moireguard.service")

    # 板端 Python 依赖清单（镜像构建时预装，板上不装 PyTorch）
    (stage / "opt" / "moireguard" / "requirements-board.txt").write_text(
        "\n".join([
            "numpy",
            "opencv-python-headless",
            "pillow",
            "pyyaml",
            "# NPU 推理（厂商源安装，勿用 pip 官方源）:",
            "# rknn-toolkit-lite2  ← apt / .deb from rockchip",
        ]) + "\n",
        encoding="utf-8",
    )

    # ── 阶段 2：打包 tar ──
    tar_path = out_dir / "moireguard-board.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tar:
        tar.add(stage, arcname=".")
    print(f"[pkg] ✅ 运行包完成: {tar_path}")
    print(f"[pkg]    stage 目录（可直接叠加进根文件系统）: {stage}")
    print("[pkg]    板上部署: SSH 进板 → 解压到 / → systemctl enable --now moireguard")
    return 0


def main() -> int:
    p = argparse.ArgumentParser("package_app")
    p.add_argument("--model", default="build/models/sfrnet.rknn")
    p.add_argument("--out", default="build/dist")
    return package(**vars(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
