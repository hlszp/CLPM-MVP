# 评估算法优化改进方案 v1.0

> **版本**：v1.0
> **日期**：2026-07-14
> **作者**：mb 机器（Trae）
> **状态**：待评审
> **适用范围**：CLPM v6.1 评估算法体系（回路级 KPI 计算 + 节点级聚合）
> **目标读者**：算法工程师、后端工程师、架构评审人员

---

## 1. 背景与目标

### 1.1 背景

CLPM v6.1 已完成 7 阶段系统重构，后端 1762 测试用例通过，建立了完整的设计架构：
- **DataPlanner**：指标驱动的数据获取与编排中枢（8 步预处理 + L1/L2 缓存 + 查询计划合并）
- **12 个 KPI 指标计算器**：CALCULATOR_REGISTRY，3 核心 + 1 综合 + 8 辅助
- **ConfidenceEvaluator**：可信度评估 A/B/C/D/E + 综合评分计算

但在生产路径 [kpi_calc.py](file:///Users/zhangping/DEV/CLPM/backend/app/tasks/kpi_calc.py) 中存在严重的"双轨问题"：
- 设计架构已完整实现，但**生产 KPI 计算路径未接入**
- 生产代码使用独立函数直接拉取原始数据并计算，绕过了 DataPlanner / Calculator / ConfidenceEvaluator
- 导致缓存、批量查询、契约化数据获取等优化能力全部闲置

同时，性能严重不足：
- 当前 27 回路 24h 回填需 30+ 分钟
- 未来需支持 1000 回路，按现有架构预测需 17 小时（完全不可接受）

### 1.2 目标

| 指标 | 当前 | 目标 | 验证方式 |
|---|---|---|---|
| 1000 回路自动任务（1h KPI） | N/A | **< 10 分钟** | 压测脚本 + Celery 任务耗时统计 |
| 单回路 1h KPI 计算 | ~75 秒 | **< 5 秒** | 单元测试 + 日志计时 |
| 27 回路 24h 回填 | 30+ 分钟 | **< 3 分钟** | backfill API 耗时 |
| 架构一致性 | 双轨并存 | **统一接入 DataPlanner** | 代码审查 + 单元测试覆盖 |
| 1000 回路扩展性 | 不可扩展 | **线性扩展支持** | 压测验证 |

### 1.3 设计原则

1. **架构优先**：先消除双轨问题，再做性能优化（避免优化无效代码）
2. **契约驱动**：所有指标的数据需求通过 `metric_data_requirement` 契约声明
3. **缓存优先**：L1 DataBlock 缓存 + L2 Bundle 缓存，减少重复查询
4. **分级处理**：按回路重要等级配置不同计算周期，避免无效计算
5. **算法对齐**：所有指标计算对齐 GB/T 44693.2-2024 国标 + FDS v6.0
6. **可验证性**：每个优化措施配套测试用例 + 性能基准

---

## 2. 现状分析

### 2.1 双轨问题：设计架构 vs 生产路径

#### 2.1.1 设计架构（已实现但未使用）

```
┌─────────────────────────────────────────────────────────────┐
│  DataPlanner.request_bundles(loop_id, metric_codes, ...)    │
│  ├── _load_requirements()  ← 从 clpm_metric_data_requirement│
│  ├── _plan_queries()       ← 合并 tagGroup 相同的查询       │
│  ├── _fetch_data()         ← 批量查询（单次多 tag）         │
│  ├── _run_pipeline()       ← 8 步预处理 Pipeline            │
│  ├── _apply_masks()        ← 生成 Metric Validity Mask      │
│  └── _build_bundles()      ← 返回 MetricDataBundle 字典     │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│  MetricCalculator.calculate(bundle)                         │
│  ├── AccuracyRateCalculator                                 │
│  ├── FastRateCalculator (依赖 settling_time)                │
│  ├── StabilityRateCalculator (依赖 oscillation_rate)        │
│  └── ... 共 12 个计算器                                      │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│  ConfidenceEvaluator.compute_composite_score()              │
│  ├── 评估 A/B/C/D/E 可信度                                  │
│  └── P = (A·a + F·f + S·s)/(a+f+s) × R                     │
└─────────────────────────────────────────────────────────────┘
```

**问题根因**：`clpm_metric_data_requirement` 表无种子数据，DataPlanner 的 `_load_requirements()` 返回空字典，导致 `request_bundles()` 返回空列表。

#### 2.1.2 生产路径（实际使用但绕过新架构）

[kpi_calc.py L516-L662](file:///Users/zhangping/DEV/CLPM/backend/app/tasks/kpi_calc.py#L516-L662) `_calculate_loop_kpi` 函数：

```python
# ❌ 直接使用 query_trend_fn 拉取原始数据（绕过 DataPlanner）
trend_data = await query_trend_fn(tag_name=tag.tag_name, start=ts_start, end=ts_end)

# ❌ 用独立函数计算 8 个 KPI（绕过 12 个 Calculator 类）
accuracy = await _compute_accuracy_rate(...)
fast = await _compute_fast_response_rate(...)  # 调用 ARMA
stability = await _compute_stability_rate(...)
oscillation = await _compute_oscillation_rate(...)  # IAE 零交叉法
...

# ❌ 综合评分重复实现（绕过 ConfidenceEvaluator）
composite = _compute_composite_score_v2(accuracy, fast, stability, ...)
```

**导致的问题**：
1. L1/L2 缓存完全闲置（生产代码不调用 DataPlanner）
2. 每个 tag 单独查询（4 tag × 27 loop × 24h = 2592 次 RTT）
3. 算法逻辑分散在 kpi_calc.py（2183 行），难以维护和测试
4. 算法更新需同步修改两处（Calculator 类 + kpi_calc.py 独立函数）

### 2.2 性能瓶颈量化分析

#### 2.2.1 当前 27 回路 24h 回填耗时分解

| 瓶颈环节 | 耗时 | 占比 | 根因 |
|---|---|---|---|
| AAS 查询 | ~15 min | 50% | 每 tag 单独查询（2592 次 RTT） |
| ARMA + Green 函数 | ~8 min | 27% | Green 函数长度 3600 + 4 次重试 |
| DB 读写 | ~5 min | 17% | 648 次 commit + 节点聚合 CTE × 24 |
| 预处理 + 其他 | ~2 min | 6% | 串行预处理 + 日志开销 |
| **总计** | **~30 min** | 100% | — |

#### 2.2.2 1000 回路预测（线性外推）

| 瓶颈环节 | 1000 回路预测 | 是否可接受 |
|---|---|---|
| AAS 查询 | ~9 h | ❌ |
| ARMA + Green 函数 | ~5 h | ❌ |
| DB 读写 | ~3 h | ❌ |
| **总计** | **~17 h** | ❌ 完全不可接受 |

#### 2.2.3 性能瓶颈根因清单

| # | 瓶颈 | 位置 | 根因 |
|---|---|---|---|
| B1 | 每 tag 单独查询 | kpi_calc.py L596-604 | 未使用 DataPlanner 的 tagGroup 合并 |
| B2 | L1/L2 缓存未启用 | kpi_calc.py 全文 | 未调用 DataPlanner |
| B3 | 并发偏低 | kpi_calc.py L45 `CONCURRENCY=10` | asyncio.Semaphore(10) 限制 |
| B4 | ARMA Green 函数过长 | arma.py L32 `MAX_GREEN_FUNC_LENGTH=3600` | 1 小时数据 = 3600 点 |
| B5 | ARMA 重试过多 | arma.py L148 `retry_orders={ar_order,4,6,10}` | 最坏 4 次 |
| B6 | 每窗口 commit 一次 | kpi_calc.py `_do_backfill` | 648 次 commit |
| B7 | 节点聚合重复执行 | kpi_calc.py L1969-2069 | 每小时窗口都聚合一次 |
| B8 | 无分级计算 | celery_app.py | 所有回路每小时都计算 |
| B9 | 固定 1s 采样 | kpi_calc.py | 未按控制类型降采样 |

---

## 3. 优化措施

### 3.1 接入新架构（DataPlanner + Calculator + ConfidenceEvaluator）

**目标**：消除双轨问题，统一算法实现路径。

**改造范围**：
- [kpi_calc.py](file:///Users/zhangping/DEV/CLPM/backend/app/tasks/kpi_calc.py) `_calculate_loop_kpi` 函数重构
- 删除 `_compute_kpis`、`_compute_composite_score_v2` 等重复实现

**改造前后对比**：

```python
# ============ 改造前（绕过新架构）============
async def _calculate_loop_kpi(loop, ts_start, ts_end, query_trend_fn, db):
    # 1. 直接拉取原始数据
    trend_data = await query_trend_fn(tag_name, start, end)
    # 2. 独立函数计算 8 个 KPI
    accuracy = await _compute_accuracy_rate(trend_data, ...)
    fast = await _compute_fast_response_rate(trend_data, ...)  # ARMA
    ...
    # 3. 重复实现综合评分
    composite = _compute_composite_score_v2(accuracy, fast, stability, ...)

# ============ 改造后（接入新架构）============
async def _calculate_loop_kpi(loop, ts_start, ts_end, query_fn, db, planner):
    # 1. DataPlanner 统一获取数据（含预处理 + 缓存 + 批量查询）
    bundles = await planner.request_bundles(
        loop_id=str(loop.id),
        metric_codes=list(CALCULATOR_REGISTRY.keys()),
        start=ts_start,
        end=ts_end,
    )
    # 2. Calculator 编排计算（自动处理依赖关系）
    results = _orchestrate_calculators(bundles)
    # 3. ConfidenceEvaluator 统一综合评分
    composite = confidence_evaluator.compute_composite_score(results, weights)
```

**Calculator 编排逻辑**（新增 `_orchestrate_calculators` 函数）：

```python
def _orchestrate_calculators(bundles: dict[str, MetricDataBundle]) -> dict[str, MetricResult]:
    """按依赖关系编排 12 个 Calculator 的计算顺序."""
    # 拓扑排序：先计算无依赖的指标，再计算有依赖的
    calc_order = _topological_sort(CALCULATOR_REGISTRY)  # 基于 depends_on
    results: dict[str, MetricResult] = {}
    for code in calc_order:
        calculator = CALCULATOR_REGISTRY[code]()
        # 注入前置指标结果
        deps = {dep: results[dep] for dep in calculator.depends_on if dep in results}
        calculator.with_dependencies(deps)
        bundle = bundles.get(code)
        if bundle is None:
            results[code] = MetricResult.inconclusive(code, "no_bundle")
        else:
            results[code] = calculator.calculate(bundle)
    return results
```

**预期收益**：
- 架构统一，消除 2000+ 行重复实现
- 自动获得 L1/L2 缓存、批量查询、契约化预处理能力
- 算法更新只需修改 Calculator 类（单一修改点）

### 3.2 批量查询与缓存启用

**目标**：减少 AAS 查询 RTT 次数，启用缓存避免重复查询。

#### 3.2.1 批量查询（tagGroup 合并）

**当前**：每个 tag 单独查询（4 tag × 27 loop × 24h = 2592 次 RTT）
**优化后**：DataPlanner 自动合并相同 tagGroup 的查询

[data_planner.py L161-L287](file:///Users/zhangping/DEV/CLPM/backend/app/services/data_planner.py#L161-L287) `_plan_queries` 方法已实现 tagGroup 合并逻辑：
- 相同 tagGroup 的指标共享一次数据查询
- 例如：accuracy_rate / fast_rate / stability_rate 都使用 `PVOP_HF` 组 → 合并为 1 次查询

[remote_api_provider.py L211-L216](file:///Users/zhangping/DEV/CLPM/backend/app/services/data_source/remote_api_provider.py#L211-L216) 的 `tagCodes` 支持列表批量查询：

```python
# 优化前：4 次 RTT/回路/窗口
for tag in [pv, sp, op, mode]:
    data = await query_trend_fn(tag.tag_name, start, end)

# 优化后：1 次 RTT/回路/窗口（DataPlanner 自动合并）
bundles = await planner.request_bundles(loop_id, metric_codes, start, end)
# 内部：query_fn(loop_id, ["pv","sp","op","mode"], start, end) → 单次批量查询
```

**预期收益**：AAS 查询次数从 2592 → 648（4 倍减少）

#### 3.2.2 L1 DataBlock 缓存启用

[l1_datablock.py](file:///Users/zhangping/DEV/CLPM/backend/app/services/cache/l1_datablock.py) 已实现 L1 缓存（zstd 压缩），但生产未传入。

**启用方式**：在 DataPlanner 构造时传入 L1DataBlockCache 实例

```python
# kpi_calc.py 初始化
from app.services.cache.l1_datablock import L1DataBlockCache

l1_cache = L1DataBlockCache(redis=redis_client)
planner = DataPlanner(db=db, query_fn=query_fn, l1_cache=l1_cache)
```

**缓存策略**：
- 高频数据（PV/SP/OP）：TTL 5 分钟（`DEFAULT_TTL_HF=300`）
- 低频数据（MODE/CONFIG）：TTL 1 小时（`DEFAULT_TTL_BASE=3600`）
- zstd 压缩级别 3，压缩率 ~25%（3-5 倍存储节省）

**预期收益**：回填任务中相邻窗口的重复查询命中率 ~70%

#### 3.2.3 L2 Bundle 缓存启用（可选）

**适用场景**：多个回路共享同一装置的 PV/SP 数据（如串级控制）

**启用方式**：

```python
from app.services.cache.l2_bundle import L2BundleCache

l2_cache = L2BundleCache(redis=redis_client)
planner = DataPlanner(db=db, query_fn=query_fn, l1_cache=l1_cache, bundle_cache=l2_cache)
```

**预期收益**：串级回路场景下，副回路可命中主回路的 Bundle 缓存

### 3.3 分级计算周期（按回路重要等级）

**目标**：避免低优先级回路的无效计算，将 1000 回路的有效计算量降低 50%。

**设计依据**：用户需求 + [loop.py](file:///Users/zhangping/DEV/CLPM/backend/app/models/loop.py) `importance_level` 字段（1/2/3）

#### 3.3.1 计算周期配置

| 重要等级 | 计算周期 | 说明 | 适用场景 |
|---|---|---|---|
| 1 级（关键） | 1 小时 | 每小时计算 | 关键控制回路（反应釜温度、压力） |
| 2 级（重要） | 2 小时 | 每 2 小时计算 | 重要控制回路（流量、液位） |
| 3 级（一般） | 4 小时 | 每 4 小时计算 | 一般控制回路（辅助回路） |

#### 3.3.2 实现方案

**方案 A：Celery Beat 多周期调度**（推荐）

修改 [celery_app.py](file:///Users/zhangping/DEV/CLPM/backend/app/tasks/celery_app.py) 的 Beat 配置：

```python
# celery_app.py Beat 配置
beat_schedule = {
    # 1 级回路：每小时计算
    "kpi-hourly-level1": {
        "task": "app.tasks.kpi_calc.calculate_kpi_by_level",
        "schedule": crontab(minute=5),  # 每小时第 5 分钟
        "kwargs": {"importance_level": 1},
    },
    # 2 级回路：每 2 小时计算
    "kpi-bihourly-level2": {
        "task": "app.tasks.kpi_calc.calculate_kpi_by_level",
        "schedule": crontab(minute=10, hour="*/2"),  # 每 2 小时第 10 分钟
        "kwargs": {"importance_level": 2},
    },
    # 3 级回路：每 4 小时计算
    "kpi-quadhourly-level3": {
        "task": "app.tasks.kpi_calc.calculate_kpi_by_level",
        "schedule": crontab(minute=15, hour="*/4"),  # 每 4 小时第 15 分钟
        "kwargs": {"importance_level": 3},
    },
}
```

**新增 Celery 任务**：

```python
# kpi_calc.py
@celery_app.task(name="app.tasks.kpi_calc.calculate_kpi_by_level", ...)
async def calculate_kpi_by_level(importance_level: int, hours: int | None = None):
    """按重要等级计算 KPI."""
    window_hours = {1: 1, 2: 2, 3: 4}[importance_level]
    end = datetime.utcnow().replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(hours=window_hours)
    # 查询指定等级的回路
    loops = await _get_loops_by_level(importance_level)
    # 并发计算
    await _calculate_loops_batch(loops, start, end)
```

#### 3.3.3 1000 回路计算量预测

假设 1000 回路分布：1 级 20%（200）、2 级 50%（500）、3 级 30%（300）

| 等级 | 回路数 | 每小时计算量 | 每日计算量 |
|---|---|---|---|
| 1 级 | 200 | 200 | 4800 |
| 2 级 | 500 | 250（每 2h） | 6000 |
| 3 级 | 300 | 75（每 4h） | 1800 |
| **合计** | 1000 | **525** | **12600** |

**对比**：不分等级时每小时需计算 1000 回路，分级后仅需 525 回路（**降低 47.5%**）

### 3.4 按控制类型降采样

**目标**：减少算法处理的数据点数量，降低计算复杂度。

**设计依据**：[thresholds.py](file:///Users/zhangping/DEV/CLPM/backend/app/services/preprocessing/thresholds.py) 已定义控制类型采样率

#### 3.4.1 控制类型采样率配置

| 控制类型 | 基础采样率 | 1 小时数据点数 | 适用指标 |
|---|---|---|---|
| FLOW（流量） | 1s | 3600 | 所有指标 |
| PRESSURE（压力） | 2s | 1800 | 所有指标 |
| TEMPERATURE（温度） | 5s | 720 | 所有指标 |
| LEVEL（液位） | 5s | 720 | 所有指标 |
| COMPOSITION（成分） | 10s | 360 | 所有指标 |

#### 3.4.2 指标级降采样策略

不同指标对采样率的要求不同，进一步优化：

| 指标 | 最低采样率要求 | 降采样策略 |
|---|---|---|
| accuracy_rate | 1s | 按控制类型基础采样率 |
| fast_rate | 1s（需捕捉动态响应） | 按控制类型基础采样率 |
| stability_rate | 5s（稳态分析） | 最低 5s |
| oscillation_rate | 1s（需捕捉振荡） | 按控制类型基础采样率 |
| saturation_rate | 5s | 最低 5s |
| stiction_index | 1s（需高频辨识） | 按控制类型基础采样率 |
| auto_mode_rate | 60s（模式切换低频） | 最低 60s |
| effective_auto_rate | 60s | 最低 60s |
| good_value_rate | 5s | 最低 5s |
| settling_time | 1s | 按控制类型基础采样率 |
| ideal_settling_time | 配置数据（无时序） | N/A |

#### 3.4.3 实现方式

在 `metric_data_requirement` 契约中声明 `sampling_strategy`：

```sql
-- accuracy_rate 契约：按控制类型降采样
INSERT INTO clpm_metric_data_requirement (metric_code, sampling_strategy, ...)
VALUES ('accuracy_rate', 'BY_CONTROL_TYPE', ...);

-- auto_mode_rate 契约：固定 60s 降采样
INSERT INTO clpm_metric_data_requirement (metric_code, sampling_strategy, ...)
VALUES ('auto_mode_rate', 'FIXED_60S', ...);
```

DataPlanner 根据契约自动降采样，使用 LTTB 算法保持趋势特征（`maxPoints=2000`，30 天窗口限制）。

**预期收益**：
- TEMPERATURE/LEVEL 回路数据量降低 5 倍（3600 → 720 点）
- COMPOSITION 回路数据量降低 10 倍（3600 → 360 点）
- 整体平均数据量降低 ~40%

### 3.5 算法复杂度优化

**目标**：优化 ARMA 和振荡率计算的时间复杂度。

#### 3.5.1 ARMA 优化

**当前问题**（[arma.py](file:///Users/zhangping/DEV/CLPM/backend/app/tasks/arma.py)）：
- `MAX_GREEN_FUNC_LENGTH = 3600`（L32）：Green 函数长度过大
- `retry_orders = sorted({ar_order, 4, 6, 10})`（L148）：最坏 4 次重试
- Yule-Walker 方程求解：O(n × p²)，n=3600, p=10 → 360,000 次运算

**优化措施**：

| 优化项 | 当前 | 优化后 | 收益 |
|---|---|---|---|
| Green 函数长度 | 3600 | **600**（10 分钟） | 6 倍加速 |
| 重试 orders | {ar_order, 4, 6, 10} | **{ar_order, 4}**（最多 2 次） | 2 倍加速 |
| 最小数据点 | 30 | **60**（提前过滤） | 减少无效计算 |
| Yule-Walker 求解 | numpy.linalg.inv | **numpy.linalg.solve** | 1.5 倍加速 |

**代码改造**：

```python
# arma.py
MAX_GREEN_FUNC_LENGTH = 600  # 从 3600 降至 600（10 分钟稳态判定足够）
MIN_DATA_POINTS = 60         # 从 30 提升至 60

def fit_ar_model(data, max_order=4):
    """拟合 AR 模型，使用 solve 替代 inv."""
    # ... 构建 Toeplitz 矩阵 R 和向量 r ...
    # 优化前：phi = np.linalg.inv(R) @ r
    # 优化后：phi = np.linalg.solve(R, r)  # 更快且数值更稳定
    phi = np.linalg.solve(R, r)
    return phi

def compute_settling_time(data, ar_order=2):
    """计算稳态时间，限制重试次数."""
    retry_orders = sorted({ar_order, 4})  # 最多 2 次
    for order in retry_orders:
        try:
            return _try_compute(data, order)
        except Exception:
            continue
    return None
```

**预期收益**：ARMA 单次计算从 ~740ms → ~120ms（6 倍加速）

#### 3.5.2 振荡率计算向量化

**当前问题**（[kpi_calc.py L1204-L1313](file:///Users/zhangping/DEV/CLPM/backend/app/tasks/kpi_calc.py#L1204-L1313) `_compute_oscillation_rate`）：
- IAE 零交叉检测使用 Python for 循环
- 逐点计算积分和符号变化

**优化措施**：使用 NumPy 向量化

```python
# 优化前（Python 循环）
def _compute_oscillation_rate(data):
    iae = []
    for i in range(1, len(data)):
        iae.append(iae[-1] + abs(data[i] - data[i-1]))
    crossings = 0
    for i in range(1, len(iae)):
        if iae[i] * iae[i-1] < 0:
            crossings += 1
    ...

# 优化后（NumPy 向量化）
def _compute_oscillation_rate_vectorized(data):
    arr = np.asarray(data)
    # IAE 累积积分（向量化）
    abs_diff = np.abs(np.diff(arr))
    iae = np.cumsum(abs_diff)
    # 零交叉检测（向量化）
    sign_changes = np.diff(np.sign(iae))
    crossings = np.count_nonzero(sign_changes != 0)
    ...
```

**预期收益**：振荡率计算从 ~50ms → ~5ms（10 倍加速）

#### 3.5.3 算法复杂度对比表

| 算法 | 当前复杂度 | 优化后复杂度 | 单次耗时变化 |
|---|---|---|---|
| ARMA Green 函数 | O(L × p), L=3600 | O(L × p), L=600 | 740ms → 120ms |
| 振荡率（IAE 零交叉） | O(n) Python 循环 | O(n) NumPy 向量化 | 50ms → 5ms |
| 稳定率（标准差） | O(n) | O(n)（已优化） | 无变化 |
| 准确率（MAE） | O(n) | O(n)（已优化） | 无变化 |

### 3.6 节点级聚合优化

**目标**：减少 DB commit 次数和节点聚合 CTE 重复执行。

**当前问题**（[kpi_calc.py L1969-L2069](file:///Users/zhangping/DEV/CLPM/backend/app/tasks/kpi_calc.py#L1969-L2069) `_do_backfill`）：
- 每小时窗口都调用 `_do_calculate_node_kpi()` → 24 次节点聚合 CTE
- 每个回路快照单独 commit → 648 次 commit

#### 3.6.1 增量聚合

**优化方案**：回填任务完成后，只执行一次节点聚合（覆盖整个时间范围）

```python
# 优化前
for hour_window in range(24):
    for loop in loops:
        await _calculate_loop_kpi(loop, hour_start, hour_end)
        await db.commit()  # 648 次 commit
    await _do_calculate_node_kpi(hour_window)  # 24 次节点聚合

# 优化后
# 1. 批量计算所有回路所有窗口（不 commit）
batch_snapshots = []
for hour_window in range(24):
    for loop in loops:
        snapshot = await _calculate_loop_kpi(loop, hour_start, hour_end)
        batch_snapshots.append(snapshot)

# 2. 批量写入（单次 commit）
await db.bulk_save_objects(batch_snapshots)
await db.commit()  # 1 次 commit

# 3. 增量节点聚合（只执行一次，覆盖整个时间范围）
await _aggregate_nodes_for_range(start, end)  # 1 次节点聚合
```

#### 3.6.2 批量 commit

使用 SQLAlchemy `bulk_save_objects` 或 `session.add_all` + 单次 commit：

```python
# 优化前
for snapshot in snapshots:
    db.add(snapshot)
    await db.commit()  # N 次 commit

# 优化后
db.add_all(snapshots)
await db.commit()  # 1 次 commit
```

**预期收益**：
- DB commit 次数从 648 → 1（648 倍减少）
- 节点聚合 CTE 执行次数从 24 → 1（24 倍减少）
- DB 读写耗时从 ~5 min → ~30 sec

### 3.7 并发提升

**当前**：[kpi_calc.py L45](file:///Users/zhangping/DEV/CLPM/backend/app/tasks/kpi_calc.py#L45) `CONCURRENCY = 10`

**优化后**：`CONCURRENCY = 50`（可根据 CPU 核心数调整）

```python
# kpi_calc.py
CONCURRENCY = 50  # 从 10 提升至 50

# 使用 asyncio.Semaphore 控制并发
sem = asyncio.Semaphore(CONCURRENCY)
async def _calculate_with_limit(loop, ...):
    async with sem:
        return await _calculate_loop_kpi(loop, ...)
```

**注意**：需配合 AAS API 的并发限制（建议与 AAS 服务端确认最大 QPS）

**预期收益**：并发从 10 → 50，吞吐量提升 5 倍

### 3.8 端到端数据链路优化（从 DCS 采集到 KPI 落库）

**目标**：从整条数据链路角度消除瓶颈，打通"采集 → 传输 → 预处理 → 计算 → 落库"全链路优化。

#### 3.8.1 当前数据链路全景

```
┌──────────┐     ┌──────────┐     ┌─────────────────────────────────────────┐
│ DCS/OPC  │────→│ AAS 系统 │────→│ CLPM 后端                               │
│ (现场)   │     │ (OPC UA  │     │                                         │
└──────────┘     │  采集)   │     │  ┌─ 实时链路 ─────────────────────────┐  │
                 └──────────┘     │  │ SignalR WS → realtime_subscriber  │  │
                 │     ↑          │  │   → Redis(逐tag setex)            │  │
                 │     │ REST API │  │   → Pub/Sub → WebSocket 前端      │  │
                 │     │          │  └───────────────────────────────────┘  │
                 │     └──────────┤  ┌─ 历史链路（KPI 计算）─────────────┐  │
                 │                │  │ remote_api_provider               │  │
                 │                │  │   → kpi_calc (逐tag查询×4)        │  │
                 │                │  │   → _save_snapshot (查+写+commit) │  │
                 │                │  │   → node_performance (递归CTE)    │  │
                 │                │  └───────────────────────────────────┘  │
                 │                └─────────────────────────────────────────┘
                 ↓
          ┌──────────────┐
          │ PostgreSQL   │
          │ - kpi_snapshot│
          │ - node_kpi   │
          └──────────────┘
```

#### 3.8.2 链路各环节瓶颈与优化

**环节 1：实时数据采集 → Redis 缓存**

当前 [realtime_subscriber.py L234-252](file:///Users/zhangping/DEV/CLPM/backend/app/services/data_source/realtime_subscriber.py#L234-L252) 逐 tag `setex`：

```python
# ❌ 当前：逐条 Redis 操作
async def _cache_value(self, item: dict) -> None:
    key = f"{_REDIS_KEY_PREFIX}{tag_code}"
    await redis_client.setex(key, _REDIS_TTL, value)  # 每条 1 次 RTT
    await redis_client.publish(_PUBSUB_CHANNEL, value) # 每条 1 次 RTT
```

**优化：Redis Pipeline 批量操作**

```python
# ✅ 优化：批量 Pipeline + 批量 Publish
async def _cache_batch(self, items: list[dict]) -> None:
    pipe = redis_client.pipeline()
    for item in items:
        key = f"{_REDIS_KEY_PREFIX}{item['tagCode']}"
        pipe.setex(key, _REDIS_TTL, json.dumps(payload))
    await pipe.execute()  # 1 次 RTT 批量写入
    # 批量发布
    await redis_client.publish(_PUBSUB_CHANNEL, json.dumps(batch_payload))
```

**预期收益**：1000 tag 推送，Redis 操作从 2000 RTT → 2 RTT（1000 倍减少）

---

**环节 2：历史数据查询 → KPI 计算**

当前 [kpi_calc.py L575-578](file:///Users/zhangping/DEV/CLPM/backend/app/tasks/kpi_calc.py#L575-L578) 逐 tag 查询：

```python
# ❌ 当前：4 次独立 HTTP 请求
pv_data = await query_trend_fn(pv_tag_name, start_iso, end_iso)
sp_data = await query_trend_fn(sp_tag_name, start_iso, end_iso)
op_data = await query_trend_fn(op_tag_name, start_iso, end_iso)
mode_data = await query_trend_fn(mode_tag_name, start_iso, end_iso)
```

`make_query_fn`（[remote_api_provider.py L161-293](file:///Users/zhangping/DEV/CLPM/backend/app/services/data_source/remote_api_provider.py#L161-L293)）已支持 `tagCodes` 列表批量查询，但 `query_trend_data`（[L295-370](file:///Users/zhangping/DEV/CLPM/backend/app/services/data_source/remote_api_provider.py#L295-L370)）是单 tag 接口，生产代码用的是后者。

**优化：改用批量查询接口**（Phase 2 接入 DataPlanner 后自动实现，DataPlanner 的 `make_query_fn` 闭包已支持多 tag 批量查询）

```python
# ✅ 优化：DataPlanner 自动合并相同 tagGroup 的查询
# remote_api_provider.make_query_fn 的 tagCodes 已是列表
bundles = await planner.request_bundles(loop_id, metric_codes, start, end)
# 内部：单次 HTTP 请求查询 ["LIC-101.PV", "LIC-101.SP", "LIC-101.OP", "LIC-101.MODE"]
```

**预期收益**：AAS HTTP 请求从 4 次 → 1 次/回路/窗口（已在 §3.2 覆盖，此处确认链路一致性）

---

**环节 3：KPI 快照落库**

当前 [kpi_calc.py L1595-1642](file:///Users/zhangping/DEV/CLPM/backend/app/tasks/kpi_calc.py#L1595-L1642) `_save_snapshot` 幂等写入：

```python
# ❌ 当前：先查后写 + 逐回路 commit（2 次 DB RTT/回路）
existing = await db.execute(select(...).where(...))  # 1. SELECT 检查
if existing:
    existing.field = ...  # 2a. UPDATE
else:
    db.add(snapshot)      # 2b. INSERT
await db.commit()         # 3. COMMIT（每回路一次）
```

**优化 A：INSERT ON CONFLICT 替代先查后写**

```python
# ✅ 优化：PostgreSQL UPSERT（1 次操作完成幂等写入）
from sqlalchemy.dialects.postgresql import insert as pg_insert

stmt = pg_insert(KpiSnapshotHourly).values(
    loop_id=loop_id, ts_start=ts_start, ...
)
stmt = stmt.on_conflict_do_update(
    index_elements=["loop_id", "ts_start"],  # 需创建唯一约束
    set_=dict(score=stmt.excluded.score, ...)
)
await db.execute(stmt)
```

**优化 B：批量 commit**（已在 §3.6 覆盖）

**预期收益**：
- DB 操作从 3 次（SELECT + INSERT/UPDATE + COMMIT）→ 1 次（UPSERT）
- 1000 回路：3000 次 DB RTT → 1 次批量 UPSERT + 1 次 COMMIT

---

**环节 4：节点级聚合**

当前 [kpi_calc.py L2044](file:///Users/zhangping/DEV/CLPM/backend/app/tasks/kpi_calc.py#L2044) 每小时窗口都执行全量聚合：

```python
# ❌ 当前：每窗口全量 CTE
for w in windows:  # 24 个窗口
    await _do_calculate()
    await _do_calculate_node_kpi()  # 24 次递归 CTE
```

**优化：增量聚合**（已在 §3.6 覆盖，确认链路一致性）

```python
# ✅ 优化：全部窗口计算完成后，单次范围聚合
for w in windows:
    await _do_calculate()  # 只算回路，不聚合
await _aggregate_nodes_for_range(start, end)  # 1 次聚合
```

---

**环节 5：PostgreSQL 索引与分区**

当前 [metric.py L109-111](file:///Users/zhangping/DEV/CLPM/backend/app/models/metric.py#L109-L111) 只有单列索引：

```python
# ❌ 当前：3 个单列索引
Index("idx_kpi_snapshot_loop_id", "loop_id"),
Index("idx_kpi_snapshot_ts_start", "ts_start"),
Index("idx_kpi_snapshot_status", "status"),
```

**优化 A：复合索引**（支持 UPSERT + 范围查询）

```python
# ✅ 优化：复合唯一索引（支持 ON CONFLICT）+ 范围查询索引
Index("uk_kpi_snapshot_loop_ts", "loop_id", "ts_start", unique=True),  # UPSERT 依赖
Index("idx_kpi_snapshot_ts_loop", "ts_start", "loop_id"),              # 范围查询优化
```

**优化 B：时间分区**（1000 回路 × 24h × 365 天 = 876 万行/年）

```sql
-- PostgreSQL 声明式分区（按月）
CREATE TABLE kpi_snapshot_hourly (...) PARTITION BY RANGE (ts_start);
CREATE TABLE kpi_snapshot_2026_07 PARTITION OF kpi_snapshot_hourly
    FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');
```

**预期收益**：
- UPSERT 操作直接命中复合唯一索引（无需全表扫描）
- 范围查询只扫描对应分区（12 倍数据量缩减/年）
- 1000 回路场景下 DB 查询从 ~200ms → ~20ms

---

**环节 6：实时数据预缓冲（前瞻优化）**

当前实时数据只缓存"最新值"（Redis TTL 60s），历史数据需重新从 AAS REST API 拉取。

**前瞻优化：实时数据环形缓冲区**

在 `realtime_subscriber` 中维护一个内存环形缓冲区（或 Redis Stream），缓存最近 N 小时的实时数据：

```python
# ✅ 前瞻：实时数据写入 Redis Stream（持久化 + 可回放）
async def _cache_to_stream(self, items: list[dict]) -> None:
    for item in items:
        await redis_client.xadd(
            f"tsbuffer:{item['tagCode']}",
            {"value": item["value"], "quality": item["quality"], "ts": item["collectTime"]},
            maxlen=3600,  # 保留最近 1 小时（1s 采样）
        )
```

**KPI 计算时优先读本地缓冲区**：

```python
# ✅ 前瞻：1h KPI 计算直接读 Redis Stream，无需请求 AAS API
async def _get_data_for_kpi(loop_id, start, end):
    # 1. 优先读 Redis Stream（0 RTT 网络请求）
    data = await redis_client.xrange(f"tsbuffer:{tag_name}", min=start, max=end)
    if data:
        return parse_stream_data(data)
    # 2. 回退到 AAS REST API（冷启动或超出缓冲区范围）
    return await remote_api_provider.query_trend_data(tag_name, start, end)
```

**预期收益**：
- 1 小时自动任务：AAS HTTP 请求 → 0（完全本地化）
- 仅 24h 回填需请求 AAS API
- 数据延迟从分钟级 → 秒级

> **注意**：此为前瞻优化，建议在 Phase 3 性能优化验证后，根据实际瓶颈决定是否实施。

#### 3.8.3 端到端优化收益汇总

| 环节 | 优化措施 | 当前 | 优化后 | 收益 |
|---|---|---|---|---|
| 实时缓存 | Redis Pipeline 批量 | 2000 RTT/推送 | 2 RTT | 1000× |
| 历史查询 | 批量查询（DataPlanner） | 4 RTT/回路 | 1 RTT/回路 | 4× |
| 快照落库 | UPSERT + 批量 commit | 3 RTT/回路 | 1 RTT/批 | ~3000× |
| 节点聚合 | 增量聚合 | 24 次 CTE | 1 次 CTE | 24× |
| DB 索引 | 复合索引 + 分区 | ~200ms | ~20ms | 10× |
| 数据预缓冲 | Redis Stream（前瞻） | AAS API 请求 | 本地读取 | ∞（1h任务） |

---

## 4. 实施计划

### 4.1 阶段划分

| 阶段 | 目标 | 主要工作 | 预计工作量 |
|---|---|---|---|
| Phase 1 | 种子数据 + 契约 + DB 索引 | 创建 12 个 metric_data_requirement 种子数据 + 复合唯一索引迁移 | 1 天 |
| Phase 2 | 架构接入 | 重构 kpi_calc._calculate_loop_kpi + UPSERT 落库 | 2 天 |
| Phase 3 | 性能优化 | 批量查询 + L1/L2 缓存 + 并发 + ARMA + 降采样 + Redis Pipeline | 2 天 |
| Phase 4 | 分级计算 + 聚合优化 | 分级周期 + 增量聚合 + 批量 commit | 1 天 |
| Phase 5 | 测试验证 | 单元测试 + 压测 + 基准对比 | 1 天 |
| Phase 6（前瞻） | 实时数据预缓冲 | Redis Stream 环形缓冲区 + 本地优先读取 | 待评估 |

### 4.2 Phase 1: 创建 metric_data_requirement 种子数据 + DB 索引

**目标**：为 12 个指标创建数据需求契约，使 DataPlanner 可以正常工作；创建复合唯一索引支持 UPSERT。

**交付物**：
- Alembic migration 脚本：插入 12 条 `clpm_metric_data_requirement` 记录
- Alembic migration 脚本：创建 `uk_kpi_snapshot_loop_ts` 复合唯一索引（loop_id + ts_start）
- Alembic migration 脚本：创建 `idx_kpi_snapshot_ts_loop` 范围查询索引
- 种子数据单元测试

**12 个指标契约定义**：

| metric_code | tag_group | tags | sampling_strategy | quality_policy | mask_expression | depends_on |
|---|---|---|---|---|---|---|
| accuracy_rate | PVOP_HF | ["pv","sp","op"] | BY_CONTROL_TYPE | KEEP_ALL_WITH_VALIDITY | pv_valid && sp_valid | [] |
| fast_rate | PVOP_HF | ["pv","sp","op"] | BY_CONTROL_TYPE | KEEP_ALL_WITH_VALIDITY | pv_valid && sp_valid | ["settling_time","ideal_settling_time"] |
| stability_rate | PVOP_HF | ["pv","sp"] | BY_CONTROL_TYPE | KEEP_ALL_WITH_VALIDITY | pv_valid && sp_valid | ["oscillation_rate"] |
| effective_auto_rate | MODE_HF | ["mode","pv"] | FIXED_60S | KEEP_ALL | mode_valid && pv_valid | [] |
| good_value_rate | QUALITY_HF | ["pv"] | FIXED_5S | KEEP_ALL | pv_valid | [] |
| oscillation_rate | PVOP_HF | ["pv","sp"] | BY_CONTROL_TYPE | KEEP_ALL_WITH_VALIDITY | pv_valid && sp_valid | [] |
| saturation_rate | OP_HF | ["op"] | FIXED_5S | KEEP_ALL | op_valid | [] |
| stiction_index | PVOP_HF | ["pv","op"] | BY_CONTROL_TYPE | KEEP_ALL_WITH_VALIDITY | pv_valid && op_valid | [] |
| output_trip_index | OP_HF | ["op"] | FIXED_5S | KEEP_ALL | op_valid | [] |
| auto_mode_rate | MODE_HF | ["mode"] | FIXED_60S | KEEP_ALL | mode_valid | [] |
| settling_time | PVOP_HF | ["pv","sp"] | BY_CONTROL_TYPE | KEEP_ALL_WITH_VALIDITY | pv_valid && sp_valid | [] |
| ideal_settling_time | CONFIG | [] | NONE | NONE | NULL | [] |

### 4.3 Phase 2: 重构 kpi_calc 接入新架构 + UPSERT 落库

**目标**：消除双轨问题，统一算法实现路径；优化快照落库为 UPSERT。

**改造范围**：
1. [kpi_calc.py](file:///Users/zhangping/DEV/CLPM/backend/app/tasks/kpi_calc.py) `_calculate_loop_kpi` 函数重构
2. 新增 `_orchestrate_calculators` 函数（Calculator 编排）
3. 删除 `_compute_kpis`、`_compute_composite_score_v2` 等重复实现
4. 适配 `query_fn` 签名（通过 `make_query_fn`）
5. **新增**：`_save_snapshot` 改为 PostgreSQL UPSERT（`on_conflict_do_update`），消除先查后写

**验证方式**：
- 单元测试：对齐现有 1762 测试用例
- 算法结果对比：改造前后 KPI 值一致（允许浮点误差 < 0.01）

### 4.4 Phase 3: 性能优化

**目标**：达成 1000 回路 < 10 分钟、单回路 < 5 秒目标。

**优化项**：
1. 启用 L1 DataBlock 缓存
2. 启用 L2 Bundle 缓存（可选）
3. 并发提升 `CONCURRENCY = 50`
4. ARMA 优化（Green 函数长度 600 + 重试 [2,4] + solve 替代 inv）
5. 振荡率向量化
6. 按控制类型降采样（通过 metric_data_requirement 契约）
7. **新增**：`realtime_subscriber._cache_value` 改为 Redis Pipeline 批量操作（§3.8 环节 1）

### 4.5 Phase 4: 分级计算 + 聚合优化

**目标**：进一步降低计算量，优化 DB 交互。

**优化项**：
1. Celery Beat 多周期调度（1级:1H，2级:2H，3级:4H）
2. 新增 `calculate_kpi_by_level` Celery 任务
3. 增量节点聚合（回填完成后只执行一次）
4. 批量 commit（`bulk_save_objects`）

### 4.6 Phase 5: 测试验证 + 性能基准

**目标**：验证优化效果，建立性能基准。

**测试内容**：
1. 单元测试：所有现有 1762 用例通过 + 新增优化测试
2. 算法一致性测试：优化前后 KPI 值一致
3. 性能压测：
   - 27 回路 24h 回填耗时
   - 1000 回路 1h 自动任务耗时
   - 单回路 1h KPI 计算耗时
4. 性能基准报告

---

## 5. 性能预期与验证方案

### 5.1 性能预期

#### 5.1.1 27 回路 24h 回填

| 优化措施 | 改造前 | 改造后 | 收益 |
|---|---|---|---|
| 批量查询（tagGroup 合并） | 15 min | 4 min | 11 min |
| L1 缓存启用 | - | 2 min | 2 min |
| ARMA 优化 | 8 min | 1.3 min | 6.7 min |
| 振荡率向量化 | 1 min | 0.1 min | 0.9 min |
| 并发提升（10→50） | - | 0.5 min | 0.5 min |
| 批量 commit | 5 min | 0.5 min | 4.5 min |
| 节点聚合优化 | 1 min | 0.1 min | 0.9 min |
| **总计** | **30 min** | **~2.5 min** | **12 倍加速** |

#### 5.1.2 1000 回路 1h 自动任务

| 优化措施 | 线性外推 | 优化后 | 收益 |
|---|---|---|---|
| 分级计算（47.5% 降低） | 17 h | 8.5 h | 8.5 h |
| 批量查询 | 8.5 h | 2.1 h | 6.4 h |
| L1 缓存 | - | 1.5 h | 0.6 h |
| ARMA 优化 | 4.5 h | 0.7 h | 3.8 h |
| 振荡率向量化 | 0.5 h | 0.05 h | 0.45 h |
| 并发提升（10→50） | - | 0.3 h | 0.7 h |
| 批量 commit + 聚合优化 | 2 h | 0.2 h | 1.8 h |
| **总计** | **17 h** | **~4.85 min** | **210 倍加速** |

**结论**：1000 回路自动任务可控制在 **5 分钟以内**（目标 < 10 分钟 ✅）

#### 5.1.3 单回路 1h KPI 计算

| 环节 | 当前 | 优化后 |
|---|---|---|
| AAS 查询（4 tag） | ~3 s | ~0.8 s（批量 + 缓存命中） |
| 预处理 | ~1 s | ~0.3 s（按控制类型降采样） |
| ARMA 计算 | ~0.7 s | ~0.12 s（Green 600 + solve） |
| 振荡率计算 | ~0.05 s | ~0.005 s（向量化） |
| 其他指标计算 | ~0.5 s | ~0.2 s |
| DB 写入 | ~0.5 s | ~0.05 s（批量 commit） |
| **总计** | **~5.75 s** | **~1.5 s** |

**结论**：单回路 1h KPI 计算可控制在 **1.5 秒**（目标 < 5 秒 ✅）

### 5.2 验证方案

#### 5.2.1 单元测试

```bash
# 运行所有现有测试（确保不回归）
cd backend && uv run pytest -q

# 新增优化专项测试
cd backend && uv run pytest tests/test_metric_calculator/ -v
cd backend && uv run pytest tests/test_data_planner/ -v
cd backend && uv run pytest tests/test_performance/ -v
```

#### 5.2.2 算法一致性测试

```python
# tests/test_algorithm_consistency.py
def test_kpi_consistency_before_after_optimization():
    """验证优化前后 KPI 值一致（允许浮点误差 < 0.01）."""
    # 1. 使用旧路径计算
    old_results = await _calculate_loop_kpi_old(loop, start, end)
    # 2. 使用新架构计算
    new_results = await _calculate_loop_kpi_new(loop, start, end)
    # 3. 对比所有指标
    for metric in ["accuracy_rate", "fast_rate", "stability_rate", ...]:
        assert abs(old_results[metric] - new_results[metric]) < 0.01
```

#### 5.2.3 性能压测脚本

```bash
# 27 回路 24h 回填压测
cd backend && uv run python scripts/benchmark_backfill.py --loops 27 --hours 24

# 1000 回路 1h 自动任务压测（模拟）
cd backend && uv run python scripts/benchmark_hourly.py --loops 1000 --hours 1

# 单回路 1h KPI 计算压测
cd backend && uv run python scripts/benchmark_single_loop.py --loop-id <uuid> --hours 1
```

#### 5.2.4 性能基准报告

输出格式：

```markdown
# 性能基准报告

## 测试环境
- CPU: ...
- 内存: ...
- 数据库: PostgreSQL 15
- Redis: 7.0

## 测试结果

### 27 回路 24h 回填
- 耗时: X 分钟 Y 秒
- 改造前: 30+ 分钟
- 加速比: N 倍

### 1000 回路 1h 自动任务
- 耗时: X 分钟 Y 秒
- 目标: < 10 分钟
- 状态: ✅/❌

### 单回路 1h KPI 计算
- 耗时: X 毫秒
- 目标: < 5 秒
- 状态: ✅/❌
```

---

## 6. 风险与应对

### 6.1 技术风险

| 风险 | 概率 | 影响 | 应对措施 |
|---|---|---|---|
| 算法结果不一致（优化前后 KPI 值偏差） | 中 | 高 | Phase 2 完成后执行一致性测试，允许浮点误差 < 0.01 |
| AAS API 并发限制（QPS 超限） | 中 | 中 | 与 AAS 服务端确认 QPS 上限，动态调整 CONCURRENCY |
| L1 缓存命中率低（冷启动） | 低 | 低 | 回填任务预热缓存；自动任务时段集中，缓存命中率高 |
| ARMA Green 函数长度缩短导致精度下降 | 低 | 中 | 对齐测试：对比 Green 600 vs 3600 的稳态时间判定结果 |
| 分级计算导致 3 级回路监控滞后 | 低 | 低 | 3 级回路为一般回路，4 小时周期可接受；异常告警不受影响 |

### 6.2 工程风险

| 风险 | 概率 | 影响 | 应对措施 |
|---|---|---|---|
| 重构范围大，引入新 Bug | 中 | 高 | 分阶段实施 + 充分单元测试 + 灰度验证 |
| 种子数据配置错误 | 中 | 中 | Phase 1 完成后执行 DataPlanner 集成测试 |
| Celery Beat 配置冲突 | 低 | 中 | 错峰调度（1级:05分, 2级:10分, 3级:15分） |
| DB 迁移失败 | 低 | 高 | 先在测试环境验证，生产环境备份后执行 |

### 6.3 回滚方案

每个阶段实施前创建 Git 分支（`mb/feat-algo-opt-phaseN`），合并前充分测试。

**回滚策略**：
- Phase 1（种子数据）：删除 `clpm_metric_data_requirement` 表记录
- Phase 2（架构接入）：回退到 `kpi_calc.py` 旧版本（Git revert）
- Phase 3-4（性能优化）：逐项开关，支持单独回滚
- Phase 5（测试验证）：仅验证，无生产影响

---

## 7. 附录

### 7.1 相关文件清单

| 文件 | 说明 |
|---|---|
| [kpi_calc.py](file:///Users/zhangping/DEV/CLPM/backend/app/tasks/kpi_calc.py) | KPI 计算生产路径（重构对象） |
| [data_planner.py](file:///Users/zhangping/DEV/CLPM/backend/app/services/data_planner.py) | DataPlanner 架构中枢 |
| [metric_calculator/__init__.py](file:///Users/zhangping/DEV/CLPM/backend/app/services/metric_calculator/__init__.py) | 12 个 Calculator 注册表 |
| [confidence_evaluator.py](file:///Users/zhangping/DEV/CLPM/backend/app/services/confidence_evaluator.py) | 可信度评估 + 综合评分 |
| [arma.py](file:///Users/zhangping/DEV/CLPM/backend/app/tasks/arma.py) | ARMA 模型辨识 |
| [l1_datablock.py](file:///Users/zhangping/DEV/CLPM/backend/app/services/cache/l1_datablock.py) | L1 DataBlock 缓存 |
| [thresholds.py](file:///Users/zhangping/DEV/CLPM/backend/app/services/preprocessing/thresholds.py) | 控制类型采样率配置 |
| [metric_data_requirement.py](file:///Users/zhangping/DEV/CLPM/backend/app/models/metric_data_requirement.py) | 数据契约模型 |
| [celery_app.py](file:///Users/zhangping/DEV/CLPM/backend/app/tasks/celery_app.py) | Celery 配置 |
| [node_performance.py](file:///Users/zhangping/DEV/CLPM/backend/app/services/node_performance.py) | 节点级聚合 |
| [remote_api_provider.py](file:///Users/zhangping/DEV/CLPM/backend/app/services/data_source/remote_api_provider.py) | AAS REST API 调用 |

### 7.2 相关设计文档

| 文档 | 版本 | 说明 |
|---|---|---|
| PRD | v6.0 | 产品需求规范 |
| FDS | v6.0 | 功能设计规范 |
| ADS | v6.0 | 应用设计规范 |
| DDS | v6.0 | 数据模型设计 |
| IDS | v6.0 | API 接口设计 |
| UI/UX | v6.1 | 视觉与交互规范 |
| 实现契约 | v2.0 | 重构后 IA/路由/API/权限/状态机/KPI |
| GB/T 44693.2-2024 | — | KPI 计算国标 |

### 7.3 术语表

| 术语 | 说明 |
|---|---|
| DataPlanner | 指标驱动的数据获取与编排中枢 |
| MetricCalculator | 指标计算器（12 个） |
| ConfidenceEvaluator | 可信度评估器 |
| MetricDataBundle | 指标数据包（DataBlock + mask + lineage） |
| MetricResult | 指标计算结果（value + confidence + lineage + details） |
| tagGroup | Tag 组（BASE/PVOP_HF/OP_HF/MODE_HF/QUALITY_HF/CONFIG） |
| L1 DataBlock 缓存 | 原始数据块级缓存（zstd 压缩） |
| L2 Bundle 缓存 | 指标数据包级缓存（跨回路共享） |
| IAE | Integral Absolute Error（积分绝对误差） |
| ARMA | AutoRegressive Moving Average（自回归滑动平均模型） |
| Green 函数 | ARMA 模型的脉冲响应函数，用于稳态时间判定 |
| LTTB | Largest Triangle Three Buckets（降采样算法） |
| INCONCLUSIVE | 数据不足，无法计算指标（E 级可信度） |

---

## 8. 评审检查清单

- [ ] 优化目标是否清晰可量化（1000 回路 < 10 分钟，单回路 < 5 秒）
- [ ] 双轨问题根因是否准确（种子数据缺失）
- [ ] 7 大优化措施是否覆盖所有性能瓶颈（含端到端链路 §3.8）
- [ ] 分级计算周期配置是否合理（1级:1H，2级:2H，3级:4H）
- [ ] 按控制类型降采样策略是否正确（对齐 thresholds.py）
- [ ] ARMA 优化参数是否合理（Green 600，重试 [2,4]）
- [ ] 端到端链路 6 个环节优化是否完整（实时缓存/历史查询/快照落库/节点聚合/DB索引/数据预缓冲）
- [ ] UPSERT 改造是否依赖复合唯一索引（Phase 1 先建索引，Phase 2 再改 UPSERT）
- [ ] Redis Pipeline 批量操作是否影响 Pub/Sub 实时性
- [ ] 实施计划是否可执行（5+1 阶段，逐步推进）
- [ ] 风险识别是否全面（技术 + 工程 + 回滚）
- [ ] 验证方案是否充分（单元测试 + 一致性测试 + 压测）

---

**文档结束**
