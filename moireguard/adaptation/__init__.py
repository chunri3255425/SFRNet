"""
适配层 (Adaptation Layer)

职责:
  - 屏蔽平台差异：怎么取一帧、用哪个后端跑模型、配置和日志落在哪、
    布尔结果如何指示、结果如何经网络出口
红线:
  ❌ 不含任何判定逻辑
  ❌ 不懂"摩尔纹"是什么
  ❌ 不 import logic_layer（消费能力一律经 base 中的抽象协议反向注入）

依赖方向: 逻辑层 → 模型层 → 适配层（本包对上游零感知）
接口定义见 SOFTWARE_DESIGN.md §4.1 与 base.py 逐字段 docstring。
"""
