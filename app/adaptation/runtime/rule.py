"""RuleRuntime — 把编译好的判别规则图跑起来（板端 NPU / 开发机 ONNX）。

这件"模型"没有权重文件可训：图由 `build/export/rule.py` 从常量解析生成，
ONNX 与 RKNN 是同一份 spec() 的两个产物。所以本类只负责**搬运像素**，
判据、阈值、增益、三道门全在图里，Python 侧一个数字都不参与。

与 SFRNet 后端的三处不同（都是被实测逼出来的，不是风格偏好）：

1. 值域。规则图的 p3 绝对门定义在 **0-255 灰度域**的带内峰功率上（AMP=64）。
   引擎走 wants_gray 快路时交来的就是 0-255 的 uint8 亮度，直接进图；旧那条
   [0,1] float32 三通道入口仍支持（测试、以及任何按老契约写的调用侧），
   折灰后乘回 255。不是多此一举：让 runtime 负责量纲，阈值才能留在图里。
2. 通道。判据只看亮度（摩尔纹是栅格干涉，彩色只是它的染色版本），所以 RGB
   先按 BT.601 折成灰度 —— 与标定时的 `cv2.IMREAD_GRAYSCALE`（JPEG 直接取 Y 分量）
   同一支系数，两边才可比。现在这一步由引擎对整图做一次 `cv2.cvtColor`：
   实测 330 片上，整型取整后的 prob 与 float 域折灰、与 JPEG 自己的 Y 三路
   **片级翻转 0 次**（最大 |Δprob| 0.021，都在门限饱和区），见
   `.scratch/gray_path_probe.py`（本机过程件，不入库）。
3. 输出。每片给一个标量 prob（整片一个结论），不是逐像素图 —— 这是"像素级
   输出不保"的那次裁剪的落地形态。既然一片只有一个结论，就不再把它广播成
   整片常数让引擎去融合：引擎把标量直接写进片网格，Decider 数的是过门片占比。
   **口径代价已实测**（494 张 / 50,731 片，`.scratch/coverage_metrics.py`）：
   C=0.01 下 片均分 92.71%、像素覆盖率 91.70%、片计数占比 92.51%、
   片窗加权均分 92.71%（误报全 0.0%），片计数与片均分之间只差 1 张；
   coverage 的单位从此是"片占比"，粒度 = 1/片数（4K 112 片 → 0.9%，
   C=0.01 等于"至少 2 片过门"）。

形状是硬约束：图里两个 512×512 的 DCT 矩阵按片边长写死，喂进来必须正好
`TILE×TILE`；不足按复制边界补到 TILE（与引擎 C-01 的零信息损失填充同一路子），
超出不接管——那是配置错误，静默 resize 会造出标定里没见过的像素。

依赖：板端 rknnlite；开发机 onnxruntime。按扩展名分派，不做静默回退
（悄悄跑成另一条后端，耗时与数值结论都会失真）。**板端只允许 NPU**：`.onnx`
在板端平台直接拒装——它只是开发机的验证件，不是板上的执行体。

**片级并行（2026-11-22，§20.12）**。一片一个结论、片与片之间没有任何依赖，
所以同一进程里开多路 RKNNLite、一路一线程，是安全的，也是板上实测最值钱的一条：
1 路 92 片/s → 2 路 1.9× → 3 路 **2.7×（250 片/s）** → 4 路无增益。天花板不是
2 核 NPU 的算力，是每片约 11 ms 里的 host 部分被算力掩盖掉了 —— 2 核在 7.5 ms/片
时才能跑到 267 片/s。切块用**连续段**而不是交错：片的源是整帧灰度图上的视图，
相邻片共享 cache line。**数值逐位不变**（同一批 160 片，不同路数 Σprob 相同）。

中转缓冲（落地 256 KB + 转置 256 KB + float32 1 MB）跟着线程走，一路一块：
转置与类型转换都是原地写，共用会让 A 的片被 B 覆盖——这种错不崩，只让 prob
偶发乱跳，比崩更难查。转置用 `cv2.transpose`（uint8 专用快路，0.9 ms/片），
numpy 的 `ascontiguousarray` 要 1.9 ms；但**先落地再转置**这一步不能省：
对 strided 视图直接转置实测 6.4 ms/片（cv2 对非连续输入走通用路径）。
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from app.adaptation.runtime.base import IRuntimeAdapter, RuntimeLoadError

try:                     # 引擎折灰也用 cv2；纯 numpy 兜底仍保留
    import cv2
except ImportError:      # pragma: no cover
    cv2 = None

TILE = 512
# 片级并行路数：板上实测 1 路 92 片/s、2 路 1.9×、3 路 2.7×、4 路无增益
# （250 片/s 已是两核 NPU 的天花板）。默认取 3，实测见 docs/guides/BOARD_BRINGUP.md §20.12。
MAX_LANES = 3
# BT.601 亮度系数：与 cv2.IMREAD_GRAYSCALE / JPEG 的 Y 分量一致
Y_RGB = np.array([0.299, 0.587, 0.114], dtype=np.float32)
# 开发机默认产物路径（板端用 /usr/share/moireguard/models/rule_v5.rknn，走配置）
DEV_MODEL = "build/models/rule_v5.onnx"
BOARD_MODEL = "/usr/share/moireguard/models/rule_v5.rknn"


def default_model_path(platform: str) -> str:
    """组合根挑默认模型用：规则后端的产物与 SFRNet 不是同一个文件。"""
    return DEV_MODEL if platform == "host" else BOARD_MODEL


class _Lane:
    """一路执行器 + 它私有的中转缓冲。

    缓冲必须私有：转置与类型转换都是原地写，两路共用同一块会让 A 的片被 B
    覆盖。这种错不会崩，只会让 prob 偶发乱跳 —— 比崩更难查，所以按线程私有。
    """

    __slots__ = ("handle", "exec", "gray", "flip", "f32")

    def __init__(self, handle, exec_fn):
        self.handle = handle       # RKNNLite / onnxruntime session（unload 用）
        self.exec = exec_fn        # 接受 (1,1,512,512) float32 → 运行时原始输出列表
        self.gray = None           # (TILE,TILE) uint8：strided 视图落地
        self.flip = None           # (TILE,TILE) uint8：转置结果
        self.f32 = None            # (1,1,TILE,TILE) float32：喂 NPU 的那块


class RuleRuntime(IRuntimeAdapter):
    """规则图后端。`.rknn` 走 NPU，`.onnx` 走 onnxruntime（同一张图，不同执行器）。"""

    # 判据只看亮度、每片只出一个结论：能力标记就是压胶水的声明根据。
    # 它们不是优化开关（“关掉会更快”是假的），而是契约声明：引擎据此决定
    # 整图折灰与否、融合在像素域还是片网格。详见模块 docstring 的三处不同。
    wants_gray = True
    scalar_output = True

    def __init__(self, core_mask: int | None = None, tile_workers: int = 0):
        """Args:
            core_mask: 传给 init_runtime 的 NPU 核掩码；None=全核（交给运行时分配）。
            tile_workers: 片级并行路数。0=自动（`.rknn` 取 MAX_LANES，`.onnx` 恒 1）；
                1=老行为（逐片串行）。路数只改并行度，不改口径 —— 片与片无依赖，
                网格结论逐位一致（板上 160 片同一批作业 Σprob 相同）。
        """
        self._core_mask = core_mask
        self._want = max(1, int(tile_workers))
        self._lanes: list[_Lane] = []
        self._pool: ThreadPoolExecutor | None = None
        self._kind = ""
        self._rknn_cls = None

    @property
    def lanes(self) -> int:
        """实际开启的并行路数（附加实例建不起来会自动降级，不拦启动）。"""
        return len(self._lanes)

    # ── 加载 ────────────────────────────────
    def load(self, model_path: str, **kwargs) -> None:
        ext = os.path.splitext(model_path)[1].lower()
        if ext == ".rknn":
            self._load_rknn(model_path)
        elif ext == ".onnx":
            self._guard_npu_only(model_path)
            self._load_onnx(model_path)
        else:
            raise RuntimeLoadError(f"规则后端只认 .rknn / .onnx，拿到 {ext or model_path}")

    @staticmethod
    def _guard_npu_only(model_path: str) -> None:
        """板端只允许 NPU。规则图的 .onnx 与 .rknn 数值等价（转置不变性已证），
        所以在开发机用它验结论不损失什么；但在板端走 .onnx 等于把判别放回 CPU，
        耗时、NPU 利用率、上板可行性当场失真，而程序一行错都不报。
        """
        from app.adaptation.factory import LINUX, OHOS, detect_platform
        plat = detect_platform()
        if plat in (LINUX, OHOS):
            raise RuntimeLoadError(
                f"E-BACKEND-CPU-FORBIDDEN: 板端平台（{plat}）只允许 NPU，拒绝加载"
                f"规则图的 {model_path}（.onnx 走 onnxruntime / CPU）。先跑 "
                f"`python build/export/rule.py rknn` 生成 .rknn 再上板。")

    def _open_rknn(self, model_path: str):
        """建一路 RKNNLite 并初始化运行时，返回 (handle, exec)。"""
        rknn = self._rknn_cls()
        if rknn.load_rknn(model_path) != 0:
            raise RuntimeLoadError(f"加载规则 .rknn 失败：{model_path}")
        ret = rknn.init_runtime(core_mask=self._core_mask
                                or self._rknn_cls.NPU_CORE_0_1_2)
        if ret != 0:                      # 与 runtime/rknn 同策略：全核失败退默认核
            ret = rknn.init_runtime()
        if ret != 0:
            raise RuntimeLoadError(f"规则图 runtime 初始化失败，返回码 {ret}")
        return rknn, (lambda x: rknn.inference(inputs=[x]))

    def _load_rknn(self, model_path: str) -> None:
        try:
            from rknnlite.api import RKNNLite
        except ImportError as e:
            raise RuntimeLoadError(
                "未安装 rknnlite（规则图在 NPU 上跑依赖 rknn-toolkit-lite2）"
            ) from e
        self._rknn_cls = RKNNLite
        try:
            self._lanes = [_Lane(*self._open_rknn(model_path))]
        except RuntimeLoadError:
            raise
        except Exception as e:
            raise RuntimeLoadError(f"规则 RKNN 初始化异常: {e}") from e
        self._kind = "rknn"
        # 附加路：建不起来就少一路，不拦启动 —— 单路就是老行为，慢但结论一样。
        for _ in range(self._want - 1):
            try:
                self._lanes.append(_Lane(*self._open_rknn(model_path)))
            except Exception:      # noqa: BLE001 - 降级到已建成的路数
                break

    def _load_onnx(self, model_path: str) -> None:
        try:
            import onnxruntime as ort
        except ImportError as e:
            raise RuntimeLoadError("未安装 onnxruntime（开发机规则后端依赖）") from e
        try:
            sess = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])
            name = sess.get_inputs()[0].name
            # 开发机只开一路：ort 自己已经多线程，而且这条路是验结论用的，
            # 并行只添乱（494 张全量对比要求逐位可复现）。
            self._lanes = [_Lane(sess, lambda x: sess.run(None, {name: x}))]
            self._kind = "onnx"
        except Exception as e:
            raise RuntimeLoadError(f"规则 ONNX 加载失败: {e}") from e

    # ── 推理 ────────────────────────────────
    def infer(self, tensor: np.ndarray) -> np.ndarray:
        """一片进，一个标量出：[1,1,1,1] float32，值为 prob-0.5（logits）。

        入口收两种形状，因为引擎的两条路都真实存在：
          [H,W] uint8         —— wants_gray 快路：引擎已整图折灰，这片是个视图（不连续），
                                 量纲 0-255，与标定同域，所以这里**不做任何缩放**。
          [1,3,H,W] float32   —— 老契约（测试/直接调用者）：[0,1] 三通道，折灰后乘回 255。
        两种入口出来的是同一个数（实测 330 片 0 翻转），所以拿哪条都不改变结论。

        H=W=TILE 由图写死；单边不足 TILE 时复制边界补齐（见模块 docstring 的「形状是硬约束」段）。
        """
        if not self._lanes:
            raise RuntimeLoadError("模型未加载")
        return self._run(self._as_gray(tensor), self._lanes[0])

    def infer_tiles(self, patches, guard=None):
        """片网格整批：按路切**连续块**，一路一线程，返回顺序与 patches 一致。

        连续块而不是交错（i::k）：一片的源是整帧灰度图上的视图，相邻片共享大量
        cache line，连续块让每路吃到自己的局部性。

        guard 每片前调一次（含单路分支），抛什么就原样抛回去 —— 运行时不知道
        停机闸长什么样，只负责“每片问一次”。
        """
        if not self._lanes:
            raise RuntimeLoadError("模型未加载")
        n = len(patches)
        out: list = [None] * n
        k = min(len(self._lanes), n)
        if k <= 1:
            for t in range(n):
                if guard is not None:
                    guard()
                out[t] = self._run(self._as_gray(patches[t]), self._lanes[0])
            return out
        if self._pool is None:
            self._pool = ThreadPoolExecutor(max_workers=MAX_LANES,
                                            thread_name_prefix="rule-lane")
        errs: list[BaseException] = []
        futs = []
        for li in range(k):
            lo, hi = li * n // k, (li + 1) * n // k
            futs.append(self._pool.submit(self._run_chunk, patches[lo:hi],
                                          self._lanes[li], guard, out, lo, errs))
        for f in futs:
            f.result()             # 只等收尾；异常已经收进 errs，按原样抛
        if errs:
            raise errs[0]
        return out

    def _run_chunk(self, chunk, lane, guard, out, base, errs) -> None:
        try:
            for t, tensor in enumerate(chunk):
                if guard is not None:
                    guard()
                out[base + t] = self._run(self._as_gray(tensor), lane)
        except BaseException as e:     # noqa: BLE001 - 原样交回调用线程
            errs.append(e)

    def _as_gray(self, tensor: np.ndarray) -> np.ndarray:
        if tensor.ndim == 2:
            if tensor.dtype != np.uint8:
                raise ValueError(f"灰度快路要 [H,W] uint8（0-255 域），拿到 "
                                 f"{tensor.shape} {tensor.dtype}")
            return tensor
        if tensor.ndim == 4 and tensor.shape[0] == 1 and tensor.shape[1] == 3:
            return self._to_gray(tensor[0])
        raise ValueError(f"规则后端要 [H,W] uint8 或 [1,3,H,W] float32，"
                         f"拿到 {tensor.shape}")

    def _run(self, gray: np.ndarray, lane: _Lane) -> np.ndarray:
        prob = float(np.asarray(lane.exec(self._layout(gray, lane))[0]).ravel()[0])
        if not np.isfinite(prob):
            # 图上三道门全是有限运算，出 inf/nan 只会是喂进去的东西或运行时本身坏了
            raise RuntimeError(f"规则图输出非有限值 {prob}（不应发生，检查输入值域）")
        return np.full((1, 1, 1, 1), prob - 0.5, dtype=np.float32)

    @staticmethod
    def _to_gray(chw: np.ndarray) -> np.ndarray:
        g = np.tensordot(Y_RGB, chw.astype(np.float32, copy=False), axes=(0, 0))
        # 乘回 255：p3 绝对门标定时就在 0-255 灰度域（AMP=64）；顺带挡住越界输入
        return np.clip(g * 255.0, 0.0, 255.0)

    def _layout(self, gray: np.ndarray, lane: _Lane) -> np.ndarray:
        if min(gray.shape) < TILE:
            pad = [(0, max(0, TILE - s)) for s in gray.shape]
            gray = np.pad(gray, pad, mode="edge")
        if gray.shape != (TILE, TILE):
            raise ValueError(f"规则图定死 {TILE}×{TILE}，拿到 {gray.shape}")
        if self._kind == "onnx":
            return np.ascontiguousarray(gray[None, :, None, :], dtype=np.float32)
        # NHWC（实测：toolkit 对 (1,512,1,512) 直接报 "expect 'nhwc' like (1,1,512,512)"）。
        # [n][h=0][w][c] 的元素序是 (w,c)，所以要把行/列频率轴转置过去。
        #
        # 顺序要紧：**先落地，再转置**。gray 在引擎整帧路上是 12 MB 灰度图上的
        # 视图（`img[y:y+512, x:x+512]`），直接转置等于“按列读源”：每读一个元素
        # 跨一个 4 KB 行，几乎全是 cache miss。板上实测（§20.12）：直接转 6.38 ms/片，
        # 先落地再转 1.01 ms/片；落地这 256 KB 拷贝是必需的，不是保守。
        #
        # 转置用 cv2（uint8 专用快路）而不是 numpy 的 ascontiguousarray：
        # 同为 512×512 uint8，cv2.transpose 0.9 ms、numpy 1.9 ms，逐值一致。
        #
        # 缓冲一路一块（lane），每次原地写：一片 1.25 MB，165 片一帧 206 MB
        # 的 malloc/free 省下来，也避开了大块 mmap 的缺页。
        #
        # 转置本身不能省。图对 X→Xᵀ 在 float64 / onnx 上数值确实不变（同一片
        # prob 差 0），但板端 toolkit 的 NHWC 重排下实测可区分（同片 prob 0.0 → 0.5），
        # 说明内存序在板上不是无所谓，必须与开发机保持同一种喂法。
        if lane.f32 is None:
            lane.gray = np.empty((TILE, TILE), np.uint8)
            lane.flip = np.empty((TILE, TILE), np.uint8)
            lane.f32 = np.empty((1, 1, TILE, TILE), np.float32)
        if gray.flags.c_contiguous:
            src = gray
        else:
            np.copyto(lane.gray, gray)
            src = lane.gray
        if cv2 is not None:
            cv2.transpose(src, lane.flip)
            np.copyto(lane.f32[0, 0], lane.flip)
        else:                                   # pragma: no cover - 无 cv2 的退路
            np.copyto(lane.f32[0, 0], src.T)
        return lane.f32

    def warmup(self) -> None:
        if not self._lanes:
            raise RuntimeLoadError("模型未加载")
        # 每一路都要热：板上跑的是灰度快路，只热老路径等于让第一帧替全局付首轮开销；
        # 多路只热一路，则第 2/3 路的首轮开销会落在第一帧上（而且是在限时里）。
        for lane in self._lanes:
            self._run(self._as_gray(np.zeros((1, 3, TILE, TILE), dtype=np.float32)), lane)
            self._run(np.zeros((TILE, TILE), dtype=np.uint8), lane)

    def unload(self) -> None:
        if self._pool is not None:
            self._pool.shutdown(wait=True)
            self._pool = None
        for lane in self._lanes:
            if lane.handle is not None:
                try:
                    lane.handle.release()
                except Exception:
                    pass
                lane.handle = None
            lane.exec = None
            lane.gray = lane.flip = lane.f32 = None
        self._lanes = []

    # ── 诊断 ────────────────────────────────
    @property
    def kind(self) -> str:
        return self._kind
