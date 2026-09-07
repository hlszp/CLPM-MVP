"""测点子表重构（codex/tag-timeseries-refactor）测试支撑包.

内容：
- ``reference_data``：P0-4 独立密集参考数据生成器（确定性、独立于生产 builder）
- 后续阶段在此追加：契约基线证据、真实 TD/PG/Redis 集成测试入口

纪律：本包**禁止** import 生产端 logical_wide_builder / point_history_repository
（它们是被测对象）；允许 import 的生产代码仅限稳定契约
（``app.contracts.data_types``）与需要被固定行为的目标函数
（契约测试里显式 patch 外部依赖）。
"""
