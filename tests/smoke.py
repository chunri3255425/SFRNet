"""板端管线冒烟测试（不依赖真实权重 / 摄像头）。

覆盖 CONTROL_LOGIC §9 验收用例：
  V-01 4K 分块 112 块   V-04 非32倍数不崩溃   V-05 极小图 32×32
  V-06b 短维不足长维超限 64×4096（原 D-01b）  V-08 全零 logits → False
  V-10 coverage 恰为 0.01 → False（严格大于）
  R2/R1 画面回传非阻塞 + 单槽覆盖
  引擎广播出口 IResultSink：限帧循环也广播、热路径不编图像、坏订阅端不拖死循环
  适配器契约：grab(timeout_ms=…) 接得住、合成帧真是 HxWx3
  平台判别：Ubuntu 固件的 /system/etc 不误判为鸿蒙；显式 device 赢过平台默认
  错误恢复 §6.1：异常分类、固定 2s 退避、重试上限、日志有界、FATAL→退出码 1
  HTTP 错误响应：未鉴权 401 对大 body 也可见、排空后 keep-alive 可复用、Expect 不传体
  状态机：READY → DETECTING → READY；FATAL 布尔固定 False
"""

import argparse
import os
import socket
import struct
import sys
import threading
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.adaptation.camera.base import Frame, ICameraAdapter
from app.adaptation.device_io.linux import HostDeviceIO
from app.adaptation.relay.wifi import WifiFrameRelay
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
    """返回常数 logits 的假后端（模拟 NPU）。seen 记下引擎实际交来的东西，
    用来验“逐像素那条契约还在”（§11）——否则备料改动会不会碰到 SFRNet 路没人知道。"""

    def __init__(self, value: float = 0.0):
        self.value = value
        self.loaded = False
        self.seen = None

    def load(self, p, **kw): self.loaded = True

    def infer(self, tensor):
        self.seen = tensor
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
from app.adaptation.network.http import HttpNetServer

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
    st8 = r8c.status
    # 先把错误页读干净再判断连。原先直接 sock.recv(1)：401 带 25 字节的 JSON body，
    # body 是否已经被 http.client 的 8KB 缓冲拖走取决于分段时机 —— 没拖走时 recv
    # 拿到的是 '{' 而不是 FIN，于是“服务端已关闭”被记成假（对照实测
    # .scratch/http401_probe.py，本机过程件：四轮全部真的断了连，裸 recv 却 3 真 1 假）。
    try:
        r8c.read()
    except Exception:
        pass                        # IncompleteRead 等：读过多少不影响断连判据
    over8 = (st8, c8b.sock.recv(1) == b"")   # 服务端已自行关闭
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

# ── 9. 平台判别与取帧源优先级（2026-11-07 板端产品路径实测炸出来的）──
# 首次用 `python3 -m app.main --camera xxx.jpg` 在 RK3576 上跑，FATAL：
# “鸿蒙相机 Kit 适配待实现”。两个真缺陷：
#   a) detect_platform 拿 /system/etc 当 OH 特征，而厂商 Ubuntu 固件本身就有
#      /system/etc/{firmware,lib} ⇒ 真板子被判成 ohos；
#   b) make_camera 的 OHOS 分支排在最前直接 return，调用侧显式传的
#      --camera 被无声丢弃（与它自己的 docstring 相矛盾的语义）。
print("[9] 平台判别 / 取帧源优先级（factory 是唯一允许平台分支的地方）")
import pathlib

from app.adaptation import factory

_real_exists = pathlib.Path.exists
# 厂商 Ubuntu 固件里真实存在的路径；/etc/ohos_release 在这块板上实测不存在
OH_TRAP = {"/system", "/system/etc", "/system/etc/firmware"}


def _board_like_exists(self, *a, **kw):
    name = str(self)
    if name in OH_TRAP:
        return True
    if name == "/etc/ohos_release":
        return False
    return _real_exists(self, *a, **kw)


pathlib.Path.exists = _board_like_exists
try:
    plat = factory.detect_platform()
    check("Ubuntu 固件的 /system/etc 不再误判为鸿蒙", plat != "ohos", f"platform={plat}")
finally:
    pathlib.Path.exists = _real_exists

check("显式 device 赢过平台默认（鸿蒙判定下也不再吞 --camera）",
      type(factory.make_camera(platform="ohos", device="/root/moire_photo.jpg")).__name__
      == "FileCamera")
check("鸿蒙无 device 仍显式抛未实现（修复不得把 fail-fast 软化成能用）",
      type(factory.make_camera(platform="ohos")).__name__ == "OhosCameraAdapter")
check("鸿蒙 + /dev/video* 也不假装能跑 V4L2",
      type(factory.make_camera(platform="ohos", device="/dev/video0")).__name__
      == "OhosCameraAdapter")
check("板端不给 device 仍默认 /dev/video33（rkisp-vir0 mainpath，非 video0 raw bayer）",
      type(factory.make_camera(platform="linux")).__name__ == "V4L2CameraAdapter")
check("开发机不给 device 仍走假源（默认定型未被重排改动）",
      type(factory.make_camera(platform="host")).__name__ == "FakeCameraAdapter")

# ── 10. 板端只允许 NPU + 规则后端契约 ───────────────
# 纪律本身要能被测：早期 make_runtime 不收 platform，这道闸只能在子进程里靠
# 环境变量验，于是“板端悄悄跑 CPU”这个最难排查的事故形态没有回归保护。
print("[10] 板端只允许 NPU / 规则后端契约")


def _raises(fn, exc=RuntimeError):
    try:
        fn()
        return False, "没报错"
    except exc as e:
        return True, str(e)[:46]
    except Exception as e:                       # 类型不对也算拦住，但要看见是什么
        return True, f"{type(e).__name__}: {str(e)[:36]}"


for plat in ("linux", "ohos"):
    for be in ("torch", "onnx"):
        hit, msg = _raises(lambda be=be: factory.make_runtime(be, platform=plat))
        check(f"板端({plat}) 拒装 {be} 后端", hit and "CPU-FORBIDDEN" in msg, msg)
try:                                   # 本环境没装 torch 也算过：能报 ImportError 就说明闸门没拦它
    _got = type(factory.make_runtime("torch", platform="host")).__name__
    _open, _why = _got == "TorchRuntime", _got
except Exception as e:
    _open, _why = "CPU-FORBIDDEN" not in str(e), f"{type(e).__name__}（已过闸门，只缺依赖）"
check("开发机仍可装 torch（闸门不是把 CPU 路径整体砸掉）", _open, _why)
check("板端 auto 仍然定到 rknn（新闸门不得改变默认选择）",
      type(factory.make_runtime("auto", platform="linux")).__name__ == "RknnRuntime")

try:
    import onnxruntime as _ort                                    # noqa: F401
    from app.adaptation.runtime import rule as rr

    _has_art = pathlib.Path(rr.DEV_MODEL).exists()
except ImportError:
    _has_art = False
    rr = None

if not _has_art:
    print("  [跳过] 无 onnxruntime 或规则图产物，契约组未跑"
          "（开发机先跑 python build/export/rule.py onnx）")
else:
    rt = rr.RuleRuntime()
    rt.load(rr.DEV_MODEL)

    class StillCam(ICameraAdapter):
        def __init__(self, img):
            self.img = img

        def open(self, **kw): pass

        def grab(self, timeout_ms=3000):
            h, w = self.img.shape[:2]
            return Frame(self.img.copy(), w, h, 3, time.time(), "rule")

        def close(self): pass

    def run(img):
        e = DetectionEngine(StillCam(img), rt, RecIO())
        e.state = "READY"
        return e.detect_once(DetectionRequest())

    n = 1024
    prof = 128 + 100 * np.cos(2 * np.pi * 0.30 * np.arange(n))     # 归一化频率 0.30，落在环带
    raster = np.tile(prof, (n, 1)).astype(np.uint8)
    flat = np.full((n, n), 128, np.uint8)
    noise = np.random.default_rng(0).integers(0, 256, (n, n), dtype=np.uint8)

    for name, arr, want in (("环带光栅→has_moire", raster, True),
                            ("平图→no_moire", flat, False),
                            ("均匀噪声→no_moire", noise, False)):
        r = run(np.dstack([arr] * 3))
        check(name, r.has_moire is want, f"coverage={r.coverage:.4f} tiles={r.tiles}")

    # 转置不变性必须拿**非退化**的片验：纯噪声片 prob 恒 0，随机片 prob 恒 1，
    # 两者都是 0 比 0 / 1 比 1，布局真错了也看不出来。下面这块把振幅 40 的环带
    # 光栅叠上 26 的 0.08 低频包络，让 p2 门落在过渡斜坡中段（prob≈0.54），
    # 任何数值扰动都会直接改输出；先断言它确实不退化，再拿它做不变性对比。
    ax = np.arange(512)
    t = np.round(128 + 40 * np.cos(2 * np.pi * 0.30 * ax)[None, :] * np.ones((512, 1))
                 + 26 * np.cos(2 * np.pi * 0.08 * ax)[:, None]
                 * np.cos(2 * np.pi * 0.08 * ax)[None, :]).astype(np.uint8)
    tens = lambda x: np.dstack([x] * 3).astype(np.float32).transpose(2, 0, 1)[None] / 255.0
    p1 = float(rt.infer(tens(t))[0, 0, 0, 0])
    p2 = float(rt.infer(tens(t.T))[0, 0, 0, 0])
    check("探针片 prob 落在门限斜坡中段（否则下面两条不变性无效）",
          0.2 < p1 + 0.5 < 0.8, f"prob={p1 + 0.5:.4f}")
    check("图对转置严格不变（所以 .rknn 的 NHWC 喂法不可能猜错）",
          abs(p1 - p2) < 1e-4, f"Δ={abs(p1 - p2):.2e}")
    out = rt.infer(tens(t))
    check("出口合契约（scalar_output → [1,1,1,1] 有限 logits）",
          out.shape == (1, 1, 1, 1) and np.isfinite(out).all(),
          f"logit={float(out[0,0,0,0]):.4f}")
    small = rt.infer(tens(np.full((96, 96), 7, np.uint8)))
    check("不足 512 的小片 edge-pad 后不崩（引擎 C-01 的零信息损失同一路子）",
          small.shape == (1, 1, 1, 1))

    os.environ["MOIREGUARD_PLATFORM"] = "linux"
    try:
        hit, msg = _raises(lambda: rr.RuleRuntime().load(rr.DEV_MODEL),
                           exc=RuntimeError)
        check("规则后端在板端平台也拒装 .onnx（不只靠 factory 那道闸）",
              hit and "CPU-FORBIDDEN" in msg, msg)
    finally:
        os.environ.pop("MOIREGUARD_PLATFORM", None)

# ── 11. CPU 胶水压掉之后的备料契约 ───────────────
# 这一组守三件事，每件都是“错了不会报错、只会静默改结论”的那类：
#   a) 片网格口径要能用手算的数对得上（coverage = 过门片占比）
#   b) 整图折灰快路必须与三通道老路给同一个 prob（否则“压胶水”就是偷换判据）
#   c) 能力标记与真实行为不一致必须响，不得退回“拿第一个值凑合”
print("[11] 片网格备料 / 整图折灰 / coverage 口径")


class StubCam(ICameraAdapter):
    def __init__(self, img):
        self.img = img

    def open(self, **kw): pass

    def grab(self, timeout_ms=3000):
        h, w = self.img.shape[:2]
        return Frame(self.img.copy(), w, h, 3, time.time(), "stub")

    def close(self): pass


class GridRuntime(IRuntimeAdapter):
    """声明两个能力标记的桩：收 [H,W] uint8，每片交回一个标量。

    fn 给的是 prob（人读得惯的量纲），出口按生产契约交 logits = prob-0.5。
    这一步不是注释上的事：直接拿 prob 当 logits 的话 0.1 和 0.6 都 > 0，
    九片全过门，coverage 恒为 1 —— 下面那条手算期望值就是拿这个错验出来的。
    """

    wants_gray = True
    scalar_output = True

    def __init__(self, fn=None, force_map=False):
        self.fn = fn or (lambda t: 0.6)
        self.force_map = force_map
        self.seen = []

    def load(self, p, **kw): pass

    def infer(self, tensor):
        self.seen.append((tensor.shape, tensor.dtype.str, tensor.flags["C_CONTIGUOUS"]))
        if self.force_map:            # 声明与行为不一致（要被测响的那种错）
            return np.full((1, 1, 512, 512), 0.6, np.float32)
        return np.full((1, 1, 1, 1), self.fn(tensor) - 0.5, np.float32)

    def warmup(self): pass

    def unload(self): pass


def _run_stub(img, rt_):
    e = DetectionEngine(StubCam(img), rt_, RecIO())
    e.state = "READY"
    return e, e.detect_once(DetectionRequest())


# 1024×1024 @512/256 → 片原点 (0,256,512)×(0,256,512) = 9 片
n = 1024
img = np.zeros((n, n, 3), np.uint8)
for (yy, xx) in [(0, 256), (256, 0), (512, 512)]:
    img[yy, xx] = 200                      # 只有这三片的左上像素被打了标
stub = GridRuntime(fn=lambda t: 0.6 if float(t[0, 0]) > 100 else 0.1)
_e, r = _run_stub(img, stub)
check("片网格口径：coverage 就是手算的过门片占比 3/9",
      abs(r.coverage - 3 / 9) < 1e-9, f"coverage={r.coverage:.6f}")
check("片网格口径：tiles 报的是片数不是像素数", r.tiles == 9, f"tiles={r.tiles}")
check("片网格口径：_last_domain 记为 tile（决定裁回原尺寸那步能不能跑）",
      _e._last_domain == "tile", _e._last_domain)
check("整图折灰快路：runtime 收到的是 [512,512] uint8 视图（引擎不预先拷）",
      len(stub.seen) == 9 and all(s[0] == (512, 512) and s[1] == "|u1" for s in stub.seen),
      str(stub.seen[0]))
check("确实递的是视图（非连续片存在 → 片预处理零拷贝）",
      any(not s[2] for s in stub.seen), f"{sum(1 for s in stub.seen if not s[2])}/9 非连续")

# 能力标记说假话：声明 scalar_output 却交回整张图
try:
    _run_stub(img, GridRuntime(force_map=True))
    ok_map, why_map = False, "没报错"
except Exception as e:
    ok_map = "scalar_output" in str(e)
    why_map = f"{type(e).__name__}: {str(e)[:56]}"
check("声明 scalar_output 却交回整张图 → 响，不拿第一个值凑合", ok_map, why_map)

# 像素后端（SFRNet 那条）契约未被动过
px = FakeRuntime(value=0.6)
e2, r2 = _run_stub(np.full((n, n, 3), 128, np.uint8), px)
check("逐像素后端仍走 [1,3,512,512] float32 [0,1] 入口",
      px.seen.shape == (1, 3, 512, 512) and px.seen.dtype == np.float32
      and float(px.seen.max()) <= 1.0, f"{px.seen.shape} {px.seen.dtype}")
check("像素路融合未回归：_last_domain=pixel、coverage 仍为像素占比",
      e2._last_domain == "pixel" and r2.coverage == 1.0, f"coverage={r2.coverage:.4f}")

# 窗缓存：小片路径原先每片重算外积
w1 = DetectionEngine._hann2d(300)
w2 = DetectionEngine._hann2d(300)
check("_hann2d 按尺寸缓存且交回只读（就地改写会留脏窗）",
      w1 is w2 and not w1.flags.writeable,
      f"same={w1 is w2} writeable={w1.flags.writeable if w1 is w2 else '-'}")

if _has_art:                              # §10 的 else 块里已装好真规则图
    probe = np.round(128 + 40 * np.cos(2 * np.pi * 0.30 * np.arange(512))[None, :]
                     + 26 * np.cos(2 * np.pi * 0.08 * np.arange(512))[:, None]
                     * np.cos(2 * np.pi * 0.08 * np.arange(512))[None, :]).astype(np.uint8)
    q_gray = float(rt.infer(probe)[0, 0, 0, 0]) + 0.5
    q_rgb = float(rt.infer(np.dstack([probe] * 3).astype(np.float32)
                           .transpose(2, 0, 1)[None] / 255.0)[0, 0, 0, 0]) + 0.5
    check("灰度快路与三通道老路探针片非退化（否则又是一条 0 比 0 的空结论）",
          0.2 < q_gray < 0.8 and 0.2 < q_rgb < 0.8, f"{q_gray:.4f} / {q_rgb:.4f}")
    check("两条备料路给同一个 prob（整图折灰不是偷换判据）",
          abs(q_gray - q_rgb) < 1e-3, f"Δ={abs(q_gray - q_rgb):.2e}")
    rt.warmup()
    check("warmup 两条入口都跑得起（板上真跑的是灰度快路）", True, "未抛异常")
else:
    print("  [跳过] 无 onnxruntime / 规则图产物，真图对照未跑")

# ── 汇总 ────────────────────────────────────────
n_ok = sum(PASS)
print(f"\n结果: {n_ok}/{len(PASS)} 通过")
sys.exit(0 if n_ok == len(PASS) else 1)
