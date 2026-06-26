# CLPM 数据模型设计说明书 (DDS)

**文档状态**: 正式版
**当前版本**: v4.0
**发布日期**: 2026-06-26
**设计依据**: PRD (v3.0), FDS (v3.0), ADS (v3.1), 关键算法设计说明 (v2.0)

---

## 0. 文档变更记录

| 版本 | 日期 | 变更说明 | 作者 |
|---|---|---|---|
| v3.0 | 2026-06-20 | 产品化架构重构版：存算分离、回路-Tag 解耦、配置驱动、PV 质量码处理、新增诊断结果表与整定记录表。 | 数据架构组 |
| v3.1 | 2026-06-22 | 对齐《关键算法设计说明》v1.0：①`metric_config.threshold` 类型 DECIMAL → JSONB，新增 `control_type` 字段；②`kpi_snapshot_hourly` 新增 `accuracy_rate`、`saturation_rate` 字段；③`diagnosis_config` 新增 `calc_method` 字段，`threshold` 类型 DECIMAL → JSONB；④`tuning_record` 新增 `fitting_score` 字段；⑤新增"算法结果存储设计"章节；⑥新增"算法版本字段"说明；⑦ER 图更新说明（新增字段不影响现有关系结构）。 | 数据架构组 |
| v4.0 | 2026-06-26 | 对齐《关键算法设计说明》v2.0：①`kpi_snapshot_hourly` 扩展 `fast_rate`/`effective_auto_rate`/`stiction_index`/`output_trip_index`/`settling_time`/`ideal_settling_time` 等指标字段及 `sampling_freq`/`quality_policy`/`valid_rate`/`confidence_level`/`data_lineage` 数据血缘字段；②新增 `kpi_snapshot_custom` 自定义任务快照表；③新增 `clpm_metric_data_requirement` 指标数据需求契约表；④新增 `diagnosis_tag` 诊断标签表；⑤新增 `unit_kpi_summary` 装置级汇总表；⑥§4.1 PV 质量码过滤策略升级为 `KEEP_ALL_WITH_VALIDITY`，引入 Metric Validity Mask 与 A/B/C/D/E 五级可信度；⑦§5.1 KPI 结果存储新增数据血缘字段说明，区分标准任务与自定义任务存储。 | 数据架构组 |

---

## 1. 设计原则

遵循 ADS (v3.1) 规定的"存算分离"原则，系统数据模型严格拆分为两大独立域：

1. **关系型业务域 (PostgreSQL)**：承载工厂拓扑模型、AAS Tag 注册表、回路台账、回路-Tag 关联、性能/诊断/引擎等可配置元数据、算法快照结果、整定记录、报表记录及轻量级状态追踪记录。要求强一致性 (ACID)。
2. **高频时序域 (TDengine)**：承载原始海量秒级运行数据（PV/SP/OP/MODE/PID_P/PID_I/PID_D 及 PV 质量码）。要求极高写入吞吐与降采样查询性能。

### 1.1 产品化配置原则

为支撑 PRD v3.0 确立的"产品化、工具化、模块内聚自包含、配置驱动"四大设计原则，本 DDS 在数据模型层面落实以下产品化配置原则：

| 配置原则 | 数据模型落地说明 |
|---|---|
| **配置驱动** | 性能指标 (`metric_config`)、诊断指标 (`diagnosis_config`)、引擎规则 (`engine_rule`) 均独立为可配置表，支持用户自助编辑公式/阈值/权重/启停，无需开发介入。 |
| **配置即时生效** | 配置表变更通过版本号 (`version`) 字段记录，配合审计日志 (`sys_audit_log`) 实现变更追溯与回滚，无需重启服务。 |
| **配置审计留痕** | 所有配置表均含 `updated_by` / `updated_at` 字段，变更记录写入 `sys_audit_log`，不可物理删除。 |
| **实体内聚** | 回路作为核心实体，其配置态（`loop_ledger` 台账 + `loop_tag_mapping` Tag 关联）与运行态（`kpi_snapshot_hourly` 快照）归属同一逻辑域，便于回路管理模块自包含管理。 |
| **AAS Tag 模型** | AAS 同步对象为 **Tag 位号**（非回路实体），回路由用户在 CLPM 系统中创建并关联 7 个 OPC Tag（PV/SP/OP/MODE/PID_P/PID_I/PID_D）。Tag 注册表 (`tag_registry`) 与回路解耦，支持多对多关联。 |

---

## 2. 关系型业务模型 (PostgreSQL)

### 2.1 工厂拓扑 (plant_node)

**表名: `plant_node` (工厂节点)**

承载工厂 → 装置 → 单元的多级层级树，v3.0 保持不变。

| 字段 | 类型 | 说明 | 约束 |
|---|---|---|---|
| id | UUID | 节点主键 | PK |
| name | VARCHAR(100) | 节点名称 (如: 常减压装置) | NOT NULL |
| type | VARCHAR(20) | 节点类型: `FACTORY`, `UNIT`, `EQUIPMENT` | NOT NULL |
| parent_id | UUID | 父节点 ID | FK -> plant_node.id |

### 2.2 回路台账 (loop_ledger)

**表名: `loop_ledger` (回路台账)**

回路作为系统核心实体，由用户在 CLPM 系统中创建并关联 Tag。v3.0 移除原 `mapping_pv/sp/op/mode` 字段（迁移至 `loop_tag_mapping` 表），新增描述、评分权重、AAS 同步时间、回路状态等扩展字段。

| 字段 | 类型 | 说明 | 约束 |
|---|---|---|---|
| id | UUID | 回路主键 | PK |
| tag_name | VARCHAR(100) | 唯一位号标识 (如: 101-FC-1023) | UNIQUE, NOT NULL |
| description | VARCHAR(255) | 回路描述 (如: 常顶塔顶温度调节回路) | |
| unit_id | UUID | 所属工艺单元 ID | FK -> plant_node.id |
| score_weight | DECIMAL(5,2) | 评分权重 (用于装置/单元级聚合时的加权计算) | |
| is_active | BOOLEAN | 是否启用全量评估计算 | DEFAULT TRUE |
| last_aas_sync_at | TIMESTAMP | 最后 AAS 同步时间 | |
| status | VARCHAR(20) | 回路状态: `READY`(就绪), `PARTIAL`(部分就绪), `INACTIVE`(未启用) | NOT NULL, DEFAULT 'PARTIAL' |

**状态语义**：
* `READY`：PV/SP/OP/MODE 四个必填 Tag 全部关联成功，回路进入评估流程。
* `PARTIAL`：必填 Tag 缺失，回路标红提示，不参与评估计算。
* `INACTIVE`：`is_active=FALSE`，回路被手动停用，不参与评估计算。

### 2.3 AAS Tag 注册表 (tag_registry)

**表名: `tag_registry` (AAS Tag 注册表)** [v3.0 新增]

AAS Integration Service 定期从 AAS 同步所有 OPC Tag 位号信息，写入本表。同步对象为 Tag 位号（非回路实体），与回路解耦。

| 字段 | 类型 | 说明 | 约束 |
|---|---|---|---|
| id | UUID | Tag 主键 | PK |
| tag_name | VARCHAR(100) | Tag 位号名 (OPC Item ID) | UNIQUE, NOT NULL |
| tag_description | VARCHAR(255) | Tag 描述 (来自 AAS) | |
| tag_type | VARCHAR(20) | Tag 类型: `PV`, `SP`, `OP`, `MODE`, `PID_P`, `PID_I`, `PID_D`, `OTHER` | NOT NULL |
| current_value | FLOAT | 当前值 (最近一次同步快照) | |
| quality | VARCHAR(20) | 数据质量码: `GOOD`, `BAD`, `UNCERTAIN` | |
| last_sync_at | TIMESTAMP | 最后同步时间 | NOT NULL |
| is_linked | BOOLEAN | 是否已关联到回路 | DEFAULT FALSE |

### 2.4 回路-Tag 关联 (loop_tag_mapping)

**表名: `loop_tag_mapping` (回路-Tag 关联)** [v3.0 新增]

记录回路与 7 个 OPC Tag 的关联关系。一个典型控制回路关联 7 个 Tag：PV/SP/OP/MODE/PID_P/PID_I/PID_D。

| 字段 | 类型 | 说明 | 约束 |
|---|---|---|---|
| id | UUID | 关联主键 | PK |
| loop_id | UUID | 关联回路 ID | FK -> loop_ledger.id, NOT NULL |
| tag_id | UUID | 关联 Tag ID | FK -> tag_registry.id, NOT NULL |
| tag_role | VARCHAR(20) | Tag 角色: `PV`, `SP`, `OP`, `MODE`, `PID_P`, `PID_I`, `PID_D` | NOT NULL |
| is_required | BOOLEAN | 是否必填 Tag (PV/SP/OP/MODE 为 TRUE，PID_* 为 FALSE) | NOT NULL |
| created_at | TIMESTAMP | 关联创建时间 | NOT NULL |

**唯一约束**: `(loop_id, tag_role)` —— 同一回路同一角色仅能关联一个 Tag。

### 2.5 性能指标配置 (metric_config)

**表名: `metric_config` (性能指标配置)** [v3.0 新增，v3.1 更新]

承载 6 大核心 KPI（好值率、自控率、平稳率、准确率、振荡率、饱和率）及变体指标的可配置元数据。权重总和约束 100%。

> **v3.1 变更**（对齐《关键算法设计说明》§10.1）：
> 1. `threshold` 字段类型由 `DECIMAL(5,2)` 变更为 `JSONB`，结构为 `{"min": number, "max": number, "alert": string}`，支持区间阈值与告警级别。
> 2. 新增 `control_type` 字段（VARCHAR(20)，默认 `STABLE`），用于权重模板选择，枚举值：`STABLE`/`SLOW`/`FAST`/`LOGIC`。

| 字段 | 类型 | 说明 | 约束 |
|---|---|---|---|
| id | UUID | 指标主键 | PK |
| metric_code | VARCHAR(50) | 指标代码: `GOOD_VALUE_RATE`, `AUTO_MODE_RATE`, `STEADY_RATE`, `ACCURACY_RATE`, `OSCILLATION_RATE`, `SATURATION_RATE` 等 | UNIQUE, NOT NULL |
| metric_name | VARCHAR(100) | 指标名称 (如: 好值率) | NOT NULL |
| formula | TEXT | 计算公式 (支持用户自定义表达式，采用 simpleeval 安全沙箱求值) | |
| weight | DECIMAL(5,2) | 权重 (总和须为 100%) | |
| threshold | JSONB | 阈值对象 `{"min": number, "max": number, "alert": string}`，用于触发诊断与告警 | |
| control_type | VARCHAR(20) | 控制类型: `STABLE`(稳定型), `SLOW`(慢速型), `FAST`(快速型), `LOGIC`(逻辑型)，用于权重模板选择 | DEFAULT 'STABLE' |
| is_enabled | BOOLEAN | 是否启用 | DEFAULT TRUE |
| updated_by | VARCHAR(50) | 最后更新人 | |
| updated_at | TIMESTAMP | 最后更新时间 | |
| version | INT | 配置版本号 (用于变更追溯与回滚) | DEFAULT 1 |

**threshold 字段结构说明**：

```json
{
  "min": 0.0,
  "max": 100.0,
  "alert": "WARNING"
}
```

* `min`：阈值下限（数值型）
* `max`：阈值上限（数值型）
* `alert`：告警级别字符串（如 `INFO`/`WARNING`/`CRITICAL`）

**control_type 枚举值说明**（对齐《关键算法设计说明》§4.7.3 默认权重配置）：

| control_type | 说明 | 适用场景 |
|---|---|---|
| `STABLE` | 稳定型 | 温度、压力控制 |
| `SLOW` | 慢速型 | 缓慢调节回路 |
| `FAST` | 快速型 | 副回路、流量控制 |
| `LOGIC` | 逻辑型 | 防回流、防超温 |

### 2.6 诊断指标配置 (diagnosis_config)

**表名: `diagnosis_config` (诊断指标配置)** [v3.0 新增，v3.1 更新]

承载诊断指标（振荡检测 FFT、粘滞检测散点拟合、参数过激检测、质量码规则等）的可配置元数据。

> **v3.1 变更**（对齐《关键算法设计说明》§10.3）：
> 1. 新增 `calc_method` 字段（VARCHAR(50)），标识诊断算法的具体计算方法。
> 2. `threshold` 字段类型由 `DECIMAL(5,2)` 变更为 `JSONB`，结构因算法而异，支持多阈值对象。

| 字段 | 类型 | 说明 | 约束 |
|---|---|---|---|
| id | UUID | 诊断指标主键 | PK |
| diag_code | VARCHAR(50) | 诊断代码: `OSCILLATION_FFT`, `STICTION_SCATTER`, `OVERAGGRESSIVE`, `QUALITY_CODE` 等 | UNIQUE, NOT NULL |
| diag_name | VARCHAR(100) | 诊断指标名称 (如: 振荡检测-FFT) | NOT NULL |
| algorithm_type | VARCHAR(50) | 算法类型 (如: FFT, SCATTER_FIT, THRESHOLD) | NOT NULL |
| calc_method | VARCHAR(50) | 计算方法枚举（见下方说明） | |
| params | JSON | 算法参数 (如: FFT 窗口长度、散点拟合阶数) | |
| threshold | JSONB | 诊断阈值对象（JSON，结构因算法而异，如 `{"similarity_threshold": 0.4}`） | |
| is_enabled | BOOLEAN | 是否启用 | DEFAULT TRUE |
| updated_by | VARCHAR(50) | 最后更新人 | |
| updated_at | TIMESTAMP | 最后更新时间 | |
| version | INT | 配置版本号 | DEFAULT 1 |

**calc_method 枚举值说明**（对齐《关键算法设计说明》§4/§5 算法实现）：

| calc_method | 说明 | 对应诊断标签 |
|---|---|---|
| `IAE_ZERO_CROSSING` | IAE 零交叉相似率法（Hägglund） | OSCILLATION |
| `FFT_WELCH` | FFT 频域法（Welch 法 PSD） | OSCILLATION |
| `CHOUDHURY_NGI_NLI` | Choudhury NGI/NLI + 椭圆拟合 | VALVE_STICTION |
| `KANO_STATISTICAL` | Kano 统计特性法 | VALVE_STICTION |
| `EXPERT_RULE` | 专家规则矩阵（多算法融合） | MANUAL_REVIEW |
| `STEP_RESPONSE_ANALYSIS` | 阶跃响应分析（超调量/衰减比） | OVERAGGRESSIVE |
| `SETTLING_TIME_ANALYSIS` | 收敛时间与 IAE 累积分析 | OVERCONSERVATIVE |
| `DISTURBANCE_FREQUENCY` | 偏差突变频率统计 | EXTERNAL_DISTURBANCE |
| `QUALITY_CODE_RULE` | PV 质量码规则诊断 | QUALITY_ABNORMAL |
| `OP_SATURATION_STAT` | OP 限位统计 | OUTPUT_SATURATION |

### 2.7 引擎规则配置 (engine_rule)

**表名: `engine_rule` (引擎规则配置)** [v3.0 新增]

承载评估引擎/诊断引擎的计算周期、数据拉取规则、调度参数等可配置规则。

| 字段 | 类型 | 说明 | 约束 |
|---|---|---|---|
| id | UUID | 规则主键 | PK |
| rule_code | VARCHAR(50) | 规则代码 (如: `EVAL_CALC_CYCLE`, `DATA_FETCH_WINDOW`, `SCHEDULE_CONCURRENCY`) | UNIQUE, NOT NULL |
| rule_name | VARCHAR(100) | 规则名称 (如: 评估计算周期) | NOT NULL |
| rule_type | VARCHAR(20) | 规则类型: `CALC_CYCLE`(计算周期), `DATA_FETCH`(数据拉取), `SCHEDULE`(调度参数) | NOT NULL |
| params | JSON | 规则参数 (如: `{"cycle_minutes": 60, "concurrency": 16}`) | |
| is_enabled | BOOLEAN | 是否启用 | DEFAULT TRUE |
| updated_by | VARCHAR(50) | 最后更新人 | |
| updated_at | TIMESTAMP | 最后更新时间 | |

### 2.8 评估快照 (kpi_snapshot_hourly)

**表名: `kpi_snapshot_hourly` (每小时性能评估快照)** [v3.1 更新，v4.0 扩展]

v3.0 保持表结构不变，但明确**好值率 (`good_value_rate`) 基于 PV 质量码 (`pv_quality`) 统计**：PV 质量码为 `Good` 的时段计入好值，`Bad` / `Uncertain` 时段不计入。

> **v3.1 变更**（对齐《关键算法设计说明》§10.2）：
> 补全 6 大 KPI 字段，新增 `accuracy_rate`（准确率）与 `saturation_rate`（饱和率）字段，使快照表完整覆盖 6 大 KPI（好值率/自控率/平稳率/准确率/振荡率/饱和率）。

> **v4.0 变更**（对齐《关键算法设计说明》v2.0）：
> 1. 新增扩展指标字段：`fast_rate`（快速率）、`effective_auto_rate`（有效自控率）、`stiction_index`（粘滞系数）、`output_trip_index`（输出值行程指数）、`settling_time`（实际稳态时间）、`ideal_settling_time`（理想稳态时间）。
> 2. 新增数据血缘字段：`sampling_freq`（数据采样频率）、`quality_policy`（质量策略）、`valid_rate`（有效数据率）、`confidence_level`（指标可信度等级）、`data_lineage`（数据血缘 JSON），支撑指标可追溯与可信度评估。

| 字段 | 类型 | 说明 | 约束 |
|---|---|---|---|
| id | UUID | 快照主键 | PK |
| loop_id | UUID | 关联回路 ID | FK -> loop_ledger.id |
| ts_start | TIMESTAMP | 评估窗口起始时间 | NOT NULL |
| ts_end | TIMESTAMP | 评估窗口结束时间 | NOT NULL |
| score | DECIMAL(5,2) | 综合评分 (0-100) | |
| good_value_rate | DECIMAL(5,2) | 好值率 (%)，基于 PV 质量码统计 | |
| auto_mode_rate | DECIMAL(5,2) | 自控率 (%) | |
| steady_rate | DECIMAL(5,2) | 平稳率 (%) | |
| accuracy_rate | DECIMAL(5,2) | 准确率 (%)，衡量 PV 达到 SP 的准确程度 | |
| oscillation_rate | DECIMAL(5,2) | 振荡率 (%) | |
| saturation_rate | DECIMAL(5,2) | 饱和率 (%)，统计 OP 处于限位的时长占比 | |
| fast_rate | DECIMAL(5,2) | 快速率 (%)，反映回路响应速度是否过快 [v4.0 新增] | |
| effective_auto_rate | DECIMAL(5,2) | 有效自控率 (%)，扣除 PV 质量异常后的自控率 [v4.0 新增] | |
| stiction_index | DECIMAL(5,2) | 粘滞系数 (%)，阀门粘滞量化指标 [v4.0 新增] | |
| output_trip_index | DECIMAL(5,2) | 输出值行程指数，反映 OP 行程范围特征 [v4.0 新增] | |
| settling_time | DECIMAL(8,2) | 实际稳态时间（秒），扰动后达到稳态所需时长 [v4.0 新增] | |
| ideal_settling_time | DECIMAL(8,2) | 理想稳态时间（秒），按对象特性计算的期望稳态时间 [v4.0 新增] | |
| algorithm_version | VARCHAR(50) | 算法版本号（如 `KPI_CALC_v1.0`），用于结果追溯 | |
| status | VARCHAR(20) | 计算状态: `SUCCESS`, `INCONCLUSIVE`, `PARTIAL` | NOT NULL |
| sampling_freq | VARCHAR(10) | 数据采样频率（如 `1s`/`5s`），记录本次快照输入数据的实际采样间隔 [v4.0 新增] | |
| quality_policy | VARCHAR(30) | 质量策略（如 `KEEP_ALL_WITH_VALIDITY`），记录本次计算采用的质量码处理策略 [v4.0 新增] | |
| valid_rate | DECIMAL(5,4) | 有效数据率（0~1），评估窗口内满足各指标 Metric Validity Mask 的数据占比 [v4.0 新增] | |
| confidence_level | CHAR(1) | 指标可信度等级（`A`/`B`/`C`/`D`/`E`），由 `valid_rate` 与样本量综合判定 [v4.0 新增] | |
| data_lineage | JSONB | 数据血缘 JSON，结构含 `tag_group`/`data_block_ids`/`aggregation_policy` 等，支撑指标可追溯 [v4.0 新增] | |

**6 大 KPI 字段说明**（对齐《关键算法设计说明》§4.0 指标体系总览）：

| KPI 字段 | metric_code | 国标对应 | 算法类型 |
|---|---|---|---|
| `good_value_rate` | `GOOD_VALUE_RATE` | 附录 F.6 (Qu) | 质量码统计 |
| `auto_mode_rate` | `AUTO_MODE_RATE` | 附录 B.1 (Auto) | 模式统计 |
| `steady_rate` | `STEADY_RATE` | 附录 B.5 (S) | 偏差标准差 |
| `accuracy_rate` | `ACCURACY_RATE` | 附录 B.3 (A) | 偏差绝对均值 |
| `oscillation_rate` | `OSCILLATION_RATE` | 附录 F.1 (Osc) | IAE 零交叉 |
| `saturation_rate` | `SATURATION_RATE` | 附录 F.3 (Sa) | 输出限位统计 |

### 2.9 异常追踪 (action_tracker)

**表名: `action_tracker` (轻量级异常追踪记录)**

v3.0 中 Action Tracker 降级为诊断中心子模块，表结构保持不变。

| 字段 | 类型 | 说明 | 约束 |
|---|---|---|---|
| id | UUID | 追踪记录主键 | PK |
| loop_id | UUID | 关联回路 ID | FK -> loop_ledger.id |
| diagnosis_label | VARCHAR(100) | 自动预诊结论 (如: 疑似阀门粘滞) | |
| action_status | VARCHAR(20) | 处理状态: `PENDING`(待处理), `IN_PROGRESS`(处理中), `IGNORED`(已忽略), `RESOLVED`(已实施) | NOT NULL, DEFAULT 'PENDING' |
| evidence_url | VARCHAR(255) | 导出的《诊断建议书》PDF S3 存储路径 | |
| updated_by | VARCHAR(50) | 最后操作人 (仪控工程师) | |
| updated_at | TIMESTAMP | 状态变更时间戳 | |

### 2.10 诊断结果 (diagnosis_result)

**表名: `diagnosis_result` (诊断结果表)** [v3.0 新增]

承载诊断引擎对回路的自动预诊结果，包括预诊标签、置信度、特征值、证据链引用及算法版本号，为诊断中心与异常跟踪子模块提供数据支撑。

| 字段 | 类型 | 说明 | 约束 |
|---|---|---|---|
| id | UUID | 诊断结果主键 | PK |
| loop_id | UUID | 关联回路 ID | FK -> loop_ledger.id |
| diag_label | VARCHAR(100) | 预诊标签（如：疑似阀门粘滞、参数过激、原因不明需人工介入） | |
| confidence | DECIMAL(5,2) | 置信度（0-100） | |
| feature_values | JSON | 特征值（FFT 主频、散点拟合参数等） | |
| evidence_chain | JSON | 证据链引用（波形时间段、散点图数据引用等） | |
| algorithm_version | VARCHAR(50) | 算法版本号 | |
| diagnosed_at | TIMESTAMP | 诊断时间 | NOT NULL |

### 2.11 整定记录 (tuning_record)

**表名: `tuning_record` (整定记录)** [v3.0 新增，v3.1 更新，Phase 2]

承载回路整定任务记录，包括模型辨识参数、推荐 PID 参数、仿真结果与效果对比。Phase 1 仅建表，Phase 2 实现算法。

> **v3.1 变更**（对齐《关键算法设计说明》§10.4）：
> 新增 `fitting_score` 字段（DECIMAL(5,2)），记录模型拟合度评分（0-100），用于评估模型辨识质量。拟合度 R² > 0.9 为优秀拟合。

| 字段 | 类型 | 说明 | 约束 |
|---|---|---|---|
| id | UUID | 整定记录主键 | PK |
| loop_id | UUID | 关联回路 ID | FK -> loop_ledger.id, NOT NULL |
| model_type | VARCHAR(20) | 模型类型: `FOPDT`(一阶惯性加纯滞后), `SOPDT`(二阶惯性加纯滞后), `IPDT`(积分加纯滞后) | NOT NULL |
| model_params | JSON | 模型参数 (如: `{"K": 1.2, "T": 30.5, "tau": 5.0}`) | |
| fitting_score | DECIMAL(5,2) | 模型拟合度评分 (0-100)，即 R² × 100，用于评估模型辨识质量 | |
| algorithm | VARCHAR(50) | 整定算法: `IMC`, `LAMBDA`, `ZN`, `COHEN_COON`, `SIMC` | NOT NULL |
| recommended_pid | JSON | 推荐 PID 参数 (如: `{"P": 1.5, "I": 0.8, "D": 0.2}`) | |
| simulation_result | JSON | 闭环仿真结果 (含阶跃响应曲线、性能指标对比) | |
| algorithm_version | VARCHAR(50) | 算法版本号（如 `FOPDT_ID_v1.0`），用于结果追溯 | |
| status | VARCHAR(20) | 整定状态: `PENDING`, `IDENTIFIED`, `SIMULATED`, `APPLIED`, `VERIFIED` | NOT NULL |
| created_by | VARCHAR(50) | 创建人 | |
| created_at | TIMESTAMP | 创建时间 | NOT NULL |

**fitting_score 字段说明**：

* 取值范围：0~100，对应模型拟合度 R² × 100
* 计算公式：$R^2 = 1 - \frac{\sum_{i=1}^{n} (y_i - \hat{y}_i)^2}{\sum_{i=1}^{n} (y_i - \bar{y})^2}$（对齐《关键算法设计说明》§6.1.5）
* 评分标准：
  * `fitting_score >= 90`：优秀拟合，模型可信
  * `70 <= fitting_score < 90`：良好拟合，模型可用
  * `fitting_score < 70`：拟合度不足，建议升级模型（如 FOPDT → SOPDT）或检查数据质量

### 2.12 报表记录 (report_record)

**表名: `report_record` (自动报表记录)** [v3.0 新增]

承载系统按班/日/周/月自动生成的《控制回路性能评估报告》归档记录。

| 字段 | 类型 | 说明 | 约束 |
|---|---|---|---|
| id | UUID | 报表记录主键 | PK |
| report_period | VARCHAR(20) | 报表周期: `SHIFT`(班), `DAILY`(日), `WEEKLY`(周), `MONTHLY`(月) | NOT NULL |
| generated_at | TIMESTAMP | 生成时间 | NOT NULL |
| status | VARCHAR(20) | 生成状态: `PROCESSING`(生成中), `COMPLETED`(成功), `FAILED`(失败) | NOT NULL |
| file_url | VARCHAR(255) | 报表文件存储路径 (S3/MinIO) | |
| created_at | TIMESTAMP | 记录创建时间 | NOT NULL |

### 2.13 审计日志 (sys_audit_log)

**表名: `sys_audit_log` (系统审计日志)**

v3.0 保持不变。所有配置变更（性能指标/诊断指标/引擎规则/角色分配等）均落入本表，不可物理删除。

| 字段 | 类型 | 说明 | 约束 |
|---|---|---|---|
| id | UUID | 日志主键 | PK |
| operator | VARCHAR(50) | 操作人 | NOT NULL |
| operation_type | VARCHAR(50) | 操作类型 (如: `METRIC_CONFIG_UPDATE`, `ROLE_ASSIGN`, `LOOP_CREATE`) | NOT NULL |
| target_type | VARCHAR(50) | 操作对象类型 (如: `loop_ledger`, `metric_config`) | |
| target_id | VARCHAR(36) | 操作对象 ID | |
| before_value | TEXT | 变更前值 (JSON 序列化) | |
| after_value | TEXT | 变更后值 (JSON 序列化) | |
| operated_at | TIMESTAMP | 操作时间 | NOT NULL |

### 2.14 自定义任务快照 (kpi_snapshot_custom)

**表名: `kpi_snapshot_custom` (自定义评估任务快照)** [v4.0 新增]

承载用户自定义评估任务的 KPI 快照结果。自定义任务由用户按需触发（如指定时间窗、指定回路集合），快照结构与 `kpi_snapshot_hourly` 对齐，但通过 `task_id` 区分独立任务，且**不参与装置级聚合**（装置级汇总仅基于 `kpi_snapshot_hourly`）。

| 字段 | 类型 | 说明 | 约束 |
|---|---|---|---|
| id | UUID | 快照主键 | PK, DEFAULT gen_random_uuid() |
| task_id | UUID | 自定义任务 ID | NOT NULL |
| loop_id | UUID | 关联回路 ID | FK -> loop_ledger.id, NOT NULL |
| ts_start | TIMESTAMP | 评估窗口起始时间 | NOT NULL |
| ts_end | TIMESTAMP | 评估窗口结束时间 | NOT NULL |
| score | DECIMAL(5,2) | 综合评分 (0-100) | |
| accuracy_rate | DECIMAL(5,2) | 准确率 (%) | |
| fast_rate | DECIMAL(5,2) | 快速率 (%) | |
| stability_rate | DECIMAL(5,2) | 平稳率 (%) | |
| effective_auto_rate | DECIMAL(5,2) | 有效自控率 (%) | |
| good_value_rate | DECIMAL(5,2) | 好值率 (%) | |
| oscillation_rate | DECIMAL(5,2) | 振荡率 (%) | |
| saturation_rate | DECIMAL(5,2) | 饱和率 (%) | |
| stiction_index | DECIMAL(5,2) | 粘滞系数 (%) | |
| output_trip_index | DECIMAL(5,2) | 输出值行程指数 | |
| settling_time | DECIMAL(8,2) | 实际稳态时间（秒） | |
| ideal_settling_time | DECIMAL(8,2) | 理想稳态时间（秒） | |
| auto_mode_rate | DECIMAL(5,2) | 自控率 (%) | |
| algorithm_version | VARCHAR(50) | 算法版本号 | |
| status | VARCHAR(20) | 计算状态: `SUCCESS`, `INCONCLUSIVE`, `PARTIAL` | |
| confidence_level | CHAR(1) | 指标可信度等级（`A`/`B`/`C`/`D`/`E`） | |
| valid_rate | DECIMAL(5,4) | 有效数据率（0~1） | |
| data_lineage | JSONB | 数据血缘 JSON | |
| created_at | TIMESTAMP | 记录创建时间 | DEFAULT NOW() |

**唯一约束**: `(task_id, loop_id)` —— 同一自定义任务下同一回路仅一条快照记录。

**说明**：自定义评估任务快照按需触发，不参与装置级聚合。

**建表 DDL**：

```sql
CREATE TABLE kpi_snapshot_custom (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id UUID NOT NULL,
    loop_id UUID NOT NULL REFERENCES loop_ledger(id),
    ts_start TIMESTAMP NOT NULL,
    ts_end TIMESTAMP NOT NULL,
    score DECIMAL(5,2),
    accuracy_rate DECIMAL(5,2),
    fast_rate DECIMAL(5,2),
    stability_rate DECIMAL(5,2),
    effective_auto_rate DECIMAL(5,2),
    good_value_rate DECIMAL(5,2),
    oscillation_rate DECIMAL(5,2),
    saturation_rate DECIMAL(5,2),
    stiction_index DECIMAL(5,2),
    output_trip_index DECIMAL(5,2),
    settling_time DECIMAL(8,2),
    ideal_settling_time DECIMAL(8,2),
    auto_mode_rate DECIMAL(5,2),
    algorithm_version VARCHAR(50),
    status VARCHAR(20),
    confidence_level CHAR(1),
    valid_rate DECIMAL(5,4),
    data_lineage JSONB,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(task_id, loop_id)
);
```

### 2.15 指标数据需求契约 (clpm_metric_data_requirement)

**表名: `clpm_metric_data_requirement` (指标数据需求契约)** [v4.0 新增]

承载每个性能/诊断指标对底层数据的契约化需求声明，包括所需 Tag 组、采样策略、质量策略、Mask 表达式、聚合策略、依赖关系等。该表为算法服务与数据采集层之间的"数据契约"，支撑数据血缘追溯（与 `kpi_snapshot_hourly.data_lineage` 字段配合）与指标可信度判定。

| 字段 | 类型 | 说明 | 约束 |
|---|---|---|---|
| id | UUID | 契约主键 | PK, DEFAULT gen_random_uuid() |
| metric_code | VARCHAR(50) | 指标代码（如 `GOOD_VALUE_RATE`、`OSCILLATION_RATE`） | UNIQUE, NOT NULL |
| tag_group | VARCHAR(20) | 所需 Tag 组（如 `PV`、`PV_SP_OP_MODE`、`PV_SP_OP`） | NOT NULL |
| tags | JSONB | 所需 Tag 角色列表（如 `["PV","SP","OP","MODE"]`） | NOT NULL |
| sampling_strategy | VARCHAR(30) | 采样策略（如 `RAW_1S`、`RAW_5S`、`DOWNSAMPLE_1MIN`） | |
| quality_policy | VARCHAR(30) | 质量策略（如 `KEEP_ALL_WITH_VALIDITY`） | |
| mask_expression | VARCHAR(200) | Metric Validity Mask 表达式（如 `pv_quality==1 && mode==1`） | |
| aggregation_policy | VARCHAR(20) | 聚合策略（如 `MEAN`、`RATIO`、`RMS`、`PERCENTILE`） | |
| depends_on | JSONB | 依赖的其他指标或数据块（如 `["GOOD_VALUE_RATE"]`） | |
| version | VARCHAR(20) | 契约版本号 | DEFAULT 'v1' |
| updated_at | TIMESTAMP | 最后更新时间 | DEFAULT NOW() |

**建表 DDL**：

```sql
CREATE TABLE clpm_metric_data_requirement (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    metric_code VARCHAR(50) NOT NULL UNIQUE,
    tag_group VARCHAR(20) NOT NULL,
    tags JSONB NOT NULL,
    sampling_strategy VARCHAR(30),
    quality_policy VARCHAR(30),
    mask_expression VARCHAR(200),
    aggregation_policy VARCHAR(20),
    depends_on JSONB,
    version VARCHAR(20) DEFAULT 'v1',
    updated_at TIMESTAMP DEFAULT NOW()
);
```

### 2.16 诊断标签 (diagnosis_tag)

**表名: `diagnosis_tag` (诊断标签表)** [v4.0 新增]

承载回路级的诊断标签记录，用于故障定位和告警，包括振荡、阀门粘滞、输出饱和、PV 质量异常等标签。与 `diagnosis_result`（诊断结果表）互补：`diagnosis_result` 存储完整诊断证据链，`diagnosis_tag` 存储可枚举、可查询、可状态流转的标签实例，支撑告警面板与标签筛选。

| 字段 | 类型 | 说明 | 约束 |
|---|---|---|---|
| id | UUID | 标签主键 | PK, DEFAULT gen_random_uuid() |
| loop_id | UUID | 关联回路 ID | FK -> loop_ledger.id, NOT NULL |
| tag_code | VARCHAR(50) | 标签代码（如 `OSCILLATION`、`VALVE_STICTION`、`OUTPUT_SATURATION`、`QUALITY_ABNORMAL`） | NOT NULL |
| severity | VARCHAR(20) | 严重等级（如 `INFO`、`WARNING`、`CRITICAL`） | NOT NULL |
| source_metric | VARCHAR(50) | 触发该标签的来源指标代码（如 `OSCILLATION_RATE`） | |
| trigger_condition | JSONB | 触发条件（如 `{"threshold": 0.4, "window_minutes": 60}`） | |
| triggered_at | TIMESTAMP | 标签触发时间 | NOT NULL, DEFAULT NOW() |
| resolved_at | TIMESTAMP | 标签解除时间 | |
| status | VARCHAR(20) | 标签状态: `ACTIVE`(生效中), `RESOLVED`(已解除), `IGNORED`(已忽略) | DEFAULT 'ACTIVE' |

**说明**：诊断标签用于故障定位和告警，包括振荡、阀门粘滞、输出饱和、PV 质量异常等标签。

**建表 DDL**：

```sql
CREATE TABLE diagnosis_tag (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    loop_id UUID NOT NULL REFERENCES loop_ledger(id),
    tag_code VARCHAR(50) NOT NULL,
    severity VARCHAR(20) NOT NULL,
    source_metric VARCHAR(50),
    trigger_condition JSONB,
    triggered_at TIMESTAMP NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMP,
    status VARCHAR(20) DEFAULT 'ACTIVE'
);
```

### 2.17 装置级汇总 (unit_kpi_summary)

**表名: `unit_kpi_summary` (装置级 KPI 汇总表)** [v4.0 新增]

承载装置（plant_node 中 `type=UNIT` 的节点）级 KPI 汇总快照，按周期对装置下所有启用回路的 `kpi_snapshot_hourly` 进行聚合。**装置级汇总仅基于标准任务（`kpi_snapshot_hourly`），自定义任务（`kpi_snapshot_custom`）不参与聚合**。

| 字段 | 类型 | 说明 | 约束 |
|---|---|---|---|
| id | UUID | 汇总主键 | PK, DEFAULT gen_random_uuid() |
| node_id | UUID | 装置节点 ID | FK -> plant_node.id, NOT NULL |
| snapshot_time | TIMESTAMP | 汇总快照时间（与聚合窗口对齐） | NOT NULL |
| avg_score | DECIMAL(5,2) | 装置内回路平均综合评分（按 `score_weight` 加权） | |
| auto_mode_rate | DECIMAL(5,2) | 装置自控率（加权聚合） | |
| effective_auto_rate | DECIMAL(5,2) | 装置有效自控率（加权聚合） | |
| stability_rate | DECIMAL(5,2) | 装置平稳率（加权聚合） | |
| accuracy_rate | DECIMAL(5,2) | 装置准确率（加权聚合） | |
| fast_rate | DECIMAL(5,2) | 装置快速率（加权聚合） | |
| good_value_rate | DECIMAL(5,2) | 装置好值率（加权聚合） | |
| oscillation_rate | DECIMAL(5,2) | 装置振荡率（加权聚合） | |
| saturation_rate | DECIMAL(5,2) | 装置饱和率（加权聚合） | |
| total_loops | INTEGER | 装置下回路总数 | |
| evaluated_loops | INTEGER | 实际参与评估的回路数（status=SUCCESS） | |
| inconclusive_loops | INTEGER | INCONCLUSIVE 状态回路数 | |
| algorithm_version | VARCHAR(50) | 聚合算法版本号 | |
| created_at | TIMESTAMP | 记录创建时间 | DEFAULT NOW() |

**唯一约束**: `(node_id, snapshot_time)` —— 同一装置同一快照时间仅一条汇总记录。

**说明**：装置级汇总仅基于标准任务（`kpi_snapshot_hourly`），自定义任务不参与。

**建表 DDL**：

```sql
CREATE TABLE unit_kpi_summary (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    node_id UUID NOT NULL REFERENCES plant_node(id),
    snapshot_time TIMESTAMP NOT NULL,
    avg_score DECIMAL(5,2),
    auto_mode_rate DECIMAL(5,2),
    effective_auto_rate DECIMAL(5,2),
    stability_rate DECIMAL(5,2),
    accuracy_rate DECIMAL(5,2),
    fast_rate DECIMAL(5,2),
    good_value_rate DECIMAL(5,2),
    oscillation_rate DECIMAL(5,2),
    saturation_rate DECIMAL(5,2),
    total_loops INTEGER,
    evaluated_loops INTEGER,
    inconclusive_loops INTEGER,
    algorithm_version VARCHAR(50),
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(node_id, snapshot_time)
);
```

---

## 3. 高频时序模型 (TDengine)

采用 TDengine 推荐的"一个设备一张表，一类设备一个超级表"设计模式。v3.0 对超级表字段进行扩展，对齐 7 个 OPC Tag 模型，并将原 `quality` 字段重命名为 `pv_quality`，明确仅针对 PV 值。

### 3.1 超级表定义 (Super Table)

**超级表名: `st_loop_data`**

该超级表定义了所有控制回路时序数据的标准 Schema，覆盖 7 个 OPC Tag 的原始秒级数据及 PV 质量码。

| 字段 | 类型 | 说明 | 约束 |
|---|---|---|---|
| ts | TIMESTAMP | 采样时间戳 | 主键 (时间列) |
| pv | FLOAT | 过程变量测量值 (来自 PV Tag) | |
| sp | FLOAT | 设定值 (来自 SP Tag) | |
| op | FLOAT | 控制器输出值 (0-100，来自 OP Tag) | |
| mode | TINYINT | 控制模式 (0=Manual, 1=Auto, 2=Cascade，来自 MODE Tag) | |
| pid_p | FLOAT | 比例参数 (来自 PID_P Tag，只读) | |
| pid_i | FLOAT | 积分参数 (来自 PID_I Tag，只读) | |
| pid_d | FLOAT | 微分参数 (来自 PID_D Tag，只读) | |
| pv_quality | TINYINT | **PV 数据质量码** (0=Bad, 1=Good, 2=Uncertain)，仅 PV Tag 携带质量码 | |

> **v3.0 变更说明**：
> 1. 新增 `pid_p`, `pid_i`, `pid_d` 三个字段，对齐 7 个 OPC Tag 模型，PID 参数从 Tag 只读读取。
> 2. 原 `quality` 字段重命名为 `pv_quality`，明确数据质量仅针对 PV 值（PV Tag 携带质量码，其余 Tag 不携带）。

**超级表标签 (Tags)**

用于快速过滤与聚合查询，保持不变。

| 标签名 | 类型 | 说明 |
|---|---|---|
| loop_id | BINARY(36) | 关联关系库的 loop_ledger.id |
| unit_id | BINARY(36) | 关联的单元 ID，用于按单元降采样聚合 |

### 3.2 子表实例化

系统每同步接入一条新回路，将自动执行建表操作：

```sql
CREATE TABLE d_loop_101_fc_1023 USING st_loop_data TAGS ('uuid-xxx', 'uuid-yyy');
```

子表命名规范：`d_loop_<位号去分隔符小写>`，如位号 `101-FC-1023` 对应子表 `d_loop_101_fc_1023`。

---

## 4. 数据容错与清洗规则

CLPM 系统的核心设计理念是**"绝对真实，不掩盖数据缺失"**。v3.0 明确 PV 质量码处理逻辑。

### 4.1 PV 质量码过滤

> **v4.0 变更**（对齐《关键算法设计说明》v2.0）：
> 质量策略由"Bad 数据剔除"升级为 **`KEEP_ALL_WITH_VALIDITY`**：保留所有数据点，按各指标的需求契约 (`clpm_metric_data_requirement`) 打 `valid` 标记，仅在指标计算时按各自的 **Metric Validity Mask** 决定是否纳入。该策略避免数据缺失被掩盖，同时为数据血缘 (`data_lineage`) 与可信度等级 (`confidence_level`) 提供量化输入。

1. **质量码字段**：基于 TDengine 超级表 `st_loop_data` 的 `pv_quality` 字段（TINYINT：0=Bad, 1=Good, 2=Uncertain）进行过滤。
2. **质量策略 `KEEP_ALL_WITH_VALIDITY`**：所有原始数据点全部保留并参与写入，计算引擎不物理剔除任何点，而是按指标契约为每个数据点打 `valid` 标记（`valid=true` 表示对该指标有效，`valid=false` 表示无效）。该策略确保数据血缘可追溯、可信度可量化，避免剔除导致的信息丢失。
3. **好值率统计**：在写入 `kpi_snapshot_hourly` 前，计算引擎扫描 `pv_quality` 字段。某时间窗内 `pv_quality=1 (Good)` 的记录占比即为**好值率**，写入 `good_value_rate` 字段。
4. **Metric Validity Mask（按指标差异化）**：不同指标使用不同的有效性掩码表达式（定义于 `clpm_metric_data_requirement.mask_expression`），决定该指标计算时是否纳入某数据点。典型掩码示例：

   | 指标 (metric_code) | Metric Validity Mask 示例 | 说明 |
   |---|---|---|
   | `GOOD_VALUE_RATE` | `pv_quality==1` | 仅 PV 质量为 Good 的点 |
   | `AUTO_MODE_RATE` | `pv_quality!=0 && mode==1` | 非 Bad 且处于 Auto 模式 |
   | `STEADY_RATE` | `pv_quality==1 && mode==1` | 仅 Good 且 Auto 时段参与偏差统计 |
   | `ACCURACY_RATE` | `pv_quality==1 && mode==1` | 仅 Good 且 Auto 时段参与准确率计算 |
   | `OSCILLATION_RATE` | `pv_quality==1 && mode==1` | 仅 Good 且 Auto 时段参与振荡检测 |
   | `SATURATION_RATE` | `pv_quality!=0` | 非 Bad 时段统计 OP 限位 |

5. **Inconclusive 触发**：若某时间窗内 `pv_quality=1 (Good)` 的记录占比低于配置阈值（默认 20%，阈值可通过 `engine_rule` 配置），则跳过各项 KPI 计算，直接将快照状态置为 `INCONCLUSIVE`，各 KPI 字段留空（NULL），**严禁写入 0 分**。
6. **Uncertain 处理**：`pv_quality=2 (Uncertain)` 的数据点在 `KEEP_ALL_WITH_VALIDITY` 策略下保留，但被多数指标的 Mask 判定为 `valid=false`，不掩盖数据缺失，通过 `Inconclusive` 状态与 `valid_rate`/`confidence_level` 字段显式反馈。
7. **指标可信度等级（A/B/C/D/E）**：每个快照根据 `valid_rate`（有效数据率，0~1）与样本量综合判定指标可信度等级，写入 `confidence_level` 字段。等级与 `valid_rate` 的对应关系如下：

   | 等级 | valid_rate 区间 | 含义 | 用途 |
   |---|---|---|---|
   | `A` | `>= 0.95` | 高可信 | 可直接用于评分、聚合与对外展示 |
   | `B` | `0.80 ~ 0.95` | 较可信 | 可用于评分，聚合时标注 |
   | `C` | `0.60 ~ 0.80` | 一般可信 | 可用于评分，但聚合降权 |
   | `D` | `0.20 ~ 0.60` | 低可信 | 评分仍计算，但需人工复核 |
   | `E` | `< 0.20` | 不可信 | 触发 `INCONCLUSIVE`，KPI 留空 |

   > 等级区间为默认配置，可通过 `engine_rule` 调整；当 `valid_rate < 0.20` 时统一触发 `INCONCLUSIVE`，与好值率阈值保持一致。

### 4.2 PV 质量码可视化

* **波形渲染**：前端渲染 PV 时序波形时，根据 `pv_quality` 分段着色：
  * `Good` (1)：实线，正常颜色。
  * `Bad` (0)：灰色虚线断线。
  * `Uncertain` (2)：黄色虚线。
* **数据点缺失**：数据点缺失时断线展示，悬浮提示缺失时段。

### 4.3 时序数据留存期 (Retention)

* **TDengine 原始秒级数据**：默认保留周期配置为 `KEEP 365` (1 年)，覆盖 7 个 OPC Tag 字段及 `pv_quality`。
* **PostgreSQL 快照记录**：`kpi_snapshot_hourly` 快照记录永久保留，支撑 P2/P3 规划中的 5 年任意查询及趋势回溯。
* **配置表版本**：`metric_config` / `diagnosis_config` / `engine_rule` 的历史版本通过 `version` 字段及 `sys_audit_log` 永久保留，支持配置回滚。

### 4.4 AAS 同步容错

* AAS Tag 同步服务具备断线重连与增量同步能力。
* 同步失败时保留 `tag_registry` 上一周期有效数据并告警，不阻塞回路监控页面读取。
* `tag_registry.last_sync_at` 字段用于展示最后同步时间，便于运维排查。

---

## 5. 算法结果存储设计 (Algorithm Result Storage)

> **v3.1 新增**：本章节对齐《关键算法设计说明》v1.0 与 ADS v3.1 §8 算法服务架构，定义 3 大算法服务（KPI 计算/诊断分析/整定计算）结果的存储策略。
>
> **v4.0 更新**：对齐《关键算法设计说明》v2.0，KPI 结果存储新增数据血缘字段，并区分标准任务（`kpi_snapshot_hourly`）与自定义任务（`kpi_snapshot_custom`）的存储路径。

### 5.1 KPI 计算结果存储策略

KPI 计算服务按任务类型分两类存储：

* **标准任务**：每小时对所有启用回路执行一次 KPI 计算，结果写入 `kpi_snapshot_hourly`，参与装置级聚合（`unit_kpi_summary`）。
* **自定义任务**：由用户按需触发（指定时间窗、回路集合），结果写入 `kpi_snapshot_custom`，**不参与装置级聚合**。

标准任务采用"小时级快照 + 日/周/月聚合"三级存储策略：

| 存储层级 | 存储表 | 存储内容 | 保留周期 | 说明 |
|---|---|---|---|---|
| 小时级快照 | `kpi_snapshot_hourly` | 6 大 KPI 值 + 扩展指标 + 综合评分 + 算法版本 + 状态 + 数据血缘字段 | 永久 | 每小时 1 条/回路，1200 回路 = 28800 条/天 |
| 装置级汇总 | `unit_kpi_summary` | 装置级加权聚合 KPI + 评分 + 回路统计 + 算法版本 | 永久 | 仅基于 `kpi_snapshot_hourly` 聚合，自定义任务不参与 |
| 日聚合 | `kpi_snapshot_hourly`（按日聚合查询） | 日均值/最大值/最小值 | 永久 | 通过 SQL 聚合查询，不单独建表 |
| 周/月聚合 | `kpi_snapshot_hourly`（按周/月聚合查询） | 周均值/月均值 | 永久 | 通过 SQL 聚合查询，支撑趋势分析 |
| 自定义任务快照 | `kpi_snapshot_custom` | 与 `kpi_snapshot_hourly` 同构指标 + `task_id` + 数据血缘字段 | 永久 | 按需触发，不参与聚合 |

**KPI 快照写入规则**：
* 每条快照记录关联 `algorithm_version` 字段（如 `KPI_CALC_v1.0`），支持算法升级后的结果对比。
* `status` 字段标记计算状态：`SUCCESS`（正常）/`INCONCLUSIVE`（好值率 < 20%，KPI 留空）/`PARTIAL`（部分 KPI 计算失败）。
* INCONCLUSIVE 快照严禁写入 0 分，KPI 字段留空（NULL），显式反馈数据质量问题。

**数据血缘存储说明** [v4.0 新增]：
* `kpi_snapshot_hourly` 与 `kpi_snapshot_custom` 均包含 5 个数据血缘字段：`sampling_freq`、`quality_policy`、`valid_rate`、`confidence_level`、`data_lineage`。
* `sampling_freq`：记录本次快照输入数据的实际采样频率（如 `1s`/`5s`），支撑采样率一致性校验与跨快照对比。
* `quality_policy`：记录本次计算采用的质量策略（默认 `KEEP_ALL_WITH_VALIDITY`），与 `clpm_metric_data_requirement.quality_policy` 对齐。
* `valid_rate`：记录评估窗口内满足各指标 Metric Validity Mask 的数据占比（0~1），是判定 `confidence_level` 的核心输入。
* `confidence_level`：指标可信度等级（A/B/C/D/E），由 `valid_rate` 与样本量综合判定（详见 §4.1）。
* `data_lineage`（JSONB）：存储完整数据血缘信息，典型结构如下：
  ```json
  {
    "tag_group": "PV_SP_OP_MODE",
    "data_block_ids": ["blk_2026062610_101fc1023", "blk_2026062611_101fc1023"],
    "aggregation_policy": "MEAN",
    "metric_mask_refs": {
      "GOOD_VALUE_RATE": "pv_quality==1",
      "STEADY_RATE": "pv_quality==1 && mode==1"
    },
    "source_table": "st_loop_data",
    "subtable": "d_loop_101_fc_1023"
  }
  ```
  * `tag_group`：本次计算使用的 Tag 组（与 `clpm_metric_data_requirement.tag_group` 对齐）。
  * `data_block_ids`：参与计算的数据块 ID 列表，支撑追溯到 TDengine 原始数据段。
  * `aggregation_policy`：聚合策略（与 `clpm_metric_data_requirement.aggregation_policy` 对齐）。
  * `metric_mask_refs`：各指标使用的 Metric Validity Mask 引用，支撑指标级可追溯。
  * `source_table` / `subtable`：数据来源超级表与子表名，支撑数据源追溯。

**标准任务与自定义任务存储区分** [v4.0 新增]：
* 标准任务结果仅写入 `kpi_snapshot_hourly`，每小时调度执行，参与 `unit_kpi_summary` 装置级聚合与日/周/月趋势分析。
* 自定义任务结果仅写入 `kpi_snapshot_custom`，通过 `task_id` 区分独立任务，不参与任何聚合，仅供用户查询与对比分析。
* 两表指标字段结构对齐，便于跨任务类型对比；但装置级汇总（`unit_kpi_summary`）严格仅基于 `kpi_snapshot_hourly`。

### 5.2 诊断结果存储策略

诊断分析服务在回路评分跌破阈值时触发，每次诊断完整结果落库，支持历史趋势回溯：

| 存储表 | 存储内容 | 保留周期 | 说明 |
|---|---|---|---|
| `diagnosis_result` | 诊断标签 + 置信度 + 特征值 + 证据链 + 算法版本 + 诊断时间 | 永久 | 每次诊断 1 条记录，含 8 类标签中的 1 个主标签 |
| `action_tracker` | 异常追踪记录（关联诊断结果） | 永久 | 跟踪诊断后的处理状态（待处理/处理中/已实施/已忽略） |

**诊断结果写入规则**：
* `diag_label` 字段存储 8 类诊断标签之一（`OSCILLATION`/`VALVE_STICTION`/`OVERAGGRESSIVE`/`OVERCONSERVATIVE`/`EXTERNAL_DISTURBANCE`/`QUALITY_ABNORMAL`/`OUTPUT_SATURATION`/`MANUAL_REVIEW`）。
* `feature_values` 字段以 JSON 存储诊断特征值（如 FFT 主频、散点拟合参数、超调量等）。
* `evidence_chain` 字段以 JSON 存储证据链引用（波形时间段、散点图数据引用等），支撑《诊断建议书》PDF 生成。
* `algorithm_version` 字段记录诊断算法版本（如 `OSC_IAE_v1.0`、`STICTION_CH_v1.0`）。

### 5.3 整定结果存储策略

整定计算服务由用户手动触发，完整记录模型辨识、PID 整定与闭环仿真全过程：

| 存储表 | 存储内容 | 保留周期 | 说明 |
|---|---|---|---|
| `tuning_record` | 模型参数 + 拟合度 + 推荐 PID + 仿真结果 + 算法版本 + 状态 | 永久 | 每次整定 1 条记录，含完整整定过程 |

**整定记录写入规则**：
* `model_params` 字段以 JSON 存储模型辨识参数（如 `{"K": 1.2, "tau": 30.5, "theta": 5.0}`）。
* `fitting_score` 字段记录模型拟合度 R² × 100（0-100），用于评估模型辨识质量。
* `recommended_pid` 字段以 JSON 存储推荐 PID 参数（如 `{"Kp": 1.5, "Ti": 0.8, "Td": 0.2}`）。
* `simulation_result` 字段以 JSON 存储闭环仿真结果（含阶跃响应曲线、性能指标对比）。
* `algorithm_version` 字段记录整定算法版本（如 `FOPDT_ID_v1.0`、`IMC_TUNE_v1.0`）。
* `status` 字段跟踪整定状态：`PENDING`→`IDENTIFIED`→`SIMULATED`→`APPLIED`→`VERIFIED`。

---

## 6. 算法版本字段 (Algorithm Version Fields)

> **v3.1 新增**：本章节对齐 ADS v3.1 §12 算法版本管理与《关键算法设计说明》§3.3，定义算法结果表中的版本字段规范。

### 6.1 算法版本字段汇总

v3.1 在以下表中新增或明确 `algorithm_version` 字段，用于算法结果追溯：

| 表名 | 字段 | 类型 | 说明 |
|---|---|---|---|
| `kpi_snapshot_hourly` | `algorithm_version` | VARCHAR(50) | KPI 计算算法版本（如 `KPI_CALC_v1.0`、`SCORE_CALC_v1.0`） |
| `diagnosis_result` | `algorithm_version` | VARCHAR(50) | 诊断算法版本（如 `OSC_IAE_v1.0`、`STICTION_CH_v1.0`） |
| `tuning_record` | `algorithm_version` | VARCHAR(50) | 整定算法版本（如 `FOPDT_ID_v1.0`、`IMC_TUNE_v1.0`） |

### 6.2 版本号格式

算法版本号格式：`<algorithm_name>v<major>.<minor>`（对齐 ADS v3.1 §12.1）

* `<algorithm_name>`：算法类别代码（如 `KPI_CALC`、`OSC_IAE`、`FOPDT_ID`、`IMC_TUNE`）
* `<major>`：主版本号，算法公式变更时递增
* `<minor>`：次版本号，参数调整时递增

### 6.3 配置表版本字段

配置表通过 `version` 字段（INT）记录配置变更版本，配合 `sys_audit_log` 实现变更追溯与回滚：

| 配置表 | 版本字段 | 说明 |
|---|---|---|
| `metric_config` | `version` (INT) | 性能指标配置版本号，每次配置变更递增 |
| `diagnosis_config` | `version` (INT) | 诊断指标配置版本号，每次配置变更递增 |

---

## 7. ER 图更新说明 (ER Diagram Update Notes)

> **v3.1 新增**：本章节说明 v3.1 变更对 ER 图关系结构的影响。

### 7.1 v3.1 新增字段汇总

| 表名 | 新增/变更字段 | 变更类型 | 说明 |
|---|---|---|---|
| `metric_config` | `threshold` | 类型变更 | DECIMAL(5,2) → JSONB |
| `metric_config` | `control_type` | 新增字段 | VARCHAR(20)，DEFAULT 'STABLE' |
| `kpi_snapshot_hourly` | `accuracy_rate` | 新增字段 | DECIMAL(5,2)，准确率 |
| `kpi_snapshot_hourly` | `saturation_rate` | 新增字段 | DECIMAL(5,2)，饱和率 |
| `kpi_snapshot_hourly` | `algorithm_version` | 新增字段 | VARCHAR(50)，算法版本号 |
| `diagnosis_config` | `calc_method` | 新增字段 | VARCHAR(50)，计算方法 |
| `diagnosis_config` | `threshold` | 类型变更 | DECIMAL(5,2) → JSONB |
| `tuning_record` | `fitting_score` | 新增字段 | DECIMAL(5,2)，模型拟合度评分 |
| `tuning_record` | `algorithm_version` | 新增字段 | VARCHAR(50)，算法版本号 |
| `tuning_record` | `algorithm` | 枚举扩展 | 新增 `SIMC` 枚举值 |

### 7.2 关系结构影响评估

**本次 v3.1 变更不影响现有 ER 图的关系结构**：

* 所有新增字段均为单表内的属性字段，不涉及表间外键关系变更。
* `metric_config.threshold` 类型变更（DECIMAL → JSONB）不影响与 `kpi_snapshot_hourly` 的逻辑关联。
* `kpi_snapshot_hourly` 新增 `accuracy_rate`、`saturation_rate`、`algorithm_version` 字段，不改变与 `loop_ledger` 的外键关系。
* `diagnosis_config` 新增 `calc_method` 字段与 `threshold` 类型变更，不改变与 `diagnosis_result` 的逻辑关联。
* `tuning_record` 新增 `fitting_score`、`algorithm_version` 字段，不改变与 `loop_ledger` 的外键关系。

### 7.3 现有关系结构（保持不变）

```text
plant_node (1) ──── (N) loop_ledger (1) ──── (N) loop_tag_mapping (N) ──── (1) tag_registry
                          │
                          ├── (N) kpi_snapshot_hourly
                          ├── (N) diagnosis_result
                          ├── (N) action_tracker
                          └── (N) tuning_record

metric_config (独立配置表)          diagnosis_config (独立配置表)
engine_rule (独立配置表)            report_record (独立记录表)
sys_audit_log (独立日志表)
```

**外键关系不变**：
* `loop_ledger.unit_id` → `plant_node.id`
* `loop_tag_mapping.loop_id` → `loop_ledger.id`
* `loop_tag_mapping.tag_id` → `tag_registry.id`
* `kpi_snapshot_hourly.loop_id` → `loop_ledger.id`
* `diagnosis_result.loop_id` → `loop_ledger.id`
* `action_tracker.loop_id` → `loop_ledger.id`
* `tuning_record.loop_id` → `loop_ledger.id`
