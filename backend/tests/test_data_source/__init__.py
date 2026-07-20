"""数据源抽象层单元测试.

覆盖：
- factory: 工厂恒返回本地 TDengineProvider（计算全本地，2026-07-20 架构决策）
- tdengine_provider: 包装 make_dataplanner_query_fn
- remote_api_provider: 远程 HTTP API 适配器（响应解析、质量码映射、错误处理，
  仅历史数据导入链路使用，计算任务不经此类）
- realtime_subscriber: Redis 缓存读写、活跃 Tag 查询
"""
