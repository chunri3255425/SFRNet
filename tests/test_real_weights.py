"""真实权重与网络出口的回归用例（补 smoke_board_pipeline 的空档）。

smoke_board_pipeline.py 13 个用例全用 FakeRuntime（返回常数 logits 的假后端），
所以"后端接口与真模型对不上"这类缺陷它一条也抓不到——合并时就撞上过一次：
TorchRuntime 把 registry 返回的 DetectorCore 当 nn.Module 调，`core(t)` 直接
TypeError，而 13 项依然全绿。本文件把真实权重纳入回归，并核对两条栈的语义一致。

跳过规则：无 torch / 无权重 / 无测试图时整组 skip（板端镜像不含 torch，属正常）。
运行：python tests/test_real_weights.py
"""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

WEIGHTS = ROOT / "assets" / "weights" / "SFR_best.pth"
SAMPLE = ROOT / "samples" / "moire_photo.jpg"

PASS: list[bool] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    PASS.append(bool(cond))
    print(f"  {'✅' if cond else '❌'} {name}" + (f" — {detail}" if detail else ""))


def main() -> int:
    try:
        import torch        # noqa: F401
        from PIL import Image  # noqa: F401
    except ImportError as e:
        print(f"[skip] 无 torch/PIL（{e}），真实权重用例跳过")
        return 0
    if not WEIGHTS.exists():
        print(f"[skip] 权重缺失: {WEIGHTS}")
        return 0
    if not SAMPLE.exists():
        print(f"[skip] 测试图缺失: {SAMPLE}")
        return 0

    from app.adaptation.runtime.torch_runtime import TorchRuntime
    from app.logic_layer.decider import Decider
    from app.logic_layer.engine import DetectionEngine, DetectionRequest
    from app.adaptation.camera.base import Frame

    # ── 1. 后端契约：真实权重能加载，且输出形状/域正确 ─────────
    print("[1] TorchRuntime 真实权重契约")
    rt = TorchRuntime(device="auto")
    rt.load(str(WEIGHTS))                       # ★ 路径必须真被使用
    rt.warmup()
    img = np.asarray(Image.open(SAMPLE).convert("RGB").resize((256, 256)))
    tensor = np.ascontiguousarray(
        (img.astype(np.float32) / 255.0).transpose(2, 0, 1))[None]   # NCHW [0,1]
    logits = rt.infer(tensor)
    check("输出形状 [1,1,H,W]", logits.shape == (1, 1, 256, 256), str(logits.shape))
    # 判据用「超出 sigmoid 值域」而不是「有负值」：实测本模型输出存在系统性
    # 高偏置（干净样张 coverage 也有 0.98+），logits 可以全为正，但永远不可能 > 1。
    check("输出是 raw logits（超出 sigmoid 值域）",
          bool(float(logits.max()) > 1.0),
          f"min={logits.min():.3f} max={logits.max():.3f}")
    rt.unload()

    # ── 2. 端到端：engine 状态机 + 真实权重（此前必崩的路径）──
    print("[2] DetectionEngine + 真实权重")

    class _OneShotCamera:
        def open(self, **kw): pass

        def grab(self, timeout_ms: 3000) -> Frame:
            return Frame(data=img, width=img.shape[1], height=img.shape[0],
                         channels=3, timestamp=0.0, frame_id="regression-1")

        def close(self): pass

    class _NullIO:
        def read_config(self): return {}

        def log(self, level, msg): pass

        def indicate(self, has_moire): pass

    rt2 = TorchRuntime(device="auto")
    engine = DetectionEngine(_OneShotCamera(), rt2, _NullIO())
    # 必须走真实 bootstrap：READY 的契约含义就是「权重已 load 且已 warmup」，
    # 直接改 state 会让运行时停在未加载态，测的就不再是合并时崩过的那条路径。
    booted = engine.bootstrap({"runtime": {"backend": "torch",
                                           "model_path": str(WEIGHTS)}})
    check("bootstrap 达到 READY", booted and engine.state == "READY",
          f"state={engine.state} warnings={engine.report.warnings if engine.report else '-'}")
    res = engine.detect_once(DetectionRequest())
    check("真实权重端到端出结果", res.tiles >= 1 and res.elapsed_ms > 0,
          f"tiles={res.tiles} coverage={res.coverage:.4f}")
    check("摩尔纹样张判 True", bool(res.has_moire), f"coverage={res.coverage:.6f}")

    # ── 3. 判定语义唯一：Decider 与 PostProcessor 边界一致 ─────
    print("[3] 双门限语义跨栈一致（V-10）")
    d = Decider()
    # 直接造 logits（而非先造 prob 再反解，那会对 prob=0 取 log(0) 触发除零告警）：
    # 100 个像素 sigmoid(+0.6)=0.646 过阈，其余 sigmoid(-3)=0.047 不过 → coverage 恰 0.01
    prob = np.full((100, 100), -3.0)
    prob.flat[:100] = 0.6
    check("coverage==0.01 → False（严格大于）",
          d.decide(prob).has_moire is False)

    from app.logic_layer.postprocess import PostProcessor
    import torch

    pp = PostProcessor()
    logits_t = torch.full((1, 1, 100, 100), -3.0, dtype=torch.float32)
    logits_t.view(-1)[:100] = 0.6
    out = pp.apply(logits_t)
    check("PostProcessor 与 Decider 同界同判",
          (out.coverage <= 0.01) == (not d.decide(logits_t.numpy()).has_moire),
          f"coverage={out.coverage:.4f}")

    # ── 4. 网络出口：真权重下的 /detect 与鉴权 ─────────────────
    print("[4] HTTP 出口 + 真实推理（localhost 回环）")
    from app import adaptation as ada
    from app.adaptation.base import NetServerOptions, NetworkConfig
    from app.adaptation.network.http_server import HttpNetServer
    from app.main import ServiceFacade, build_config
    from app.logic_layer.inference_service import InferenceService
    from app.logic_layer.config import ConfigManager
    from app.model_layer.registry import ModelRegistry

    io = ada.make_device_io("host")
    cm: ConfigManager = build_config(io, _FakeArgs(str(WEIGHTS)))
    facade = ServiceFacade(InferenceService(ModelRegistry(), cm), cm, io)
    cfg = NetworkConfig(enabled=True, host="127.0.0.1", port=8467, token="secret")
    server = HttpNetServer(NetServerOptions(image_service=facade, engine=facade,
                                            config=cfg), io)
    host, port = server.start()
    try:
        payload = SAMPLE.read_bytes()
        req = urllib.request.Request(f"http://{host}:{port}/detect", data=payload,
                                     headers={"Content-Type": "image/jpeg",
                                              "Authorization": "Bearer secret"})
        with urllib.request.urlopen(req, timeout=600) as r:
            body = json.loads(r.read().decode("utf-8"))
        check("POST /detect 200 且带判定字段",
              body.get("has_moire") in (True, False) and "coverage" in body,
              f"coverage={body.get('coverage')}")
        check("DTO 默认不带宽高图（省流量，需显式 opt-in）",
              "mask_png_b64" not in body and "mask_png" not in body,
              ",".join(sorted(body.keys()))[:80])
        req_masked = urllib.request.Request(
            f"http://{host}:{port}/detect", data=payload,
            headers={"Content-Type": "image/jpeg",
                     "Authorization": "Bearer secret",
                     "X-With-Mask": "1"})
        with urllib.request.urlopen(req_masked, timeout=600) as r:
            body_m = json.loads(r.read().decode("utf-8"))
        check("X-With-Mask: 1 → 返回 mask_png_b64",
              bool(body_m.get("mask_png_b64")), f"len={len(body_m.get('mask_png_b64', ''))}")
        bad = urllib.request.Request(f"http://{host}:{port}/detect", data=payload,
                                     headers={"Content-Type": "image/jpeg"})
        try:
            urllib.request.urlopen(bad, timeout=10)
            check("缺 token 被拒", False, "竟返回 2xx")
        except urllib.error.HTTPError as e:
            check("缺 token 被拒 401", e.code == 401, str(e.code))
        # 存活探针必须免鉴权：systemd/监控不持 token，否则“服务健康”会被
        # 探成 401 而判成“服务已死”（配 token 实测撞过）。而 /status 带引擎
        # 状态与配置快照，必须仍然鉴权——两者不能同批处理。
        try:
            with urllib.request.urlopen(f"http://{host}:{port}/healthz", timeout=10) as r:
                check("无 token 的 /healthz 仍可 200（探针免鉴权）",
                      r.status == 200 and json.loads(r.read()).get("ok") is True)
        except urllib.error.HTTPError as e:
            check("无 token 的 /healthz 仍可 200（探针免鉴权）", False, f"{e.code}")
        try:
            urllib.request.urlopen(f"http://{host}:{port}/status", timeout=10)
            check("无 token 的 /status 仍被拒（诊断面不开放）", False, "竟返回 2xx")
        except urllib.error.HTTPError as e:
            check("无 token 的 /status 仍被拒（诊断面不开放）", e.code == 401, str(e.code))
    finally:
        server.stop()

    print(f"\n结果: {sum(PASS)}/{len(PASS)} 通过")
    return 0 if all(PASS) else 1


class _FakeArgs:
    """build_config 只需要 model/backend/relay 等少量字段。"""

    def __init__(self, model: str):
        self.model = model
        self.backend = "torch"
        self.threshold = None
        self.coverage_threshold = None
        self.tile_size = None
        self.relay = False
        self.relay_host = "127.0.0.1"
        self.relay_port = 0
        self.camera = None


if __name__ == "__main__":
    raise SystemExit(main())
