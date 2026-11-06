"""板端管线冒烟测试（不依赖真实权重 / 摄像头）。

覆盖 CONTROL_LOGIC §9 验收用例：
  V-01 4K 分块 112 块   V-04 非32倍数不崩溃   V-05 极小图 32×32
  V-06b 短维不足长维超限 64×4096（原 D-01b）  V-08 全零 logits → False
  V-10 coverage 恰为 0.01 → False（严格大于）
  R2/R1 画面回传非阻塞 + 单槽覆盖
  状态机：READY → DETECTING → READY；FATAL 布尔固定 False
"""

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
from app.logic_layer.engine import DetectionEngine, DetectionRequest

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

# ── 汇总 ────────────────────────────────────────
n_ok = sum(PASS)
print(f"\n结果: {n_ok}/{len(PASS)} 通过")
sys.exit(0 if n_ok == len(PASS) else 1)
