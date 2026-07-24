# 仪表故障率（instrument_fault_rate）显示异常修复技术文档

| 项目 | 内容 |
|---|---|
| 文档版本 | v1.0 |
| 创建日期 | 2026-07-24 |
| 关联模块 | 性能评估（dashboard）/ 节点级聚合（node_performance）/ 指标计算器（metric_calculator） |
| 影响范围 | 工作台看板仪表故障率卡片、节点级 KPI 聚合、历史数据回填 |
| 严重等级 | P1（用户可见数据失真） |

---

## 1. 问题描述

### 1.1 现象

前端工作台看板的"仪表故障率"卡片出现两个阶段的问题：

1. **初始显示为 1%**：仪表故障率显示为 1%，明显偏低
2. **切换时间窗口后固定为 36.03%**：当切换时间范围（近 8 小时、近 168 小时等）时，仪表故障率始终固定在 36.03%，而其他指标（综合评分、自控率、稳定率等）会随时间窗口正常变化

### 1.2 预期行为

仪表故障率应与其他指标一致，随时间窗口切换而动态变化。

---

## 2. 根因分析

经排查发现**两个独立的缺陷**叠加导致了上述现象：

### 2.1 缺陷一：NULL 稀释（Dashboard 窗口聚合）

**位置**：`backend/app/api/v1/endpoints/dashboard.py` — `_WINDOW_RATE_FIELD_KEYS` 窗口聚合逻辑

**根因**：仪表故障率作为 Phase 1 新增指标，在 `UnitKpiSummary` 表中存在大量历史快照的 `instrument_fault_rate` 列为 NULL（因为这些快照创建时该指标尚未实现或未正确写入）。

窗口聚合的加权平均公式为：

```
weighted_avg = SUM(field × evaluated_loops) / SUM(evaluated_loops)
```

**问题**：分母 `SUM(evaluated_loops)` 包含了 `instrument_fault_rate IS NULL` 的行，导致分母被放大、计算结果被严重稀释。例如 100 个回路中有 99 个 NULL，实际只有 1 个回路有值，但分母仍为 100，导致结果约为真实值的 1/100。

**修复方案**：对每个 rate 字段使用**按字段非 NULL 分母**，即分母只累加该字段非 NULL 的行的 `evaluated_loops`：

```python
# 修复前（错误）
func.sum(UnitKpiSummary.evaluated_loops).label("esum")

# 修复后（正确：按字段非 NULL 过滤分母）
func.sum(UnitKpiSummary.evaluated_loops)
    .filter(getattr(UnitKpiSummary, field).is_not(None))
    .label(f"{field}_esum")
```

### 2.2 缺陷二：聚合结果缺失字段（Node 级快照写入）

**位置**：`backend/app/services/node_performance.py` — `aggregate_node_snapshot_with_presets` 函数

**根因**：`aggregate_node_snapshot_with_presets` 函数的返回字典中**缺少 `instrument_fault_rate` 字段**。该函数负责生成节点级快照并持久化到 `UnitKpiSummary` 表，但返回结果中没有这个字段，导致：

- 每小时自动聚合时，`UnitKpiSummary.instrument_fault_rate` 列始终写入 NULL
- Dashboard 查询到的节点级快照该字段全为 NULL
- 只有手动计算的回路级快照（`kpi_snapshot_hourly`）有值

**修复方案**：在返回字典中补充缺失字段：

```python
# 修复前（缺失）
# 返回字典中无 instrument_fault_rate

# 修复后
"instrument_fault_rate": avg_value("instrument_fault_rate"),
```

### 2.3 缺陷叠加效应

两个缺陷叠加产生了用户看到的现象：

1. 由于缺陷二，`UnitKpiSummary` 表中 `instrument_fault_rate` 列几乎全为 NULL
2. 由于缺陷一，少数非 NULL 行的值被 NULL 行的 `evaluated_loops` 稀释
3. 修复缺陷一后，由于非 NULL 行极少，聚合结果退化为某一条非 NULL 快照的值，恰好为 36.03%
4. 该值在不同时间窗口下不变，因为各窗口内仅有同一条非 NULL 快照参与计算

---

## 3. 修复方案

### 3.1 修复一：Dashboard 窗口聚合 NULL 稀释

**文件**：`backend/app/api/v1/endpoints/dashboard.py`

对 `_WINDOW_RATE_FIELD_KEYS` 中所有 rate 字段（10 个），将加权平均的分母从全局 `SUM(evaluated_loops)` 改为按字段非 NULL 过滤的分母：

```python
_WINDOW_RATE_FIELD_KEYS = {
    "avg_score": "avgScore",
    "auto_mode_rate": "autoModeRate",
    "stability_rate": "stabilityRate",
    "effective_auto_rate": "effectiveAutoRate",
    "accuracy_rate": "accuracyRate",
    "fast_rate": "fastRate",
    "good_value_rate": "goodValueRate",
    "oscillation_rate": "oscillationRate",
    "saturation_rate": "saturationRate",
    "instrument_fault_rate": "instrumentFaultRate",
}
```

每个字段的加权平均：
```python
# 分子：SUM(field × evaluated_loops) WHERE field IS NOT NULL
# 分母：SUM(evaluated_loops) WHERE field IS NOT NULL
```

### 3.2 修复二：节点级聚合补充字段

**文件**：`backend/app/services/node_performance.py`

在 `aggregate_node_snapshot_with_presets` 函数返回字典中补充：

```python
"instrument_fault_rate": avg_value("instrument_fault_rate"),
```

同时确认 `aggregate_node_snapshot` 函数已正确包含该字段（此前已修复）。

### 3.3 修复三：历史数据回填

**文件**：`backend/scripts/backfill_instrument_fault_rate.py`（新建）

由于历史 `UnitKpiSummary` 快照的 `instrument_fault_rate` 列全为 NULL，需要回填历史数据：

1. **Loop 级回填**：查询所有 `kpi_snapshot_hourly` 中 `status=SUCCESS` 但 `instrument_fault_rate IS NULL` 的快照，使用 DataPlanner 仅请求 PV 信号 + `InstrumentFaultRateCalculator` 重新计算并 UPDATE
2. **Node 级回填**：逐小时窗口执行 `batch_calculate_and_save_node_snapshots` 更新 `UnitKpiSummary`

回填统计：
- Loop 级：4997 个回路-小时快照
- Node 级：1590 个节点-小时快照

### 3.4 修复四：计算逻辑抽取为独立工具函数

**文件**：`backend/app/utils/instrument_fault_rate.py`（新建）

将仪表故障率核心计算逻辑从 `InstrumentFaultRateCalculator` 中抽取为独立工具函数 `calculate_instrument_fault_rate()`，脱离 `MetricDataBundle`/`DataBlock` 抽象依赖，便于其他模块直接复用：

```python
from app.utils.instrument_fault_rate import calculate_instrument_fault_rate

result = calculate_instrument_fault_rate(
    pv_outlier_reasons=[["FROZEN"], [], ["OUT_OF_RANGE"]],
    point_count=3,
)
# result.fault_rate == 66.67
# result.fault_point_count == 2
# result.freeze_count == 1
# result.overrange_count == 1
```

`InstrumentFaultRateCalculator` 重构为委托调用该工具函数，确保计算逻辑单一来源。

---

## 4. 涉及文件清单

| 文件 | 变更类型 | 说明 |
|---|---|---|
| `backend/app/api/v1/endpoints/dashboard.py` | 修改 | 窗口聚合 NULL 稀释修复（按字段非 NULL 分母） |
| `backend/app/services/node_performance.py` | 修改 | `aggregate_node_snapshot_with_presets` 补充 `instrument_fault_rate` 字段 |
| `backend/scripts/backfill_instrument_fault_rate.py` | 新建 | 定向回填脚本 |
| `backend/app/utils/instrument_fault_rate.py` | 新建 | 独立工具函数 |
| `backend/app/utils/__init__.py` | 新建 | 包初始化文件 |
| `backend/app/services/metric_calculator/instrument_fault.py` | 修改 | 委托调用工具函数 |
| `backend/tests/test_utils/test_instrument_fault_rate.py` | 新建 | 工具函数单元测试（17 项） |
| `backend/tests/test_utils/__init__.py` | 新建 | 测试包初始化 |

---

## 5. 验证数据

### 5.1 单元测试

```bash
cd backend && uv run pytest tests/test_utils/test_instrument_fault_rate.py tests/test_metric_calculator/test_phase1_metrics.py -v
```

结果：**99 passed**（17 项工具函数新测试 + 82 项既有计算器测试回归通过）

### 5.2 代码质量门禁

```bash
cd backend && uv run ruff check . && uv run ruff format --check .
```

结果：**All checks passed**

### 5.3 数据回填验证

| 验证项 | 结果 |
|---|---|
| `kpi_snapshot_hourly` 中 `instrument_fault_rate IS NULL` 的 SUCCESS 快照 | 0（100% 回填） |
| `UnitKpiSummary` 中 `instrument_fault_rate IS NULL` 的快照占比 | 0.7%（99.3% 回填） |
| Dashboard 各时间窗口仪表故障率是否随窗口变化 | ✅ 是 |

### 5.4 前端验证

| 时间窗口 | 仪表故障率 | 其他指标是否变化 |
|---|---|---|
| 近 8 小时 | 动态变化 | ✅ |
| 今日 | 动态变化 | ✅ |
| 昨日 | 动态变化 | ✅ |
| 近 7 天 | 动态变化 | ✅ |
| 近 30 天 | 动态变化 | ✅ |

### 5.5 未来自动聚合验证

确认 Celery Beat 每小时触发的节点级聚合任务链包含 `instrument_fault_rate` 字段：

1. `kpi_calc.py` → `calculate_single_loop_kpi` → 调用 `InstrumentFaultRateCalculator`（已委托工具函数）
2. `kpi_calc.py` → `batch_calculate_and_save_node_snapshots` → 调用 `aggregate_node_snapshot_with_presets`（已补充字段）
3. 结果写入 `UnitKpiSummary.instrument_fault_rate`（非 NULL）

---

## 6. 工具函数 API 文档

### `calculate_instrument_fault_rate`

```python
from app.utils.instrument_fault_rate import calculate_instrument_fault_rate
```

**签名**：

```python
def calculate_instrument_fault_rate(
    pv_outlier_reasons: list[list[str]],
    point_count: int | None = None,
) -> InstrumentFaultRateResult | None
```

**参数**：

| 参数 | 类型 | 说明 |
|---|---|---|
| `pv_outlier_reasons` | `list[list[str]]` | PV 信号每个采样点的异常原因码列表，外层长度应等于 `point_count`；不足时尾部自动补空列表，超出时截断 |
| `point_count` | `int \| None` | 评估时段总采样点数。`None` 时取 `len(pv_outlier_reasons)` |

**返回值**：

`InstrumentFaultRateResult | None` — `point_count <= 0` 时返回 `None`

| 字段 | 类型 | 说明 |
|---|---|---|
| `fault_rate` | `float` | 故障率百分比（0~100），保留 2 位小数 |
| `fault_point_count` | `int` | 含故障原因码的采样点数（不重复计数） |
| `sample_count` | `int` | 总采样点数 |
| `freeze_count` | `int` | 冻结（FROZEN）点数 |
| `mutation_count` | `int` | 突变（JUMP）点数 |
| `overrange_count` | `int` | 超量程（OUT_OF_RANGE）点数 |
| `source` | `str` | 数据来源标识，默认 `"outlier_reasons"` |

**故障原因码**：

| 原因码 | 说明 | 计入仪表故障 |
|---|---|---|
| `OUT_OF_RANGE` | 超量程 | ✅ |
| `FROZEN` | 信号冻结 | ✅ |
| `JUMP` | 信号突变 | ✅ |
| `SPIKE` | 尖峰 | ❌ |
| `NaN` | NaN/Inf/NULL | ❌ |
| `QC_BAD` | 质量码异常 | ❌ |
| `HF_NOISE` | 高频噪声 | ❌ |
| `TS_ANOMALY` | 时间戳异常 | ❌ |

---

## 7. 经验教训

1. **新增指标必须同步检查聚合链路**：新增 `instrument_fault_rate` 指标时，只更新了 `aggregate_node_snapshot` 的返回字典，遗漏了 `aggregate_node_snapshot_with_presets` 函数。聚合链路中每个写入 `UnitKpiSummary` 的入口都必须覆盖所有 AGGREGATABLE 字段。

2. **加权平均必须使用按字段非 NULL 分母**：当表中新旧数据共存（部分行新字段为 NULL）时，全局分母会导致 NULL 稀释。所有 rate 类字段的加权平均都应使用 `.filter(field.is_not(None))` 过滤分母。

3. **新增指标后必须回填历史数据**：仅修复代码不会让历史快照自动获得新字段值。需要编写定向回填脚本，仅重算新指标（不重算已有指标），避免全量重算的性能开销。

4. **核心计算逻辑应抽取为独立工具函数**：将仪表故障率计算逻辑从 `InstrumentFaultRateCalculator`（依赖 `MetricDataBundle`）中抽取为独立工具函数（仅依赖原始 `list[list[str]]`），使其他模块可在不构建完整 DataPlanner 管线的情况下直接复用。

5. **大范围删除的 commit 必须跑全量 pytest**：此次修复中发现此前 5cae2e5a 提交删除了 kpi_calc.py 中 22 个函数但未跑全量测试，导致模块级 ImportError 未被发现（已在 2026-07-23 修复）。任何涉及指标计算链路的修改都应跑全量 `pytest` 而非仅跑相关测试文件。

---

## 8. 复盘检查清单

- [x] Dashboard 窗口聚合 NULL 稀释修复
- [x] `aggregate_node_snapshot_with_presets` 补充 `instrument_fault_rate` 字段
- [x] 历史数据回填（Loop 级 + Node 级）
- [x] 核心计算逻辑抽取为独立工具函数
- [x] `InstrumentFaultRateCalculator` 委托调用工具函数
- [x] 工具函数单元测试（17 项）
- [x] 既有计算器测试回归通过（82 项）
- [x] ruff check + format 通过
- [x] 前端验证各时间窗口指标动态变化
- [x] 未来每小时自动聚合包含 `instrument_fault_rate`
- [x] 技术文档生成
