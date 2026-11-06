"""板端管线冒烟测试（不依赖真实权重 / 摄像头）。

覆盖 CONTROL_LOGIC §9 验收用例：
  V-01 4K 分块 112 块   V-04 非32倍数不崩溃   V-05 极小图 32×32
  V-06b 短维不足长维超限 64×4096（原 D-01b）  V-08 全零 logits → False
  V-10 coverage 恰为 0.01 → False（严格大于）
  R2/R1 画面回传非阻塞 + 单槽覆盖
  引擎广播出口 IResultSink：限帧循环也广播、热路径不编图像、坏订阅端不拖死循环
  适配器契约：grab(timeout_ms=…) 接得住、合成帧真是 HxWx3
  错误恢复 §6.1：异常分类、固定 2s 退避、重试上限、日志有界、FATAL→退出码 1
  HTTP 错误响应：未鉴权 401 对大 body 也可见、排空后 keep-alive 可复用、Expect 不传体
  状态机：READY → DETECTING → READY；FATAL 布尔固定 False
"""

import argparse
import socket
import struct
import sys
import threading
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.adaptation.camera.base import Frame, ICameraAdapter
from app.adaptation.device_io.linux_io import HostDeviceIO
from app.adaptation.relay.wifi_frame_relay import WifiFrameRelay
from app.adaptation.runtime.base import IRuntimeAdapter
from app.logic_layer.decider import Decider
from app.logic_layer.engine import (RETRY_INFER_MAX, RETRY_UNKNOWN_MAX, DetectionEngine,
                                    DetectionRequest)

PASS = []


def check(name: str, cond: bool, detail: str = ""):
    PASS.append(cond)
    print(f"  {'✅' if cond else '❌'} {name}" + (f" — {detail}" if detail else ""))


# ── 假摄像头 / 假推理后端 ─────────────────────────
class FakeCamera(ICameraAdapter):
    def __init__(self, size=(640, 480)):
        self.size = size
        self.frame = np.random.randint(0, 255, (size[1], size[0], 3), np.uint8)

    def open(self, **kw): pass

    def grab(self, timeout_ms=3000):
        h, w = self.size[1], self.size[0]
        return Frame(self.frame.copy(), w, h, 3, time.time(), "f000001")

    def close(self): pass


class FakeRuntime(IRuntimeAdapter):
    """返回常数 logits 的假后端（模拟 NPU）。"""

    def __init__(self, value: float = 0.0):
        self.value = value
        self.loaded = False

    def load(self, p, **kw): self.loaded = True

    def infer(self, tensor):
        n, c, h, w = tensor.shape
        return np.full((n, 1, h, w), self.value, np.float32)

    def warmup(self): pass

    def unload(self): self.loaded = False


# ── 1. Decider 双门限 ────────────────────────────
print("[1] Decider 判定（CONTROL_LOGIC §5.1）")
d = Decider()
check("V-08 全零 logits → False", d.decide(np.zeros((1, 1, 64, 64), np.float32)).has_moire is False)
r = d.decide(np.full((1, 1, 64, 64), 10.0, np.float32))
check("V-08b 全正大 logits → True", r.has_moire and r.coverage == 1.0)

# V-10: coverage 恰为 0.01 → False（严格大于）
prob = np.zeros((100, 100), np.float64)
prob.flat[:100] = 0.6   # 100/10000 = 0.01
logits = np.log(prob / (1 - prob))
check("V-10 coverage==0.01 → False（严格大于）", d.decide(logits).has_moire is False)

# ── 2. 分块坐标（D-01b 修复验证）─────────────────
print("[2] 分块坐标 _axis")
from app.logic_layer.engine import DetectionEngine as E
check("V-01 4K → 112 块", len(E._axis(2160, 512, 256)) * len(E._axis(3840, 512, 256)) == 112,
      f"{len(E._axis(2160,512,256))}×{len(E._axis(3840,512,256))}")
check("V-06b 64×4096 不崩溃（原 IndexError）", E._axis(64, 512, 256) == [0] and len(E._axis(4096, 512, 256)) > 0)
check("V-06 512×2000 尾块贴边", E._axis(2000, 512, 256)[-1] == 1488)

# ── 3. 尺寸准入 C-01 / 填充模式 ──────────────────
print("[3] 尺寸对齐 _align（§4.2）")
for size, tag in [(511, "V-04 非32倍数"), (32, "V-05 极小图"), (255, "V-05b 临界")]:
    img = np.random.randint(0, 255, (size, size, 3), np.uint8)
    out, pad = E._align(img)
    check(f"{tag} {size}×{size} 对齐通过", out.shape[0] % 32 == 0 and out.shape[1] % 32 == 0
          and out.shape[:2] == (size + pad[0], size + pad[1]))

# ── 4. 端到端 detect_once（假摄像头 + 假 NPU）────
print("[4] 端到端 detect_once")
io = HostDeviceIO()
eng = DetectionEngine(FakeCamera((1280, 720)), FakeRuntime(2.0), io)
eng._decider = Decider()   # 同阈值
eng.state = "READY"
res = eng.detect_once(DetectionRequest())
check("720p 检测出 True（logits=2.0 全图过阈）", res.has_moire is True and res.coverage == 1.0,
      f"coverage={res.coverage:.4f}, tiles={res.tiles}")

eng2 = DetectionEngine(FakeCamera((64, 64)), FakeRuntime(-2.0), io)
eng2.state = "READY"
r2 = eng2.detect_once(DetectionRequest())
check("64×64 极小图端到端不崩溃 → False", r2.has_moire is False,
      f"coverage={r2.coverage:.6f}")

# ── 5. WiFi 回传：单槽覆盖 + 非阻塞 + 实收 ────────
print("[5] WifiFrameRelay（SDD §4.5.2）")
srv = socket.socket()
srv.bind(("127.0.0.1", 0))
srv.listen(1)
port = srv.getsockname()[1]
received = []


def accept_loop():
    conn, _ = srv.accept()
    while True:
        head = b""
        while len(head) < 4:
            b1 = conn.recv(4 - len(head))
            if not b1:
                return
            head += b1
        (size,) = struct.unpack("<I", head)
        data = b""
        while len(data) < size:
            data += conn.recv(size - len(data))
        received.append(data)


t = threading.Thread(target=accept_loop, daemon=True)
t.start()

relay = WifiFrameRelay(interval_frames=1, jpeg_quality=50, downscale=320, overlay_result=True)
relay.start("127.0.0.1", port)
time.sleep(0.3)
cam = FakeCamera((640, 480))
t0 = time.perf_counter()
for i in range(20):
    relay.push(cam.grab(), None)
push_ms = (time.perf_counter() - t0) * 1000 / 20
check("R2 push 非阻塞（均值 <5ms）", push_ms < 5.0, f"{push_ms:.2f} ms/帧")
time.sleep(1.0)
check("上位机实收 JPEG 帧", len(received) >= 1 and received[-1][:2] == b"\xff\xd8",
      f"收到 {len(received)} 帧, SOI={received[-1][:2] if received else '-'}")
relay.stop()
srv.close()

# ── 6. 引擎广播出口（IResultSink，SSE/串口/MQTT 共用同一个注入点）──
print("[6] 引擎广播出口接线")


class RecordingSink:
    def __init__(self):
        self.items = []

    def publish(self, dto):
        self.items.append(dto)


class BadSink:
    def __init__(self):
        self.n = 0

    def publish(self, dto):
        self.n += 1
        raise RuntimeError("订阅端炸了")


sink = RecordingSink()
eng3 = DetectionEngine(FakeCamera((640, 480)), FakeRuntime(2.0), io, sinks=[sink])
eng3.state = "READY"
eng3.run_forever(DetectionRequest(), max_frames=3)
check("限帧循环同样广播（--frames N 不再静默不推流）", len(sink.items) == 3,
      f"收到 {len(sink.items)} 条")
d0 = sink.items[0] if sink.items else None
check("广播 DTO 不携带图像字节（热路径零编码）",
      d0 is not None and d0.mask_png is None and d0.prob_png is None,
      f"coverage={d0.coverage:.4f} max_prob={d0.max_prob:.4f}" if d0 else "-")
check("有人订阅才算 mean_prob", d0 is not None and d0.mean_prob > 0.0,
      f"mean={d0.mean_prob:.4f}" if d0 else "-")

eng4 = DetectionEngine(FakeCamera((640, 480)), FakeRuntime(2.0), io)
eng4.state = "READY"
r4 = eng4.detect_once(DetectionRequest())
check("无人订阅时不算 mean_prob（零成本）", r4.mean_prob == 0.0 and r4.max_prob > 0.0,
      f"mean={r4.mean_prob} max={r4.max_prob:.4f}")

bad = BadSink()
eng5 = DetectionEngine(FakeCamera((640, 480)), FakeRuntime(2.0), io, sinks=[bad])
eng5.state = "READY"
eng5.run_forever(DetectionRequest(), max_frames=4)
check("坏 sink 不得拖死检测循环", bad.n == 4 and eng5.state in ("READY", "SHUTDOWN"),
      f"sink 抛了 {bad.n} 次, state={eng5.state}")

sink2 = RecordingSink()
eng6 = DetectionEngine(FakeCamera((640, 480)), FakeRuntime(2.0), io)
eng6.attach_sink(sink2)
eng6.state = "READY"
eng6.run_forever(DetectionRequest(), max_frames=2)
check("attach_sink 后置挂载可用（组合根解引用环）", len(sink2.items) == 2,
      f"收到 {len(sink2.items)} 条")

# ── 7. 适配器契约与错误恢复上限（CONTROL_LOGIC §6.1）──
# 本组的存在理由：开发机默认形态（裸跑 python -m app.main）曾经一帧都
# 出不来——假相机 grab() 不收 timeout_ms（TypeError），且合成帧形状是
# (H,1,1)（np.where 广播少一维）。而上面的 [4] 用的是自造 FakeCamera，签名和
# 形状都是对的，所以这个默认路径从没被任何测试踩过。以下逐条钉住。
print("[7] 适配器契约 + 退避与恢复上限")
import inspect

import app.logic_layer.engine as engine_mod
from app.adaptation.camera.base import frame_shape_error
from app.adaptation.camera.fake import FakeCameraAdapter
from app.logic_layer.engine import MoireEngineError


class RecIO:
    """只收集不输出的 IDeviceIO（上面的用例用 HostDeviceIO，日志会刷屏）。"""

    def __init__(self):
        self.lines = []

    def read_config(self): return {}

    def log(self, level, msg): self.lines.append((level, msg))

    def indicate(self, has_moire): pass

    def count(self, needle): return sum(needle in m for _, m in self.lines)


sig = inspect.signature(FakeCameraAdapter.grab).parameters
check("假相机接得住 grab(timeout_ms=…)", "timeout_ms" in sig, str(list(sig)))
fc = FakeCameraAdapter(320, 240)
fc.open()
fr = fc.grab(timeout_ms=1000)
check("假相机出的是契约形状 HxWx3 且与元数据一致",
      fr.data.shape == (240, 320, 3) and frame_shape_error(fr) == "",
      f"shape={fr.data.shape} err={frame_shape_error(fr) or '无'}")


class BoomCamera(ICameraAdapter):
    """取帧原生异常（不是 MoireEngineError）：必须归 capture 走 C-10。"""

    def open(self, **kw): pass

    def grab(self, timeout_ms=3000):
        raise RuntimeError("模拟设备掉线")

    def close(self): pass


class MalformedCamera(ICameraAdapter):
    def open(self, **kw): pass

    def grab(self, timeout_ms=3000):
        return Frame(np.zeros((64, 1, 1), np.uint8), 64, 64, 1, time.time(), "bad-1")

    def close(self): pass


kind_of = {}
for tag, cam in (("capture", BoomCamera()), ("bad_frame", MalformedCamera())):
    e = DetectionEngine(cam, FakeRuntime(2.0), RecIO())
    e.state = "READY"
    try:
        e.detect_once(DetectionRequest())
        kind_of[tag] = "没报错"
    except MoireEngineError as ex:
        kind_of[tag] = ex.kind
check("取帧原生异常归 capture（而非 unknown）", kind_of["capture"] == "capture",
      kind_of["capture"])
check("形状坏帧归 bad_frame（不去重载模型）", kind_of["bad_frame"] == "bad_frame",
      kind_of["bad_frame"])


class BoomRuntime(IRuntimeAdapter):
    """前向每帧必炸（走 C-11：3 次上限）。"""

    def load(self, p, **kw): pass

    def infer(self, tensor): raise ValueError("模拟后端炸")

    def warmup(self): pass

    def unload(self): pass


class OkCamera(ICameraAdapter):
    def open(self, **kw): pass

    def grab(self, timeout_ms=3000):
        return Frame(np.zeros((64, 64, 3), np.uint8), 64, 64, 3, time.time(), "ok-1")

    def close(self): pass


# 退避不实等：打桩记下请求值，既测“固定 2s”本身，也让冒烟不跑成十几秒
io7, sleeps = RecIO(), []
real_sleep = time.sleep
engine_mod.time.sleep = lambda s: sleeps.append(s)
try:
    e9 = DetectionEngine(OkCamera(), BoomRuntime(), io7)
    e9.state = "READY"
    e9.run_forever(DetectionRequest(), max_frames=100)
finally:
    engine_mod.time.sleep = real_sleep
check("推理失败退避按 §6.1 固定 2s（请求值）", sleeps == [2.0] * RETRY_INFER_MAX,
      str(sleeps))
check("恢复超限后记 FATAL 原因（给退出码用）",
      "推理恢复超限" in e9.fatal_reason and e9.state == "SHUTDOWN", e9.fatal_reason or e9.state)
check("日志有界：超限后不再刷（今天实刷 8.3MB）",
      io7.count("推理失败") == RETRY_INFER_MAX, f"{io7.count('推理失败')} 行")


class IndicateBoomIO(RecIO):
    def indicate(self, has_moire): raise RuntimeError("指示器炸了")


io8, unknown_sleeps = IndicateBoomIO(), []
engine_mod.time.sleep = lambda s: unknown_sleeps.append(s)
try:
    e10 = DetectionEngine(OkCamera(), FakeRuntime(2.0), io8)
    e10.state = "READY"
    e10.run_forever(DetectionRequest(), max_frames=100)
finally:
    engine_mod.time.sleep = real_sleep
check("未知异常也有上限（3 次→FATAL）",
      io8.count("未知异常") == RETRY_UNKNOWN_MAX and "unknown" in e10.fatal_reason,
      f"{io8.count('未知异常')} 行 / {e10.fatal_reason}")
check("未知异常带完整堆栈（§6.1 动作①）",
      any("Traceback" in m for _, m in io8.lines))
check("正常收摊不记 fatal_reason（退出码 0 的依据）",
      DetectionEngine(OkCamera(), FakeRuntime(2.0), RecIO()).fatal_reason == "")

# 退出码必须真到组合根：上面只测了引擎自己，而 moireguard.service 是
# Restart=on-failure，_run_loop 给 0 就等于告诉 systemd“正常收摊”，不再拉回。
# 只偷换两个工厂函数（假相机/必炸后端），走的仍是 main 里那段真装配。
from app import main as app_main

_keep = (app_main.ada.make_camera, app_main.ada.make_runtime)
app_main.ada.make_camera = lambda **kw: OkCamera()
app_main.ada.make_runtime = lambda *a, **kw: BoomRuntime()
engine_mod.time.sleep = lambda s: None          # 退避不实等
try:
    ns = argparse.Namespace(model=None, backend="torch", threshold=None,
                            coverage_threshold=None, tile_size=None,
                            relay=False, relay_host=None, relay_port=None,
                            camera=None, frames=50, interval_ms=0)
    rc_loop = app_main._run_loop("host", RecIO(),
                                 app_main.build_config(RecIO(), ns), ns)
finally:
    app_main.ada.make_camera, app_main.ada.make_runtime = _keep
    engine_mod.time.sleep = real_sleep
check("循环中 FATAL → 组合根退出码 1", rc_loop == 1, str(rc_loop))

# ── 8. 网络出口的错误响应（不碰推理，所以不需要真权重）─────
print("[8] HTTP 错误响应：排空与 Expect")
import http.client
import shutil
import subprocess
import tempfile
import urllib.request

from app.adaptation.base import NetServerOptions, NetworkConfig
from app.adaptation.network.http_server import HttpNetServer

no_proxy_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
io8s = RecIO()
# port=0 → 内核分配空余端口，start() 返回实际绑定值，不会和并行跑的其它用例相撞
cfg8 = NetworkConfig(enabled=True, host="127.0.0.1", port=0, token="secret",
                     max_upload_mb=8)
srv8 = HttpNetServer(NetServerOptions(image_service=None, engine=None, config=cfg8), io8s)
h8, p8 = srv8.start()


def _post8(body: bytes, token: str | None = None) -> object:
    req = urllib.request.Request(f"http://{h8}:{p8}/detect", data=body)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with no_proxy_opener.open(req, timeout=20) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception as e:                       # URLError / ConnectionReset 等
        return type(e).__name__


try:
    # 实测（本轮修复前）：未带 token 的 1 MB / 4 MB 上传各 6 次全是 URLError。
    codes8 = {mb: [_post8(b"x" * (mb << 20)) for _ in range(3)] for mb in (1, 4)}
    check("未鉴权 1 MB / 4 MB 都稳定得 401", all(v == [401, 401, 401] for v in codes8.values()),
          str(codes8))

    # 残体会把同一条连接的下一个请求带偏：401 后紧跟着一个 GET，必须是 200 而不是错位。
    c8 = http.client.HTTPConnection(h8, p8, timeout=20)
    c8.request("POST", "/detect", body=b"y" * (1 << 20))
    r8 = c8.getresponse()
    first8, _ = r8.status, r8.read()
    c8.request("GET", "/healthz")
    r8b = c8.getresponse()
    second8, _ = r8b.status, r8b.read()
    c8.close()
    check("401 排空后同连接下一请求仍对齐", first8 == 401 and second8 == 200,
          f"{first8}→{second8}")

    # 声明体量超 max_upload_mb 就不陪它读：写完头就关发送端，服务必须回 401 后断连，
    # 不能卡在排空上等那 100 MB。
    c8b = http.client.HTTPConnection(h8, p8, timeout=20)
    c8b.connect()
    c8b.putrequest("POST", "/detect")
    c8b.putheader("Content-Length", str(100 << 20))
    c8b.endheaders()
    c8b.sock.shutdown(socket.SHUT_WR)
    r8c = c8b.getresponse()
    over8 = (r8c.status, c8b.sock.recv(1) == b"")   # 服务端已自行关闭
    c8b.close()
    check("声明超上限：不排空、回 401 后断连", over8 == (401, True), str(over8))

    # 两个 503 出口也得排空：它们不读图，但图已经传上来了。
    # 注意每轮各起一个自己的服务实例（engine 在 start() 时被闭包捕获），必须连
    # 到新返回的端口，否则会测成上一个实例。
    for tag, eng in (("E-ENGINE-NOT-READY", None), ("E-NO-UPLOAD", object())):
        sv = HttpNetServer(NetServerOptions(image_service=None, engine=eng,
                                            config=cfg8), io8s)
        hh, pp = sv.start()
        cc = http.client.HTTPConnection(hh, pp, timeout=20)
        try:
            cc.request("POST", "/detect", body=b"q" * (1 << 20),
                       headers={"Authorization": "Bearer secret"})
            rr = cc.getresponse()
            body8 = rr.read()
            cc.request("GET", "/healthz")
            ok8 = cc.getresponse().status
            cc.close()
            check(f"503（{tag}）也排空，同连接可复用",
                  rr.status == 503 and tag.encode() in body8 and ok8 == 200,
                  f"{rr.status}/{ok8}")
        finally:
            sv.stop()

    # Expect: 100-continue：curl 对 >1024B 体默认带。未授权就该在传体前被拦下，
    # 上传字节必须为 0（比事后排空省一轮回环带宽，也不给身份不明者任何读取量）。
    if shutil.which("curl"):
        blob = Path(tempfile.mkdtemp()) / "expect_body.bin"
        blob.write_bytes(b"z" * (2 << 20))
        try:
            proc8 = subprocess.run(["curl", "-sS", "--noproxy", "*", "-o", "/dev/null",
                                    "-w", "%{http_code} %{size_upload}",
                                    "-X", "POST", "--data-binary", f"@{blob}",
                                    f"http://{h8}:{p8}/detect"],
                                   capture_output=True, text=True, timeout=60)
            fields8 = proc8.stdout.split()
            ok8e = len(fields8) == 2 and fields8[0] == "401" and int(float(fields8[1])) == 0
            detail8 = proc8.stdout or f"rc={proc8.returncode} {proc8.stderr.strip()[:90]}"
            check("Expect 下未授权者 0 字节即得 401", ok8e, detail8)
        except Exception as e:
            check("Expect 下未授权者 0 字节即得 401", False, repr(e)[:90])
        finally:
            blob.unlink(missing_ok=True)
    else:
        print("  [跳过] 无 curl，Expect: 100-continue 分支未跑")
finally:
    srv8.stop()

# ── 汇总 ────────────────────────────────────────
n_ok = sum(PASS)
print(f"\n结果: {n_ok}/{len(PASS)} 通过")
sys.exit(0 if n_ok == len(PASS) else 1)
