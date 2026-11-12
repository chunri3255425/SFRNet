"""YOLO 级联接入冒烟测试（不依赖真实权重/摄像头/板子）。

覆盖 SDD v2.4.1 §3.3 / §4.2 / §4.3 的关键行为：
  P3   门控失败 → 禁用级联回退全图，不 FATAL
  P4   三态输出 screen_detected / has_moire / no_moire；布尔派生兼容
  灰区 prefer_screen 宁报不漏
  后处理 decode（DFL 期望投影 + sigmoid conf）与 letterbox 坐标还原
  v2.3 兼容：不装 gate 时行为与旧版一致（风险 #17 双路对照的代码侧基线）
  result_to_bool 归约：screen_detected/has_moire → True，no_moire/False → False

运行：python tests/test_yolo_cascade.py
"""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.adaptation.camera.base import Frame, ICameraAdapter
from app.adaptation.device_io.base import IDeviceIO, result_to_bool
from app.logic_layer.engine import DetectionEngine, DetectionRequest
from app.model_layer.detectors.yolo_postprocess import decode_boxes, letterbox
from app.model_layer.detectors.yolo_detector import YoloGateError, YoloScreenGate

PASS = []


def check(name: str, cond: bool, detail: str = ""):
    PASS.append(cond)
    print(f"  {'✅' if cond else '❌'} {name}" + (f" — {detail}" if detail else ""))


# ── 假件 ────────────────────────────────────────
import time


class FakeCamera(ICameraAdapter):
    _seq = 0

    def __init__(self, size=(640, 480)):
        self._size = size

    def open(self): ...
    def close(self): ...

    def grab(self, timeout_ms=3000) -> Frame:
        FakeCamera._seq += 1
        h, w = self._size
        data = np.zeros((h, w, 3), np.uint8)
        return Frame(data=data, width=w, height=h, channels=3,
                     timestamp=time.time(), frame_id=f"f{FakeCamera._seq}")


class FakeSfrRuntime:
    """SFRNet 替身：整块 logits 恒为 value。"""

    def __init__(self, value: float):
        self.value = value

    def infer(self, tensor):
        return np.full((1, 1, tensor.shape[2], tensor.shape[3]), self.value, np.float32)

    def warmup(self): ...
    def unload(self): ...


class FakeYoloRuntime:
    """YOLO 裸主干替身：返回合成三尺度特征图。"""

    def __init__(self, logits_by_grid: dict | None = None):
        self.logits_by_grid = logits_by_grid or {}   # {(stride, gy, gx): logit}

    def infer(self, nchw):
        rng = np.random.default_rng(0)
        feats = []
        for s, g in ((8, 80), (16, 40), (32, 20)):
            # 底噪取 -8：sigmoid≈3e-4，低于宽收门限（真实模型的背景分布形态）
            f = rng.normal(-8, 0.01, (1, 65, g, g)).astype("float32")
            for (ss, gy, gx), v in self.logits_by_grid.items():
                if ss == s:
                    f[0, 64, gy, gx] = v
            feats.append(f)
        return feats


class BoomGate:
    """每次 screen_conf_max 都抛错（P3 降级用例）。"""

    def screen_conf_max(self, frame):
        raise YoloGateError("boom")

    def screened(self, conf):
        return False


class StubGate:
    """可编程门控替身（引擎行为用例）。"""

    def __init__(self, conf_fn):
        self._conf_fn = conf_fn
        self.calls = 0

    def screen_conf_max(self, frame):
        self.calls += 1
        return self._conf_fn(frame)

    def screened(self, conf):
        return conf >= 0.5


class RecIO(IDeviceIO):
    def __init__(self):
        self.indicated = []
        self.logs = []

    def read_config(self):
        return {}

    def log(self, level, msg):
        self.logs.append((level, msg))

    def indicate(self, result):
        self.indicated.append(result)


def make_engine(gate, sfr_value=2.0, size=(640, 480)):
    io = RecIO()
    eng = DetectionEngine(FakeCamera(size), FakeSfrRuntime(sfr_value), io, yolo_gate=gate)
    eng.state = "READY"      # 跳过 bootstrap（单帧行为测试）
    return eng, io


# ── 1. 后处理：decode + letterbox 坐标还原 ─────────
def t_postprocess():
    print("\n[1] yolo_postprocess decode / 坐标还原")
    feats = FakeYoloRuntime({(8, 20, 20): 8.0}).infer(np.zeros((1, 3, 640, 640), np.float32))
    boxes = decode_boxes(feats, nc=1)
    check("强响应格点解出 1 框", len(boxes) == 1, f"n={len(boxes)}")
    check("conf = sigmoid(8) ≈ 0.9997", abs(boxes[0, 4] - 0.99966) < 1e-3, f"{boxes[0, 4]:.4f}")
    exp = (20 + 0.5) * 8
    check("框中心在格点 (164,164) 附近",
          abs(boxes[0, 0] - exp) < 4 and abs(boxes[0, 1] - exp) < 4,
          f"cx={boxes[0, 0]:.1f} cy={boxes[0, 1]:.1f}")

    img = np.zeros((1080, 1920, 3), np.uint8)
    lb, scale, px, py = letterbox(img, 640)
    check("1920×1080 letterbox: scale=1/3, 灰边只在上下",
          abs(scale - 1 / 3) < 1e-6 and px == 0 and py == 140,
          f"scale={scale:.4f} pad=({px},{py})")

    gate = YoloScreenGate(FakeYoloRuntime({(8, 20, 20): 8.0}))
    b2 = gate.detect(img)
    check("坐标还原到原图域（cx≈(164-0)/scale）",
          len(b2) == 1 and abs(b2[0, 0] - (boxes[0, 0] - px) / scale) < 1.0,
          f"cx={b2[0, 0]:.1f}" if len(b2) else "no box")
    check("screen_conf_max = 0.9997", abs(gate.screen_conf_max(img) - 0.99966) < 1e-3)


# ── 2. 门控判定：门限 + 灰区 ───────────────────────
def t_gate_policy():
    print("\n[2] 门限与灰区策略（v2.4.1 宁报不漏）")
    g = YoloScreenGate(None, screen_conf=0.5, gray_zone=0.1, gray_policy="prefer_screen")
    check("conf=0.55 ≥ 门限 → 有屏", g.screened(0.55))
    check("conf=0.45 落灰区 + prefer_screen → 有屏", g.screened(0.45))
    g2 = YoloScreenGate(None, screen_conf=0.5, gray_zone=0.1, gray_policy="fallback")
    check("conf=0.45 + fallback → 无屏", not g2.screened(0.45))
    check("conf=0.2 远低于门限 → 无屏", not g.screened(0.2))
    try:
        YoloScreenGate(None, screen_conf=1.5)
        check("非法门限拒绝", False)
    except ValueError:
        check("非法门限拒绝", True)


# ── 3. 引擎级联：有屏短路 ──────────────────────────
def t_engine_screen():
    print("\n[3] 引擎级联：有屏短路出 screen_detected")
    eng, io = make_engine(StubGate(lambda f: 0.9))
    r = eng.detect_once(DetectionRequest())
    check("result = screen_detected", r.result == "screen_detected", r.result)
    check("stage1_conf = 0.9", abs((r.stage1_conf or 0) - 0.9) < 1e-9)
    check("SFRNet 未跑（tiles=0）", r.tiles == 0, f"tiles={r.tiles}")
    check("派生布尔 has_moire=False", r.has_moire is False)
    check("indicate 收到三态字符串", io.indicated and io.indicated[0] == "screen_detected")
    check("state 回 READY", eng.state == "READY")


# ── 4. 引擎级联：无屏走 SFRNet ─────────────────────
def t_engine_no_screen():
    print("\n[4] 引擎级联：无屏 → SFRNet 判纹（三态后两态）")
    eng, io = make_engine(StubGate(lambda f: 0.2), sfr_value=2.0)
    r = eng.detect_once(DetectionRequest())
    check("result = has_moire（恒值 2.0 → 高 coverage）", r.result == "has_moire", r.result)
    check("stage1_conf = 0.2 透传", abs((r.stage1_conf or 0) - 0.2) < 1e-9)
    check("tiles > 0（全图滑窗）", r.tiles > 0)
    check("has_moire 布尔与 result 一致", r.has_moire is True)

    eng2, _ = make_engine(StubGate(lambda f: 0.2), sfr_value=-2.0)
    r2 = eng2.detect_once(DetectionRequest())
    check("负 logits → result = no_moire", r2.result == "no_moire", r2.result)
    check("no_moire → indicate(False) 归约", result_to_bool("no_moire") is False)
    check("screen_detected → indicate(True) 归约", result_to_bool("screen_detected") is True)
    check("has_moire → indicate(True) 归约", result_to_bool("has_moire") is True)
    check("布尔兼容：indicate(False) 归约", result_to_bool(False) is False)


# ── 5. P3 降级：门控炸 → 回退全图，不 FATAL ─────────
def t_engine_degrade():
    print("\n[5] P3 降级：Stage-1 失败禁用级联")
    eng, io = make_engine(BoomGate(), sfr_value=-2.0)
    r = eng.detect_once(DetectionRequest())
    check("回退全图路径，result = no_moire", r.result == "no_moire", r.result)
    check("warnings 含 cascade_disabled", "cascade_disabled" in r.warnings)
    check("WARN 日志有记录", any(lv == "WARN" for lv, _ in io.logs))
    check("未 FATAL", eng.state != "FATAL", eng.state)
    r2 = eng.detect_once(DetectionRequest())
    check("后续帧不再触碰门控（v2.3 行为，无新 WARN）",
          "cascade_disabled" not in r2.warnings and r2.result == "no_moire")


# ── 6. v2.3 兼容：不装 gate ────────────────────────
def t_v23_compat():
    print("\n[6] v2.3 兼容路径（级联关闭 = 双路对照基线）")
    eng, io = make_engine(None, sfr_value=2.0)
    r = eng.detect_once(DetectionRequest())
    check("无 gate → result = has_moire（与旧布尔行为一致）", r.result == "has_moire")
    check("stage1_conf 为 None", r.stage1_conf is None)
    check("indicate 收到三态", io.indicated[0] == "has_moire")


if __name__ == "__main__":
    t_postprocess()
    t_gate_policy()
    t_engine_screen()
    t_engine_no_screen()
    t_engine_degrade()
    t_v23_compat()
    n_ok = sum(PASS)
    print(f"\n结果: {n_ok}/{len(PASS)} 通过")
    sys.exit(0 if n_ok == len(PASS) else 1)
