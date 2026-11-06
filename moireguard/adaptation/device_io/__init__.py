"""
设备 IO 适配层实现

按 v2.2 §4.1.3 的 IDeviceIO（接口本体住在 adaptation/base.py，全局唯一定义处）：
  linux_io.py  Linux：/etc/moireguard 配置 + 标准输出日志 + GPIO sysfs 指示
  ohos_io.py   （待建：鸿蒙沙箱路径 + GPIO NAPI）
"""
