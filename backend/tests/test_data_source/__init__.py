"""数据源抽象层单元测试.

覆盖：
- factory: 工厂按 DATA_SOURCE_TYPE 返回对应 Provider
- tdengine_provider: 包装 make_dataplanner_query_fn
- remote_api_provider: 远程 HTTP API 适配器（响应解析、质量码映射、错误处理）
- realtime_subscriber: Redis 缓存读写、活跃 Tag 查询
"""
