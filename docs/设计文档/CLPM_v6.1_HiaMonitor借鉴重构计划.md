# CLPM v6.1 性能评估与诊断模块重构计划（参考 HiaMonitor V3.1.0）

**文档状态**: 修订版（已采纳评审报告修正）
**当前版本**: v1.1（2026-07-23 修订，消除技术错误 + 调整优先级）
**设计依据**: HiaMonitor V3.1.0 用户手册、CLPM v6.1 实现契约、FDS v6.0、IDS v6.0、现有代码实现
**评审报告**: `docs/过程文档/HiaMonitor借鉴重构计划-评审报告-2026-07-23.md`

---

## 0. 修订说明（v1.0 → v1.1）

本版依据评审报告消除以下问题，贯彻"最小化现有代码改动、选择性借鉴、增量式升级"三原则：

| 编号 | 原问题 | 修订动作 |
|-----|--------|---------|
| P0-1 | 伪代码引用不存在的 `bundle.signals` | 改为 `bundle.data_block.outlier_reasons` / `.signals` |
| P0-2 | 仪表故障检测与既有 8 类异常检测重复 | 删除"新增预处理步骤 9/10/11"，改为复用 `DataBlock.outlier_reasons` |
| P0-3 | 新增 `metric_config.algorithm_params` 冗余 | 复用既有 `metric_config.threshold` JSONB 字段 |
| P1-1 | 新建 `pid_structure_template` 与既有 DCS 体系重复 | 改为扩展 `dcs_model` 表，复用 `dcs_mode_mapping` |
| P1-2 | 配置链误挂 `loop_type_weight` | 改为基于 `control_type` 维度（已迁移到 LoopLedger） |
| P1-3 | 新指标未说明三层编排归属 | 补充 Layer/depends_on/聚合策略表 |
| P1-4 | 新指标未决策节点聚合去留 | 补充聚合策略枚举与 `AGGREGATE_FIELDS` 处理 |
| P1-5 | 复杂回路聚合未对接 NodeAggregator | 补充去重规则与 RFC 要求 |
| P2-1 | 新字段用 FLOAT 与既有 Numeric 不一致 | 统一 Numeric，同步两张快照表 |
| P2-2 | 可信度阈值配置化风险 | 降级为高级选项，联动校验 |
| P2-3 | 抗扰性分析引用不存在方法 | 改为 fast_rate 可选分支，开关控制 |
| P2-4 | 复杂回路超 MVP 范围 | 归入独立 Phase，RFC 先行 |

---

## 1. 概述

参考和利时 HiaMonitor V3.1.0 的产品设计，对 CLPM 性能评估与诊断模块进行**增量式重构**。核心原则：

1. **保持架构稳定**：复用现有 `MetricCalculator` 插件式架构、`DataPlanner` 数据编排层、8 步预处理流水线
2. **全面配置化**：消除硬编码阈值，复用既有 `metric_config.threshold` JSONB 字段，建立三层配置优先级链
3. **增强指标覆盖**：补充 HiaMonitor 中有但 CLPM 缺失的高价值诊断指标（仪表故障率复用既有异常检测）
4. **扩展复杂回路支持**：支持串级、超驰、NooM（独立 Phase，RFC 先行）
5. **优化前端体验**：参考 HiaMonitor 综合评估页面布局

**关键复用点**：现有 8 类异常值检测（[outlier_detection.py](file:///Users/zhangping/DEV/CLPM/backend/app/services/preprocessing/outlier_detection.py)）已覆盖 HiaMonitor 的冻结/突变/超限三类仪表故障，本计划直接复用其检测结果，不重复造轮子。

---

## 2. 配置化改造（Phase 0）

### 2.1 当前硬编码参数清单

| 参数名 | 当前值 | 所在文件 | 影响范围 | 配置化策略 |
|-------|--------|---------|---------|-----------|
| `SIMILARITY_THRESHOLD` | 0.4 | `metric_calculator/oscillation.py` | 振荡判定 | P0 配置化 |
| `MIN_ZERO_CROSSINGS` | 4 | `metric_calculator/oscillation.py` | 最小零交叉点 | P0 配置化 |
| `SETTLING_THRESHOLD` | 0.05 | `metric_calculator/settling_time.py` | 稳态收敛阈值 | P0 配置化 |
| `MIN_GOOD_RATIO` | 0.20 | `kpi_calc.py` | INCONCLUSIVE 触发 | P1 联动校验 |
| `DEFAULT_E_MAX_RATIO` | 0.05 | `metric_calculator/effective_auto.py` | 有效自控偏差 | P0 配置化 |
| `TRIP_INACTIVE/NORMAL/FREQUENT` | 0.01/0.1/1.0 | `metric_calculator/output_trip.py` | 行程分级 | P0 配置化 |
| `DEFAULT_CONFIDENCE_THRESHOLDS` | A:0.95/B:0.80/C:0.60/D:0.20 | `confidence_evaluator.py` | 可信度分级 | **P2 高级选项**（见 §2.5） |

### 2.2 配置架构设计（复用既有字段）

采用**三层配置优先级链**，基于 `control_type` 维度（非 `loop_type_weight`）：

```
系统级默认（algorithm_parameter 表，按 control_type 分组）
    ↓ 覆盖
指标级覆盖（复用既有 metric_config.threshold JSONB 字段）
    ↓ 覆盖
回路级覆盖（loop_ledger 新增 algorithm_params JSONB 字段）
```

> **修订说明**：`control_type` 已从 `metric_config` 迁移到 [LoopLedger.control_type](file:///Users/zhangping/DEV/CLPM/backend/app/models/loop.py#L52-L56)（STABLE/SLOW/FAST/LOGIC，对齐 GB/T 44693.2-2024）。配置模板按 `control_type` 分组，与既有 [ControlTypeThreshold](file:///Users/zhangping/DEV/CLPM/backend/app/services/preprocessing/thresholds.py) 阈值表机制一致。`loop_type_weight` 是业务类型（温度/压力/流量）权重表，不承载算法阈值。

#### 2.2.1 新增配置表：`algorithm_parameter`

| 字段 | 类型 | 说明 |
|-----|------|-----|
| `id` | UUID | 主键 |
| `parameter_code` | VARCHAR(50) | 参数编码（如 `osc_similarity_threshold`） |
| `parameter_name` | VARCHAR(100) | 参数名称 |
| `control_type` | VARCHAR(20) | 控制类型：`STABLE`/`SLOW`/`FAST`/`LOGIC`/`ALL`（ALL=全类型通用） |
| `category` | VARCHAR(20) | 分类（`OSCILLATION`/`SETTLING`/`STABILITY`/`EFFECTIVE_AUTO`/`TRIP_INDEX`/`FAULT_DETECTION`） |
| `default_value` | FLOAT | 默认值 |
| `min_value` | FLOAT | 最小值约束 |
| `max_value` | FLOAT | 最大值约束 |
| `unit` | VARCHAR(20) | 单位 |
| `description` | TEXT | 参数说明 |
| `created_at` / `updated_at` | TIMESTAMP | 时间戳 |

#### 2.2.2 复用 `metric_config.threshold`（不新增字段）

[metric_config](file:///Users/zhangping/DEV/CLPM/backend/app/models/metric.py#L30-L59) 已有 `threshold: JSONB` 字段（[metric.py:43](file:///Users/zhangping/DEV/CLPM/backend/app/models/metric.py#L43)），直接承载指标级参数覆盖：

```json
{"osc_similarity_threshold": 0.5, "min_zero_crossings": 6}
```

> **不新增 `algorithm_params` 字段**（评审 P0-3）：`threshold` 语义即为阈值配置，复用避免冗余。

#### 2.2.3 扩展 `loop_ledger` 表

| 新增字段 | 类型 | 说明 |
|---------|------|-----|
| `algorithm_params` | JSONB | 回路级算法参数覆盖（最高优先级） |

> 故障检测阈值（冻结/突变）已由 `ControlTypeThreshold` 按控制类型承载，无需在 `loop_ledger` 单独加 `freeze_threshold`/`mutation_threshold` 字段（评审 P0-2：复用既有阈值表）。

### 2.3 参数加载机制

```python
def get_algorithm_param(param_code: str, loop_id: str | None = None,
                       metric_code: str | None = None) -> float:
    """参数加载优先级：
    1. loop_ledger.algorithm_params[param_code]（回路级，最高）
    2. metric_config.threshold[param_code]（指标级）
    3. algorithm_parameter WHERE control_type=回路控制类型 AND parameter_code=...（系统默认）
    """
```

### 2.4 前端配置页面优化

**现有页面**: `metric/config.vue`

新增：算法参数配置卡片组（按 category 分组，按 control_type 切换）、范围校验、重置默认、审计入口。

### 2.5 可信度阈值配置化（P2 高级选项，延后）

可信度阈值 A:0.95/B:0.80/C:0.60/D:0.20 是 [ConfidenceLevel](file:///Users/zhangping/DEV/CLPM/backend/app/contracts/data_types.py#L77-L87) 枚举硬编码，且 E 级 `<0.20` 与 [kpi_calc.py MIN_GOOD_RATIO=0.20](file:///Users/zhangping/DEV/CLPM/backend/app/tasks/kpi_calc.py#L61) 耦合。**配置化需联动修改两者并加一致性校验**，风险高，列为 P2 高级选项，Phase 0 不实施。

---

## 3. 新增指标扩展（Phase 1）

### 3.1 新增指标清单（含编排层级与聚合策略）

| 指标代码 | 名称 | Layer | depends_on | 参与综合评分 | 节点聚合策略 | 优先级 |
|---------|------|-------|-----------|------------|------------|--------|
| `instrument_fault_rate` | 仪表故障率 | L1 | 无 | 否 | AGGREGATABLE | P0 |
| `pv_mean`/`pv_std` | PV 均值/标准差 | L1 | 无 | 否 | DISPLAY_ONLY | P0 |
| `sp_mean`/`sp_std` | SP 均值/标准差 | L1 | 无 | 否 | DISPLAY_ONLY | P0 |
| `op_mean`/`op_std` | OP 均值/标准差 | L1 | 无 | 否 | DISPLAY_ONLY | P0 |
| `error_mean`/`error_std` | 偏差均值/标准差 | L1 | 无 | 否 | DISPLAY_ONLY | P1 |
| `valve_linearity` | 阀门线性度 | L1 | 无 | 否 | DISPLAY_ONLY | P0 |
| `valve_nonlinearity` | 阀门非线性度 | L1 | 无 | 否 | DISPLAY_ONLY | P0 |
| `valve_operating_range` | 阀门运行区间 | L1 | 无 | 否 | DISPLAY_ONLY | P0 |
| `oscillation_amplitude` | 振荡幅值 | L2 | `oscillation_rate` | 否 | DISPLAY_ONLY | P0 |
| `setpoint_crossing_count` | 设定点穿越次数 | L1 | 无 | 否 | DISPLAY_ONLY | P0 |
| `time_constant` | 时间常数 | L1 | 无 | 否 | DISPLAY_ONLY | P1 |

> **聚合策略枚举**（评审 P1-4）：
> - `AGGREGATABLE`：加入 [node_aggregation.AGGREGATE_FIELDS](file:///Users/zhangping/DEV/CLPM/backend/app/services/node_aggregation.py#L64-L75)，参与节点加权平均（仅故障率等有装置级意义的指标）
> - `DISPLAY_ONLY`：仅回路级展示，不参与节点聚合（统计型/诊断型指标，避免均值再平均失真）

### 3.2 指标注册机制（三处注册点）

新增指标需同步注册到**三处**（评审 P1-3）：

**① 计算器注册表** [metric_calculator/__init__.py](file:///Users/zhangping/DEV/CLPM/backend/app/services/metric_calculator/__init__.py#L27-L40)：
```python
CALCULATOR_REGISTRY["instrument_fault_rate"] = InstrumentFaultRateCalculator
# ... 其余新指标
```

**② DB 列名映射** [kpi_calc.py _DB_TO_CALCULATOR_METRIC_CODE](file:///Users/zhangping/DEV/CLPM/backend/app/tasks/kpi_calc.py#L72-L85)：
```python
_DB_TO_CALCULATOR_METRIC_CODE["instrument_fault_rate"] = "instrument_fault_rate"
# ... 其余新指标
```

**③ Layer2 依赖声明** [kpi_calc.py _LAYER2_DEPENDENCIES](file:///Users/zhangping/DEV/CLPM/backend/app/tasks/kpi_calc.py#L96-L99)：
```python
_LAYER2_DEPENDENCIES["oscillation_amplitude"] = ["oscillation_rate"]
```

> 纯展示型指标（统计/诊断）**不参与综合评分**，不入 `CORE_METRIC_CODES`/`AUXILIARY_METRIC_CODES` 的加权逻辑，仅写入快照表供前端读取。

### 3.3 数据库表扩展（统一 Numeric，同步两张快照表）

[kpi_snapshot_hourly](file:///Users/zhangping/DEV/CLPM/backend/app/models/metric.py#L62-L116) 与 [kpi_snapshot_custom](file:///Users/zhangping/DEV/CLPM/backend/app/models/metric.py#L119-L177) **同步**新增字段（评审 P2-1），统一 `Numeric` 类型对齐既有字段：

| 新增字段 | 类型 | 说明 |
|-----|------|-----|
| `instrument_fault_rate` | Numeric(5,2) | 仪表故障率（%） |
| `pv_mean`/`pv_std`/`sp_mean`/`sp_std`/`op_mean`/`op_std` | Numeric(10,3) | 统计指标 |
| `error_mean`/`error_std` | Numeric(10,3) | 偏差统计 |
| `valve_linearity`/`valve_nonlinearity` | Numeric(5,4) | 阀门线性度（0~1） |
| `valve_op_min`/`valve_op_max` | Numeric(8,2) | 阀门运行区间 |
| `oscillation_amplitude` | Numeric(8,2) | 振荡幅值 |
| `setpoint_crossing_count` | Integer | 设定点穿越次数 |
| `time_constant` | Numeric(8,2) | 时间常数（秒） |

> 迁移必须两张表同批应用（项目记忆硬约束：模型变更必须与迁移同批提交）。

---

## 4. 仪表故障检测（Phase 2）— 复用既有异常检测

### 4.1 核心策略：复用而非重建

HiaMonitor 的三类仪表故障（超限/冻结/突变）**已被现有 8 类异常值检测覆盖**（评审 P0-2）：

| HiaMonitor 故障类型 | 现有 OutlierReason | 现有检测函数 | 检测逻辑 |
|-------------------|-------------------|------------|---------|
| 超限 | `OUT_OF_RANGE` | [detect_out_of_range()](file:///Users/zhangping/DEV/CLPM/backend/app/services/preprocessing/outlier_detection.py#L55-L81) | PV 超出量程上下限 |
| 冻结 | `FROZEN` | [detect_frozen()](file:///Users/zhangping/DEV/CLPM/backend/app/services/preprocessing/outlier_detection.py#L137-L156) | 滑动窗口 std < 阈值×量程 |
| 突变 | `JUMP` | [detect_jump()](file:///Users/zhangping/DEV/CLPM/backend/app/services/preprocessing/outlier_detection.py#L184-L221) | 相邻点变化 > 阈值×量程 |

检测结果已由 [OutlierDetector.detect_all()](file:///Users/zhangping/DEV/CLPM/backend/app/services/preprocessing/outlier_detection.py#L421-L508) 汇总，存入 [DataBlock.outlier_reasons](file:///Users/zhangping/DEV/CLPM/backend/app/contracts/data_types.py#L213)（`dict[str, list[list[str]]]`，key=tag名，value=每点原因码列表）。

**本计划不新增预处理步骤**，仅新增一个聚合计算器把既有 reason 码汇总为故障率指标。

### 4.2 新增计算器：InstrumentFaultRateCalculator

**实现模式**：参照 [GoodValueRateCalculator](file:///Users/zhangping/DEV/CLPM/backend/app/services/metric_calculator/good_value.py)（同为"全量点统计率"型指标，直接读 `bundle.data_block` 而非 masked_indices，因为故障点本身 valid=False 已被排除在 mask 之外）。

```python
# 新增文件：backend/app/services/metric_calculator/instrument_fault.py
from app.contracts.data_types import MetricDataBundle, MetricResult, OutlierReason
from app.services.metric_calculator.base import MetricCalculatorBase

#: 仪表故障对应的异常原因码集合
_FAULT_REASONS = frozenset({
    OutlierReason.OUT_OF_RANGE.value,  # 超限
    OutlierReason.FROZEN.value,        # 冻结
    OutlierReason.JUMP.value,          # 突变
})


class InstrumentFaultRateCalculator(MetricCalculatorBase):
    """仪表故障率计算器（复用既有 outlier_detection 检测结果）.

    公式：故障率 = 故障点数 / 总点数 × 100%
    故障点 = PV 信号在 outlier_reasons 中标记为
            OUT_OF_RANGE / FROZEN / JUMP 的点。

    定位：辅助诊断指标，AGGREGATABLE（参与节点聚合，装置级故障率有意义）。
    """

    @property
    def metric_code(self) -> str:
        return "instrument_fault_rate"

    def calculate(self, bundle: MetricDataBundle) -> MetricResult:
        block = bundle.data_block
        n = block.point_count
        if n == 0:
            return self._make_inconclusive(bundle, "empty_data_block")

        # 从 DataBlock.outlier_reasons 读取 PV 每点原因码（非 bundle.signals）
        pv_reasons = block.outlier_reasons.get("pv", [])
        # pv_reasons 为 list[list[str]]，索引对齐数据点
        freeze_count = mutation_count = overrange_count = 0
        fault_point_count = 0
        for reasons in pv_reasons:
            reason_set = set(reasons) if reasons else set()
            is_fault = False
            if OutlierReason.FROZEN.value in reason_set:
                freeze_count += 1
                is_fault = True
            if OutlierReason.JUMP.value in reason_set:
                mutation_count += 1
                is_fault = True
            if OutlierReason.OUT_OF_RANGE.value in reason_set:
                overrange_count += 1
                is_fault = True
            if is_fault:
                fault_point_count += 1

        fault_rate = (fault_point_count / n) * 100.0 if n > 0 else 0.0
        fault_rate = self._clamp(fault_rate)

        return self._make_result(
            bundle,
            fault_rate,
            {
                "fault_rate": round(fault_rate, 2),
                "freeze_count": freeze_count,
                "mutation_count": mutation_count,
                "overrange_count": overrange_count,
                "fault_point_count": fault_point_count,
                "sample_count": n,
                "source": "outlier_reasons",
            },
        )
```

### 4.3 需要修改的函数与调用点清单

| # | 文件 | 修改类型 | 具体内容 |
|---|-----|---------|---------|
| 1 | `backend/app/services/metric_calculator/instrument_fault.py` | **新增文件** | `InstrumentFaultRateCalculator` 类（见 §4.2） |
| 2 | [metric_calculator/__init__.py](file:///Users/zhangping/DEV/CLPM/backend/app/services/metric_calculator/__init__.py) | 注册 | `CALCULATOR_REGISTRY["instrument_fault_rate"] = InstrumentFaultRateCalculator`；import 语句 |
| 3 | [kpi_calc.py](file:///Users/zhangping/DEV/CLPM/backend/app/tasks/kpi_calc.py#L72-L85) | 映射 | `_DB_TO_CALCULATOR_METRIC_CODE["instrument_fault_rate"] = "instrument_fault_rate"` |
| 4 | [kpi_calc.py](file:///Users/zhangping/DEV/CLPM/backend/app/tasks/kpi_calc.py) | UPSERT | 在写入 `kpi_snapshot_hourly` 的 UPSERT 字典中新增 `instrument_fault_rate` 字段（搜索现有 `stiction_index` 写入处，同模式追加） |
| 5 | [models/metric.py](file:///Users/zhangping/DEV/CLPM/backend/app/models/metric.py) | 模型 | `KpiSnapshotHourly` 与 `KpiSnapshotCustom` 同步新增 `instrument_fault_rate: Numeric(5,2)` 字段 |
| 6 | `backend/alembic/versions/xxx_add_fault_metrics.py` | **新增迁移** | 两张快照表 ADD COLUMN（同批应用） |
| 7 | [node_aggregation.py](file:///Users/zhangping/DEV/CLPM/backend/app/services/node_aggregation.py#L64-L75) | 聚合 | `AGGREGATE_FIELDS` 元组新增 `"instrument_fault_rate"`（装置级故障率有意义） |
| 8 | [data_planner.py](file:///Users/zhangping/DEV/CLPM/backend/app/services/data_planner.py) | 取数 | 确认 `instrument_fault_rate` 的数据需求契约：使用 BASE tagGroup（含 PV），无需新 tagGroup；在 `clpm_metric_data_requirement` 配置表新增一行 |
| 9 | 前端 `metric/` API + 页面 | 展示 | 新增故障率卡片（红色预警阈值可配置） |

**关键注意点**：
- **不修改** [outlier_detection.py](file:///Users/zhangping/DEV/CLPM/backend/app/services/preprocessing/outlier_detection.py)（复用既有检测，零改动）
- **不修改** DataPlanner 预处理流水线（8 步不变，不新增步骤 9/10/11）
- **不修改** [metric_data_bundle.py](file:///Users/zhangping/DEV/CLPM/backend/app/services/metric_data_bundle.py)（DataBlock.outlier_reasons 已由流水线填充）
- 计算器读 `bundle.data_block.outlier_reasons`（**非** `bundle.signals`，评审 P0-1）
- 故障率统计用**全量点**（`block.point_count`），非 `masked_indices`（故障点 valid=False 已被排除在 mask 外）

### 4.4 抗扰性分析（fast_rate 可选分支）

**现有问题**: [fast_rate](file:///Users/zhangping/DEV/CLPM/backend/app/services/metric_calculator/fast_rate.py) 仅在 SP 变化时计算稳态时间，SP 长时间不变时无法评估。

**改进方案**（评审 P2-3：可选分支，开关控制，零回归）：

在 `FastRateCalculator.calculate` 中增加抗扰性分支，通过 `algorithm_parameter` 的 `anti_disturbance_enabled` 开关控制（默认关闭，关闭时走原逻辑）：

```python
def calculate(self, bundle: MetricDataBundle) -> MetricResult:
    sp_changes = self._detect_sp_changes(bundle)  # 抽取既有逻辑为私有方法
    if sp_changes:
        return self._calculate_from_sp_changes(bundle, sp_changes)  # 原逻辑
    # 开关关闭或无扰动 → 原 INCONCLUSIVE 逻辑
    if not self._is_anti_disturbance_enabled():
        return self._make_inconclusive(bundle, "no_sp_change")
    disturbances = self._detect_disturbances(bundle)  # 新增扰动检测
    if disturbances:
        return self._calculate_from_disturbances(bundle, disturbances)
    return self._make_inconclusive(bundle, "no_sp_change_and_no_disturbance")
```

**扰动检测算法**: PV 一阶差分绝对值超阈值的时间段，排除短于最小持续时间的噪声段。

> 开关默认关闭，确保既有 fast_rate 行为零回归。抗扰性分支为 P1 增强项，Phase 2 可选实施。

---

## 5. 复杂回路支持（Phase 3 — 独立 Phase，RFC 先行）

> 评审 P2-4：复杂回路属高阶能力，超 Phase 1 MVP 范围。本节为 RFC 纲要，需评审通过后实施。

### 5.1 数据模型变更

**`loop_ledger` 表新增字段**:

| 字段 | 类型 | 说明 |
|-----|------|-----|
| `complex_loop_type` | VARCHAR(30) | `SIMPLE`/`CASCADE_MASTER`/`CASCADE_SLAVE`/`NOOM`/`OVERRIDE_HIGH`/`OVERRIDE_LOW`/`SELECT_CASCADE_MASTER`/`SELECT_CASCADE_SLAVE` |
| `parent_loop_id` | UUID | 父回路 ID（串级副回路引用主回路，FK→loop_ledger.id） |
| `complex_loop_group_id` | UUID | 复杂回路组 ID（NooM/超驰/选择串级共用） |

### 5.2 聚合规则对接（评审 P1-5，必须解决）

现有节点聚合有两套权重（[node_aggregation.py:13-33](file:///Users/zhangping/DEV/CLPM/backend/app/services/node_aggregation.py#L13-L33)）：
- `NodeAggregator.aggregate`（[L587](file:///Users/zhangping/DEV/CLPM/backend/app/services/node_aggregation.py#L587)）：回路→节点，按 importance_level（1:3,2:2,3:1）
- `_weighted_average`（[L86](file:///Users/zhangping/DEV/CLPM/backend/app/services/node_aggregation.py#L86)）：节点→日/月，按 loop_count

复杂回路需明确：

| 决策点 | 规则（待 RFC 确认） |
|-------|------------------|
| `NodeAggregator` 输入去重 | 串级主副回路算 2 条参与计算，列表显示算 1 条（对齐 HiaMonitor） |
| `loop_count` 计数口径 | 复杂回路组算 1 还是按成员数？建议按组算 1（避免虚增节点规模） |
| 去重键 | `complex_loop_group_id` 作为聚合去重键；SIMPLE 回路 group_id=NULL 各算 1 |
| 复用 `include_in_evaluation` | [loop.py:75-80](file:///Users/zhangping/DEV/CLPM/backend/app/models/loop.py#L75-L80) 已有"是否参与聚合"开关，复杂回路副回路是否置 false？ |

> **RFC 必须输出**：① `NodeAggregator.aggregate` 改造方案（输入侧按 group_id 去重）；② `loop_count` 统计口径；③ 装置级 `auto_loop_ratio` 中复杂回路计数规则。

### 5.3 API 与前端

新增 `/api/v1/loops/complex` 系列接口；`loop/manage.vue` 增加树形展示与"创建复杂回路"入口。

---

## 6. PID 结构模板（Phase 4 — 复用既有 DCS 体系）

### 6.1 复用既有 DCS 表（评审 P1-1，不新建表）

代码库已有完整 DCS 体系：
- [dcs_vendor.py](file:///Users/zhangping/DEV/CLPM/backend/app/models/dcs_vendor.py) — 厂商表
- [dcs_model.py](file:///Users/zhangping/DEV/CLPM/backend/app/models/dcs_model.py) — 型号表
- [dcs_mode_mapping.py](file:///Users/zhangping/DEV/CLPM/backend/app/models/dcs_mode_mapping.py) — MODE 映射表
- [LoopLedger.dcs_model_id](file:///Users/zhangping/DEV/CLPM/backend/app/models/loop.py#L121-L126) 已关联型号

**方案**：新增 `dcs_pid_structure` 子表关联 `dcs_model.id`，承载 PID 结构参数（P 类型、I/D 单位、微分滤波）。**MODE 映射复用 `dcs_mode_mapping`，绝不 JSONB 重复存储**。

| 字段 | 类型 | 说明 |
|-----|------|-----|
| `id` | UUID | 主键 |
| `dcs_model_id` | UUID FK | 关联 [dcs_model](file:///Users/zhangping/DEV/CLPM/backend/app/models/dcs_model.py) |
| `p_type` | VARCHAR(20) | `PROPORTION`/`PROPORTION_BAND` |
| `i_unit`/`d_unit` | VARCHAR(10) | `SECONDS`/`MINUTES` |
| `d_filter_enabled` | BOOLEAN | 是否启用微分滤波 |
| `d_filter_unit` | VARCHAR(10) | 微分滤波单位 |
| `d_filter_multiplier` | BOOLEAN | 是否乘法因子 |

### 6.2 前端页面

新增 `loop/pid-template.vue`：展示/编辑 PID 结构模板，绑定到 DCS 型号。

---

## 7. 前端界面优化（Phase 4）

### 7.1 综合评估页面重构（参考 HiaMonitor 布局）

```
┌─ 回路名称 / 所属装置 / 统计时间 ─────────────────────────────┐
│ ┌综合评分┐ ┌快速率┐ ┌准确率┐                                │
│ │  85    │ │  76  │ │  88  │                                │
│ │[GOOD]  │ │[FAIR]│ │[GOOD]│                                │
│ └────────┘ └──────┘ └──────┘                                │
│ ┌─ 雷达图（5 核心指标）──────────────────────────────────┐  │
│ └──────────────────────────────────────────────────────┘  │
│ ┌指标参数图(横向条)┐ ┌PV/SP/OP 统计柱状图──────────────┐  │
│ │ 自控率 ██████     │ │ PV均值/标准差 SP均值/标准差... │  │
│ │ 稳定率 █████      │ └──────────────────────────────┘  │
│ │ 故障率 ██(红)     │ ┌PV/OP 散点图(阀门线性度)──────┐  │
│ └─────────────────┘ └──────────────────────────────┘  │
│ ┌PV/SP/MODE/OP 趋势图 [时间段查询]─────────────────────┐  │
│ ┌评价指标趋势图 ☑稳定率 ☑准确率 ☑故障率(多选对比)────┐  │
└──────────────────────────────────────────────────────────┘
```

### 7.2 全局看板优化

新增：仪表故障率卡片（红色预警）、阀门运行区间异常告警。趋势图支持多选指标对比（≤5）、置信度等级标记。

### 7.3 回路管理页面优化

批量配置评价周期、批量配置算法参数、复杂回路树形展示。

---

## 8. 实施路线图（修订后优先级）

| 阶段 | 名称 | 工期 | 交付物 | 依赖 | 风险 |
|-----|------|------|--------|-----|------|
| Phase 0 | 配置化基础设施 | 2 周 | `algorithm_parameter` 表、参数加载机制（复用 metric_config.threshold）、配置 UI | 无 | 低 |
| Phase 1 | 仪表故障率 + 统计指标 | 2 周 | InstrumentFaultRateCalculator（复用 outlier_reasons）+ 统计指标、迁移、聚合策略 | Phase 0 | 低 |
| Phase 2 | 抗扰性分析 + 振荡增强 | 2 周 | fast_rate 可选分支、相似度带宽配置、振荡幅值/穿越次数 | Phase 1 | 中 |
| Phase 3 | 前端综合评估页 | 2 周 | 雷达图/散点图/统计柱状图/故障率卡片 | Phase 1 | 低 |
| Phase 4 | 复杂回路 RFC + 实施 | 1+4 周 | RFC 文档（NodeAggregator 改造）→ 实施 | Phase 1 | 高 |
| Phase 5 | PID 模板（扩展 dcs_model） | 2 周 | dcs_pid_structure 表、模板 UI | Phase 0 | 中 |

### 里程碑

| 节点 | 验收标准 |
|-----|---------|
| W2 | Phase 0：参数配置页可用，硬编码参数可 UI 修改 |
| W4 | Phase 1：仪表故障率正确（复用检测），统计指标 API 完整 |
| W6 | Phase 2：抗扰性分析开关可控，振荡增强生效 |
| W8 | Phase 3：前端综合评估页重构完成 |
| W9 | Phase 4 RFC：复杂回路聚合方案评审通过 |
| W13 | Phase 4：串级/超驰/NooM 可创建展示 |
| W15 | Phase 5：PID 模板配置可用 |

---

## 9. 风险评估（补充评审识别项）

| 风险 | 概率 | 影响 | 缓解措施 |
|-----|------|-----|---------|
| 新指标误入 AGGREGATE_FIELDS 致节点看板失真 | 中 | 高 | §3.1 聚合策略枚举，仅故障率 AGGREGATABLE |
| 可信度阈值配置化破坏 INCONCLUSIVE 一致性 | 中 | 中 | §2.5 降级 P2，联动校验 |
| 两张快照表字段不同步 | 中 | 中 | §3.3 同批迁移 |
| 复杂回路聚合与 importance_level 权重冲突 | 中 | 高 | §5.2 RFC 先行 |
| 抗扰性分析改变 fast_rate 依赖链 | 低 | 中 | §4.4 开关默认关闭 |
| 配置化改造引入回归 | 中 | 高 | 保留默认值，完整 pytest |

---

## 10. 资源需求

| 角色 | 人月 | 职责 |
|-----|------|-----|
| 后端开发 | 2.5 | 计算器、数据模型、API、聚合改造 |
| 前端开发 | 1.5 | 配置页、综合评估页、看板 |
| 测试 | 1 | 功能/回归/性能 |

工具：pytest（单元）、Playwright（E2E）、Alembic（迁移，两张快照表同批）。

---

## 11. 附录：HiaMonitor 与 CLPM 指标对照表

| HiaMonitor 指标 | CLPM 现状 | 建议动作 |
|----------------|----------|---------|
| 稳定率 | ✅ 已有 | CDF 算法作可选（P2） |
| 自控率/饱和率/行程指数/粘滞 | ✅ 已有 | — |
| 阀门线性/非线性 | ❌ 缺失 | P0 新增（L1） |
| 振荡周期 | ⚠️ 部分 | 独立展示 |
| 振荡幅值 | ❌ 缺失 | P0 新增（L2 依赖振荡率） |
| 设定点穿越次数 | ❌ 缺失 | P0 新增（L1） |
| PV/SP/OP 均值+标准差 | ❌ 缺失 | P0 新增（L1，DISPLAY_ONLY） |
| 偏差均值/标准差 | ❌ 缺失 | P1 新增 |
| 阀门运行区间 | ❌ 缺失 | P0 新增 |
| **回路仪表故障率** | ❌ 缺失 | **P0 新增（复用 outlier_reasons，AGGREGATABLE）** |
| 仪表故障状态(冻结/突变/超限) | ✅ 既有检测 | 复用，不重建 |
| 时间常数 | ❌ 缺失 | P1 新增 |
| 快速率 | ✅ 已有 | 抗扰性分析可选分支 |
| 综合评分 | ✅ 已有 | 保持 R 折扣因子（国标对齐），不照搬 HiaMonitor 饱和惩罚 |
