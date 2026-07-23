# 重构计划评审报告：CLPM v6.1 HiaMonitor 借鉴重构计划

**评审对象**: `docs/设计文档/CLPM_v6.1_HiaMonitor借鉴重构计划.md` v1.0
**评审日期**: 2026-07-23
**评审依据**: 现有代码实现、FDS v6.0、实现契约 v2.0、PRD v6.1、HiaMonitor V3.1.0 用户手册
**评审原则**: 最小化现有代码改动、选择性借鉴成熟产品、增量式系统升级

---

## 一、总体评估结论

**结论：方向正确，但技术细节存在多处与现有架构脱节，需修订后方可实施。**

重构计划在战略层面值得肯定——采用增量式而非推倒重来，符合"最小化改动"原则；指标覆盖差距分析准确，借鉴 HiaMonitor 的切入点选择合理。但在技术落地层面，计划存在 **3 处严重技术错误**（会导致实现无法工作）、**5 处架构复用遗漏**（违反最小化改动原则，重复造轮子）和 **若干边界未澄清**。

核心问题可归纳为一句话：**计划对现有架构的复用深度不足，多处"新增"实际已有实现，而真正需要扩展的地方（节点聚合、三层编排）却未触及。**

### 评审打分

| 维度 | 评分 | 说明 |
|-----|------|-----|
| 业务需求匹配度 | 良好 | 指标差距分析准确，复杂回路/故障检测需求真实 |
| 技术可行性 | 中下 | 3 处技术错误，伪代码无法直接落地 |
| 与现有系统兼容性 | 中下 | 5 处未复用既有实现，存在重复建表/重复检测 |
| 借鉴适用性 | 良好 | 借鉴点选择合理，但本地化调整不足 |
| 风险识别 | 中 | 缺少对节点聚合、三层编排影响的分析 |
| 实施路径 | 中 | Phase 划分合理，但依赖关系与工作量估算偏乐观 |

---

## 二、严重问题清单（P0 — 必须修订）

### P0-1：MetricDataBundle 不存在 `signals` 字段，Phase 2 伪代码无法运行

**位置**: 计划 §4.1 代码示例

**问题**: 计划假设 `bundle.signals.get("freeze_mask")` 可用，但实际 `MetricDataBundle` 数据结构（[data_types.py:265-283](file:///Users/zhangping/DEV/CLPM/backend/app/contracts/data_types.py#L265-L283)）只有 5 个字段：`metric_code`、`data_block`、`mask_expression`、`masked_indices`、`lineage`，**没有 `signals` 字典**。

```python
# 计划假设的（错误）
freeze_mask = bundle.signals.get("freeze_mask")  # ❌ signals 不存在

# 实际结构
@dataclass
class MetricDataBundle:
    metric_code: str
    data_block: DataBlock       # 数据块，signals 在这里
    mask_expression: str
    masked_indices: list[int]
    lineage: DataLineage        # 无 signals
```

**修订建议**: 信号数据在 `bundle.data_block.signals`（[data_types.py:211](file:///Users/zhangping/DEV/CLPM/backend/app/contracts/data_types.py#L211)），异常原因在 `bundle.data_block.outlier_reasons`（[data_types.py:213](file:///Users/zhangping/DEV/CLPM/backend/app/contracts/data_types.py#L213)）。仪表故障率计算器应从 `data_block.outlier_reasons` 读取，而非虚构的 `bundle.signals`。

---

### P0-2：仪表故障检测三类（冻结/突变/超限）已被现有 8 类异常值检测覆盖，新增步骤 9/10/11 属重复建设

**位置**: 计划 §4.1 "新增预处理步骤 9/10/11"

**问题**: 现有预处理流水线的 `outlier_detection.py` 已实现 8 类异常检测（[outlier_detection.py](file:///Users/zhangping/DEV/CLPM/backend/app/services/preprocessing/outlier_detection.py)），其中：

| HiaMonitor 故障类型 | 现有 OutlierReason | 现有实现函数 | 状态 |
|-------------------|-------------------|------------|------|
| 超限 | `OUT_OF_RANGE` | `detect_out_of_range()` | ✅ 已实现 |
| 冻结 | `FROZEN` | （冻结值检测器） | ✅ 已实现 |
| 突变 | `JUMP` | （跳变检测器） | ✅ 已实现 |

检测结果已存入 `DataBlock.outlier_reasons`（每点的 reason 码列表）。计划提出的"步骤 9 冻结检测、步骤 10 突变检测、步骤 11 超限检测"与既有检测器**功能完全重叠**。

**修订建议**: 删除"新增预处理步骤 9/10/11"。仪表故障率计算器直接消费 `DataBlock.outlier_reasons` 中已有的 `FROZEN`/`JUMP`/`OUT_OF_RANGE` 标记，按时间占比统计即可。真正要做的是：①确认冻结/跳变检测阈值是否配置化（见 P1-1）；②新增一个聚合计算器把既有 reason 码汇总为故障率指标。这完全符合"最小化改动"原则。

---

### P0-3：metric_config 已有 `threshold` JSONB 字段，新增 `algorithm_params` 字段冗余

**位置**: 计划 §2.2.2

**问题**: `metric_config` 表已存在 `threshold: JSONB` 字段（[metric.py:43](file:///Users/zhangping/DEV/CLPM/backend/app/models/metric.py#L43)），用途正是"阈值配置"。计划新增 `algorithm_params` JSONB 字段与 `threshold` 语义重叠，违反最小化改动原则。

**修订建议**: 复用现有 `threshold` 字段承载算法参数覆盖，不新增字段。三层配置链改为：`algorithm_parameter`（系统默认）→ `metric_config.threshold`（指标级覆盖）→ `loop_ledger` 的回路级覆盖（见 P1-2）。同时需澄清：`metric_config` 当前是"每指标一行"（`uk_metric_config_code` 唯一约束，[metric.py:58](file:///Users/zhangping/DEV/CLPM/backend/app/models/metric.py#L58)），无法承载"按控制类型"的模板，需明确"控制类型模板"如何落库。

---

## 三、架构复用遗漏清单（P1 — 强烈建议修订）

### P1-1：PID 结构模板应复用既有 `dcs_model` / `dcs_vendor` / `dcs_mode_mapping` 表，而非新建 `pid_structure_template`

**位置**: 计划 §6.1

**问题**: 代码库已存在完整的 DCS 厂商/型号体系：
- [dcs_vendor.py](file:///Users/zhangping/DEV/CLPM/backend/app/models/dcs_vendor.py) — DCS 厂商表
- [dcs_model.py](file:///Users/zhangping/DEV/CLPM/backend/app/models/dcs_model.py) — DCS 型号表
- [dcs_mode_mapping.py](file:///Users/zhangping/DEV/CLPM/backend/app/models/dcs_mode_mapping.py) — MODE 值映射表
- `LoopLedger.dcs_model_id`（[loop.py:121-126](file:///Users/zhangping/DEV/CLPM/backend/app/models/loop.py#L121-L126)）已关联 DCS 型号

计划新建 `pid_structure_template` 表（含 `dcs_model_id` FK、`mode_mapping` JSONB）与既有 `dcs_model` + `dcs_mode_mapping` **功能高度重叠**。`mode_mapping` 已由 `dcs_mode_mapping` 表承载。

**修订建议**: PID 结构参数（P 类型、I/D 单位、微分滤波等）应作为 `dcs_model` 表的扩展字段，或新增一张 `dcs_pid_structure` 子表关联 `dcs_model.id`，复用既有厂商/型号/MODE 映射体系。MODE 映射绝不应用 JSONB 重复存储。

---

### P1-2：配置三层链"控制类型模板（loop_type_weight 表扩展）"架构定位错误

**位置**: 计划 §2.2

**问题**: 计划把"控制类型模板"挂到 `loop_type_weight` 表，但：
- `control_type` 已从 `metric_config` 迁移到 `LoopLedger.control_type`（[metric.py:44 注释](file:///Users/zhangping/DEV/CLPM/backend/app/models/metric.py#L44)："MIGRATED: 已迁移至 loop_ledger.control_type"）
- `loop_type_weight` 是"回路业务类型（温度/压力/流量）权重"表，与"控制类型（STABLE/SLOW/FAST/LOGIC）算法阈值"是两个不同维度

计划混淆了 `loop_type`（业务类型）与 `control_type`（控制类型，决定算法阈值表，[data_types.py:25-36](file:///Users/zhangping/DEV/CLPM/backend/app/contracts/data_types.py#L25-L36)）。

**修订建议**: "控制类型模板"应基于 `control_type` 维度建模，而非 `loop_type_weight`。建议 `algorithm_parameter` 表增加 `control_type` 字段，使系统默认参数可按控制类型分组（对齐既有 `ControlTypeThreshold` 阈值表机制，[outlier_detection.py:30](file:///Users/zhangping/DEV/CLPM/backend/app/services/preprocessing/outlier_detection.py#L30)）。

---

### P1-3：新增 15 指标未说明三层编排层级归属与依赖注册

**位置**: 计划 §3.2

**问题**: KPI 计算采用三层编排（[kpi_calc.py:4-5,96-99](file:///Users/zhangping/DEV/CLPM/backend/app/tasks/kpi_calc.py#L4-L99)）：
- Layer1：无依赖指标
- Layer2：有依赖指标（`_LAYER2_DEPENDENCIES` 显式声明，如 `fast_rate` 依赖 `settling_time`）
- Layer3：综合评分

计划仅展示了 `_DB_TO_CALCULATOR_METRIC_CODE` 映射注册，**完全未提及**：
1. 新指标归属 Layer1 还是 Layer2？（如 `valve_linearity` 依赖 PV+OP 数据，是否需 Layer2？）
2. 是否需要扩展 `_LAYER2_DEPENDENCIES`？
3. 新增的统计指标（pv_mean 等）是否参与综合评分？还是纯展示型？

**修订建议**: 每个新指标必须标注：①所属 Layer；②depends_on 列表；③是否参与综合评分公式；④是否纳入节点聚合 `AGGREGATE_FIELDS`。

---

### P1-4：节点级聚合未处理新增指标的去留

**位置**: 计划缺失（应补充到 §3 / §5）

**问题**: 节点聚合有两套权重体系（[node_aggregation.py:13-33](file:///Users/zhangping/DEV/CLPM/backend/app/services/node_aggregation.py#L13-L33)）：
- `NodeAggregator.aggregate`：回路→节点小时聚合，按 importance_level（1:3,2:2,3:1）
- `_weighted_average` + `AGGREGATE_FIELDS`：节点小时→日→月聚合，按 loop_count

`AGGREGATE_FIELDS`（[node_aggregation.py:64-75](file:///Users/zhangping/DEV/CLPM/backend/app/services/node_aggregation.py#L64-L75)）硬编码了 10 个参与聚合的 KPI 字段。新增 15 个指标后必须决策：
- 诊断型指标（阀门线性度、振荡幅值、穿越次数）**不应**参与节点加权平均（无聚合物理意义）
- 统计型指标（PV/SP/OP 均值）**不应**参与聚合（均值再平均会失真）
- 仪表故障率**可**参与聚合（装置级故障率有意义）

计划完全未提及此决策，直接上线会导致节点看板数据错误。

**修订建议**: 明确每个新指标的"聚合策略"枚举：`AGGREGATABLE`（加入 AGGREGATE_FIELDS）/`DISPLAY_ONLY`（仅回路级展示）/`COMPOSITE_DERIVED`（由其他指标派生）。

---

### P1-5：复杂回路聚合规则未对接现有 `NodeAggregator` 与 `loop_count` 机制

**位置**: 计划 §5.3

**问题**: 计划提出"串级主副回路算两条参与计算、列表算一条"，但未说明如何落地：
- `NodeAggregator.aggregate`（[node_aggregation.py:587](file:///Users/zhangping/DEV/CLPM/backend/app/services/node_aggregation.py#L587)）接收 `loop_scores` 列表，复杂回路是否去重？
- `loop_count`（节点快照的回路计数）如何统计复杂回路？主副算 1 还是 2？
- `include_in_evaluation` 字段（[loop.py:75-80](file:///Users/zhangping/DEV/CLPM/backend/app/models/loop.py#L75-L80)）已存在"是否参与聚合"开关，复杂回路是否复用此字段？

**修订建议**: Phase 3 必须补充 RFC，明确：①复杂回路在 `NodeAggregator` 输入侧的去重规则；②`loop_count` 计数口径；③是否引入 `complex_loop_group_id` 作为聚合去重键。

---

## 四、技术细节问题清单（P2 — 建议修订）

### P2-1：kpi_snapshot_hourly 新增字段类型应与既有 Numeric(5,2) 对齐

**位置**: 计划 §3.3

**问题**: 既有所有 KPI 字段均为 `Numeric(5,2)` / `Decimal`（[metric.py:77-89](file:///Users/zhangping/DEV/CLPM/backend/app/models/metric.py#L77-L89)），计划却用 `FLOAT`。混用 Float/Decimal 会引入精度问题与 ORM 类型不一致。`setpoint_crossing_count` 用 INT 合理，但需确认是否同步加入 `KpiSnapshotCustom`（[metric.py:119-177](file:///Users/zhangping/DEV/CLPM/backend/app/models/metric.py#L119-L177)）以保持两张快照表一致（项目记忆教训：两张表字段需对齐）。

**修订建议**: 新增 KPI 字段统一 `Numeric(5,2)`（百分比类）或 `Numeric(8,2)`（时间类），同步加到 `kpi_snapshot_hourly` 与 `kpi_snapshot_custom`。

---

### P2-2：可信度阈值配置化需协调 `ConfidenceLevel` 枚举与 `MIN_GOOD_RATIO`

**位置**: 计划 §2.1（`DEFAULT_CONFIDENCE_THRESHOLDS`）

**问题**: 可信度阈值 A:0.95/B:0.80/C:0.60/D:0.20 是 `ConfidenceLevel` 枚举注释（[data_types.py:83-87](file:///Users/zhangping/DEV/CLPM/backend/app/contracts/data_types.py#L83-L87)）的硬编码，且 E 级 `<0.20` 与 `kpi_calc.py` 的 `MIN_GOOD_RATIO = 0.20`（[kpi_calc.py:61](file:///Users/zhangping/DEV/CLPM/backend/app/tasks/kpi_calc.py#L61)）耦合。配置化后若用户改 D 级阈值而未改 `MIN_GOOD_RATIO`，INCONCLUSIVE 判定会与可信度等级矛盾。

**修订建议**: 可信度阈值配置化应作为"高级选项"延后，或必须同时修改 `MIN_GOOD_RATIO` 与 `ConfidenceLevel` 判定逻辑，并在配置 UI 加一致性校验。

---

### P2-3：抗扰性分析伪代码引用了不存在的方法

**位置**: 计划 §4.2

**问题**: `self._detect_sp_changes(bundle)`、`self._is_anti_disturbance_enabled(bundle)` 等方法在现有 `FastRateCalculator` 中不存在，且 `FastRateCalculator` 的实际依赖是 `["settling_time", "ideal_settling_time"]`（[kpi_calc.py:98](file:///Users/zhangping/DEV/CLPM/backend/app/tasks/kpi_calc.py#L98)）。抗扰性分析需新增扰动段检测，会改变 fast_rate 的依赖与计算路径，需评估对 Layer2 编排的影响。

**修订建议**: 抗扰性分析应作为 fast_rate 的可选分支，通过 `algorithm_parameter` 开关控制启用，关闭时走原逻辑（零回归风险）。

---

### P2-4：Phase 3 复杂回路可能超出 Phase 1 MVP 范围

**位置**: 计划 §5 vs PRD

**问题**: AGENTS.md 记载"首版主线 Phase 1 (MVP/V1.0)：跑通自动评估、自动诊断、轻量跟踪闭环"，回路整定 Phase 2 才完成闭环。复杂回路（串级/超驰/NooM）属高阶能力，4 周工期在 MVP 阶段投入需论证优先级。

**修订建议**: 复杂回路建议明确归入 Phase 2+，Phase 1 先完成 P0 配置化 + 仪表故障率 + 统计指标（高 ROI、低风险）。

---

## 五、借鉴适用性与本地化建议

### 值得借鉴（计划已正确识别）

1. **仪表故障率指标**：HiaMonitor 的故障率/故障状态是预防性维护核心，CLPM 缺失——但实现应复用既有 outlier_detection（见 P0-2）
2. **PV/SP/OP 统计指标**：基础统计是分析能力基石，新增成本低、价值高
3. **设定点穿越次数 + 振荡幅值**：直观反映稳定性，与现有振荡指数互补
4. **相似度容忍带宽可配置**：HiaMonitor 的产品化细节，提升振荡判定灵活性
5. **综合评估页面雷达图 + PV/OP 散点图**：信息密度高，符合用户"最大化数据墨水比"偏好

### 需本地化调整

1. **综合评分公式**：HiaMonitor 用 `(100-饱和率)` 线性惩罚，CLPM 用 `R` 折扣因子（国标对齐）。**保持 CLPM 现状**，仅把"饱和率惩罚"作为可选项，不照搬 HiaMonitor 公式（项目记忆硬约束：必须用 `P=(A·a+F·f+S·s)/(a+f+s)×R`）
2. **稳定率 CDF 算法**：HiaMonitor 正态分布 CDF 更严谨，但 CLPM 现有指数衰减已对齐国标。建议作为"可选算法"而非替换
3. **复杂回路类型**：HiaMonitor 的 NooM/超驰/选择串级在化工场景常见，但 CLPM 当前 AAS 数据模型是"回路关联 7 个 tag"，复杂回路需重新设计 tag 关联模型，不宜简单加字段

---

## 六、改进建议与优先级排序

### 修订后的实施优先级

| 优先级 | 内容 | 理由 | 工期修正 |
|-------|------|------|---------|
| **P0-A** | 修订计划技术错误（P0-1/2/3）+ 架构复用（P1-1/2） | 否则无法实施 | 0.5 周（文档修订） |
| **P0-B** | 配置化基础设施（复用 metric_config.threshold + algorithm_parameter 表） | 后续指标依赖 | 2 周（维持） |
| **P1** | 仪表故障率（复用 outlier_reasons）+ 统计指标 + 振荡幅值/穿越次数 | 高 ROI、低风险、复用既有检测 | 2 周（原 3 周下调，因复用） |
| **P2** | 抗扰性分析（fast_rate 可选分支）+ 相似度带宽配置 | 增强型，开关控制 | 2 周 |
| **P3** | 前端综合评估页（雷达图/散点图/统计柱状图） | 依赖 P1 指标落地 | 2 周 |
| **P4** | 复杂回路 RFC + 实施（单独 Phase，需先评审） | 架构级变更，超 MVP 范围 | 4 周 + RFC 1 周 |
| **P5** | PID 结构模板（扩展 dcs_model，非新建表） | 依赖 DCS 厂商体系 | 2 周（原 3 周下调） |

### 关键修订动作（按优先级）

1. **删除** §4.1 的"新增预处理步骤 9/10/11"，改为"复用 DataBlock.outlier_reasons"
2. **删除** §2.2.2 的 `algorithm_params` 新字段，改为复用 `metric_config.threshold`
3. **删除** §6.1 的 `pid_structure_template` 表，改为扩展 `dcs_model`
4. **补充** §3.2 每个新指标的 Layer 归属、depends_on、聚合策略
5. **补充** §5.3 复杂回路在 `NodeAggregator` 与 `loop_count` 的去重规则
6. **修正** §4.1/§4.2 伪代码：`bundle.signals` → `bundle.data_block.outlier_reasons`/`.signals`
7. **修正** §3.3 字段类型：FLOAT → Numeric，同步两张快照表
8. **澄清** §2.2 配置链：基于 `control_type` 而非 `loop_type_weight`

---

## 七、风险评估补充

| 风险（计划未识别） | 影响 | 应对 |
|------------------|------|-----|
| 新指标混入 `AGGREGATE_FIELDS` 导致节点看板失真 | 高 | P1-4 聚合策略枚举 |
| 可信度阈值配置化破坏 INCONCLUSIVE 一致性 | 中 | P2-2 联动校验 |
| 两张快照表（hourly/custom）字段不同步 | 中 | P2-1 同步迁移 |
| 复杂回路聚合与既有 importance_level 权重冲突 | 高 | P1-5 RFC 先行 |
| 抗扰性分析改变 fast_rate 依赖链影响 Layer2 编排 | 中 | P2-3 开关控制 |

---

## 八、结论

重构计划的**战略方向正确**（增量式、配置化、补齐指标差距），但**战术落地需大幅修订**。核心矛盾在于：计划对现有架构的复用深度不足——多处"新增"（故障检测、PID 模板、配置字段）实际已有实现，而真正需要扩展的节点聚合、三层编排却未触及。

**建议**：先按 P0-A 修订计划文档（0.5 周），消除技术错误与复用遗漏，再启动实施。修订后 P1（故障率+统计指标，2 周）可立即获得高 ROI 且零回归风险，是最佳首发切入点。复杂回路（P4）作为独立 Phase，需 RFC 评审后再推进。
