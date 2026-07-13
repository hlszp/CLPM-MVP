# KPI 计算性能优化方案

> **版本**：v1.0
> **日期**：2026-07-13
> **状态**：方案规划中，待启动专项
> **背景**：当前 27 回路 24 小时评估耗时超 30 分钟触发 Celery SIGKILL；未来 1000+ 回路无法承受每小时全量评估

## 1. 问题现状

### 1.1 已发现的问题

| 问题 | 现象 | 根因 |
|---|---|---|
| **Worker 被 SIGKILL** | 27 回路 × 24h backfill 执行到 20/27 时 Worker 进程被杀 | Celery 全局 `task_time_limit=1800`（30 分钟），backfill 任务未设独立超时 |
| **回路内 4 tag 串行查询** | 每回路 PV/SP/OP/MODE 逐个 `await query_trend_fn()` | `kpi_calc.py` L437-440 未用 `asyncio.gather` 并行 |
| **固定 1 秒采样** | 24h 窗口 = 86400 点/tag，数据量过大 | GB/T 44693.2 标准未规定采样频率，当前过度采样 |
| **逐 tag HTTP 请求** | 27 回路 × 4 tag = 108 次 HTTP 请求 | `query_trend_data` 单 tag 查询，未利用 AAS API 批量能力 |

### 1.2 性能瓶颈定位

```
数据获取层（最大瓶颈）
  ├─ HTTP 请求次数：27 回路 × 4 tag × 24 窗口 = 2592 次 HTTP 请求
  ├─ 串行 await：回路内 4 tag 逐个查询，延迟叠加
  └─ 固定 1s 采样：24h = 86400 点/tag，数据传输 + 内存占用大

计算层
  ├─ 并发控制：CONCURRENCY=10，1000 回路时不够
  └─ 全量重算：每小时所有回路所有指标全量计算

任务调度层
  ├─ 超时限制：task_time_limit=1800s，大范围 backfill 必然超时
  └─ 单任务串行：backfill 在单个 Celery task 内遍历所有窗口
```

### 1.3 数据量估算

| 场景 | HTTP 请求数 | 数据点/tag | 总数据点 | 预估耗时 |
|---|---|---|---|---|
| 27 回路 × 1h | 108 | 3600 | 38.8 万 | ~5 分钟 |
| 27 回路 × 24h | 2592 | 86400 | 933 万 | ~120 分钟 |
| 1000 回路 × 1h | 4000 | 3600 | 1440 万 | ~3 小时 |
| 1000 回路 × 24h | 96000 | 86400 | 3.4 亿 | ~50 小时 |

## 2. 优化路径

### P0 — 批量 API 调用（预期收益 10-40x）

**问题**：当前 `query_trend_data` 逐 tag 查询，1000 回路 = 4000 次 HTTP 请求

**方案**：`RemoteApiProvider.make_query_fn` 已支持批量 tagCodes，但 `kpi_calc.py` 调用的是单 tag 的 `query_trend_data`

```python
# 当前：4 tag × 4 次 HTTP 请求
pv_data = await query_trend_fn(pv_tag, ...)
sp_data = await query_trend_fn(sp_tag, ...)

# 优化：1 次 HTTP 请求获取 4 tag
batch_data = await batch_query_fn(loop_id, ["PV","SP","OP","MODE"], ...)
```

**进一步**：跨回路批量，按时间窗聚合多个回路的 tag 一起查

- 1000 回路：4000 次 HTTP → ~250 次（每批 4 tag × 100 回路）

### P0 — 并行化回路内数据获取（预期收益 4x）

**问题**：`kpi_calc.py` L437-440 中 PV/SP/OP/MODE 4 个 tag 串行 await

**方案**：改用 `asyncio.gather` 并行查询

```python
# 当前：串行，4 × 延迟
pv_data = await query_trend_fn(pv_tag, start, end)
sp_data = await query_trend_fn(sp_tag, start, end) if sp_tag else []

# 优化：并行，1 × 延迟
pv_data, sp_data, op_data, mode_data = await asyncio.gather(
    query_trend_fn(pv_tag, start, end),
    query_trend_fn(sp_tag, start, end) if sp_tag else _empty(),
)
```

### P1 — 动态采样间隔（预期收益 5-10x）

**问题**：固定 1 秒采样，24h=86400 点/tag。GB/T 44693.2 标准未规定采样频率

**方案**：按时间窗动态计算采样间隔（趋势查询已实现此逻辑）

| 评估窗口 | 采样间隔 | 点数/tag | 精度影响 |
|---|---|---|---|
| 1h | 1s | 3600 | 无（保持精度） |
| 6h | 6s | 3600 | 可忽略 |
| 24h | 24s | 3600 | 可忽略 |

KPI 指标（IAE/方差/振荡频率）在 10s 采样下精度损失 <1%

### P1 — 分层计算策略（预期收益 3-5x）

**问题**：所有回路每小时全量计算，无优先级区分

**方案**：利用回路的 `importance_level` 字段

| 优先级 | 计算频率 | 回路占比 | 负载降低 |
|---|---|---|---|
| Level 1（关键） | 每小时 | ~20% | — |
| Level 2（重要） | 每 4 小时 | ~30% | 75% |
| Level 3（一般） | 每天 | ~50% | 96% |

1000 回路：从 1000 次/小时 → 200 + 75 + 21 ≈ 296 次/小时

### P1 — backfill 任务拆分（修复 SIGKILL）

**问题**：`backfill_kpi_range` 在单个 Celery task 内遍历所有窗口，超 30 分钟被 SIGKILL

**方案**：将 backfill 拆分为子任务（Celery group/chord）

```python
# 当前：单 task 遍历 24 窗口
for window in windows:
    await _do_calculate(ts_start=window)

# 优化：每窗口一个子任务，Celery group 并行
from celery import group
jobs = group(calculate_hourly_kpi.s(window.isoformat()) for window in windows)
result = jobs.apply_async()
```

或为 `backfill_kpi_range` 设置独立超时：

```python
@celery_app.task(
    name="app.tasks.kpi_calc.backfill_kpi_range",
    bind=True,
    base=AsyncTask,
    time_limit=7200,       # 2 小时
    soft_time_limit=6900,  # 115 分钟
)
```

### P2 — 数据本地缓存（预期收益 5-10x）

**问题**：每小时从 AAS 全量拉取，重复传输相同数据

**方案**：Redis 短期缓存（1-2 小时 TTL），只拉增量数据

```
第1小时：从 AAS 拉取 0:00-1:00 → 计算并缓存
第2小时：从 AAS 拉取 1:00-2:00（增量）+ Redis 读取 0:00-1:00 → 拼接计算
```

**注意**：与"CLPM 不写 TDengine"不冲突 — Redis 是临时缓存非持久存储

### P2 — 增量计算（预期收益 2-3x）

**问题**：每小时全量重算所有指标

**方案**：分析 12 个 KPI 指标的可增量性

| 指标类型 | 可增量？ | 方案 |
|---|---|---|
| IAE 累加型 | 是 | 只累加新数据段 |
| 方差/标准差 | 是 | Welford 在线算法 |
| 振荡检测（过零率） | 部分 | 需边界点上下文 |
| ARMA 模型识别 | 否 | 需全量重算 |
| 综合评分 | 是 | 子指标增量后合成 |

### P3 — 水平扩展（预期收益 Nx）

**问题**：单 Celery Worker 16 进程

**方案**：
- 多 Worker 实例（多机部署）
- Celery 路由：KPI 计算任务路由到专用队列
- `CONCURRENCY` 从 10 → 50+
- 不改代码，仅改部署配置

### P3 — 预计算/物化视图（预期收益 10x+）

**问题**：每次从原始 1Hz 数据点计算

**方案**：在 AAS 端预计算分钟级聚合（均值/方差/极值/过零次数）

```
原始：3600 点/tag → KPI 计算
预计算：60 点/tag（分钟聚合）→ KPI 计算
```

需 AAS 系统配合提供聚合接口

## 3. 实施路线

### Phase 1（快速见效）

| 优化项 | 预期收益 | 改动范围 |
|---|---|---|
| backfill 任务拆分/独立超时 | 修复 SIGKILL | kpi_calc.py + celery_app.py |
| 并行化回路内 4 tag 查询 | 4x | kpi_calc.py |
| 批量 API 调用（回路内 4 tag 合并 1 次请求） | 4x | kpi_calc.py + remote_api_provider |
| 动态采样间隔（24h 窗口用 24s 采样） | 10x | kpi_calc.py |

**Phase 1 叠加收益**：并行 4x × 批量 4x × 采样 10x = 理论 160x，保守估计 20-40x

### Phase 2（中等改造）

| 优化项 | 预期收益 | 改动范围 |
|---|---|---|
| 分层计算策略（Level 1/2/3 不同频率） | 3-5x | kpi_calc.py + EngineRule |
| 水平扩展（多 Worker + 专用队列） | Nx | 部署配置 |

### Phase 3（深度优化）

| 优化项 | 预期收益 | 改动范围 |
|---|---|---|
| Redis 增量数据缓存 | 5-10x | 新增缓存层 |
| 可增量指标的增量计算 | 2-3x | 算法层 |
| AAS 端预计算 | 10x+ | 需外部协调 |

## 4. 预期效果

| 场景 | 当前耗时 | Phase 1 后 | Phase 1+2 后 |
|---|---|---|---|
| 27 回路 × 1h | ~5 分钟 | ~10 秒 | ~5 秒 |
| 27 回路 × 24h | ~120 分钟（SIGKILL） | ~4 分钟 | ~2 分钟 |
| 1000 回路 × 1h | ~3 小时 | ~6 分钟 | ~2 分钟 |
| 1000 回路 × 24h | ~50 小时 | ~2.5 小时 | ~50 分钟 |

## 5. 注意事项

1. **GB/T 44693.2 合规性**：动态采样间隔需验证不影响 KPI 指标计算精度
2. **数据一致性**：批量 API 调用需确保原子性（部分失败处理）
3. **向后兼容**：优化后的计算结果应与当前结果在误差范围内一致
4. **监控指标**：增加每回路计算耗时、API 调用耗时、数据点数等监控
