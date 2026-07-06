# DDS v4.1 基准信息提取

> 提取日期：2026-07-06
> 来源文件：`/Users/zhangping/DEV/CLPM/docs/设计文档/04-DDS/DDS.md`
> 源文件行数：921 行

---

## 1. 版本号声明

| 项 | 内容 |
|---|---|
| 文档当前版本号 | v4.1 |
| 发布日期 | 2026-07-04 |
| 文档状态 | 正式版 |
| 设计依据 | PRD (v3.1), FDS (v5.1，表名/字段名权威基线), ADS (v3.1), 关键算法设计说明 (v2.0) |
| 适用范围 | CLPM 数据模型设计说明书 (DDS)，承载关系型业务域 (PostgreSQL) 与高频时序域 (TDengine) 的数据模型设计 |

### 变更历史摘要

| 版本 | 日期 | 变更说明 | 作者 |
|---|---|---|---|
| v3.0 | 2026-06-20 | 产品化架构重构版：存算分离、回路-Tag 解耦、配置驱动、PV 质量码处理、新增诊断结果表与整定记录表。 | 数据架构组 |
| v3.1 | 2026-06-22 | 对齐《关键算法设计说明》v1.0：①`metric_config.threshold` 类型 DECIMAL → JSONB，新增 `control_type` 字段；②`kpi_snapshot_hourly` 新增 `accuracy_rate`、`saturation_rate` 字段；③`diagnosis_config` 新增 `calc_method` 字段，`threshold` 类型 DECIMAL → JSONB；④`tuning_record` 新增 `fitting_score` 字段；⑤新增"算法结果存储设计"章节；⑥新增"算法版本字段"说明；⑦ER 图更新说明。 | 数据架构组 |
| v4.0 | 2026-06-26 | 对齐《关键算法设计说明》v2.0：①`kpi_snapshot_hourly` 扩展 `fast_rate`/`effective_auto_rate`/`stiction_index`/`output_trip_index`/`settling_time`/`ideal_settling_time` 等指标字段及 `sampling_freq`/`quality_policy`/`valid_rate`/`confidence_level`/`data_lineage` 数据血缘字段；②新增 `kpi_snapshot_custom` 自定义任务快照表；③新增 `clpm_metric_data_requirement` 指标数据需求契约表；④新增 `diagnosis_tag` 诊断标签表；⑤新增 `unit_kpi_summary` 装置级汇总表；⑥§4.1 PV 质量码过滤策略升级为 `KEEP_ALL_WITH_VALIDITY`，引入 Metric Validity Mask 与 A/B/C/D/E 五级可信度；⑦§5.1 KPI 结果存储新增数据血缘字段说明。 | 数据架构组 |
| v4.1 | 2026-07-04 | 对齐 FDS v5.1：①`loop_ledger` 新增 `control_type` / `importance_level` / `include_in_evaluation` 三字段；②`metric_config` 新增 `grading_thresholds` 字段，`formula` 字段标注为废弃，`control_type` 字段标注为迁移至 `loop_ledger`；③`unit_kpi_summary` 新增 `excluded_loops` / `status` 字段；④`kpi_snapshot_custom.stability_rate` 修正为 `steady_rate`；⑤全文术语"稳定率"统一。 | 数据架构组 |

---

## 2. PostgreSQL 表清单

共 17 张表。

### 2.1 plant_node（工厂节点）

**用途**：承载工厂 → 装置 → 单元的多级层级树。

| 字段名 | 类型 | 可空 | 默认值 | 注释 |
|---|---|---|---|---|
| id | UUID | 否 | | 节点主键 |
| name | VARCHAR(100) | 否 | | 节点名称 (如: 常减压装置) |
| type | VARCHAR(20) | 否 | | 节点类型: `FACTORY`, `UNIT`, `EQUIPMENT` |
| parent_id | UUID | 是 | | 父节点 ID |

- **主键**：id
- **外键**：parent_id → plant_node.id（自引用）
- **索引**：（文档未提及）
- **约束**：（文档未提及）

### 2.2 loop_ledger（回路台账）

**用途**：回路作为系统核心实体，由用户在 CLPM 系统中创建并关联 Tag。v4.1 新增 `control_type` / `importance_level` / `include_in_evaluation` 三字段，对齐 FDS v5.1 §5.2.3 回路评估参与配置。

| 字段名 | 类型 | 可空 | 默认值 | 注释 |
|---|---|---|---|---|
| id | UUID | 否 | | 回路主键 |
| tag_name | VARCHAR(100) | 否 | | 唯一位号标识 (如: 101-FC-1023) |
| description | VARCHAR(255) | 是 | | 回路描述 (如: 常顶塔顶温度调节回路) |
| unit_id | UUID | 是 | | 所属工艺单元 ID |
| score_weight | DECIMAL(5,2) | 是 | | 评分权重 (用于装置/单元级聚合时的加权计算) |
| is_active | BOOLEAN | 是 | TRUE | 是否启用全量评估计算 |
| last_aas_sync_at | TIMESTAMP | 是 | | 最后 AAS 同步时间 |
| status | VARCHAR(20) | 否 | 'PARTIAL' | 回路状态: `READY`/`PARTIAL`/`INACTIVE` |
| control_type | VARCHAR(20) | 否 | 'STABLE' | 控制类型: `STABLE`/`SLOW`/`FAST`/`LOGIC` [v4.1 新增] |
| importance_level | SMALLINT | 否 | 2 | 重要等级：1/2/3 [v4.1 新增] |
| include_in_evaluation | BOOLEAN | 否 | TRUE | 是否参与评估 [v4.1 新增] |

- **主键**：id
- **外键**：unit_id → plant_node.id
- **索引**：（文档未提及）
- **约束**：tag_name UNIQUE NOT NULL

### 2.3 tag_registry（AAS Tag 注册表）

**用途**：AAS Integration Service 定期从 AAS 同步所有 OPC Tag 位号信息，写入本表。同步对象为 Tag 位号（非回路实体），与回路解耦。

| 字段名 | 类型 | 可空 | 默认值 | 注释 |
|---|---|---|---|---|
| id | UUID | 否 | | Tag 主键 |
| tag_name | VARCHAR(100) | 否 | | Tag 位号名 (OPC Item ID) |
| tag_description | VARCHAR(255) | 是 | | Tag 描述 (来自 AAS) |
| tag_type | VARCHAR(20) | 否 | | Tag 类型: `PV`/`SP`/`OP`/`MODE`/`PID_P`/`PID_I`/`PID_D`/`OTHER` |
| current_value | FLOAT | 是 | | 当前值 (最近一次同步快照) |
| quality | VARCHAR(20) | 是 | | 数据质量码: `GOOD`/`BAD`/`UNCERTAIN` |
| last_sync_at | TIMESTAMP | 否 | | 最后同步时间 |
| is_linked | BOOLEAN | 是 | FALSE | 是否已关联到回路 |

- **主键**：id
- **外键**：（无）
- **索引**：（文档未提及）
- **约束**：tag_name UNIQUE NOT NULL

### 2.4 loop_tag_mapping（回路-Tag 关联）

**用途**：记录回路与 7 个 OPC Tag 的关联关系。一个典型控制回路关联 7 个 Tag：PV/SP/OP/MODE/PID_P/PID_I/PID_D。

| 字段名 | 类型 | 可空 | 默认值 | 注释 |
|---|---|---|---|---|
| id | UUID | 否 | | 关联主键 |
| loop_id | UUID | 否 | | 关联回路 ID |
| tag_id | UUID | 否 | | 关联 Tag ID |
| tag_role | VARCHAR(20) | 否 | | Tag 角色: `PV`/`SP`/`OP`/`MODE`/`PID_P`/`PID_I`/`PID_D` |
| is_required | BOOLEAN | 否 | | 是否必填 Tag (PV/SP/OP/MODE 为 TRUE，PID_* 为 FALSE) |
| created_at | TIMESTAMP | 否 | | 关联创建时间 |

- **主键**：id
- **外键**：loop_id → loop_ledger.id；tag_id → tag_registry.id
- **索引**：（文档未提及）
- **约束**：UNIQUE(loop_id, tag_role) —— 同一回路同一角色仅能关联一个 Tag

### 2.5 metric_config（性能指标配置）

**用途**：承载 6 大核心 KPI（好值率、自控率、稳定率、准确率、振荡率、饱和率）及变体指标的可配置元数据。权重总和约束 100%。v4.1 `formula` 标注废弃，`control_type` 迁移至 `loop_ledger`，新增 `grading_thresholds`。

| 字段名 | 类型 | 可空 | 默认值 | 注释 |
|---|---|---|---|---|
| id | UUID | 否 | | 指标主键 |
| metric_code | VARCHAR(50) | 否 | | 指标代码（12 项之一） |
| metric_name | VARCHAR(100) | 否 | | 指标名称 (如: 好值率) |
| formula | TEXT | 是 | | ~~计算公式（已废弃）~~ [v4.1 标注废弃]，字段保留以兼容历史数据，不开放 API/UI |
| weight | DECIMAL(5,2) | 是 | | 权重 (3 项核心指标 A/F/S 权重，按 control_type 分 4 套模板；总和须为 1.0；辅助诊断指标置 NULL) |
| threshold | JSONB | 是 | | 阈值对象 `{"min": number, "max": number, "alert": number}` |
| control_type | VARCHAR(20) | 是 | 'STABLE' | ~~控制类型（已迁移至 `loop_ledger.control_type`）~~ [v4.1 标注迁移] |
| grading_thresholds | JSONB | 是 | | 性能定级 5 级阈值（EXCELLENT/GOOD/FAIR/WARNING/POOR） [v4.1 新增] |
| is_enabled | BOOLEAN | 是 | TRUE | 是否启用 |
| updated_by | VARCHAR(50) | 是 | | 最后更新人 |
| updated_at | TIMESTAMP | 是 | | 最后更新时间 |
| version | INT | 是 | 1 | 配置版本号 (用于变更追溯与回滚) |

- **主键**：id
- **外键**：（无）
- **索引**：（文档未提及）
- **约束**：metric_code UNIQUE NOT NULL

### 2.6 diagnosis_config（诊断指标配置）

**用途**：承载诊断指标（振荡检测 FFT、粘滞检测散点拟合、参数过激检测、质量码规则等）的可配置元数据。

| 字段名 | 类型 | 可空 | 默认值 | 注释 |
|---|---|---|---|---|
| id | UUID | 否 | | 诊断指标主键 |
| diag_code | VARCHAR(50) | 否 | | 诊断代码: `OSCILLATION_FFT`, `STICTION_SCATTER`, `OVERAGGRESSIVE`, `QUALITY_CODE` 等 |
| diag_name | VARCHAR(100) | 否 | | 诊断指标名称 (如: 振荡检测-FFT) |
| algorithm_type | VARCHAR(50) | 否 | | 算法类型 (如: FFT, SCATTER_FIT, THRESHOLD) |
| calc_method | VARCHAR(50) | 是 | | 计算方法枚举（10 项之一） |
| params | JSON | 是 | | 算法参数 (如: FFT 窗口长度、散点拟合阶数) |
| threshold | JSONB | 是 | | 诊断阈值对象（JSON，结构因算法而异，如 `{"similarity_threshold": 0.4}`） |
| is_enabled | BOOLEAN | 是 | TRUE | 是否启用 |
| updated_by | VARCHAR(50) | 是 | | 最后更新人 |
| updated_at | TIMESTAMP | 是 | | 最后更新时间 |
| version | INT | 是 | 1 | 配置版本号 |

- **主键**：id
- **外键**：（无）
- **索引**：（文档未提及）
- **约束**：diag_code UNIQUE NOT NULL

### 2.7 engine_rule（引擎规则配置）

**用途**：承载评估引擎/诊断引擎的计算周期、数据拉取规则、调度参数等可配置规则。

| 字段名 | 类型 | 可空 | 默认值 | 注释 |
|---|---|---|---|---|
| id | UUID | 否 | | 规则主键 |
| rule_code | VARCHAR(50) | 否 | | 规则代码 (如: `EVAL_CALC_CYCLE`, `DATA_FETCH_WINDOW`, `SCHEDULE_CONCURRENCY`) |
| rule_name | VARCHAR(100) | 否 | | 规则名称 (如: 评估计算周期) |
| rule_type | VARCHAR(20) | 否 | | 规则类型: `CALC_CYCLE`/`DATA_FETCH`/`SCHEDULE` |
| params | JSON | 是 | | 规则参数 (如: `{"cycle_minutes": 60, "concurrency": 16}`) |
| is_enabled | BOOLEAN | 是 | TRUE | 是否启用 |
| updated_by | VARCHAR(50) | 是 | | 最后更新人 |
| updated_at | TIMESTAMP | 是 | | 最后更新时间 |

- **主键**：id
- **外键**：（无）
- **索引**：（文档未提及）
- **约束**：rule_code UNIQUE NOT NULL

### 2.8 kpi_snapshot_hourly（每小时性能评估快照）

**用途**：承载每小时性能评估快照，包含 6 大 KPI、扩展指标、综合评分、算法版本、状态及数据血缘字段。好值率基于 PV 质量码统计。

| 字段名 | 类型 | 可空 | 默认值 | 注释 |
|---|---|---|---|---|
| id | UUID | 否 | | 快照主键 |
| loop_id | UUID | 是 | | 关联回路 ID |
| ts_start | TIMESTAMP | 否 | | 评估窗口起始时间 |
| ts_end | TIMESTAMP | 否 | | 评估窗口结束时间 |
| score | DECIMAL(5,2) | 是 | | 综合评分 (0-100) |
| good_value_rate | DECIMAL(5,2) | 是 | | 好值率 (%)，基于 PV 质量码统计 |
| auto_mode_rate | DECIMAL(5,2) | 是 | | 自控率 (%) |
| steady_rate | DECIMAL(5,2) | 是 | | 稳定率 (%) |
| accuracy_rate | DECIMAL(5,2) | 是 | | 准确率 (%)，衡量 PV 达到 SP 的准确程度 |
| oscillation_rate | DECIMAL(5,2) | 是 | | 振荡率 (%) |
| saturation_rate | DECIMAL(5,2) | 是 | | 饱和率 (%)，统计 OP 处于限位的时长占比 |
| fast_rate | DECIMAL(5,2) | 是 | | 快速率 (%)，反映回路响应速度是否过快 [v4.0 新增] |
| effective_auto_rate | DECIMAL(5,2) | 是 | | 有效自控率 (%)，扣除 PV 质量异常后的自控率 [v4.0 新增] |
| stiction_index | DECIMAL(5,2) | 是 | | 粘滞系数 (%)，阀门粘滞量化指标 [v4.0 新增] |
| output_trip_index | DECIMAL(5,2) | 是 | | 输出值行程指数 [v4.0 新增] |
| settling_time | DECIMAL(8,2) | 是 | | 实际稳态时间（秒） [v4.0 新增] |
| ideal_settling_time | DECIMAL(8,2) | 是 | | 理想稳态时间（秒） [v4.0 新增] |
| algorithm_version | VARCHAR(50) | 是 | | 算法版本号（如 `KPI_CALC_v1.0`） |
| status | VARCHAR(20) | 否 | | 计算状态: `SUCCESS`/`INCONCLUSIVE`/`PARTIAL` |
| sampling_freq | VARCHAR(10) | 是 | | 数据采样频率（如 `1s`/`5s`） [v4.0 新增] |
| quality_policy | VARCHAR(30) | 是 | | 质量策略（如 `KEEP_ALL_WITH_VALIDITY`） [v4.0 新增] |
| valid_rate | DECIMAL(5,4) | 是 | | 有效数据率（0~1） [v4.0 新增] |
| confidence_level | CHAR(1) | 是 | | 指标可信度等级（`A`/`B`/`C`/`D`/`E`） [v4.0 新增] |
| data_lineage | JSONB | 是 | | 数据血缘 JSON [v4.0 新增] |

- **主键**：id
- **外键**：loop_id → loop_ledger.id
- **索引**：（文档未提及）
- **约束**：（文档未提及）

### 2.9 action_tracker（轻量级异常追踪记录）

**用途**：v3.0 中 Action Tracker 降级为诊断中心子模块，表结构保持不变。跟踪诊断后的处理状态。

| 字段名 | 类型 | 可空 | 默认值 | 注释 |
|---|---|---|---|---|
| id | UUID | 否 | | 追踪记录主键 |
| loop_id | UUID | 是 | | 关联回路 ID |
| diagnosis_label | VARCHAR(100) | 是 | | 自动预诊结论 (如: 疑似阀门粘滞) |
| action_status | VARCHAR(20) | 否 | 'PENDING' | 处理状态: `PENDING`/`IN_PROGRESS`/`IGNORED`/`RESOLVED` |
| evidence_url | VARCHAR(255) | 是 | | 导出的《诊断建议书》PDF S3 存储路径 |
| updated_by | VARCHAR(50) | 是 | | 最后操作人 (仪控工程师) |
| updated_at | TIMESTAMP | 是 | | 状态变更时间戳 |

- **主键**：id
- **外键**：loop_id → loop_ledger.id
- **索引**：（文档未提及）
- **约束**：（文档未提及）

### 2.10 diagnosis_result（诊断结果表）

**用途**：承载诊断引擎对回路的自动预诊结果，包括预诊标签、置信度、特征值、证据链引用及算法版本号。

| 字段名 | 类型 | 可空 | 默认值 | 注释 |
|---|---|---|---|---|
| id | UUID | 否 | | 诊断结果主键 |
| loop_id | UUID | 是 | | 关联回路 ID |
| diag_label | VARCHAR(100) | 是 | | 预诊标签（如：疑似阀门粘滞、参数过激、原因不明需人工介入） |
| confidence | DECIMAL(5,2) | 是 | | 置信度（0-100） |
| feature_values | JSON | 是 | | 特征值（FFT 主频、散点拟合参数等） |
| evidence_chain | JSON | 是 | | 证据链引用（波形时间段、散点图数据引用等） |
| algorithm_version | VARCHAR(50) | 是 | | 算法版本号 |
| diagnosed_at | TIMESTAMP | 否 | | 诊断时间 |

- **主键**：id
- **外键**：loop_id → loop_ledger.id
- **索引**：（文档未提及）
- **约束**：（文档未提及）

### 2.11 tuning_record（整定记录）

**用途**：承载回路整定任务记录，包括模型辨识参数、推荐 PID 参数、仿真结果与效果对比。Phase 1 仅建表，Phase 2 实现算法。

| 字段名 | 类型 | 可空 | 默认值 | 注释 |
|---|---|---|---|---|
| id | UUID | 否 | | 整定记录主键 |
| loop_id | UUID | 否 | | 关联回路 ID |
| model_type | VARCHAR(20) | 否 | | 模型类型: `FOPDT`/`SOPDT`/`IPDT` |
| model_params | JSON | 是 | | 模型参数 (如: `{"K": 1.2, "T": 30.5, "tau": 5.0}`) |
| fitting_score | DECIMAL(5,2) | 是 | | 模型拟合度评分 (0-100)，即 R² × 100 |
| algorithm | VARCHAR(50) | 否 | | 整定算法: `IMC`/`LAMBDA`/`ZN`/`COHEN_COON`/`SIMC` |
| recommended_pid | JSON | 是 | | 推荐 PID 参数 (如: `{"P": 1.5, "I": 0.8, "D": 0.2}`) |
| simulation_result | JSON | 是 | | 闭环仿真结果 (含阶跃响应曲线、性能指标对比) |
| algorithm_version | VARCHAR(50) | 是 | | 算法版本号（如 `FOPDT_ID_v1.0`） |
| status | VARCHAR(20) | 否 | | 整定状态: `PENDING`/`IDENTIFIED`/`SIMULATED`/`APPLIED`/`VERIFIED` |
| created_by | VARCHAR(50) | 是 | | 创建人 |
| created_at | TIMESTAMP | 否 | | 创建时间 |

- **主键**：id
- **外键**：loop_id → loop_ledger.id
- **索引**：（文档未提及）
- **约束**：（文档未提及）

### 2.12 report_record（自动报表记录）

**用途**：承载系统按班/日/周/月自动生成的《控制回路性能评估报告》归档记录。

| 字段名 | 类型 | 可空 | 默认值 | 注释 |
|---|---|---|---|---|
| id | UUID | 否 | | 报表记录主键 |
| report_period | VARCHAR(20) | 否 | | 报表周期: `SHIFT`/`DAILY`/`WEEKLY`/`MONTHLY` |
| generated_at | TIMESTAMP | 否 | | 生成时间 |
| status | VARCHAR(20) | 否 | | 生成状态: `PROCESSING`/`COMPLETED`/`FAILED` |
| file_url | VARCHAR(255) | 是 | | 报表文件存储路径 (S3/MinIO) |
| created_at | TIMESTAMP | 否 | | 记录创建时间 |

- **主键**：id
- **外键**：（无）
- **索引**：（文档未提及）
- **约束**：（文档未提及）

### 2.13 sys_audit_log（系统审计日志）

**用途**：所有配置变更均落入本表，不可物理删除。

| 字段名 | 类型 | 可空 | 默认值 | 注释 |
|---|---|---|---|---|
| id | UUID | 否 | | 日志主键 |
| operator | VARCHAR(50) | 否 | | 操作人 |
| operation_type | VARCHAR(50) | 否 | | 操作类型 (如: `METRIC_CONFIG_UPDATE`, `ROLE_ASSIGN`, `LOOP_CREATE`) |
| target_type | VARCHAR(50) | 是 | | 操作对象类型 (如: `loop_ledger`, `metric_config`) |
| target_id | VARCHAR(36) | 是 | | 操作对象 ID |
| before_value | TEXT | 是 | | 变更前值 (JSON 序列化) |
| after_value | TEXT | 是 | | 变更后值 (JSON 序列化) |
| operated_at | TIMESTAMP | 否 | | 操作时间 |

- **主键**：id
- **外键**：（无）
- **索引**：（文档未提及）
- **约束**：（文档未提及）

### 2.14 kpi_snapshot_custom（自定义评估任务快照）

**用途**：承载用户自定义评估任务的 KPI 快照结果。自定义任务由用户按需触发，**不参与装置级聚合**。

| 字段名 | 类型 | 可空 | 默认值 | 注释 |
|---|---|---|---|---|
| id | UUID | 否 | gen_random_uuid() | 快照主键 |
| task_id | UUID | 否 | | 自定义任务 ID |
| loop_id | UUID | 否 | | 关联回路 ID |
| ts_start | TIMESTAMP | 否 | | 评估窗口起始时间 |
| ts_end | TIMESTAMP | 否 | | 评估窗口结束时间 |
| score | DECIMAL(5,2) | 是 | | 综合评分 (0-100) |
| accuracy_rate | DECIMAL(5,2) | 是 | | 准确率 (%) |
| fast_rate | DECIMAL(5,2) | 是 | | 快速率 (%) |
| steady_rate | DECIMAL(5,2) | 是 | | 稳定率 (%) [v4.1 修正：原 `stability_rate` 已统一为 `steady_rate`] |
| effective_auto_rate | DECIMAL(5,2) | 是 | | 有效自控率 (%) |
| good_value_rate | DECIMAL(5,2) | 是 | | 好值率 (%) |
| oscillation_rate | DECIMAL(5,2) | 是 | | 振荡率 (%) |
| saturation_rate | DECIMAL(5,2) | 是 | | 饱和率 (%) |
| stiction_index | DECIMAL(5,2) | 是 | | 粘滞系数 (%) |
| output_trip_index | DECIMAL(5,2) | 是 | | 输出值行程指数 |
| settling_time | DECIMAL(8,2) | 是 | | 实际稳态时间（秒） |
| ideal_settling_time | DECIMAL(8,2) | 是 | | 理想稳态时间（秒） |
| auto_mode_rate | DECIMAL(5,2) | 是 | | 自控率 (%) |
| algorithm_version | VARCHAR(50) | 是 | | 算法版本号 |
| status | VARCHAR(20) | 是 | | 计算状态: `SUCCESS`/`INCONCLUSIVE`/`PARTIAL` |
| confidence_level | CHAR(1) | 是 | | 指标可信度等级（`A`/`B`/`C`/`D`/`E`） |
| valid_rate | DECIMAL(5,4) | 是 | | 有效数据率（0~1） |
| data_lineage | JSONB | 是 | | 数据血缘 JSON |
| created_at | TIMESTAMP | 是 | NOW() | 记录创建时间 |

- **主键**：id
- **外键**：loop_id → loop_ledger.id
- **索引**：（文档未提及）
- **约束**：UNIQUE(task_id, loop_id)

### 2.15 clpm_metric_data_requirement（指标数据需求契约）

**用途**：承载每个性能/诊断指标对底层数据的契约化需求声明，是算法服务与数据采集层之间的"数据契约"，支撑数据血缘追溯与指标可信度判定。

| 字段名 | 类型 | 可空 | 默认值 | 注释 |
|---|---|---|---|---|
| id | UUID | 否 | gen_random_uuid() | 契约主键 |
| metric_code | VARCHAR(50) | 否 | | 指标代码（如 `GOOD_VALUE_RATE`、`OSCILLATION_RATE`） |
| tag_group | VARCHAR(20) | 否 | | 所需 Tag 组（如 `PV`、`PV_SP_OP_MODE`、`PV_SP_OP`） |
| tags | JSONB | 否 | | 所需 Tag 角色列表（如 `["PV","SP","OP","MODE"]`） |
| sampling_strategy | VARCHAR(30) | 是 | | 采样策略（如 `RAW_1S`、`RAW_5S`、`DOWNSAMPLE_1MIN`） |
| quality_policy | VARCHAR(30) | 是 | | 质量策略（如 `KEEP_ALL_WITH_VALIDITY`） |
| mask_expression | VARCHAR(200) | 是 | | Metric Validity Mask 表达式（如 `pv_quality==1 && mode==1`） |
| aggregation_policy | VARCHAR(20) | 是 | | 聚合策略（如 `MEAN`、`RATIO`、`RMS`、`PERCENTILE`） |
| depends_on | JSONB | 是 | | 依赖的其他指标或数据块（如 `["GOOD_VALUE_RATE"]`） |
| version | VARCHAR(20) | 是 | 'v1' | 契约版本号 |
| updated_at | TIMESTAMP | 是 | NOW() | 最后更新时间 |

- **主键**：id
- **外键**：（无）
- **索引**：（文档未提及）
- **约束**：metric_code UNIQUE NOT NULL

### 2.16 diagnosis_tag（诊断标签表）

**用途**：承载回路级的诊断标签记录，用于故障定位和告警。与 `diagnosis_result` 互补：`diagnosis_result` 存储完整诊断证据链，`diagnosis_tag` 存储可枚举、可查询、可状态流转的标签实例。

| 字段名 | 类型 | 可空 | 默认值 | 注释 |
|---|---|---|---|---|
| id | UUID | 否 | gen_random_uuid() | 标签主键 |
| loop_id | UUID | 否 | | 关联回路 ID |
| tag_code | VARCHAR(50) | 否 | | 标签代码（如 `OSCILLATION`、`VALVE_STICTION`、`OUTPUT_SATURATION`、`QUALITY_ABNORMAL`） |
| severity | VARCHAR(20) | 否 | | 严重等级（如 `INFO`、`WARNING`、`CRITICAL`） |
| source_metric | VARCHAR(50) | 是 | | 触发该标签的来源指标代码（如 `OSCILLATION_RATE`） |
| trigger_condition | JSONB | 是 | | 触发条件（如 `{"threshold": 0.4, "window_minutes": 60}`） |
| triggered_at | TIMESTAMP | 否 | NOW() | 标签触发时间 |
| resolved_at | TIMESTAMP | 是 | | 标签解除时间 |
| status | VARCHAR(20) | 是 | 'ACTIVE' | 标签状态: `ACTIVE`/`RESOLVED`/`IGNORED` |

- **主键**：id
- **外键**：loop_id → loop_ledger.id
- **索引**：（文档未提及）
- **约束**：（文档未提及）

### 2.17 unit_kpi_summary（装置级 KPI 汇总表）

**用途**：承载装置级 KPI 汇总快照，按周期对装置下所有参评回路（`include_in_evaluation=TRUE` 且回路 KPI 快照 `status ≠ INCONCLUSIVE`）的 `kpi_snapshot_hourly` 进行聚合。聚合权重按 `loop_ledger.importance_level` 映射（一级=3、二级=2、三级=1）。

| 字段名 | 类型 | 可空 | 默认值 | 注释 |
|---|---|---|---|---|
| id | UUID | 否 | gen_random_uuid() | 汇总主键 |
| node_id | UUID | 否 | | 装置节点 ID |
| snapshot_time | TIMESTAMP | 否 | | 汇总快照时间（与聚合窗口对齐） |
| avg_score | DECIMAL(5,2) | 是 | | 装置级综合性能评分（按 `importance_level` 权重加权聚合） |
| auto_mode_rate | DECIMAL(5,2) | 是 | | 装置级平均自控率（加权聚合） |
| effective_auto_rate | DECIMAL(5,2) | 是 | | 装置级有效自控率（加权聚合） |
| stability_rate | DECIMAL(5,2) | 是 | | 装置级稳定率（参评回路 `steady_rate` 加权聚合） |
| accuracy_rate | DECIMAL(5,2) | 是 | | 装置级准确率（加权聚合） |
| fast_rate | DECIMAL(5,2) | 是 | | 装置级快速率（加权聚合） |
| good_value_rate | DECIMAL(5,2) | 是 | | 装置级好值率（加权聚合） |
| oscillation_rate | DECIMAL(5,2) | 是 | | 装置级振荡率（加权聚合） |
| saturation_rate | DECIMAL(5,2) | 是 | | 装置级饱和率（加权聚合） |
| total_loops | INTEGER | 是 | | 装置下回路总数 |
| evaluated_loops | INTEGER | 是 | | 实际参与评估的回路数（`include_in_evaluation=TRUE` 且 `status=SUCCESS`） |
| inconclusive_loops | INTEGER | 是 | | INCONCLUSIVE 状态回路数 |
| excluded_loops | INTEGER | 是 | | 不参评回路数（`include_in_evaluation=FALSE`） [v4.1 新增] |
| status | VARCHAR(20) | 否 | 'SUCCESS' | 聚合状态: `SUCCESS`/`PARTIAL`/`EMPTY` [v4.1 新增] |
| algorithm_version | VARCHAR(50) | 是 | | 聚合算法版本号 |
| created_at | TIMESTAMP | 是 | NOW() | 记录创建时间 |

- **主键**：id
- **外键**：node_id → plant_node.id
- **索引**：（文档未提及）
- **约束**：UNIQUE(node_id, snapshot_time)

---

## 3. TDengine 超级表清单

### 3.1 st_loop_data（控制回路时序数据超级表）

**用途**：定义所有控制回路时序数据的标准 Schema，覆盖 7 个 OPC Tag 的原始秒级数据及 PV 质量码。

**Tag 列**：

| 标签名 | 类型 | 说明 |
|---|---|---|
| loop_id | BINARY(36) | 关联关系库的 loop_ledger.id |
| unit_id | BINARY(36) | 关联的单元 ID，用于按单元降采样聚合 |

**Field 列**：

| 字段名 | 类型 | 说明 | 约束 |
|---|---|---|---|
| ts | TIMESTAMP | 采样时间戳 | 主键 (时间列) |
| pv | FLOAT | 过程变量测量值 (来自 PV Tag) | |
| sp | FLOAT | 设定值 (来自 SP Tag) | |
| op | FLOAT | 控制器输出值 (0-100，来自 OP Tag) | |
| mode | TINYINT | 控制模式 (0=Manual, 1=Auto, 2=Cascade，来自 MODE Tag) | |
| pid_p | FLOAT | 比例参数 (来自 PID_P Tag，只读) | |
| pid_i | FLOAT | 积分参数 (来自 PID_I Tag，只读) | |
| pid_d | FLOAT | 微分参数 (来自 PID_D Tag，只读) | |
| pv_quality | TINYINT | PV 数据质量码 (0=Bad, 1=Good, 2=Uncertain) | |

**子表命名规则**：`d_loop_<位号去分隔符小写>`，如位号 `101-FC-1023` 对应子表 `d_loop_101_fc_1023`。

**子表创建示例**：
```sql
CREATE TABLE d_loop_101_fc_1023 USING st_loop_data TAGS ('uuid-xxx', 'uuid-yyy');
```

---

## 4. 枚举值定义

### 4.1 plant_node.type（节点类型）

| 取值 | 中文显示名 | 业务含义 |
|---|---|---|
| `FACTORY` | 工厂 | 工厂级节点 |
| `UNIT` | 装置 | 装置级节点 |
| `EQUIPMENT` | 设备 | 设备级节点 |

### 4.2 loop_ledger.status（回路状态）

| 取值 | 中文显示名 | 业务含义 |
|---|---|---|
| `READY` | 就绪 | PV/SP/OP/MODE 四个必填 Tag 全部关联成功，回路进入评估流程 |
| `PARTIAL` | 部分就绪 | 必填 Tag 缺失，回路标红提示，不参与评估计算 |
| `INACTIVE` | 未启用 | `is_active=FALSE`，回路被手动停用，不参与评估计算 |

### 4.3 loop_ledger.control_type（控制类型，[v4.1 新增]）

| 取值 | 中文显示名 | 业务含义 |
|---|---|---|
| `STABLE` | 稳定型 | 温度、压力控制 |
| `SLOW` | 慢速型 | 缓慢调节回路 |
| `FAST` | 快速型 | 副回路、流量控制 |
| `LOGIC` | 逻辑型 | 防回流、防超温 |

### 4.4 loop_ledger.importance_level（重要等级，[v4.1 新增]）

| 取值 | 中文显示名 | 业务含义 |
|---|---|---|
| `1` | 一级 | 装置级聚合权重 w_level = 3 |
| `2` | 二级 | 装置级聚合权重 w_level = 2 |
| `3` | 三级 | 装置级聚合权重 w_level = 1 |

### 4.5 tag_registry.tag_type（Tag 类型）

| 取值 | 中文显示名 | 业务含义 |
|---|---|---|
| `PV` | 过程变量 | 过程变量测量值 |
| `SP` | 设定值 | 设定值 |
| `OP` | 控制器输出 | 控制器输出值 |
| `MODE` | 控制模式 | 控制模式 |
| `PID_P` | 比例参数 | PID 比例参数 |
| `PID_I` | 积分参数 | PID 积分参数 |
| `PID_D` | 微分参数 | PID 微分参数 |
| `OTHER` | 其他 | 其他类型 |

### 4.6 tag_registry.quality（数据质量码）

| 取值 | 中文显示名 | 业务含义 |
|---|---|---|
| `GOOD` | 好 | 数据质量良好 |
| `BAD` | 坏 | 数据质量差 |
| `UNCERTAIN` | 不确定 | 数据质量不确定 |

### 4.7 loop_tag_mapping.tag_role（Tag 角色）

| 取值 | 中文显示名 | 业务含义 |
|---|---|---|
| `PV` | 过程变量 | 过程变量 |
| `SP` | 设定值 | 设定值 |
| `OP` | 控制器输出 | 控制器输出 |
| `MODE` | 控制模式 | 控制模式 |
| `PID_P` | 比例参数 | 比例参数 |
| `PID_I` | 积分参数 | 积分参数 |
| `PID_D` | 微分参数 | 微分参数 |

### 4.8 metric_config.metric_code（指标代码，12 项）

| 取值 | 中文显示名 | 业务含义 |
|---|---|---|
| `GOOD_VALUE_RATE` | 好值率 | 基于 PV 质量码统计，国标附录 F.6 (Qu) |
| `AUTO_MODE_RATE` | 自控率 | 模式统计，国标附录 B.1 (Auto) |
| `STEADY_RATE` | 稳定率 | 偏差标准差，国标附录 B.5 (S) |
| `ACCURACY_RATE` | 准确率 | 偏差绝对均值，国标附录 B.3 (A) |
| `OSCILLATION_RATE` | 振荡率 | IAE 零交叉，国标附录 F.1 (Osc) |
| `SATURATION_RATE` | 饱和率 | 输出限位统计，国标附录 F.3 (Sa) |
| `FAST_RATE` | 快速率 | 反映回路响应速度是否过快 |
| `EFFECTIVE_AUTO_RATE` | 有效自控率 | 扣除 PV 质量异常后的自控率 |
| `STICTION_INDEX` | 粘滞系数 | 阀门粘滞量化指标 |
| `OUTPUT_TRIP_INDEX` | 输出值行程指数 | 反映 OP 行程范围特征 |
| `SETTLING_TIME` | 实际稳态时间 | 扰动后达到稳态所需时长 |
| `IDEAL_SETTLING_TIME` | 理想稳态时间 | 按对象特性计算的期望稳态时间 |

### 4.9 metric_config 性能定级 5 级（grading_thresholds，[v4.1 新增]）

| 取值 | 中文显示名 | 业务含义 | 评分区间 |
|---|---|---|---|
| `EXCELLENT` | 一级 | 优秀 | 90 ≤ P < 100，绿色 |
| `GOOD` | 二级 | 良好 | 80 ≤ P < 90，蓝色 |
| `FAIR` | 三级 | 一般 | 70 ≤ P < 80，黄色 |
| `WARNING` | 四级 | 警告 | 60 ≤ P < 70，橙色 |
| `POOR` | 五级 | 差 | 0 ≤ P < 60，红色 |

### 4.10 diagnosis_config.diag_code（诊断代码）

| 取值 | 中文显示名 | 业务含义 |
|---|---|---|
| `OSCILLATION_FFT` | 振荡检测-FFT | 振荡检测（FFT 频域法） |
| `STICTION_SCATTER` | 粘滞检测-散点拟合 | 阀门粘滞检测（散点拟合） |
| `OVERAGGRESSIVE` | 参数过激检测 | 参数过激检测 |
| `QUALITY_CODE` | 质量码规则诊断 | PV 质量码规则诊断 |
| 等其他 | （文档未完整列出） | 其他诊断代码 |

### 4.11 diagnosis_config.calc_method（计算方法，10 项）

| 取值 | 中文显示名 | 业务含义 | 对应诊断标签 |
|---|---|---|---|
| `IAE_ZERO_CROSSING` | IAE 零交叉相似率法（Hägglund） | 振荡检测 | OSCILLATION |
| `FFT_WELCH` | FFT 频域法（Welch 法 PSD） | 振荡检测 | OSCILLATION |
| `CHOUDHURY_NGI_NLI` | Choudhury NGI/NLI + 椭圆拟合 | 阀门粘滞检测 | VALVE_STICTION |
| `KANO_STATISTICAL` | Kano 统计特性法 | 阀门粘滞检测 | VALVE_STICTION |
| `EXPERT_RULE` | 专家规则矩阵（多算法融合） | 人工复核 | MANUAL_REVIEW |
| `STEP_RESPONSE_ANALYSIS` | 阶跃响应分析（超调量/衰减比） | 参数过激检测 | OVERAGGRESSIVE |
| `SETTLING_TIME_ANALYSIS` | 收敛时间与 IAE 累积分析 | 参数过保守检测 | OVERCONSERVATIVE |
| `DISTURBANCE_FREQUENCY` | 偏差突变频率统计 | 外部扰动检测 | EXTERNAL_DISTURBANCE |
| `QUALITY_CODE_RULE` | PV 质量码规则诊断 | PV 质量异常 | QUALITY_ABNORMAL |
| `OP_SATURATION_STAT` | OP 限位统计 | 输出饱和 | OUTPUT_SATURATION |

### 4.12 engine_rule.rule_type（规则类型）

| 取值 | 中文显示名 | 业务含义 |
|---|---|---|
| `CALC_CYCLE` | 计算周期 | 评估计算周期配置 |
| `DATA_FETCH` | 数据拉取 | 数据拉取规则配置 |
| `SCHEDULE` | 调度参数 | 调度并发参数 |

### 4.13 kpi_snapshot_hourly.status（计算状态）

| 取值 | 中文显示名 | 业务含义 |
|---|---|---|
| `SUCCESS` | 成功 | 正常计算完成 |
| `INCONCLUSIVE` | 不可判定 | 好值率 < 20%，KPI 留空 |
| `PARTIAL` | 部分失败 | 部分 KPI 计算失败 |

### 4.14 confidence_level（指标可信度等级，A/B/C/D/E）

| 取值 | valid_rate 区间 | 中文显示名 | 业务含义 |
|---|---|---|---|
| `A` | >= 0.95 | 高可信 | 可直接用于评分、聚合与对外展示 |
| `B` | 0.80 ~ 0.95 | 较可信 | 可用于评分，聚合时标注 |
| `C` | 0.60 ~ 0.80 | 一般可信 | 可用于评分，但聚合降权 |
| `D` | 0.20 ~ 0.60 | 低可信 | 评分仍计算，但需人工复核 |
| `E` | < 0.20 | 不可信 | 触发 `INCONCLUSIVE`，KPI 留空 |

### 4.15 action_tracker.action_status（处理状态）

| 取值 | 中文显示名 | 业务含义 |
|---|---|---|
| `PENDING` | 待处理 | 默认状态，等待处理 |
| `IN_PROGRESS` | 处理中 | 正在处理中 |
| `IGNORED` | 已忽略 | 已忽略不处理 |
| `RESOLVED` | 已实施 | 已实施完成 |

### 4.16 diagnosis_tag.tag_code（诊断标签代码）

| 取值 | 中文显示名 | 业务含义 |
|---|---|---|
| `OSCILLATION` | 振荡 | 回路存在振荡 |
| `VALVE_STICTION` | 阀门粘滞 | 阀门存在粘滞 |
| `OUTPUT_SATURATION` | 输出饱和 | OP 输出饱和 |
| `QUALITY_ABNORMAL` | PV 质量异常 | PV 质量码异常 |
| `OVERAGGRESSIVE` | 参数过激 | （文档未明确列出，对应诊断标签） |
| `OVERCONSERVATIVE` | 参数过保守 | （文档未明确列出，对应诊断标签） |
| `EXTERNAL_DISTURBANCE` | 外部扰动 | （文档未明确列出，对应诊断标签） |
| `MANUAL_REVIEW` | 人工复核 | （文档未明确列出，对应诊断标签） |

### 4.17 diagnosis_tag.severity（严重等级）

| 取值 | 中文显示名 | 业务含义 |
|---|---|---|
| `INFO` | 提示 | 信息级提示 |
| `WARNING` | 警告 | 警告级 |
| `CRITICAL` | 严重 | 严重级 |

### 4.18 diagnosis_tag.status（标签状态）

| 取值 | 中文显示名 | 业务含义 |
|---|---|---|
| `ACTIVE` | 生效中 | 标签生效中 |
| `RESOLVED` | 已解除 | 标签已解除 |
| `IGNORED` | 已忽略 | 标签已忽略 |

### 4.19 diagnosis_result.diag_label（诊断预诊标签，8 类）

| 取值 | 中文显示名 | 业务含义 |
|---|---|---|
| `OSCILLATION` | 振荡 | 回路振荡 |
| `VALVE_STICTION` | 阀门粘滞 | 阀门粘滞 |
| `OVERAGGRESSIVE` | 参数过激 | 参数过激 |
| `OVERCONSERVATIVE` | 参数过保守 | 参数过保守 |
| `EXTERNAL_DISTURBANCE` | 外部扰动 | 外部扰动 |
| `QUALITY_ABNORMAL` | PV 质量异常 | PV 质量异常 |
| `OUTPUT_SATURATION` | 输出饱和 | 输出饱和 |
| `MANUAL_REVIEW` | 人工介入 | 原因不明需人工介入 |

### 4.20 tuning_record.model_type（模型类型）

| 取值 | 中文显示名 | 业务含义 |
|---|---|---|
| `FOPDT` | 一阶惯性加纯滞后 | 一阶惯性加纯滞后模型 |
| `SOPDT` | 二阶惯性加纯滞后 | 二阶惯性加纯滞后模型 |
| `IPDT` | 积分加纯滞后 | 积分加纯滞后模型 |

### 4.21 tuning_record.algorithm（整定算法）

| 取值 | 中文显示名 | 业务含义 |
|---|---|---|
| `IMC` | 内模控制 | 内模控制整定 |
| `LAMBDA` | Lambda 法 | Lambda 整定 |
| `ZN` | Ziegler-Nichols | Ziegler-Nichols 整定 |
| `COHEN_COON` | Cohen-Coon | Cohen-Coon 整定 |
| `SIMC` | SIMC | SIMC 整定 |

### 4.22 tuning_record.status（整定状态）

| 取值 | 中文显示名 | 业务含义 |
|---|---|---|
| `PENDING` | 待开始 | 整定任务待开始 |
| `IDENTIFIED` | 已辨识 | 模型辨识完成 |
| `SIMULATED` | 已仿真 | 闭环仿真完成 |
| `APPLIED` | 已应用 | 参数已应用 |
| `VERIFIED` | 已验证 | 应用效果已验证 |

### 4.23 report_record.report_period（报表周期）

| 取值 | 中文显示名 | 业务含义 |
|---|---|---|
| `SHIFT` | 班 | 班报表 |
| `DAILY` | 日 | 日报表 |
| `WEEKLY` | 周 | 周报表 |
| `MONTHLY` | 月 | 月报表 |

### 4.24 report_record.status（生成状态）

| 取值 | 中文显示名 | 业务含义 |
|---|---|---|
| `PROCESSING` | 生成中 | 报表正在生成 |
| `COMPLETED` | 成功 | 报表生成成功 |
| `FAILED` | 失败 | 报表生成失败 |

### 4.25 unit_kpi_summary.status（聚合状态，[v4.1 新增]）

| 取值 | 中文显示名 | 业务含义 |
|---|---|---|
| `SUCCESS` | 成功 | 有参评回路且全部 SUCCESS |
| `PARTIAL` | 部分 | 部分回路 INCONCLUSIVE |
| `EMPTY` | 空 | 装置内无参评回路 |

### 4.26 TDengine pv_quality（PV 数据质量码）

| 取值 | 中文显示名 | 业务含义 |
|---|---|---|
| `0` | Bad | 数据质量差 |
| `1` | Good | 数据质量良好 |
| `2` | Uncertain | 数据质量不确定 |

### 4.27 TDengine mode（控制模式）

| 取值 | 中文显示名 | 业务含义 |
|---|---|---|
| `0` | Manual | 手动模式 |
| `1` | Auto | 自动模式 |
| `2` | Cascade | 串级模式 |

---

## 5. 数据血缘字段

### 5.1 数据血缘字段汇总（实际为 5 个独立字段）

> **重要说明**：用户预期"数据血缘 8 字段"，但 DDS v4.1 §5.1 明确指出 `kpi_snapshot_hourly` 与 `kpi_snapshot_custom` 均包含 **5 个** 数据血缘字段（`sampling_freq`、`quality_policy`、`valid_rate`、`confidence_level`、`data_lineage`）。其中 `data_lineage`（JSONB）内部包含 6 个子字段。因此若以"独立字段 + data_lineage 子字段"计算，共 4 + 6 = 10 个；若仅算独立字段则为 5 个。文档未提及"8 字段"概念。

| 字段名 | 类型 | 用途 |
|---|---|---|
| `sampling_freq` | VARCHAR(10) | 记录本次快照输入数据的实际采样频率（如 `1s`/`5s`），支撑采样率一致性校验与跨快照对比 |
| `quality_policy` | VARCHAR(30) | 记录本次计算采用的质量策略（默认 `KEEP_ALL_WITH_VALIDITY`），与 `clpm_metric_data_requirement.quality_policy` 对齐 |
| `valid_rate` | DECIMAL(5,4) | 记录评估窗口内满足各指标 Metric Validity Mask 的数据占比（0~1），是判定 `confidence_level` 的核心输入 |
| `confidence_level` | CHAR(1) | 指标可信度等级（A/B/C/D/E），由 `valid_rate` 与样本量综合判定 |
| `data_lineage` | JSONB | 存储完整数据血缘信息（结构见下方 5.2） |

### 5.2 data_lineage JSON 内部结构（6 个子字段）

| 子字段名 | 用途 |
|---|---|
| `tag_group` | 本次计算使用的 Tag 组（与 `clpm_metric_data_requirement.tag_group` 对齐） |
| `data_block_ids` | 参与计算的数据块 ID 列表，支撑追溯到 TDengine 原始数据段 |
| `aggregation_policy` | 聚合策略（与 `clpm_metric_data_requirement.aggregation_policy` 对齐） |
| `metric_mask_refs` | 各指标使用的 Metric Validity Mask 引用，支撑指标级可追溯 |
| `source_table` | 数据来源超级表名 |
| `subtable` | 数据来源子表名 |

### 5.3 data_lineage 典型结构示例

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

---

## 6. 数据模型关系图

### 6.1 关系图（文字描述）

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

### 6.2 表关系列表

| 关系类型 | 表 A | 表 B | 关系说明 |
|---|---|---|---|
| 一对多 | plant_node | loop_ledger | 一个工厂节点（装置）下有多个回路；通过 `loop_ledger.unit_id` 关联 |
| 一对多 | plant_node | plant_node | 自引用层级关系；通过 `parent_id` 关联 |
| 一对多 | loop_ledger | loop_tag_mapping | 一个回路关联多个 Tag；通过 `loop_id` 关联 |
| 多对一 | loop_tag_mapping | tag_registry | 多个关联记录指向同一个 Tag；通过 `tag_id` 关联 |
| 一对多 | loop_ledger | kpi_snapshot_hourly | 一个回路有多条每小时快照；通过 `loop_id` 关联 |
| 一对多 | loop_ledger | kpi_snapshot_custom | 一个回路有多条自定义任务快照；通过 `loop_id` 关联 |
| 一对多 | loop_ledger | diagnosis_result | 一个回路有多条诊断结果；通过 `loop_id` 关联 |
| 一对多 | loop_ledger | action_tracker | 一个回路有多条异常追踪记录；通过 `loop_id` 关联 |
| 一对多 | loop_ledger | tuning_record | 一个回路有多条整定记录；通过 `loop_id` 关联 |
| 一对多 | loop_ledger | diagnosis_tag | 一个回路有多条诊断标签；通过 `loop_id` 关联 |
| 一对多 | plant_node | unit_kpi_summary | 一个装置节点有多条汇总快照；通过 `node_id` 关联 |
| 独立 | metric_config | （无外键） | 独立配置表 |
| 独立 | diagnosis_config | （无外键） | 独立配置表 |
| 独立 | engine_rule | （无外键） | 独立配置表 |
| 独立 | report_record | （无外键） | 独立记录表 |
| 独立 | sys_audit_log | （无外键） | 独立日志表 |
| 独立 | clpm_metric_data_requirement | （无外键） | 独立契约表（与 `kpi_snapshot_hourly.data_lineage` 逻辑关联） |

### 6.3 外键关系清单（保持不变）

- `loop_ledger.unit_id` → `plant_node.id`
- `loop_tag_mapping.loop_id` → `loop_ledger.id`
- `loop_tag_mapping.tag_id` → `tag_registry.id`
- `kpi_snapshot_hourly.loop_id` → `loop_ledger.id`
- `kpi_snapshot_custom.loop_id` → `loop_ledger.id`
- `diagnosis_result.loop_id` → `loop_ledger.id`
- `action_tracker.loop_id` → `loop_ledger.id`
- `tuning_record.loop_id` → `loop_ledger.id`
- `diagnosis_tag.loop_id` → `loop_ledger.id`
- `unit_kpi_summary.node_id` → `plant_node.id`

---

## 7. KPI 相关表

### 7.1 kpi_snapshot_hourly（每小时性能评估快照）

**用途**：承载每小时性能评估快照（标准任务），是 KPI 计算的核心存储表，参与装置级聚合与日/周/月趋势分析。

**关键字段含义**：
- 6 大 KPI：`good_value_rate`（好值率）、`auto_mode_rate`（自控率）、`steady_rate`（稳定率）、`accuracy_rate`（准确率）、`oscillation_rate`（振荡率）、`saturation_rate`（饱和率）
- 扩展指标：`fast_rate`（快速率）、`effective_auto_rate`（有效自控率）、`stiction_index`（粘滞系数）、`output_trip_index`（输出值行程指数）、`settling_time`（实际稳态时间）、`ideal_settling_time`（理想稳态时间）
- 综合评分：`score`（0-100）
- 状态：`status`（SUCCESS/INCONCLUSIVE/PARTIAL）
- 数据血缘：`sampling_freq`、`quality_policy`、`valid_rate`、`confidence_level`、`data_lineage`
- 算法版本：`algorithm_version`

### 7.2 kpi_snapshot_custom（自定义评估任务快照）

**用途**：承载用户自定义评估任务的 KPI 快照结果。**不参与装置级聚合**。结构与 `kpi_snapshot_hourly` 对齐，通过 `task_id` 区分独立任务。

**关键字段含义**：与 `kpi_snapshot_hourly` 同构，新增 `task_id` 字段。

### 7.3 unit_kpi_summary（装置级 KPI 汇总表）

**用途**：承载装置级 KPI 汇总快照，按周期对装置下所有参评回路（`include_in_evaluation=TRUE` 且回路 KPI 快照 `status ≠ INCONCLUSIVE`）的 `kpi_snapshot_hourly` 进行聚合。**仅基于标准任务，自定义任务不参与聚合**。

**关键字段含义**：
- 三大装置级 KPI：`avg_score`（综合性能评分）、`auto_mode_rate`（平均自控率）、`stability_rate`（稳定率，注意 loop-level 为 `steady_rate`，unit-level 聚合字段名为 `stability_rate`）
- 回路统计：`total_loops`（回路总数）、`evaluated_loops`（参评且 SUCCESS）、`inconclusive_loops`（参评但 INCONCLUSIVE）、`excluded_loops`（不参评） [v4.1 新增]
- 聚合状态：`status`（SUCCESS/PARTIAL/EMPTY） [v4.1 新增]

### 7.4 metric_config（性能指标配置）

**用途**：承载 6 大核心 KPI 及变体指标的可配置元数据，含权重、阈值、性能定级阈值等。

**关键字段含义**：
- `metric_code`：12 项指标代码
- `weight`：3 项核心指标 (A/F/S) 权重，按 control_type 分 4 套模板；总和须为 1.0
- `threshold`：阈值对象 JSONB
- `grading_thresholds`：性能定级 5 级阈值 [v4.1 新增]
- `formula`：~~已废弃~~（v5.0 起算法公式固化为独立函数模块）
- `control_type`：~~已迁移至 `loop_ledger.control_type`~~

### 7.5 clpm_metric_data_requirement（指标数据需求契约）

**用途**：承载每个性能/诊断指标对底层数据的契约化需求声明，是算法服务与数据采集层之间的"数据契约"，支撑数据血缘追溯与指标可信度判定。

**关键字段含义**：
- `metric_code`：指标代码
- `tag_group`：所需 Tag 组
- `tags`：所需 Tag 角色列表
- `sampling_strategy`：采样策略
- `quality_policy`：质量策略
- `mask_expression`：Metric Validity Mask 表达式
- `aggregation_policy`：聚合策略
- `depends_on`：依赖的其他指标或数据块

---

## 8. 任务跟踪相关表

### 8.1 action_tracker（轻量级异常追踪记录）

**用途**：v3.0 中 Action Tracker 降级为诊断中心子模块。跟踪诊断后的处理状态（待处理/处理中/已实施/已忽略）。

**关键字段含义**：
- `loop_id`：关联回路 ID
- `diagnosis_label`：自动预诊结论
- `action_status`：处理状态（PENDING/IN_PROGRESS/IGNORED/RESOLVED）
- `evidence_url`：诊断建议书 PDF S3 路径
- `updated_by`：最后操作人
- `updated_at`：状态变更时间戳

### 8.2 tuning_record（整定记录）

**用途**：承载回路整定任务记录，包括模型辨识参数、推荐 PID 参数、仿真结果与效果对比。Phase 1 仅建表，Phase 2 实现算法。

**关键字段含义**：
- `loop_id`：关联回路 ID
- `model_type`：模型类型（FOPDT/SOPDT/IPDT）
- `model_params`：模型参数 JSON
- `fitting_score`：模型拟合度评分（0-100）
- `algorithm`：整定算法（IMC/LAMBDA/ZN/COHEN_COON/SIMC）
- `recommended_pid`：推荐 PID 参数 JSON
- `simulation_result`：闭环仿真结果 JSON
- `algorithm_version`：整定算法版本号
- `status`：整定状态（PENDING/IDENTIFIED/SIMULATED/APPLIED/VERIFIED）

### 8.3 report_record（自动报表记录）

**用途**：承载系统按班/日/周/月自动生成的《控制回路性能评估报告》归档记录。

**关键字段含义**：
- `report_period`：报表周期（SHIFT/DAILY/WEEKLY/MONTHLY）
- `generated_at`：生成时间
- `status`：生成状态（PROCESSING/COMPLETED/FAILED）
- `file_url`：报表文件存储路径 (S3/MinIO)

---

## 9. 审计日志相关表

### 9.1 sys_audit_log（系统审计日志）

**用途**：所有配置变更（性能指标/诊断指标/引擎规则/角色分配等）均落入本表，不可物理删除。

**关键字段含义**：
- `operator`：操作人
- `operation_type`：操作类型（如 `METRIC_CONFIG_UPDATE`、`ROLE_ASSIGN`、`LOOP_CREATE`）
- `target_type`：操作对象类型（如 `loop_ledger`、`metric_config`）
- `target_id`：操作对象 ID
- `before_value`：变更前值（JSON 序列化）
- `after_value`：变更后值（JSON 序列化）
- `operated_at`：操作时间

**关联说明**：配置表（`metric_config`、`diagnosis_config`）通过 `version` 字段配合 `sys_audit_log` 实现变更追溯与回滚。

---

## 10. 引用的其他文档

| 文档名称 | 版本 | 引用位置/用途 |
|---|---|---|
| PRD（产品需求规范） | v3.1 | 设计依据（文档头部） |
| FDS（功能设计规范） | v5.1（表名/字段名权威基线） | 设计依据；v4.1 变更对齐 FDS v5.1 §5.2.3、§5.3.1.2、§5.3.7.1、§5.3.7.2、§5.3.7.3 |
| ADS（应用设计规范） | v3.1 | 设计依据；§1 存算分离原则；§8 算法服务架构；§12 算法版本管理 |
| 关键算法设计说明 | v2.0 | 设计依据；v4.0 变更对齐；§3.3 算法版本；§4/§5 算法实现；§6.1.5 模型拟合度；§10.1/§10.2/§10.3/§10.4 |
| GB/T 44693.2-2024（国标） | （未明确版本号） | §2.5 v4.1 变更引用；12 项指标算法已按 GB/T 44693.2-2024 固化为独立函数模块；6 大 KPI 国标对应（附录 B.1/B.3/B.5/F.1/F.3/F.6） |
| 关键算法设计说明 | v1.0（历史版本） | v3.1 变更对齐（已被 v2.0 取代） |

---

## 附录：表清单汇总

| 序号 | 表名 | 用途 | 类型 |
|---|---|---|---|
| 1 | plant_node | 工厂节点（工厂→装置→设备层级树） | PostgreSQL |
| 2 | loop_ledger | 回路台账 | PostgreSQL |
| 3 | tag_registry | AAS Tag 注册表 | PostgreSQL |
| 4 | loop_tag_mapping | 回路-Tag 关联 | PostgreSQL |
| 5 | metric_config | 性能指标配置 | PostgreSQL |
| 6 | diagnosis_config | 诊断指标配置 | PostgreSQL |
| 7 | engine_rule | 引擎规则配置 | PostgreSQL |
| 8 | kpi_snapshot_hourly | 每小时性能评估快照 | PostgreSQL |
| 9 | action_tracker | 轻量级异常追踪记录 | PostgreSQL |
| 10 | diagnosis_result | 诊断结果表 | PostgreSQL |
| 11 | tuning_record | 整定记录 | PostgreSQL |
| 12 | report_record | 自动报表记录 | PostgreSQL |
| 13 | sys_audit_log | 系统审计日志 | PostgreSQL |
| 14 | kpi_snapshot_custom | 自定义评估任务快照 [v4.0 新增] | PostgreSQL |
| 15 | clpm_metric_data_requirement | 指标数据需求契约 [v4.0 新增] | PostgreSQL |
| 16 | diagnosis_tag | 诊断标签表 [v4.0 新增] | PostgreSQL |
| 17 | unit_kpi_summary | 装置级 KPI 汇总表 [v4.0 新增] | PostgreSQL |
| 18 | st_loop_data | 控制回路时序数据超级表 | TDengine |
