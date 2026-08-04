# CLPM 可信度计算统一与简化改进方案（草案 v1.0）

> 日期：2026-08-04 ｜ 基线：实现契约 v2.3 / PRD v6.1 / UI-UX v6.1 ｜ 状态：**草案，待评审**
> 范围：数据预处理管道、可信度评估器、KPI 计算、诊断引擎、回路整定、可信度落库与前端展示
> 性质：**做减法的架构收敛**——统一 valid_rate 口径、可信度回归回路级单一值、消除三链路预处理分叉

---

## 0. 文档定位与阅读说明

本方案是可信度计算链路的**架构收敛方案**，目标是将当前"三链路各自实现预处理 + 四套 valid_rate 口径 + 两套可信度枚举"的分散状态，收敛为"一条共享数据质量评估内核 + 一个回路级可信度 + 一套枚举"。

文档结构：
- §1 背景与目标
- §2 现状诊断（带代码证据的缺口清单）
- §3 目标架构
- §4 共享数据质量评估内核设计（DataQualityAssessor）
- §5 可信度定义收敛（回路级单一值 + 指标级降级为可计算性）
- §6 信息模型调整与迁移
- §7 API 调整
- §8 前端调整
- §9 显式代码修改清单
- §10 风险与决策点
- §11 实施阶段划分
- §12 回归测试

**重要**：本方案是"删减/合并"而非"新增"，净复杂度下降。不涉及任何算法语义变更（异常值检测 8 类原因码、MARK_ONLY 集合、综合评分公式均保留）。

---

## 1. 背景与目标

### 1.1 问题陈述

可信度的本质是"这批数据整体可不可信"，应是回路级的一个单一判断，在评估、诊断、整定所有场景一致消费。当前实现偏离了这一本质：

- **4 套 valid_rate 口径**：KPI 指标级 / 数据块级（审计）/ 诊断 / 整定清洗，分母与分子各不相同
- **2 套 ConfidenceLevel 枚举**：平台级（A/B/C/D/E）与辨识专用（多 INCONCLUSIVE），阈值与判定逻辑不同
- **3 份预处理实现**：KPI 用 8 步 Pipeline、诊断自写 `_apply_outlier_preprocessing`、整定自写 `_clean_nan_segments`
- **概念混淆**：指标级 mask（决定哪些点参与计算）与可信度等级（A/B/C/D/E）被捆绑，导致每个指标都有自己的可信度，但综合评分只取最低、诊断用回路级单一值——细粒度大部分被浪费
- **字段错配**：`kpi_snapshot_hourly.valid_rate` 存单指标口径，同行的 `confidence_level` 存回路级口径，语义不一致
- **前端自推导**：`loop/detail.vue` 用 `good_value_rate` 推导可信度，第四套口径

### 1.2 核心目标

1. **统一 valid_rate 口径**：全链路使用同一个"回路级 valid_rate"，由共享内核一次计算
2. **可信度回归回路级单一值**：一个回路一个 A/B/C/D/E，跨场景一致
3. **指标级 mask 降级**：从"打可信度等级"降级为"判定可计算性"（能算 / INCONCLUSIVE）
4. **统一预处理内核**：抽取共享 DataQualityAssessor，诊断链路接入，消除分叉
5. **统一枚举与阈值**：一套 ConfidenceLevel，一套阈值，解决多进程同步

### 1.3 设计原则

- **保留领域复杂度，消除工程分叉**：8 类异常值原因码、MARK_ONLY 集合、综合评分公式是领域本质，保留；三份预处理实现、四套口径是工程债，消除
- **解耦"能否计算"与"可信度等级"**：指标 mask 只决定 value 是否为 None，不再产生 A/B/C/D/E
- **渐进式收敛**：分 3 个 Phase，每阶段可独立验证与回滚

---

## 2. 现状诊断（缺口清单）

### 2.1 四套 valid_rate 口径

| 链路 | valid_rate 来源 | 分母 | 分子 | 代码位置 |
|---|---|---|---|---|
| KPI 指标级 | `MetricCalculatorBase._get_valid_rate` | DataBlock.point_count | len(masked_indices) | `metric_calculator/base.py:113-127` |
| 数据块级（审计） | `compute_quality_summary` | point_count | 全 tag valid 交集 | `preprocessing/quality_summary.py:62-75` |
| 诊断 | `compute_quality_summary`（仅 pv_valid） | **n_raw（原始宽表点数）** | pv_valid True 数 | `diagnosis_engine.py:4078-4086` |
| 整定（取数） | `pvop_block.quality_summary.valid_rate` | DataBlock.point_count | PVOP tag 交集 | `tuning.py:398` |
| 整定（清洗） | `len(u)/n` | 清洗前点数 | 清洗后点数 | `tuning_identification/pipeline.py:196` |

**问题**：
- 诊断分母用 `n_raw`（含 SP/OP 缺失的对齐外点），系统性偏低
- 诊断 `compute_quality_summary` docstring 明确"不参与可信度判定"，但 `diagnosis_engine.py:1109` 却用它判定 —— 文档与实现矛盾
- KPI 指标级与数据块级并存，数据块级声称"仅审计"却通过 lineage 进入血缘字段

### 2.2 字段口径错配

`kpi_snapshot_hourly` 同一行两个字段口径不同（`kpi_calc.py:1569-1618`）：

| 字段 | 口径 | 来源 |
|---|---|---|
| `valid_rate` | **单指标**（accuracy_rate 的 DataBlock valid_rate） | `_extract_lineage_info` L1588-1592 |
| `confidence_level` | **回路级**（综合评分可信度） | `_extract_lineage_info` L1607-1609 |

同一行 valid_rate（单指标）与 confidence_level（回路级）语义错配，用户无法从 valid_rate 反推 confidence_level。

### 2.3 概念捆绑：指标 mask 与可信度

当前 26 条 mask 契约（`db/postgresql/02_seed_data.sql:647-716`）让每个指标产生独立 valid_rate → 独立 A/B/C/D/E。但消费侧：
- 综合评分可信度 = 核心指标 + R 的**最低**等级（`confidence_evaluator.py:393-418`），细粒度被"取最低"浪费
- 诊断用回路级**单一**可信度（`diagnosis_engine.py:1109`），完全没用指标级
- `loop_confidence_latest.metrics` 存 12 子指标可信度，但前端仅 `loop-performance.vue` 可信度抽屉消费（`kpi-report.vue` 不消费）

### 2.4 两套 ConfidenceLevel 枚举

| 枚举 | 位置 | 值 | 阈值依据 |
|---|---|---|---|
| 平台级 | `contracts/data_types.py:77-87` | A/B/C/D/E | valid_rate 0.95/0.80/0.60/0.20 |
| 辨识专用 | `tuning_identification/types.py:29-37` | A/B/C/D/E/**INCONCLUSIVE** | R² 0.90/0.80/0.70/0.50 + 残差 + 激励 |

辨识专用枚举多一个 INCONCLUSIVE，且判定维度完全不同（算法拟合度 vs 数据质量），两者却都叫 ConfidenceLevel，维护易混淆。

### 2.5 阈值多进程不同步

`ConfidenceEvaluator.set_thresholds()` 只更新当前进程的模块级 `_threshold_cache`（`confidence_evaluator.py:92`）。uvicorn + 多 Celery worker 场景下，配置接口只生效于处理该 HTTP 请求的进程，其他 worker 用旧阈值。

### 2.6 前端自推导可信度

`loop/detail.vue:205-214` 用 `good_value_rate` 推导可信度（阈值 95/80/60/20），与后端 `valid_rate` 口径不同。`good_value_rate` 是质量码维度，`valid_rate` 是异常值剔除维度，两者含义不同，导致 detail 页徽章与性能评估页不一致。

---

## 3. 目标架构

```
┌─────────────────────────────────────────────────────────────┐
│  共享数据质量评估内核（DataQualityAssessor）—— 新增           │
│  输入：原始时序（pv/sp/op/mode）+ 量程 + 控制类型             │
│  处理：质量码识别 + 异常值检测（8类）+ validity 标记           │
│  输出：validity 字典 + 回路级 valid_rate + outlier_reasons    │
│  不做：归一化、删点（纯质量评估，KEEP_ALL_WITH_VALIDITY）      │
└─────────────────────────────────────────────────────────────┘
            │                    │                    │
            ▼                    ▼                    ▼
    ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
    │ KPI 链路      │     │ 诊断链路      │     │ 整定链路      │
    │ +归一化       │     │ +原始值对齐   │     │ +连续段清洗   │
    │ +指标计算     │     │ +诊断算法     │     │ +辨识算法     │
    └──────────────┘     └──────────────┘     └──────────────┘
            │                    │                    │
            └────────┬───────────┴────────────────────┘
                     ▼
┌─────────────────────────────────────────────────────────────┐
│  统一可信度（ConfidenceEvaluator）—— 收敛                    │
│  回路级 valid_rate → A/B/C/D/E（一套枚举、一套阈值）          │
│  所有场景消费同一个回路级可信度                                │
└─────────────────────────────────────────────────────────────┘
```

**关键变化**：
1. 预处理内核共享——诊断不再自写 `_apply_outlier_preprocessing`，调 DataQualityAssessor
2. valid_rate 统一为回路级（核心 tag 交集 / point_count），一次计算处处消费
3. 可信度回归回路级单一值；指标级 mask 降级为"依赖 tag 声明 → 可计算性"
4. 一套 ConfidenceLevel，辨识链路的算法可信度改名为 `algorithm_confidence` 与数据可信度区分

---

## 4. 共享数据质量评估内核设计（DataQualityAssessor）

### 4.1 定位

从 `PreprocessingPipeline` 的 step①②④⑧ 中抽取**纯质量评估**部分，成为无归一化、无删点的纯函数模块。KPI Pipeline 内部调用它完成质量评估，再单独做 step③归一化；诊断链路直接调用它，拿到 validity + valid_rate 后自行决定删点。

### 4.2 接口

```python
# app/services/preprocessing/data_quality_assessor.py（新增）

@dataclass
class QualityAssessment:
    """数据质量评估结果（共享内核输出）."""
    validity: dict[str, list[bool]]       # {"pv_valid": [...], "sp_valid": [...], ...}
    outlier_reasons: dict[str, list[list[str]]]
    loop_valid_rate: float                # 回路级 valid_rate（核心 tag 交集）
    quality_summary: QualitySummary       # 审计摘要（保留 missing_rate 等）
    consecutive_segments: list[tuple[int, int]]


class DataQualityAssessor:
    """共享数据质量评估内核.

    职责：质量码识别 + 异常值检测（8类）+ validity 标记 + 回路级 valid_rate 计算。
    不做归一化、不删点。KPI/诊断/整定共享，保证 valid_rate 口径统一。

    设计依据：算法说明 §3.4.2, §3.7.2
    """

    #: 参与回路级 valid_rate 的核心 tag（回路评估必需信号）
    CORE_TAGS: tuple[str, ...] = ("pv", "sp", "op", "mode")

    def __init__(self, config: LoopPreprocessConfig) -> None: ...

    def assess(
        self,
        raw: RawTimeSeries,
        skip_frozen_signals: frozenset[str] | None = None,
    ) -> QualityAssessment:
        """评估原始时序的数据质量.

        Args:
            raw: 原始时序（工程值，未归一化）
            skip_frozen_signals: 跳过冻结检测的信号（诊断默认 skip pv 以外，
                KPI 默认 skip sp/op/mode/pid_*）

        Returns:
            QualityAssessment，含 validity + 回路级 valid_rate
        """
```

### 4.3 回路级 valid_rate 定义

```
loop_valid_rate = sum(pv_valid[i] && sp_valid[i] && op_valid[i] && mode_valid[i])
                  / point_count
```

即**核心 tag（pv/sp/op/mode）同时有效的点占比**。这是当前"数据块级 valid_rate"的子集（剔除 PID_P/PID_I/PID_D 等非评估信号），避免无关 tag 拉低。

### 4.4 KPI Pipeline 改造

`PreprocessingPipeline.process()` 内部改为调用 `DataQualityAssessor.assess()`，再叠加 step③归一化：

```python
def process(self, raw, tag_group) -> DataBlock:
    # 共享内核：质量评估（不归一化）
    assessment = self._assessor.assess(raw, skip_frozen_signals=_SKIP_FROZEN_SIGNALS)
    # KPI 专属：归一化
    signals = self._step3_normalize(raw.signals) if self.config.normalize else raw.signals
    # 组装 DataBlock（用 assessment 的 validity / valid_rate）
    ...
```

### 4.5 诊断链路改造

`_apply_outlier_preprocessing` 改为调用共享内核，消除自写编排：

```python
# diagnosis_engine.py
assessor = DataQualityAssessor(config)
assessment = assessor.assess(raw_series, skip_frozen_signals=frozenset())  # 诊断全检测
valid_rate = assessment.loop_valid_rate          # ← 统一口径
# 诊断专属：按 pv_valid 删点（保留当前删除语义）
pv_valid = assessment.validity["pv_valid"]
filtered = [d for i, d in enumerate(aligned) if pv_valid[src_indices[i]]]
confidence_level = ConfidenceEvaluator.evaluate(valid_rate).value
```

### 4.6 整定链路改造

整定已通过 DataPlanner 复用 Pipeline，但 valid_rate 取自 PVOP_HF DataBlock（仅 pv+op 交集）。改为：

```python
# tuning.py _fetch_preprocessed_signals
# 额外调用共享内核计算回路级 valid_rate（含 sp/mode）
assessment = assessor.assess(raw_series)
valid_rate = assessment.loop_valid_rate   # ← 替代 pvop_block.quality_summary.valid_rate
```

或更简单：DataPlanner 在组装 bundle 时，由共享内核统一产出一个回路级 valid_rate 挂在 DataBlock 元数据上，整定直接读取。

---

## 5. 可信度定义收敛

### 5.1 回路级单一可信度

**定义**：一个回路一次评估周期，产生**一个**可信度等级 A/B/C/D/E，由回路级 valid_rate 经 `ConfidenceEvaluator.evaluate()` 判定。

**消费**：
- KPI：`kpi_snapshot_hourly.confidence_level` / `loop_confidence_latest.confidence_level` 直接存回路级
- 诊断：每条 `DiagnosisResult.feature_values.confidence_level` 存回路级（现状已是回路级，仅需统一 valid_rate 口径）
- 整定：`TuningRecord.confidence_level` 存数据可信度（回路级 valid_rate）与算法可信度取较低者（现状逻辑保留）

### 5.2 指标级 mask 降级为可计算性

**现状**：每个指标经 `_make_result` 用自己的 valid_rate 打 A/B/C/D/E。
**目标**：指标的可信度等级 = 回路级可信度（统一）；指标 mask 只决定 value 是否为 None（INCONCLUSIVE）。

> **澄清：指标级 mask 降级 ≠ 废弃**。降级的只是"打 A/B/C/D/E 等级"这一职责，回归到回路级单一可信度；mask 本身的"精准筛选"价值（判定指标可计算性、决定 value 是否为 None）完整保留。mask 仍然是"该指标依赖哪些 tag、有效点是否足够"的事实来源，只是不再承担"为指标单独定级"的职责——定级回归回路级 valid_rate 统一判定，避免 12 个子指标各打一档、最终又被"取最低"或"被诊断忽略"而浪费细粒度。

改造 `MetricCalculatorBase`：

```python
def _make_result(self, bundle, value, details=None, precision=2) -> MetricResult:
    # 可计算性：依赖 tag 有效点是否足够（沿用 mask，但只判 INCONCLUSIVE）
    vr = self._get_valid_rate(bundle)
    is_inconclusive = vr < _INCONCLUSIVE_THRESHOLD  # 例如 < 0.20 → value=None
    # 可信度等级：统一用回路级（从 bundle.data_block 注入）
    loop_confidence = bundle.data_block.loop_confidence_level  # 新增字段
    if is_inconclusive:
        return self._make_inconclusive(bundle, "data_insufficient")
    return MetricResult(
        metric_code=self.metric_code,
        value=round(float(value), precision),
        confidence_level=loop_confidence,   # ← 回路级，不再用指标级 valid_rate
        lineage=self._build_lineage(bundle, vr),
        details=details or {},
    )
```

`DataBlock` 新增 `loop_confidence_level: str` 字段，由 Pipeline 调用 `ConfidenceEvaluator.evaluate(loop_valid_rate)` 一次算出，所有指标读取。

### 5.3 综合评分可信度简化

`compute_composite_score` 的 `_min_confidence`（取核心指标+R 最低）简化为直接用回路级可信度：

```python
# 综合评分可信度 = 回路级可信度（不再取各指标最低）
confidence = metric_results["accuracy_rate"].confidence_level  # 已是回路级
```

INCONCLUSIVE 判定保留：R 缺失或核心指标 value=None → 综合评分 INCONCLUSIVE。

### 5.4 辨识专用 ConfidenceLevel 合并

辨识链路的算法可信度（R²+残差+激励）改名 `algorithm_confidence`，用独立枚举 `AlgorithmConfidenceLevel`（A/B/C/D/E/INCONCLUSIVE），与数据可信度 `ConfidenceLevel`（A/B/C/D/E）区分。最终整定可信度 = `min(data_confidence, algorithm_confidence)`，逻辑不变，仅命名清晰化。

### 5.5 契约简化

`clpm_metric_data_requirement` 表的 `mask_expression` 字符串字段，改为 `required_tags: list[str]` 声明（如 `["pv","sp"]`）。可计算性判定改为"依赖 tag 全有效点占比 ≥ 阈值"。mask 求值器（`validity_mask.py`）可下线，或保留为 `required_tags` 的内部实现。

26 条契约从"布尔表达式字符串"简化为"tag 列表声明"，可读性与可维护性提升。

---

## 6. 信息模型调整与迁移

### 6.1 DataBlock 结构调整

```python
@dataclass
class DataBlock:
    ...
    # 新增：回路级可信度（由 Pipeline 一次算出，所有指标读取）
    loop_confidence_level: str = "E"
    loop_valid_rate: float = 0.0
```

### 6.2 kpi_snapshot_hourly 字段口径统一

| 字段 | 现状口径 | 目标口径 |
|---|---|---|
| `valid_rate` | 单指标（accuracy_rate DataBlock） | **回路级**（核心 tag 交集） |
| `confidence_level` | 回路级综合评分 | 回路级（不变） |

`_extract_lineage_info` 改为取回路级 valid_rate（修复 §2.2 错配）。

### 6.3 loop_confidence_latest 简化

| 字段 | 现状 | 目标 |
|---|---|---|
| `confidence_level` | 回路级 | 不变 |
| `valid_rate` | 单指标 | 回路级 |
| `metrics` JSONB | 12 子指标 `{value, confidence}` | 12 子指标 `{value}`（去掉 confidence，因统一为回路级） |

`_extract_metrics_detail` 不再写 `confidence` 字段；前端去掉子指标可信度列。`metrics` 列保留（子指标 value 仍有展示价值），或视评审决定整体下线。

### 6.4 迁移

- **无 schema 变更**：`valid_rate`/`confidence_level`/`metrics` 字段类型不变，仅写入口径变更
- **数据回填**：历史快照的 `valid_rate` 仍是单指标口径，可在下次评估周期自然覆盖；如需严格统一，提供回填脚本重算历史 `loop_valid_rate`
- **alembic**：无需新增迁移（字段已存在）；若 `metrics` JSONB 决定下线，加一个 drop column 迁移

---

## 7. API 调整

### 7.1 `GET /loops/{loop_id}/confidence-latest`

响应 `LoopConfidenceLatestItem`：
- `confidenceLevel` / `validRate`：已是回路级，口径统一后语义不变（valid_rate 从单指标改为回路级，值更稳定）
- `metrics`：子指标对象去掉 `confidence` 字段，仅保留 `value`

### 7.2 `GET /loops/{loop_id}/confidence-latest` 消费方

- `loop-performance.vue` 可信度抽屉：去掉 12 子指标可信度列，保留回路级 + 子指标 value
- `kpi-report.vue`：无改动（已只消费回路级）

### 7.3 阈值配置 `POST /configs/confidence-thresholds`

增加多进程同步：保存后通过 Redis pub/sub 广播 `confidence:thresholds:updated`，各 worker 订阅后调 `set_thresholds()`。或 worker 启动时从 DB 加载当前阈值（更简单，但有启动延迟）。

---

## 8. 前端调整

### 8.1 loop/detail.vue（修复自推导）

```ts
// 现状：用 good_value_rate 推导（错误口径）
const confidenceLevel = computed(() => {
  const rate = monitorDetail.value?.kpiSummary.good_value_rate ?? 0;
  ...
});

// 目标：直接用后端 confidence_level，仅在后端缺失时才兜底
const confidenceLevel = computed(() => {
  return monitorDetail.value?.kpiSummary.confidenceLevel ?? '—';
});
```

### 8.2 loop-performance.vue 可信度抽屉

- 删除 `CONFIDENCE_METRIC_META`（L820-833）、`confMetricRows`（L857-865）
- 删除"子指标可信度"表格（L1986-2005），改为"子指标数值"表格（仅 value，无 confidence 列）
- 保留回路级 confidenceLevel + validRate 展示（L1972-1979）

### 8.3 confidence-badge.vue

无改动（仍是 A/B/C/D/E 徽章，输入回路级等级）。

---

## 9. 显式代码修改清单

### 9.1 新增

| 文件 | 内容 |
|---|---|
| `backend/app/services/preprocessing/data_quality_assessor.py` | DataQualityAssessor 共享内核 + QualityAssessment 数据类 |

### 9.2 后端修改

| 文件 | 行号 | 改动 |
|---|---|---|
| `app/services/preprocessing/pipeline.py` | L75-173 | process() 内部改调 DataQualityAssessor；归一化改为可选 |
| `app/services/preprocessing/quality_summary.py` | L32-39 | docstring 更新：loop_valid_rate 参与可信度判定 |
| `app/services/confidence_evaluator.py` | L169-390 | compute_composite_score 可信度改用回路级；_min_confidence 简化 |
| `app/services/metric_calculator/base.py` | L133-209 | _make_result 用回路级可信度；_make_inconclusive 阈值化 |
| `app/contracts/data_types.py` | L188-232 | DataBlock 新增 loop_confidence_level / loop_valid_rate |
| `app/tasks/kpi_calc.py` | L1569-1618 | _extract_lineage_info 取回路级 valid_rate |
| `app/tasks/kpi_calc.py` | L1495-1517 | _extract_metrics_detail 去掉 confidence 字段 |
| `app/tasks/diagnosis_engine.py` | L3981-4091 | _apply_outlier_preprocessing 改调 DataQualityAssessor |
| `app/tasks/diagnosis_engine.py` | L1107-1115 | valid_rate 用回路级口径 |
| `app/services/tuning.py` | L324-432 | _fetch_preprocessed_signals 用回路级 valid_rate |
| `app/services/tuning_identification/types.py` | L29-37 | ConfidenceLevel 改名 AlgorithmConfidenceLevel |
| `app/services/tuning_identification/pipeline.py` | L1209-1226 | _assess_confidence 返回 AlgorithmConfidenceLevel |
| `app/api/v1/endpoints/confidence_config.py` | L293-361 | POST 后 Redis 广播阈值更新 |
| `app/models/metric_data_requirement.py` | L49 | mask_expression → required_tags（或并存） |

### 9.3 种子数据/迁移

| 文件 | 改动 |
|---|---|
| `db/postgresql/02_seed_data.sql` | 26 条契约 mask_expression → required_tags（或保留 mask_expression 由 assessor 内部解析） |
| `backend/alembic/versions/xxx_confidence_unification.py` | 若 metrics 下线则 drop column；否则无 schema 迁移 |

### 9.4 前端修改

| 文件 | 行号 | 改动 |
|---|---|---|
| `views/loop/detail.vue` | L205-214 | confidenceLevel 改用后端字段 |
| `views/metric/loop-performance.vue` | L820-833, L857-865, L1986-2005 | 删除子指标可信度表格 |
| `api/metric.ts` | L1257-1296 | LoopConfidenceLatestItem.metrics 去掉 confidence |
| `api/tuning.ts` | L60 | ConfidenceLevel → AlgorithmConfidenceLevel（辨识专用） |

---

## 10. 风险与决策点

### 10.1 决策点（已定稿 2026-08-04）

| # | 决策 | **定稿结论** | 实施约束 |
|---|---|---|---|
| D1 | 回路级 valid_rate 的核心 tag 集合 | **pv/sp/op/mode** | 架构层面采用 PV/SP/OP/MODE 模式；`DataQualityAssessor.CORE_TAGS = ("pv","sp","op","mode")`；`loop_valid_rate = Σ(pv∧sp∧op∧mode valid) / point_count` |
| D2 | `metrics` JSONB 去向 | **保留 value，去除 confidence** | 数据清洗规则：`{metric: {value, confidence}} → {metric: {value}}`；建立质量校验：value 类型一致性 + null 标注 INCONCLUSIVE |
| D3 | mask_expression 声明方式 | **改 required_tags 声明** | 重新定义规范：`required_tags: list[str]`（如 `["pv","sp"]`）；更新种子契约；assessor 内部按 required_tags 求交集判定可计算性；保留 mask_expression 列做兼容过渡（标注 deprecated） |
| D4 | 阈值多进程同步方式 | **Redis pub/sub** | 主题 `confidence:thresholds:updated`；消息结构 `{version, thresholds, updated_at, source}`；POST 接口发布，worker 订阅后调 `set_thresholds()`；可靠性：消息含版本号去重 + 启动时从 DB 全量加载兜底 |
| D5 | 指标 INCONCLUSIVE 阈值 | **vr < 0.20（沿用 E 级阈值）** | 代码审查与测试严格执行；建立阈值监控：valid_rate 落入 [0.20, 0.30) 区间时告警（濒临 INCONCLUSIVE） |

### 10.2 风险

| 风险 | 影响 | 缓解 |
|---|---|---|
| 回路级 valid_rate 比 accuracy 指标级更低（含 OP/MODE） | 部分回路可信度降级 | D1 可调整核心 tag；提供对比报表 |
| 诊断删点语义与共享内核 KEEP_ALL 策略冲突 | 诊断需在内核输出后自行删点 | §4.5 已设计：内核不删点，诊断拿 validity 后自行删 |
| 辨识枚举改名波及前端/序列化 | API 字段名变更 | 保留对外字段名 confidenceLevel，仅内部枚举改名 |
| 历史 valid_rate 口径不一致 | 历史快照 valid_rate 仍单指标口径 | 自然覆盖或提供回填脚本 |
| DataPlanner bundle 复用与归一化开关冲突 | 同一 bundle 不能按 metric 切归一化 | 共享内核不归一化，归一化在 KPI Pipeline 内部，不污染 bundle |

---

## 11. 实施阶段划分

> 执行模式：单一 agent 顺序推进，每 Phase 独立验证 + 提交，失败可回滚至上一 Phase 稳定点。
> 时间节点：Phase 1 当次会话完成；Phase 2/3 待 Phase 1 验收通过后启动。

### Phase 1：共享内核 + 诊断接入（统一 valid_rate 口径）✅ 已完成

**目标**：消除诊断链路的自写预处理，统一 valid_rate 口径。**不改可信度定义**。

| 步骤 | 内容 | 验证 | 状态 |
|---|---|---|---|
| P1-1 | 新增 `DataQualityAssessor`（CORE_TAGS=pv/sp/op/mode，assess() 返回 validity+loop_valid_rate+outlier_reasons） | 单元测试：各异常组合下 validity + loop_valid_rate 正确 | ✅ |
| P1-2 | KPI 链路复用 `compute_loop_valid_rate`（基于现有 Pipeline validity 计算回路级，行为零回归） | pytest：现有 KPI 计算结果不变 | ✅ |
| P1-3 | 诊断 `_apply_outlier_preprocessing` 改调 assessor，valid_rate 用 `loop_valid_rate`（替代 pv_valid/n_raw） | pytest：诊断可信度不再系统性偏低 | ✅ |
| P1-4 | 整定 `_fetch_preprocessed_signals` valid_rate 改用回路级口径 | pytest：整定数据可信度口径与 KPI 一致 | ✅ |
| P1-5 | 修复 `_extract_lineage_info`：`kpi_snapshot_hourly.valid_rate` 改存回路级 | 结构性断言：valid_rate 经 evaluate 得 confidence_level | ✅ |
| P1-6 | 更新 `quality_summary.py` docstring（loop_valid_rate 参与判定） | ruff/docstring 检查 | ✅ |

**门禁**：`pytest -q` 全绿（3700 passed）+ `ruff check` 全绿 + `alembic check` 退出码 0 + 结构性断言（`test_confidence_unification_structural.py` 10 项全绿，验证三链路均通过 `DataQualityAssessor` 计算 valid_rate）。

### Phase 2：可信度定义收敛（回路级单一值）✅ 已完成

**目标**：可信度回归回路级，指标级 mask 降级为可计算性。

| 步骤 | 内容 | 验证 | 状态 |
|---|---|---|---|
| P2-1 | DataBlock 新增 `loop_confidence_level` / `loop_valid_rate` 字段 | 数据结构测试 | ✅ |
| P2-2 | `_make_result` 用回路级可信度；`_make_inconclusive` 阈值化（vr<0.20，D5） | 指标可信度=回路级 | ✅ |
| P2-3 | `compute_composite_score` 可信度简化（直接用回路级） | 综合评分测试 | ✅ |
| P2-4 | D2：`_extract_metrics_detail` 去掉 confidence，仅保留 value；质量校验 | metrics 结构测试 | ✅ |
| P2-5 | D3：契约 mask_expression → required_tags 声明；assessor 内部按 required_tags 求交集 | 契约解析测试 | ✅ |
| P2-6 | 前端 loop/detail.vue 修复（用后端 confidence_level）；loop-performance.vue 去子指标可信度列；monitor.py `_aggregate_kpi_snapshots` 补 confidence_level | check:type + E2E | ✅ |

**门禁**：`pytest -q` 3700 passed + `check:type` 2/2 通过 + ruff 全绿 + 结构性断言 10 项全绿。✅

### Phase 3：枚举统一 + 阈值同步 ✅ 已完成

**目标**：一套 ConfidenceLevel，阈值多进程同步。

| 步骤 | 内容 | 验证 | 状态 |
|---|---|---|---|
| P3-1 | 辨识专用 ConfidenceLevel 改名 AlgorithmConfidenceLevel（对外 API 字段名不变，保留兼容别名） | 序列化测试 107 passed | ✅ |
| P3-2 | D4：阈值配置 Redis pub/sub（主题 `confidence:thresholds:updated`，消息含 version 去重，worker 订阅 + 启动全量加载兜底） | 多进程同步测试 22 passed | ✅ |
| P3-3 | D5 监控：valid_rate ∈ [D, D+0.10) 告警（告警区间跟随 D 阈值配置） | 告警链路测试 4 passed | ✅ |
| P3-4 | 前端 tuning API 类型对齐（ConfidenceLevel → AlgorithmConfidenceLevel） | check:type 通过 | ✅ |

**门禁**：`pytest -q` 3722 passed + ruff check+format 全绿 + `alembic check` 退出码 0 + `check:type` 2/2 通过 + 22 个多 worker 阈值同步测试全绿。✅

---

## 13. 实施效果评估机制

### 13.1 评估指标（实施前后对比）

| 维度 | 指标 | 采集方式 |
|---|---|---|
| 口径一致性 | 同回路同时段 KPI/诊断/整定 valid_rate 差值 | 结构性断言 + 抽样报表 |
| 可信度稳定性 | 诊断可信度不再系统性低于 KPI 的回路占比 | 评估前后对比 |
| 代码复杂度 | valid_rate 计算实现处数量（4→1）、ConfidenceLevel 枚举数（2→1） | 代码盘点 |
| 字段错配 | `valid_rate` 与 `confidence_level` 口径错配数（2→0） | 数据校验脚本 |
| 性能 | 评估任务平均耗时（应持平或下降，因诊断不再自写预处理） | 任务日志 |

### 13.2 校验机制

- **数据质量校验（D2）**：`metrics` JSONB 清洗后，校验每条 `{value}` 的类型一致性（float|null），null 必须对应 INCONCLUSIVE 状态
- **阈值监控（D5）**：valid_rate ∈ [0.20, 0.30) 时记 WARN 日志并上报，便于发现"濒临 INCONCLUSIVE"的回路
- **口径一致性巡检**：每周期抽样 10 回路，断言 `evaluate(loop_valid_rate).value == confidence_level`
- **HF 组语义退化评估**：验证 `DataQualityAssessor` 是否缓解 HF 组语义退化——当前 `valid_rate` 受 `tagGroup` 切分影响（PVOP_HF 仅 pv+op 交集、BASE 仅基础采样），同回路不同组块口径漂移；改造后统一为回路级核心 tag（pv/sp/op/mode）交集口径，不再因组块切分而退化。评估方式：对同回路同时段抽样，断言 `KPI/诊断/整定` 三链路 `valid_rate` 差值 ≤ 0.02（容差来自对齐插值），并对比改造前后差值分布是否收敛

---

## 14. 回滚方案

### 14.1 回滚原则

- 每 Phase 独立提交（Conventional Commits），失败时 `git revert` 该 Phase 的 commit 即可恢复
- Phase 1 不改可信度定义（仅统一口径），回滚零风险
- Phase 2 改可信度定义，回滚需同步回退前端
- 数据库无 schema 变更（字段已存在），回滚不涉及迁移回退

### 14.2 各 Phase 回滚步骤

| Phase | 回滚动作 | 恢复点 |
|---|---|---|
| Phase 1 | `git revert` Phase 1 commits | 诊断恢复自写预处理，valid_rate 恢复多口径（可接受） |
| Phase 2 | `git revert` Phase 2 commits + 前端回退 | 指标级可信度恢复，metrics 恢复含 confidence |
| Phase 3 | `git revert` Phase 3 commits | 枚举恢复两套，阈值恢复单进程 |

### 14.3 数据回滚

- `metrics` JSONB 清洗（去 confidence）为向后兼容操作（前端忽略多余字段），回滚后旧前端仍可读 value
- `kpi_snapshot_hourly.valid_rate` 口径变更：历史数据保留原值，新周期自然覆盖；如需严格回滚，提供回填脚本重算单指标口径

### 14.4 稳定性保障

- 每 Phase 完成后跑完整门禁（pytest + ruff + alembic check + check:type + E2E）
- Phase 1 验收通过后再启动 Phase 2，不跨 Phase 并行
- 关键回归测试：综合评分值、诊断标签、整定可信度门禁三项行为对比

---

## 12. 回归测试

### 12.1 单元测试

- `tests/services/test_data_quality_assessor.py`（新增）：内核在各异常组合下的 validity + loop_valid_rate
- `tests/services/test_confidence_evaluator.py`：回路级可信度判定、综合评分可信度简化
- `tests/tasks/test_diagnosis_engine.py`：诊断 valid_rate 口径与 KPI 一致
- `tests/services/test_tuning.py`：整定 valid_rate 回路级口径
- `tests/tasks/test_kpi_calc.py`：`_extract_lineage_info` 回路级 valid_rate、`_extract_metrics_detail` 无 confidence

### 12.2 结构性断言

- 同回路同时段：KPI confidence_level == 诊断 confidence_level == 整定 data_confidence
- `kpi_snapshot_hourly.valid_rate` 与 `confidence_level` 口径一致（valid_rate 经 evaluate 得 confidence_level）

### 12.3 E2E

- 性能评估页可信度抽屉：回路级展示，无子指标可信度列
- 回路详情页：可信度徽章与性能评估页一致（不再用 good_value_rate 推导）
- 整定模型页：可信度门禁逻辑不变（A/B/C 允许，D/E/INCONCLUSIVE 阻断）

### 12.4 门禁

- `cd backend && uv run pytest -q` 全绿
- `cd backend && uv run ruff check . && uv run ruff format --check .`
- `cd backend && uv run alembic check`（退出码 0）
- `cd frontend && pnpm run check:type`
- `cd e2e && pnpm exec playwright test`

---

## 附录 A：与基线文档的对齐

| 基线文档 | 版本 | 本方案影响 |
|---|---|---|
| 实现契约 | v2.3 | 可信度定义、valid_rate 口径、指标级 mask 语义——需升 v2.4 |
| PRD | v6.1 | 可信度章节（§5.5）口径统一——需同步 |
| FDS | v6.0 | §5.3.7 可信度评估、§5.3.10 数据血缘——需同步 |
| UI-UX | v6.1 | §7.15 可信度徽章——展示层简化——需同步 |
| 算法说明 | — | §3.7.2 可信度判定、§3.4.2 预处理——需同步 |

## 附录 B：复杂度对比

| 维度 | 现状 | 目标 |
|---|---|---|
| valid_rate 口径 | 4 套 | 1 套（回路级） |
| ConfidenceLevel 枚举 | 2 套 | 1 套 + 1 个独立算法可信度枚举 |
| 预处理实现 | 3 份 | 1 份共享内核 + 各链路薄封装 |
| 可信度值数量 | 12 指标级 + 1 综合 + 1 诊断 | 1 回路级 |
| mask 契约 | 26 条布尔表达式字符串 + 求值器 | 26 条 required_tags 声明 |
| 跨场景一致性 | ❌ | ✅ |
| 字段口径错配 | 2 处（valid_rate/confidence_level、前端推导） | 0 |
