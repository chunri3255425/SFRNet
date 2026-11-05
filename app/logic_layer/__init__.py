"""板端逻辑层 — 操作主体（SDD §4.3）。

红线：不 import 任何平台 SDK，不碰 state_dict。
组件：Bootstrapper（启动序列）/ DetectionEngine（状态机主体）/
     Decider（双门限判定）/ Supervisor（错误恢复）。
"""
