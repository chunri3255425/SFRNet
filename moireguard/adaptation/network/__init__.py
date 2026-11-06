"""
网络出口适配层实现

  http_server.py  纯标准库 HTTP（REST + SSE 广播），零第三方依赖

通道选型说明：板端内存预算 4GB，且 v2.2 引用约束要求平台字样只出现在
adaptation/ 与 build/ 之下——HTTP 用 stdlib http.server 足够支撑取证
微服务与演示大屏两类消费者，不引入 FastAPI/Flask 依赖面。
"""
