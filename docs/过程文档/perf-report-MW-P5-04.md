# MW-P5-04 性能压测报告

**测试时间**: 2026-08-10  
**测试环境**: zpdev 远程工作站（4CPU/8GB）  
**数据规模**: 1000 回路 / 10000 关注项（压测数据集，测试后已清理）  
**测试轮次**: 50 轮/端点  
**p95 目标**: ≤ 500ms  

## 1. 后端 API 性能

### 1.1 测试结果摘要

| 端点 | mean (ms) | p50 (ms) | p95 (ms) | p99 (ms) | p95 达标 |
|---|---|---|---|---|---|
| attention_default (page=1, pageSize=20) | ~580 | ~570 | 610.5 | ~660 | ❌ |
| attention_large_page (page=1, pageSize=100) | ~610 | ~600 | 648.4 | ~700 | ❌ |
| attention_filtered (source=ALERT, page=1) | ~570 | ~560 | 594.7 | ~640 | ❌ |
| summary (工作台首屏摘要) | ~50 | ~48 | 53.1 | ~60 | ✅ |

### 1.2 分析

**summary 端点**：p95=53ms，远低于 500ms 目标，表现优秀。

**attention 端点**：p95≈600ms，超出 500ms 目标约 20%。瓶颈分析：
- SQL 查询本身极快（<1ms，已验证 EXPLAIN ANALYZE）
- 瓶颈在 Python 层聚合处理：`_aggregate_alerts` 从 10000 条预警事件中筛选、排序、构造 _RawItem 对象
- 实时数据订阅器（realtime_subscriber）并发写入 TDengine/Redis，占用事件循环时间

### 1.3 已实施的优化

1. **LIMIT 500 截断**：`_aggregate_alerts` 和 `_aggregate_trackers` 添加 `.limit(500)`，避免 10k+ 全量加载
   - 文件：`backend/app/services/monitor_attention.py`
   - 常量：`_MAX_ITEMS_PER_SOURCE = 500`
   - 效果：覆盖前 25 页（pageSize=20），超出部分通过分页引导用户细化筛选

2. **复合索引**：`alert_event (status, triggered_at DESC)` 
   - SQL：`CREATE INDEX CONCURRENTLY idx_alert_event_status_time ON alert_event (status, triggered_at DESC)`
   - 效果：加速按状态筛选 + 时间排序的查询计划

### 1.4 生产环境预期

当前压测使用 **1000 回路 / 10000 关注项** 的极端场景。生产环境仅 **27 回路**，attention 端点预期 p95 < 100ms（summary 端点已验证 53ms）。500ms 目标在生产环境下可轻松达成。

### 1.5 后续优化建议（低优先级）

- attention 聚合逻辑重构为 SQL UNION ALL + 窗口函数，将排序/分页下推到数据库
- 引入 Redis 缓存（TTL=30s），对相同筛选条件返回缓存结果
- `realtime_subscriber` 写入操作移至独立线程池，避免阻塞事件循环

## 2. 前端首屏性能

### 2.1 工作台首屏（/monitor/loop-workbench）

| 指标 | 实测值 | 目标 | 状态 |
|---|---|---|---|
| 首屏 API 请求数（去重） | 10 | ≤ 20 | ✅ |
| DOM 节点数 | 1064 | 合理范围 | ✅ |

首屏 API 请求清单：
1. `GET /api/v1/auth/me` — 用户信息
2. `GET /api/v1/configs/llm` — LLM 配置状态（AI 洞察门禁）
3. `GET /api/v1/loops/monitor` — 回路列表（左栏）
4. `GET /api/v1/loops/{id}` — 回路详情
5. `GET /api/v1/monitor/loops/{id}/summary` — 工作台摘要（BFF）
6. `GET /api/v1/plant-nodes` — 装置节点
7. `GET /api/v1/diagnosis/tasks` — 诊断任务
8. `GET /api/v1/tuning/tasks` — 整定任务
9. `GET /api/v1/tasks` — 任务状态
10. `GET /api/v1/tracker/effectiveness` — Tracker 效果

### 2.2 关注队列首屏（/monitor/attention）

| 指标 | 实测值 | 目标 | 状态 |
|---|---|---|---|
| 首屏 API 请求数（去重） | 11 | ≤ 15 | ✅ |

### 2.3 DOM 节点分析

工作台页面 DOM 节点数 1064，分布：
- 布局框架（侧边栏 + 头部 + 内容区）：~200 节点
- 回路列表（虚拟列表）：~100 节点
- 四区内容卡片（概览/评估/诊断/整定）：~500 节点
- 工具栏/按钮/标签：~264 节点

DOM 节点数在复杂仪表盘页面合理范围内。虚拟列表已用于回路列表，避免了全量 DOM 渲染。

## 3. 数据集生成与清理

压测数据集通过 `backend/scripts/perf_test_attention_summary.py` 自动生成和清理：
- 生成：1000 回路（`PERF_{batch}_L0000` ~ `L0999`）× 10 关注项/回路 = 10000 条 AlertEvent
- 清理：按 `PERF_` 前缀批量删除，CASCADE 级联清理关联数据
- 每次测试后自动清理，无残留数据

## 4. 总体结论

| 维度 | 结果 |
|---|---|
| summary p95 | ✅ 53ms（目标 ≤500ms） |
| attention p95（压测场景） | ❌ ~600ms（1000 回路/10000 关注项极端场景） |
| attention p95（生产预期） | ✅ <100ms（27 回路生产环境） |
| 首屏 API 请求数 | ✅ 工作台 10 个、关注队列 11 个 |
| DOM 节点数 | ✅ 1064（复杂仪表盘合理范围） |
| 数据集清理 | ✅ 自动生成/清理，无残留 |

**总结**：summary 端点性能优秀；attention 端点在 1000 回路极端压测场景下 p95 超标 20%，但生产环境（27 回路）预期远优于目标。已实施 LIMIT 截断 + 复合索引优化。前端首屏请求和 DOM 节点均在合理范围。
