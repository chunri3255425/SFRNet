"""真实权重与网络出口的回归用例（补 smoke_board_pipeline 的空档）。

smoke_board_pipeline.py 13 个用例全用 FakeRuntime（返回常数 logits 的假后端），
所以"后端接口与真模型对不上"这类缺陷它一条也抓不到——合并时就撞上过一次：
TorchRuntime 把 registry 返回的 DetectorCore 当 nn.Module 调，`core(t)` 直接
TypeError，而 13 项依然全绿。本文件把真实权重纳入回归，并核对两条栈的语义一致。

跳过规则：无 torch / 无权重 / 无测试图时整组 skip（板端镜像不含 torch，属正常）。
运行：python tests/test_real_weights.py
"""

from __future__ import annotations

import http.client
import json
import shutil
import subprocess
import sys
import tempfile
import time
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
    # 回环断言必须绕开系统代理：urllib 的 no_proxy 匹配不认“127.*”这种写法
    # （实测 proxy_bypass("127.0.0.1") 返 False），请求会先去代理一跳再绕回来。
    # 那个中间层不是被测对象，它把上游中断翻译成 502 就能把“应得 401”判成失败。
    # （502 的根因已查清并修掉：原先未鉴权不排空请求体，大 body 的客户端自己在
    # 写入途中撞上连接关闭。绕代理只是为了别把环境算进断言。）
    local = urllib.request.build_opener(urllib.request.ProxyHandler({}))
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
        with local.open(req, timeout=600) as r:
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
        with local.open(req_masked, timeout=600) as r:
            body_m = json.loads(r.read().decode("utf-8"))
        check("X-With-Mask: 1 → 返回 mask_png_b64",
              bool(body_m.get("mask_png_b64")), f"len={len(body_m.get('mask_png_b64', ''))}")
        bad = urllib.request.Request(f"http://{host}:{port}/detect", data=payload,
                                     headers={"Content-Type": "image/jpeg"})
        try:
            local.open(bad, timeout=10)
            check("缺 token 被拒", False, "竟返回 2xx")
        except urllib.error.HTTPError as e:
            check("缺 token 被拒 401", e.code == 401, str(e.code))
        # 401 对所有体量都得看得到。上一版为了“不给身份不明者读数据”而不排空
        # 请求体，结果 body 一超过套接字缓冲，客户端就在写入途中被断连：实测
        # 1 MB / 4 MB 各 6 次全是 URLError，一个状态码都拿不到。
        for size_mb in (1, 4):
            codes = []
            for _ in range(3):
                req = urllib.request.Request(f"http://{host}:{port}/detect",
                                             data=b"x" * (size_mb << 20))
                try:
                    codes.append(local.open(req, timeout=20).status)
                except urllib.error.HTTPError as e:
                    codes.append(e.code)
                except Exception as e:
                    codes.append(type(e).__name__)
            check(f"未鉴权 {size_mb} MB 请求体也稳定拿 401", codes == [401] * 3, str(codes))
        # 排空不只为当前请求：keep-alive 下残体会被当下一个请求解析。
        # http.client 不读环境变量代理，天然直连。
        conn = http.client.HTTPConnection(host, port, timeout=20)
        conn.request("POST", "/detect", body=b"y" * (1 << 20))
        resp = conn.getresponse()
        code_ka = resp.status
        resp.read()
        conn.request("GET", "/healthz")
        resp2 = conn.getresponse()
        next_ok = resp2.status == 200
        resp2.read()
        conn.close()
        check("401 后同一条 keep-alive 连接仍可正常复用", code_ka == 401 and next_ok,
              f"首个={code_ka} 续请={'200' if next_ok else '非 200'}")
        # Expect: 100-continue（curl 对 >1024B 体默认带）：未授权就不该让它开始传体，
        # 授权则必须回 100——实测不回 100 时 curl 白等 1.008 s（回 100 只要 0.048 s）。
        if shutil.which("curl"):
            blob = Path(tempfile.mkdtemp()) / "expect_body.bin"
            blob.write_bytes(b"z" * (2 << 20))
            try:
                proc = subprocess.run(
                    ["curl", "-s", "--noproxy", "*", "-o", "/dev/null",
                     "-w", "%{http_code} %{size_upload}", "-X", "POST",
                     "--data-binary", f"@{blob}", f"http://{host}:{port}/detect"],
                    capture_output=True, text=True, timeout=60)
                code_e, up_e = proc.stdout.split()
                check("Expect 下未授权者 0 字节即得 401",
                      code_e == "401" and int(float(up_e)) == 0, proc.stdout)
            except Exception as e:
                check("Expect 下未授权者 0 字节即得 401", False, repr(e)[:80])
            finally:
                blob.unlink(missing_ok=True)
        else:
            print("  [跳过] 无 curl，Expect: 100-continue 分支未跑")
        # 存活探针必须免鉴权：systemd/监控不持 token，否则“服务健康”会被
        # 探成 401 而判成“服务已死”（配 token 实测撞过）。而 /status 带引擎
        # 状态与配置快照，必须仍然鉴权——两者不能同批处理。
        try:
            with local.open(f"http://{host}:{port}/healthz", timeout=10) as r:
                check("无 token 的 /healthz 仍可 200（探针免鉴权）",
                      r.status == 200 and json.loads(r.read()).get("ok") is True)
        except urllib.error.HTTPError as e:
            check("无 token 的 /healthz 仍可 200（探针免鉴权）", False, f"{e.code}")
        try:
            local.open(f"http://{host}:{port}/status", timeout=10)
            check("无 token 的 /status 仍被拒（诊断面不开放）", False, "竟返回 2xx")
        except urllib.error.HTTPError as e:
            check("无 token 的 /status 仍被拒（诊断面不开放）", e.code == 401, str(e.code))
    finally:
        server.stop()

    # ── 5. 常驻循环 + 网络出口同跑：真权重下 SSE 必须收到实时帧 ──
    # 这一条是合并时没接通的洞：IResultSink 当时只在适配层声明，engine._publish
    # 只存不推，所以 /stream 永远只能看到“通过 HTTP 自己进来的”检测。
    print("[5] 循环 + HTTP 同跑（--serve-net --loop 形态）")
    import threading

    cfg2 = NetworkConfig(enabled=True, host="127.0.0.1", port=8468, token="secret")
    # image_service 故意不装：该形态下不允许多起一个推理实例（板端内存翻倍）
    srv2 = HttpNetServer(NetServerOptions(image_service=None, engine=engine,
                                          config=cfg2), io)
    h2, p2 = srv2.start()
    engine.attach_sink(srv2)

    events: list[dict] = []

    def _subscribe() -> None:
        conn = http.client.HTTPConnection(h2, p2, timeout=600)
        conn.request("GET", "/stream", headers={"Authorization": "Bearer secret"})
        resp = conn.getresponse()
        while len(events) < 2:
            raw = resp.fp.readline()
            if raw == b"":
                break
            text = raw.decode("utf-8").strip()
            if text.startswith("data:"):
                events.append(json.loads(text[5:].strip()))
        conn.close()

    sub = threading.Thread(target=_subscribe, daemon=True)
    sub.start()
    # 等订阅者真注册上：否则事件在无人时被丢弃，测不到东西也不代表功能坏
    for _ in range(400):
        st = http.client.HTTPConnection(h2, p2, timeout=10)
        st.request("GET", "/status", headers={"Authorization": "Bearer secret"})
        info = json.loads(st.getresponse().read())
        st.close()
        if info.get("sse_clients", 0) >= 1:
            break
        time.sleep(0.05)
    check("/status 报的是真实状态机（不是 facade 常量）",
          info.get("engine_state") == "READY", str(info)[:120])

    engine.run_forever(DetectionRequest(), max_frames=1)
    sub.join(120)
    check("实时帧经 SSE 到达（事件来自循环，非 /detect）",
          len(events) >= 1 and events[0].get("source_id") == "regression-1",
          f"events={len(events)} payload={str(events[:1])[:120]}")
    if events:
        e0 = events[0]
        check("循环广播带真实数值（有人订阅才算 mean_prob）",
              e0.get("has_moire") is True and e0.get("coverage", 0) > 0.9
              and e0.get("mean_prob", 0) > 0.0 and e0.get("elapsed_ms", 0) > 0,
              f"cov={e0.get('coverage')} mean={round(e0.get('mean_prob', 0), 4)}")
    else:
        check("循环广播带真实数值（有人订阅才算 mean_prob）", False, "无事件")
    try:
        conn = http.client.HTTPConnection(h2, p2, timeout=10)
        conn.request("POST", "/detect", body=b"not-an-image",
                     headers={"Authorization": "Bearer secret",
                              "Content-Type": "image/jpeg"})
        resp = conn.getresponse()
        detail = resp.read().decode("utf-8", "replace")
        conn.close()
        check("该形态的 /detect 明确拒因（不加载第二份权重，而非含糊 503）",
              resp.status == 503 and "E-NO-UPLOAD" in detail, detail[:120])
    except Exception as e:
        check("该形态的 /detect 明确拒因（不加载第二份权重，而非含糊 503）", False, repr(e))
    srv2.stop()

    # ── 6. systemd 停机语义：SIGTERM 必须走收摊路径 ──────────────
    # 主机侧网络形态原先只 catch KeyboardInterrupt，而 systemd 默认
    # StopSignal=SIGTERM → 进程被默认行为直接杀掉（退出码 -15），server.stop()
    # 与 indicate(False) 全部不跑。这条只能靠真发信号测出来。
    print("[6] SIGTERM 优雅停机")
    import signal as _sig

    with tempfile.TemporaryFile(mode="w+") as log:
        proc = subprocess.Popen([sys.executable, "-m", "app.main", "--serve-net",
                                 "--port", "8469", "--token", "sek"],
                                stdout=log, stderr=subprocess.STDOUT, text=True)
        t0 = time.time()
        served = False
        while time.time() - t0 < 180:
            log.seek(0)
            if "serving on" in log.read():
                served = True
                break
            time.sleep(0.5)
        proc.send_signal(_sig.SIGTERM)
        rc = proc.wait(timeout=120)
        log.seek(0)
        tail = log.read()
    check("--serve-net 收到 SIGTERM 后以 0 退出（不是 -15 被杀）", served and rc == 0,
          f"rc={rc}")
    check("收摊日志真的落盘（非静默退出）", "net: stopped" in tail,
          " | ".join(l for l in tail.splitlines() if "net:" in l or "SHUTDOWN" in l)[:120])
    refused = False
    try:
        c = http.client.HTTPConnection("127.0.0.1", 8469, timeout=5)
        c.request("GET", "/healthz")
        c.getresponse()
        c.close()
    except OSError:
        refused = True
    check("停机后监听端口已释放（收摊路径真跑过）", refused)

    # ── 7. 开发机默认入口（裸跑）端到端 ──────────────────────
    # 今晨实测的坑：python -m app.main（不带任何参数）就是板端/开发机的默认
    # 形态，却一帧都出不来：假相机 grab() 不收 timeout_ms → TypeError → 被归
    # unknown → 旧版既不退避也不升级 → 12s 刷了 8.3MB 日志。这条只能靠真子进程
    # 跑真入口测出来：进程内自造的假摄像头会把适配器契约一起绕过去。
    print("[7] 默认形态（裸跑 + 真权重 + 真假相机）")
    import os

    with tempfile.TemporaryFile(mode="w+b") as log7:      # 写读兼得：只给 "wb" 会 raise
        proc7 = subprocess.Popen([sys.executable, "-m", "app.main", "--frames", "2"],
                                 stdout=log7, stderr=subprocess.STDOUT, text=False,
                                 env={**os.environ, "MOIREGUARD_PLATFORM": "host"})
        rc7 = proc7.wait(timeout=300)
        log7.seek(0)
        raw7 = log7.read().decode("utf-8", "replace")
    frame_lines = [l for l in raw7.splitlines() if "frame=" in l]
    check("裸跑就能出帧（日志里有 frame= 行）", rc7 == 0 and len(frame_lines) == 2,
          f"rc={rc7} frame行={len(frame_lines)}")
    check("日志量有界（回归前 12s 就 8.3MB）", len(raw7) < 64 * 1024,
          f"{len(raw7)} 字节 / 2 帧")
    check("不再把契约错报成未知异常", "未知异常" not in raw7,
          next((l for l in raw7.splitlines() if "ERROR" in l), "无 ERROR"))

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
