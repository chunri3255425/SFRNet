"""鸿蒙相机 Kit 实现占位。

鸿蒙侧 Python 运行时方案未定（SDD §6 风险 #4），当前仅保留接口位。
实现策略：Python 独立进程 + IPC，由 ArkTS 壳送帧（NDBuffer→共享内存），
或 hdc 通道；确认后在 grab() 内对接，接口不变。
"""

from app.adaptation.camera.base import Frame, ICameraAdapter


class OhosCameraAdapter(ICameraAdapter):
    """OpenHarmony 相机 Kit 适配（占位）。"""

    def open(self, **kwargs) -> None:
        raise NotImplementedError("鸿蒙相机 Kit 适配待 OH 镜像方案确认后实现（SDD §6 风险 #4）")

    def grab(self, timeout_ms: int = 3000) -> Frame:
        raise NotImplementedError("鸿蒙相机 Kit 适配待 OH 镜像方案确认后实现")

    def close(self) -> None:
        pass
