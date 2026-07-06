# CLPM 数据模型设计说明书 (DDS)

**文档状态**: 正式版
**当前版本**: v6.0
**发布日期**: 2026-07-06
**设计依据**: PRD (v6.0), FDS (v6.0，表名/字段名权威基线), ADS (v6.0), 关键算法设计说明 (v2.0), 实现契约 (v2.0)

---

## 0. 文档变更记录

| 版本 | 日期 | 变更说明 | 作者 |
|---|---|---|---|
| v3.0 | 2026-06-20 | 产品化架构重构版：存算分离、回路-Tag 解耦、配置驱动、PV 质量码处理、新增诊断结果表与整定记录表。 | 数据架构组 |
| v3.1 | 2026-06-22 | 对齐《关键算法设计说明》v1.0：①`metric_config.threshold` 类型 DECIMAL → JSONB，新增 `control_type` 字段；②`kpi_snapshot_hourly` 新增 `accuracy_rate`、`saturation_rate` 字段；③`diagnosis_config` 新增 `calc_method` 字段，`threshold` 类型 DECIMAL → JSONB；④`tuning_record` 新增 `fitting_score` 字段；⑤新增"算法结果存储设计"章节；⑥新增"算法版本字段"说明；⑦ER 图更新说明（新增字段不影响现有关系结构）。 | 数据架构组 |
| v4.0 | 2026-06-26 | 对齐《关键算法设计说明》v2.0：①`kpi_snapshot_hourly` 扩展 `fast_rate`/`effective_auto_rate`/`stiction_index`/`output_trip_index`/`settling_time`/`ideal_settling_time` 等指标字段及 `sampling_freq`/`quality_policy`/`valid_rate`/`confidence_level`/`data_lineage` 数据血缘字段；②新增 `kpi_snapshot_custom` 自定义任务快照表；③新增 `clpm_metric_data_requirement` 指标数据需求契约表；④新增 `diagnosis_tag` 诊断标签表；⑤新增 `unit_kpi_summary` 装置级汇总表；⑥§4.1 PV 质量码过滤策略升级为 `KEEP_ALL_WITH_VALIDITY`，引入 Metric Validity Mask 与 A/B/C/D/E 五级可信度；⑦§5.1 KPI 结果存储新增数据血缘字段说明，区分标准任务与自定义任务存储。 | 数据架构组 |
| v4.1 | 2026-07-04 | 对齐 FDS v5.1：①`loop_ledger` 新增 `control_type` / `importance_level` / `include_in_evaluation` 三字段（回路评估参与配置）；②`metric_config` 新增 `grading_thresholds` 字段（性能定级阈值 JSONB），`formula` 字段标注为废弃（v5.0 起算法公式固化为独立函数模块），`control_type` 字段标注为迁移至 `loop_ledger`；③`unit_kpi_summary` 新增 `excluded_loops` / `status` 字段（不参评回路数与聚合状态）；④`kpi_snapshot_custom.stability_rate` 修正为 `steady_rate`（与 `kpi_snapshot_hourly` 字段命名对齐，loop-level 字段统一为 `steady_rate`，`stability_rate` 仅用于 `unit_kpi_summary` 装置级聚合）；⑤全文术语"稳定率"统一为"稳定率"。 | 数据架构组 |
| v6.0 | 2026-07-06 | 对齐 v6.0 文档统一升级与代码事实（26 张 ORM 模型）：①补全代码特有 9 张表字段定义：节点级 KPI 快照三件套（`kpi_node_snapshot_hourly`/`_daily`/`_monthly`，§2.18-§2.20）、回路配置三件套（`loop_mode_mapping`/`loop_type_weight`/`loop_level_weight`，§2.22-§2.24）、系统配置 `sys_config`（§2.25）、报表配置 `report_config`（§2.26）、系统用户 `sys_user`（§2.21）；②标注 `report_schedule` 已被 `report_config` 替代（§2.27）；③标注 `sys_role`/`sys_user_role` 为"计划中，未实现"（角色用枚举存储于 `sys_user.role`，§2.27）；④ER 图与表清单同步更新（§7）；⑤引用文档版本统一：PRD v3.1 → v6.0、FDS v5.1 → v6.0、ADS v3.1 → v6.0、新增引用实现契约 v2.0；⑥`diagnosis_tag` 字段对齐代码事实（新增 `tag_name`/`trigger_value`/`resolved_by`/`resolution_note` 字段，严重等级枚举对齐 `INFO`/`WARN`/`ERROR`/`CRITICAL`，状态对齐 `ACTIVE`/`RESOLVED`/`SUPPRESSED`）。 | 数据架构组 |

---

## 1. 设计原则

遵循 ADS (v6.0) 规定的"存算分离"原则，系统数据模型严格拆分为两大独立域：

1. **关系型业务域 (PostgreSQL)**：承载工厂拓扑模型、AAS Tag 注册表、回路台账、回路-Tag 关联、性能/诊断/引擎等可配置元数据、算法快照结果、整定记录、报表记录及轻量级状态追踪记录。要求强一致性 (ACID)。
2. **高频时序域 (TDengine)**：承载原始海量秒级运行数据（PV/SP/OP/MODE/PID_P/PID_I/PID_D 及 PV 质量码）。要求极高写入吞吐与降采样查询性能。

### 1.1 产品化配置原则

为支撑 PRD v6.0 确立的"产品化、工具化、模块内聚自包含、配置驱动"四大设计原则，本 DDS 在数据模型层面落实以下产品化配置原则：

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

回路作为系统核心实体，由用户在 CLPM 系统中创建并关联 Tag。v3.0 移除原 `mapping_pv/sp/op/mode` 字段（迁移至 `loop_tag_mapping` 表），新增描述、评分权重、AAS 同步时间、回路状态等扩展字段。v4.1 新增 `control_type` / `importance_level` / `include_in_evaluation` 三字段，对齐 FDS v6.0 §5.2.3 回路评估参与配置。

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
| control_type | VARCHAR(20) | 控制类型: `STABLE`(稳定型), `SLOW`(慢速型), `FAST`(快速型), `LOGIC`(逻辑型)，决定该回路自动套用的权重模板 [v4.1 新增] | NOT NULL, DEFAULT 'STABLE' |
| importance_level | SMALLINT | 重要等级：`1`(一级)、`2`(二级)、`3`(三级)，决定装置级聚合权重（一级=3、二级=2、三级=1）。**前后端数据交互统一使用 int 类型**，不使用字符串枚举 [v4.1 新增] | NOT NULL, DEFAULT 2 |
| include_in_evaluation | BOOLEAN | 是否参与评估：`TRUE` 时回路进入综合性能评分与装置级 KPI 聚合；`FALSE` 时单回路 KPI 仍正常计算但不参与装置级统计。默认 `TRUE` [v4.1 新增] | NOT NULL, DEFAULT TRUE |

**状态语义**：
* `READY`：PV/SP/OP/MODE 四个必填 Tag 全部关联成功，回路进入评估流程。
* `PARTIAL`：必填 Tag 缺失，回路标红提示，不参与评估计算。
* `INACTIVE`：`is_active=FALSE`，回路被手动停用，不参与评估计算。

**control_type 枚举值说明**（对齐 FDS v6.0 §5.3.7.1 默认权重配置）：

| control_type | 说明 | 适用场景 |
|---|---|---|
| `STABLE` | 稳定型 | 温度、压力控制 |
| `SLOW` | 慢速型 | 缓慢调节回路 |
| `FAST` | 快速型 | 副回路、流量控制 |
| `LOGIC` | 逻辑型 | 防回流、防超温 |

**importance_level 数值映射**（对齐 FDS v6.0 §5.3.7.2 装置级聚合权重）：

| importance_level | 中文名称 | 装置级聚合权重 w_level |
|:---:|---|:---:|
| 1 | 一级 | 3 |
| 2 | 二级 | 2 |
| 3 | 三级 | 1 |

**include_in_evaluation 语义**（对齐 FDS v6.0 §5.2.3 回路评估参与配置说明）：
* `TRUE` 且回路 `status=READY` 且回路 KPI 快照 `status ≠ INCONCLUSIVE`：进入综合性能评分（详见 FDS §5.3.7.2）与装置级三大 KPI 聚合（综合性能 / 平均自控率 / 稳定率，详见 FDS §5.3.7.3）。
* `FALSE`：单回路 KPI 仍按引擎规则正常计算（可在回路监控、诊断中心查看），但不进入综合性能评分、不参与装置级聚合、不出现在低效回路排行。典型场景：试运行回路、临时停用回路、非关键测量回路、未完成调试的新回路。

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

**表名: `metric_config` (性能指标配置)** [v3.0 新增，v3.1 更新，v4.1 修订]

承载 6 大核心 KPI（好值率、自控率、稳定率、准确率、振荡率、饱和率）及变体指标的可配置元数据。权重总和约束 100%。

> **v3.1 变更**（对齐《关键算法设计说明》§10.1）：
> 1. `threshold` 字段类型由 `DECIMAL(5,2)` 变更为 `JSONB`，结构为 `{"min": number, "max": number, "alert": string}`，支持区间阈值与告警级别。
> 2. 新增 `control_type` 字段（VARCHAR(20)，默认 `STABLE`），用于权重模板选择，枚举值：`STABLE`/`SLOW`/`FAST`/`LOGIC`。

> **v4.1 变更**（对齐 FDS v5.1 §5.3.1.2）：
> 1. `formula` 字段标注为**废弃**（deprecated）：v5.0 起 12 项指标的算法公式已按 GB/T 44693.2-2024 与《关键算法设计说明 v2.0》固化为独立函数模块（详见 FDS §5.3.1.3），不再支持用户自定义公式覆盖。底层表保留该字段以兼容历史数据，但不再开放 API 与 UI 入口。
> 2. `control_type` 字段标注为**迁移至 `loop_ledger`**：因控制类型是回路属性而非指标属性，同一套权重模板适用于所有回路，仅按回路控制类型套用。底层表保留该字段以兼容历史数据，但新写入应使用 `loop_ledger.control_type`。
> 3. 新增 `grading_thresholds` 字段（JSONB）：5 级性能定级阈值（EXCELLENT/GOOD/FAIR/WARNING/POOR），国标默认值详见 FDS §5.3.7.1，可在"权重配置管理"页面手工配置覆盖。
> 4. `weight` 字段语义调整：按控制类型分 4 套模板（STABLE/SLOW/FAST/LOGIC），仅在 3 项核心指标 (A/F/S) 上配置；权重总和须为 1.0（即 100%）；8 项辅助诊断指标不参与评分，权重置 NULL。

| 字段 | 类型 | 说明 | 约束 |
|---|---|---|---|
| id | UUID | 指标主键 | PK |
| metric_code | VARCHAR(50) | 指标代码: `GOOD_VALUE_RATE`, `AUTO_MODE_RATE`, `STEADY_RATE`, `ACCURACY_RATE`, `OSCILLATION_RATE`, `SATURATION_RATE`, `FAST_RATE`, `EFFECTIVE_AUTO_RATE`, `STICTION_INDEX`, `OUTPUT_TRIP_INDEX`, `SETTLING_TIME`, `IDEAL_SETTLING_TIME` | UNIQUE, NOT NULL |
| metric_name | VARCHAR(100) | 指标名称 (如: 好值率) | NOT NULL |
| formula | TEXT | ~~计算公式（已废弃）~~ [v4.1 标注废弃] 12 项指标算法已固化为独立函数模块（FDS §5.3.1.3），不再支持用户自定义公式。字段保留以兼容历史数据，不开放 API/UI | |
| weight | DECIMAL(5,2) | 权重 (3 项核心指标 A/F/S 权重，按 control_type 分 4 套模板；总和须为 1.0；辅助诊断指标置 NULL) | |
| threshold | JSONB | 阈值对象 `{"min": number, "max": number, "alert": number}`，对应最小值/最大值/告警阈值，用于触发诊断与告警 | |
| control_type | VARCHAR(20) | ~~控制类型（已迁移至 `loop_ledger.control_type`）~~ [v4.1 标注迁移] 字段保留以兼容历史数据，新写入应使用 `loop_ledger.control_type` | DEFAULT 'STABLE' |
| grading_thresholds | JSONB | 性能定级 5 级阈值（EXCELLENT/GOOD/FAIR/WARNING/POOR），国标默认值详见 FDS §5.3.7.1，可在权重配置管理页面手工配置覆盖 [v4.1 新增] | |
| is_enabled | BOOLEAN | 是否启用 | DEFAULT TRUE |
| updated_by | VARCHAR(50) | 最后更新人 | |
| updated_at | TIMESTAMP | 最后更新时间 | |
| version | INT | 配置版本号 (用于变更追溯与回滚) | DEFAULT 1 |

**threshold 字段结构说明**：

```json
{
  "min": 0.0,
  "max": 100.0,
  "alert": 90.0
}
```

* `min`：阈值下限（数值型）
* `max`：阈值上限（数值型）
* `alert`：告警阈值（数值型，如稳定率低于 90 触发告警）

**grading_thresholds 字段结构说明** [v4.1 新增]：

```json
{
  "EXCELLENT": {"min": 90, "max": 100},
  "GOOD":      {"min": 80, "max": 90},
  "FAIR":      {"min": 70, "max": 80},
  "WARNING":   {"min": 60, "max": 70},
  "POOR":      {"min": 0,  "max": 60}
}
```

* 国标默认值，可在"权重配置管理"页面手工配置覆盖。
* 性能定级 5 级标准（对齐 FDS §5.3.7.1）：

| 性能评分 P | 国标定级 | 英文标识 | 颜色 |
|---|---|---|---|
| 90 ≤ P < 100 | 一级 | EXCELLENT | 绿色 |
| 80 ≤ P < 90 | 二级 | GOOD | 蓝色 |
| 70 ≤ P < 80 | 三级 | FAIR | 黄色 |
| 60 ≤ P < 70 | 四级 | WARNING | 橙色 |
| 0 ≤ P < 60 | 五级 | POOR | 红色 |

**control_type 枚举值说明**（已迁移至 `loop_ledger.control_type`，详见 §2.2；本字段保留以兼容历史数据）：

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
> 补全 6 大 KPI 字段，新增 `accuracy_rate`（准确率）与 `saturation_rate`（饱和率）字段，使快照表完整覆盖 6 大 KPI（好值率/自控率/稳定率/准确率/振荡率/饱和率）。

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
| steady_rate | DECIMAL(5,2) | 稳定率 (%) | |
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
| action_status | VARCHAR(20) | 处理状态: `PENDING`(待处理), `IN_PROGRESS`(处理中), `IGNORED`(已忽略), `IMPLEMENTED`(已实施) | NOT NULL, DEFAULT 'PENDING' |
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
| steady_rate | DECIMAL(5,2) | 稳定率 (%) [v4.1 修正：原字段名 `stability_rate` 与 `kpi_snapshot_hourly.steady_rate` 不一致，已统一为 `steady_rate`；`stability_rate` 仅用于 `unit_kpi_summary` 装置级聚合] | |
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
    steady_rate DECIMAL(5,2),
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

**表名: `unit_kpi_summary` (装置级 KPI 汇总表)** [v4.0 新增，v4.1 修订]

承载装置（plant_node 中 `type=UNIT` 的节点）级 KPI 汇总快照，按周期对装置下所有参评回路（`include_in_evaluation=TRUE` 且回路 KPI 快照 `status ≠ INCONCLUSIVE`）的 `kpi_snapshot_hourly` 进行聚合。**装置级汇总仅基于标准任务（`kpi_snapshot_hourly`），自定义任务（`kpi_snapshot_custom`）不参与聚合**。聚合权重按 `loop_ledger.importance_level` 映射（一级=3、二级=2、三级=1，对齐 FDS v6.0 §5.3.7.2）。

> **v4.1 变更**（对齐 FDS v5.1 §5.3.7.3）：
> 1. 新增 `excluded_loops` 字段（INTEGER）：装置下 `include_in_evaluation=FALSE` 的回路数（不参评回路数）。
> 2. 新增 `status` 字段（VARCHAR(20)）：聚合状态，枚举值 `SUCCESS` / `PARTIAL` / `EMPTY`。`SUCCESS` 表示有参评回路且全部 SUCCESS；`PARTIAL` 表示部分回路 INCONCLUSIVE；`EMPTY` 表示装置内无参评回路（所有回路均 INCONCLUSIVE 或 `include_in_evaluation=FALSE`）。
> 3. `avg_score` 字段语义明确：装置级综合性能评分，按 `importance_level` 权重加权聚合（原 `score_weight` 字段语义迁移至 `importance_level`，原字段保留以兼容历史数据）。
> 4. `auto_mode_rate` 字段语义明确：装置级平均自控率（参评回路 `auto_mode_rate` 加权聚合）。
> 5. `stability_rate` 字段语义明确：装置级稳定率（参评回路 `steady_rate` 加权聚合，注意 loop-level 字段名为 `steady_rate`，unit-level 聚合字段名为 `stability_rate`）。

| 字段 | 类型 | 说明 | 约束 |
|---|---|---|---|
| id | UUID | 汇总主键 | PK, DEFAULT gen_random_uuid() |
| node_id | UUID | 装置节点 ID | FK -> plant_node.id, NOT NULL |
| snapshot_time | TIMESTAMP | 汇总快照时间（与聚合窗口对齐） | NOT NULL |
| avg_score | DECIMAL(5,2) | 装置级综合性能评分（按 `importance_level` 权重加权聚合参评回路的 `kpi_snapshot_hourly.score`，对齐 FDS §5.3.7.2） | |
| auto_mode_rate | DECIMAL(5,2) | 装置级平均自控率（参评回路 `auto_mode_rate` 按 `importance_level` 权重加权聚合，对齐 FDS §5.3.7.3） | |
| effective_auto_rate | DECIMAL(5,2) | 装置级有效自控率（加权聚合） | |
| stability_rate | DECIMAL(5,2) | 装置级稳定率（参评回路 `steady_rate` 按 `importance_level` 权重加权聚合，对齐 FDS §5.3.7.3） | |
| accuracy_rate | DECIMAL(5,2) | 装置级准确率（加权聚合） | |
| fast_rate | DECIMAL(5,2) | 装置级快速率（加权聚合） | |
| good_value_rate | DECIMAL(5,2) | 装置级好值率（加权聚合） | |
| oscillation_rate | DECIMAL(5,2) | 装置级振荡率（加权聚合） | |
| saturation_rate | DECIMAL(5,2) | 装置级饱和率（加权聚合） | |
| total_loops | INTEGER | 装置下回路总数 | |
| evaluated_loops | INTEGER | 实际参与评估的回路数（`include_in_evaluation=TRUE` 且 `status=SUCCESS`） | |
| inconclusive_loops | INTEGER | INCONCLUSIVE 状态回路数（参评回路中 KPI 快照为 INCONCLUSIVE 的数量） | |
| excluded_loops | INTEGER | 不参评回路数（`include_in_evaluation=FALSE` 的回路数）[v4.1 新增] | |
| status | VARCHAR(20) | 聚合状态: `SUCCESS`(全部参评回路 SUCCESS), `PARTIAL`(部分回路 INCONCLUSIVE), `EMPTY`(无参评回路) [v4.1 新增] | NOT NULL, DEFAULT 'SUCCESS' |
| algorithm_version | VARCHAR(50) | 聚合算法版本号 | |
| created_at | TIMESTAMP | 记录创建时间 | DEFAULT NOW() |

**唯一约束**: `(node_id, snapshot_time)` —— 同一装置同一快照时间仅一条汇总记录。

**说明**：装置级汇总仅基于标准任务（`kpi_snapshot_hourly`），自定义任务不参与。聚合权重按 `loop_ledger.importance_level` 映射（一级=3、二级=2、三级=1）。

**字段关系说明** [v4.1 新增]：

* `total_loops = evaluated_loops + inconclusive_loops + excluded_loops + other_loops`
  * `evaluated_loops`：参评且 KPI 快照 SUCCESS 的回路数
  * `inconclusive_loops`：参评但 KPI 快照 INCONCLUSIVE 的回路数
  * `excluded_loops`：`include_in_evaluation=FALSE` 的回路数
  * `other_loops`：回路 `status ≠ READY`（如 PARTIAL/INACTIVE）的回路数
* 三大 KPI 字段（`avg_score` / `auto_mode_rate` / `stability_rate`）仅在 `evaluated_loops > 0` 时有值，否则置 NULL，且 `status = EMPTY`。

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
    excluded_loops INTEGER,
    status VARCHAR(20) NOT NULL DEFAULT 'SUCCESS',
    algorithm_version VARCHAR(50),
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(node_id, snapshot_time)
);
```

### 2.18 节点级每小时 KPI 快照 (kpi_node_snapshot_hourly)

**表名: `kpi_node_snapshot_hourly` (节点级每小时性能评估快照)** [v6.0 新增，对齐代码 `models/node_kpi.py`]

承载按 `plant_node` 节点（工厂/装置/单元）维度聚合的每小时 KPI 快照。对齐 GB/T 44693.2-2024 §6.4 综合评估：按 `plant_node` 递归收集下属回路，以 `score_weight` 加权聚合回路级 `kpi_snapshot_hourly` 快照，支持企业级/装置级/单元级 KPI。与 `unit_kpi_summary` 互补：`unit_kpi_summary` 仅承载装置级聚合，本表承载任意节点级聚合，且包含 `auto_loop_ratio` 与 `realtime_auto_rate` 等运行指标。

| 字段 | 类型 | 说明 | 约束 |
|---|---|---|---|
| id | UUID | 快照主键 | PK, DEFAULT gen_random_uuid() |
| plant_node_id | UUID | 节点 ID | FK -> plant_node.id (ON DELETE CASCADE), NOT NULL |
| ts_start | TIMESTAMP | 评估窗口起始时间 | NOT NULL |
| ts_end | TIMESTAMP | 评估窗口结束时间 | NOT NULL |
| score | DECIMAL(5,2) | 综合评分 (0-100) | |
| good_value_rate | DECIMAL(5,2) | 好值率 (%) | |
| auto_mode_rate | DECIMAL(5,2) | 自控率 (%) | |
| effective_auto_rate | DECIMAL(5,2) | 有效自控率 (%) | |
| steady_rate | DECIMAL(5,2) | 稳定率 (%) | |
| accuracy_rate | DECIMAL(5,2) | 准确率 (%) | |
| fast_rate | DECIMAL(5,2) | 快速率 (%) | |
| oscillation_rate | DECIMAL(5,2) | 振荡率 (%) | |
| saturation_rate | DECIMAL(5,2) | 饱和率 (%) | |
| stiction_index | DECIMAL(5,2) | 粘滞系数 (%) | |
| settling_time | DECIMAL(8,2) | 实际稳态时间（秒） | |
| output_trip_index | DECIMAL(8,2) | 输出值行程指数 | |
| ideal_settling_time | DECIMAL(8,2) | 理想稳态时间（秒） | |
| auto_loop_ratio | DECIMAL(5,2) | 投用率（自动回路占比，%） | |
| realtime_auto_rate | DECIMAL(5,2) | 实时自控率（取窗口末尾瞬时值，非聚合） | |
| loop_count | INTEGER | 参与聚合的回路数 | NOT NULL, DEFAULT 0 |
| status | VARCHAR(20) | 聚合状态: `EXCELLENT`/`GOOD`/`FAIR`/`WARNING`/`POOR`/`INCONCLUSIVE` | NOT NULL |
| algorithm_version | VARCHAR(30) | 算法版本号 | |
| created_at | TIMESTAMP | 记录创建时间 | NOT NULL, DEFAULT NOW() |

**约束**：
* `CHECK (status IN ('EXCELLENT','GOOD','FAIR','WARNING','POOR','INCONCLUSIVE'))`
* `CHECK (ts_end > ts_start)`

**索引**：
* `idx_kpi_node_snapshot_node_id` (`plant_node_id`)
* `idx_kpi_node_snapshot_ts_start` (`ts_start`)
* `idx_kpi_node_snapshot_status` (`status`)
* `idx_kpi_node_snapshot_node_ts` (`plant_node_id`, `ts_start`)
* `idx_kpi_node_snapshot_ts_status` (`ts_start`, `status`, `score`)

### 2.19 节点级日 KPI 快照 (kpi_node_snapshot_daily)

**表名: `kpi_node_snapshot_daily` (节点级日性能评估快照)** [v6.0 新增，对齐代码 `models/node_kpi.py`]

承载按 `plant_node` 节点维度聚合的日 KPI 快照。按 `loop_count` 加权聚合当天 24 条小时快照；`realtime_auto_rate` 取当天最后一次小时快照的值（非聚合）。

| 字段 | 类型 | 说明 | 约束 |
|---|---|---|---|
| id | UUID | 快照主键 | PK, DEFAULT gen_random_uuid() |
| plant_node_id | UUID | 节点 ID | FK -> plant_node.id (ON DELETE CASCADE), NOT NULL |
| stat_date | DATE | 统计日期 | NOT NULL |
| score | DECIMAL(5,2) | 综合评分 (0-100) | |
| good_value_rate | DECIMAL(5,2) | 好值率 (%) | |
| auto_mode_rate | DECIMAL(5,2) | 自控率 (%) | |
| effective_auto_rate | DECIMAL(5,2) | 有效自控率 (%) | |
| steady_rate | DECIMAL(5,2) | 稳定率 (%) | |
| accuracy_rate | DECIMAL(5,2) | 准确率 (%) | |
| fast_rate | DECIMAL(5,2) | 快速率 (%) | |
| oscillation_rate | DECIMAL(5,2) | 振荡率 (%) | |
| saturation_rate | DECIMAL(5,2) | 饱和率 (%) | |
| stiction_index | DECIMAL(5,2) | 粘滞系数 (%) | |
| settling_time | DECIMAL(8,2) | 实际稳态时间（秒） | |
| output_trip_index | DECIMAL(8,2) | 输出值行程指数 | |
| ideal_settling_time | DECIMAL(8,2) | 理想稳态时间（秒） | |
| auto_loop_ratio | DECIMAL(5,2) | 投用率 (%) | |
| realtime_auto_rate | DECIMAL(5,2) | 实时自控率（取当日最后一次小时快照值） | |
| loop_count | INTEGER | 参与聚合的回路数 | NOT NULL, DEFAULT 0 |
| status | VARCHAR(20) | 聚合状态: `EXCELLENT`/`GOOD`/`FAIR`/`WARNING`/`POOR`/`INCONCLUSIVE` | NOT NULL |
| algorithm_version | VARCHAR(30) | 算法版本号 | |
| created_at | TIMESTAMP | 记录创建时间 | NOT NULL, DEFAULT NOW() |

**约束**：
* `CHECK (status IN ('EXCELLENT','GOOD','FAIR','WARNING','POOR','INCONCLUSIVE'))`
* `UNIQUE (plant_node_id, stat_date)` —— 同一节点同一日期仅一条快照记录

**索引**：
* `idx_kpi_node_snapshot_daily_node_id` (`plant_node_id`)
* `idx_kpi_node_snapshot_daily_stat_date` (`stat_date`)
* `idx_kpi_node_snapshot_daily_status` (`status`)
* `idx_kpi_node_snapshot_daily_node_date` (`plant_node_id`, `stat_date`)

### 2.20 节点级月 KPI 快照 (kpi_node_snapshot_monthly)

**表名: `kpi_node_snapshot_monthly` (节点级月性能评估快照)** [v6.0 新增，对齐代码 `models/node_kpi.py`]

承载按 `plant_node` 节点维度聚合的月 KPI 快照。按 `loop_count` 加权聚合当月所有日快照；`realtime_auto_rate` 取当月最后一次小时快照的值（非聚合）。

| 字段 | 类型 | 说明 | 约束 |
|---|---|---|---|
| id | UUID | 快照主键 | PK, DEFAULT gen_random_uuid() |
| plant_node_id | UUID | 节点 ID | FK -> plant_node.id (ON DELETE CASCADE), NOT NULL |
| stat_month | DATE | 统计月份（用该月 1 日表示） | NOT NULL |
| score | DECIMAL(5,2) | 综合评分 (0-100) | |
| good_value_rate | DECIMAL(5,2) | 好值率 (%) | |
| auto_mode_rate | DECIMAL(5,2) | 自控率 (%) | |
| effective_auto_rate | DECIMAL(5,2) | 有效自控率 (%) | |
| steady_rate | DECIMAL(5,2) | 稳定率 (%) | |
| accuracy_rate | DECIMAL(5,2) | 准确率 (%) | |
| fast_rate | DECIMAL(5,2) | 快速率 (%) | |
| oscillation_rate | DECIMAL(5,2) | 振荡率 (%) | |
| saturation_rate | DECIMAL(5,2) | 饱和率 (%) | |
| stiction_index | DECIMAL(5,2) | 粘滞系数 (%) | |
| settling_time | DECIMAL(8,2) | 实际稳态时间（秒） | |
| output_trip_index | DECIMAL(8,2) | 输出值行程指数 | |
| ideal_settling_time | DECIMAL(8,2) | 理想稳态时间（秒） | |
| auto_loop_ratio | DECIMAL(5,2) | 投用率 (%) | |
| realtime_auto_rate | DECIMAL(5,2) | 实时自控率（取当月最后一次小时快照值） | |
| loop_count | INTEGER | 参与聚合的回路数 | NOT NULL, DEFAULT 0 |
| status | VARCHAR(20) | 聚合状态: `EXCELLENT`/`GOOD`/`FAIR`/`WARNING`/`POOR`/`INCONCLUSIVE` | NOT NULL |
| algorithm_version | VARCHAR(30) | 算法版本号 | |
| created_at | TIMESTAMP | 记录创建时间 | NOT NULL, DEFAULT NOW() |

**约束**：
* `CHECK (status IN ('EXCELLENT','GOOD','FAIR','WARNING','POOR','INCONCLUSIVE'))`
* `UNIQUE (plant_node_id, stat_month)` —— 同一节点同一月份仅一条快照记录

**索引**：
* `idx_kpi_node_snapshot_monthly_node_id` (`plant_node_id`)
* `idx_kpi_node_snapshot_monthly_stat_month` (`stat_month`)
* `idx_kpi_node_snapshot_monthly_status` (`status`)
* `idx_kpi_node_snapshot_monthly_node_month` (`plant_node_id`, `stat_month`)

### 2.21 系统用户 (sys_user)

**表名: `sys_user` (系统用户表)** [v6.0 新增，对齐代码 `models/sys_user.py`]

承载系统登录用户的认证信息与角色枚举。角色通过 `role` 字段以字符串枚举存储（5 种角色：`ADMIN`/`IC_ENGINEER`/`PE_ENGINEER`/`SPONSOR`/`EXPERT`），不再单独建 `sys_role`/`sys_user_role` 关联表（详见 §2.27）。

| 字段 | 类型 | 说明 | 约束 |
|---|---|---|---|
| id | UUID | 用户主键 | PK, DEFAULT gen_random_uuid() |
| username | VARCHAR(50) | 登录用户名 | UNIQUE, NOT NULL |
| password_hash | VARCHAR(255) | 密码哈希（bcrypt/argon2） | NOT NULL |
| display_name | VARCHAR(100) | 显示名称（如：张工） | NOT NULL |
| email | VARCHAR(255) | 邮箱 | UNIQUE |
| role | VARCHAR(20) | 角色枚举: `ADMIN`(管理员)/`IC_ENGINEER`(仪控工程师)/`PE_ENGINEER`(工艺工程师)/`SPONSOR`(赞助人)/`EXPERT`(专家) | NOT NULL |
| is_active | BOOLEAN | 是否启用 | DEFAULT TRUE |
| last_login_at | TIMESTAMP | 最后登录时间 | |
| created_at | TIMESTAMP | 创建时间 | NOT NULL, DEFAULT NOW() |
| updated_at | TIMESTAMP | 更新时间 | NOT NULL, DEFAULT NOW() |

**约束**：
* `CHECK (role IN ('ADMIN', 'IC_ENGINEER', 'PE_ENGINEER', 'SPONSOR', 'EXPERT'))`

**索引**：
* `uk_sys_user_username` (`username`, UNIQUE)
* `uk_sys_user_email` (`email`, UNIQUE)
* `idx_sys_user_is_active` (`is_active`)

**说明**：对齐实现契约 v2.0 §4.5 权限契约的 5 种角色定义；角色以枚举形式存储于 `role` 字段，无需独立角色表。

### 2.22 回路模式映射 (loop_mode_mapping)

**表名: `loop_mode_mapping` (回路投用定义/模式映射)** [v6.0 新增，对齐代码 `models/loop_config.py`]

承载回路 `MODE` 值到控制模式的映射，用于实时自控率/有效自控率/投用率计算，替代硬编码 `{1,2,3}=自动`。每个回路可配置多个 `MODE` 值的语义，由用户按 DCS 实际语义配置。

| 字段 | 类型 | 说明 | 约束 |
|---|---|---|---|
| id | UUID | 映射主键 | PK, DEFAULT gen_random_uuid() |
| loop_id | UUID | 关联回路 ID | FK -> loop_ledger.id (ON DELETE CASCADE), NOT NULL |
| mode_value | INTEGER | DCS 返回的 MODE 值（整数） | NOT NULL |
| mode_label | VARCHAR(20) | 控制模式: `AUTO`(自动)/`CAS`(串级)/`REMOTE`(远程)/`APC`(先进控制)/`MANUAL`(手动) | NOT NULL |
| is_auto | BOOLEAN | 是否算自动控制（`AUTO`/`CAS`/`REMOTE`/`APC` 为 TRUE） | NOT NULL, DEFAULT FALSE |
| is_effective | BOOLEAN | 是否算有效自动（不饱和的自动模式为 TRUE） | NOT NULL, DEFAULT FALSE |
| created_at | TIMESTAMP | 创建时间 | NOT NULL, DEFAULT NOW() |

**约束**：
* `CHECK (mode_label IN ('AUTO', 'CAS', 'REMOTE', 'APC', 'MANUAL'))`
* `UNIQUE (loop_id, mode_value)` —— 同一回路同一 MODE 值仅一条映射

**索引**：
* `uk_loop_mode_mapping_loop_mode` (`loop_id`, `mode_value`, UNIQUE)
* `idx_loop_mode_mapping_loop_id` (`loop_id`)

**说明**：对齐实现契约 v2.0 与 GB/T 44693.2-2024 投用率定义；用于支撑实时自控率 (`realtime_auto_rate`) 与有效自控率 (`effective_auto_rate`) 的差异化计算。

### 2.23 回路类型权重 (loop_type_weight)

**表名: `loop_type_weight` (回路类型权重)** [v6.0 新增，对齐代码 `models/loop_config.py`]

承载按回路类型（4 类）配置的权重模板，用于回路级综合评分公式：`P = [(A*a) + (F*f) + (S*s)] / (a+f+s) * R`。对齐 GB/T 44693.2-2024 附表 1。

| 字段 | 类型 | 说明 | 约束 |
|---|---|---|---|
| id | UUID | 权重主键 | PK, DEFAULT gen_random_uuid() |
| loop_type | VARCHAR(20) | 回路类型: `STABLE`(稳定型)/`SLOW`(慢速型)/`FAST`(快速型)/`LOGIC`(逻辑型) | UNIQUE, NOT NULL |
| type_name | VARCHAR(50) | 类型名称（稳定型/慢速型/快速型/逻辑型） | NOT NULL |
| weight_a | DECIMAL(3,2) | 准确率权重 `a` | NOT NULL |
| weight_f | DECIMAL(3,2) | 快速率权重 `f` | NOT NULL |
| weight_s | DECIMAL(3,2) | 平稳率权重 `s` | NOT NULL |
| description | TEXT | 描述说明 | |
| updated_by | VARCHAR(50) | 最后更新人 | |
| updated_at | TIMESTAMP | 最后更新时间 | DEFAULT NOW() ON UPDATE NOW() |

**约束**：
* `CHECK (loop_type IN ('STABLE', 'SLOW', 'FAST', 'LOGIC'))`

**默认值（国标附表 1）**：

| loop_type | type_name | weight_a | weight_f | weight_s | 适用场景 |
|---|---|---|---|---|---|
| `STABLE` | 稳定型 | 0.20 | 0.30 | 0.50 | 温度/压力控制 |
| `SLOW` | 慢速型 | 0.30 | 0.10 | 0.60 | 缓慢调节 |
| `FAST` | 快速型 | 0.20 | 0.50 | 0.30 | 副回路/速度控制 |
| `LOGIC` | 逻辑型 | 0.00 | 0.50 | 0.60 | 逻辑规则控制 |

**说明**：本表与 §2.5 `metric_config.weight` 互补——`metric_config.weight` 保留以兼容历史数据，新写入应使用本表（按 `loop_ledger.control_type` 关联查询）。

### 2.24 回路级别权重 (loop_level_weight)

**表名: `loop_level_weight` (回路级别权重)** [v6.0 新增，对齐代码 `models/loop_config.py`]

承载按回路重要等级（3 级）配置的权重，用于装置级聚合公式：`装置平均性能评分 = Σ(w_i * P_i) / Σw_i`。对齐 GB/T 44693.2-2024 附表 2，与 `loop_ledger.importance_level` 字段配合使用。

| 字段 | 类型 | 说明 | 约束 |
|---|---|---|---|
| id | UUID | 权重主键 | PK, DEFAULT gen_random_uuid() |
| level | INTEGER | 回路级别: `1`(一级)/`2`(二级)/`3`(三级) | UNIQUE, NOT NULL |
| level_name | VARCHAR(50) | 级别名称（一级/二级/三级） | NOT NULL |
| weight | DECIMAL(3,1) | 级别权重: `3.0`/`2.0`/`1.0` | NOT NULL |
| description | TEXT | 描述说明 | |
| updated_by | VARCHAR(50) | 最后更新人 | |
| updated_at | TIMESTAMP | 最后更新时间 | DEFAULT NOW() ON UPDATE NOW() |

**约束**：
* `CHECK (level IN (1, 2, 3))`

**默认值（国标附表 2）**：

| level | level_name | weight | 适用场景 |
|---|---|---|---|
| 1 | 一级 | 3.0 | 决定性影响：负荷控制/联锁相关 |
| 2 | 二级 | 2.0 | 辅助保障：稳定性/设备安全 |
| 3 | 三级 | 1.0 | 次要辅助：维持辅助设备运行 |

**说明**：本表为 `loop_ledger.importance_level` 字段提供权重查询源；装置级聚合（`unit_kpi_summary` 与 `kpi_node_snapshot_*`）按本表权重加权。

### 2.25 系统配置 (sys_config)

**表名: `sys_config` (系统键值配置表)** [v6.0 新增，对齐代码 `models/sys_config.py`]

承载运行时可变的键值对系统配置，包括 AAS 同步周期、缓存策略、特性开关等运行时参数。配置变更通过 `updated_by`/`updated_at` 字段留痕，配合 `sys_audit_log` 实现变更追溯。

| 字段 | 类型 | 说明 | 约束 |
|---|---|---|---|
| key | VARCHAR(100) | 配置键名（如 `aas_sync_interval`、`cache_ttl`） | PK |
| value | TEXT | 配置值（字符串形式存储，复杂结构用 JSON 序列化） | |
| description | VARCHAR(255) | 配置说明 | |
| updated_by | VARCHAR(50) | 最后更新人 | |
| updated_at | TIMESTAMP | 最后更新时间 | NOT NULL, DEFAULT NOW() ON UPDATE NOW() |

**索引**：
* `idx_sys_config_key` (`key`, UNIQUE)

**说明**：本表为运行时键值存储，区别于 `engine_rule`（结构化引擎规则）与 `metric_config`/`diagnosis_config`（指标配置）；适合存储特性开关、运行时参数等非结构化配置。

### 2.26 报表配置 (report_config)

**表名: `report_config` (自动报表配置)** [v6.0 新增，对齐代码 `models/report_config.py`]

承载自动报表生成配置：报表周期、收件人、内容模板等。实际生成的报表归档记录存储于 `report_record`（§2.12）。本表替代 v3.0 规划中的 `report_schedule` 表（详见 §2.27）。

| 字段 | 类型 | 说明 | 约束 |
|---|---|---|---|
| id | UUID | 配置主键 | PK, DEFAULT gen_random_uuid() |
| name | VARCHAR(100) | 报表配置名称（如：装置日报-常减压） | NOT NULL |
| report_period | VARCHAR(20) | 报表周期: `SHIFT`(班)/`DAILY`(日)/`WEEKLY`(周)/`MONTHLY`(月) | NOT NULL |
| recipients | TEXT | 收件人列表（逗号分隔的邮箱或用户名） | NOT NULL |
| content_template | TEXT | 内容模板（JSON 序列化的模板配置） | |
| is_enabled | BOOLEAN | 是否启用 | DEFAULT TRUE |
| created_by | VARCHAR(50) | 创建人 | |
| updated_by | VARCHAR(50) | 最后更新人 | |
| created_at | TIMESTAMP | 创建时间 | NOT NULL, DEFAULT NOW() |
| updated_at | TIMESTAMP | 更新时间 | NOT NULL, DEFAULT NOW() ON UPDATE NOW() |

**约束**：
* `CHECK (report_period IN ('SHIFT', 'DAILY', 'WEEKLY', 'MONTHLY'))`

**索引**：
* `idx_report_config_period` (`report_period`)
* `idx_report_config_is_enabled` (`is_enabled`)

**说明**：本表为 `report_record` 的配置源；用户在系统管理模块配置报表规则后，调度器按 `report_period` 周期性生成 `report_record` 记录。

### 2.27 计划中/已替代表说明

> **v6.0 新增**：本章节说明 DDS 历史版本规划但代码中未实现的表，以及代码实现与历史规划不一致的替代关系，确保文档与代码完全一致。

#### 2.27.1 `report_schedule` 已被替代

**表名: `report_schedule`（报表计划表，已替代）**

历史规划中承载报表调度计划的表。v6.0 起代码实际使用 `report_config`（§2.26）承载报表配置，调度计划通过 `report_config.report_period` + `is_enabled` 字段实现，无需独立的 `report_schedule` 表。

* **状态**：已替代
* **替代表**：`report_config`（§2.26）
* **影响**：`report_record`（§2.12）通过 `report_config` 关联生成，不再有独立的 schedule 表

#### 2.27.2 `sys_role` / `sys_user_role` 计划中未实现

**表名: `sys_role`（系统角色表）/ `sys_user_role`（用户-角色关联表）**

历史规划中承载 RBAC 角色与用户-角色多对多关联的表。v6.0 起代码采用枚举方式存储角色于 `sys_user.role` 字段（5 种角色：`ADMIN`/`IC_ENGINEER`/`PE_ENGINEER`/`SPONSOR`/`EXPERT`），不再单独建 `sys_role` 与 `sys_user_role` 表。

* **状态**：计划中，未实现
* **替代方案**：`sys_user.role` 字段以字符串枚举存储角色（详见 §2.21）
* **影响**：当前不支持自定义角色，5 种角色为系统固定枚举；如未来需支持自定义角色，可按本规划补建 `sys_role` 与 `sys_user_role` 表

#### 2.27.3 表清单对比汇总

| DDS v6.0 表名 | 代码 ORM 模型 | 状态 |
|---|---|---|
| §2.1 `plant_node` | `PlantNode` | 一致 |
| §2.2 `loop_ledger` | `LoopLedger` | 一致 |
| §2.3 `tag_registry` | `TagRegistry` | 一致 |
| §2.4 `loop_tag_mapping` | `LoopTagMapping` | 一致 |
| §2.5 `metric_config` | `MetricConfig` | 一致 |
| §2.6 `diagnosis_config` | `DiagnosisConfig` | 一致 |
| §2.7 `engine_rule` | `EngineRule` | 一致 |
| §2.8 `kpi_snapshot_hourly` | `KpiSnapshotHourly` | 一致 |
| §2.9 `action_tracker` | `ActionTracker` | 一致 |
| §2.10 `diagnosis_result` | `DiagnosisResult` | 一致 |
| §2.11 `tuning_record` | `TuningRecord` | 一致 |
| §2.12 `report_record` | `ReportRecord` | 一致 |
| §2.13 `sys_audit_log` | `SysAuditLog` | 一致 |
| §2.14 `kpi_snapshot_custom` | `KpiSnapshotCustom` | 一致 |
| §2.15 `clpm_metric_data_requirement` | `ClpmMetricDataRequirement` | 一致 |
| §2.16 `diagnosis_tag` | `DiagnosisTag` | 一致 |
| §2.17 `unit_kpi_summary` | `UnitKpiSummary` | 一致 |
| §2.18 `kpi_node_snapshot_hourly` | `KpiNodeSnapshotHourly` | 一致 |
| §2.19 `kpi_node_snapshot_daily` | `KpiNodeSnapshotDaily` | 一致 |
| §2.20 `kpi_node_snapshot_monthly` | `KpiNodeSnapshotMonthly` | 一致 |
| §2.21 `sys_user` | `SysUser` | 一致 |
| §2.22 `loop_mode_mapping` | `LoopModeMapping` | 一致 |
| §2.23 `loop_type_weight` | `LoopTypeWeight` | 一致 |
| §2.24 `loop_level_weight` | `LoopLevelWeight` | 一致 |
| §2.25 `sys_config` | `SysConfig` | 一致 |
| §2.26 `report_config` | `ReportConfig` | 一致 |
| — `report_schedule` | — | 已替代（→ `report_config`） |
| — `sys_role` / `sys_user_role` | — | 计划中，未实现（角色用枚举） |

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

> **v3.1 新增**：本章节对齐《关键算法设计说明》v1.0 与 ADS v6.0 §8 算法服务架构，定义 3 大算法服务（KPI 计算/诊断分析/整定计算）结果的存储策略。
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

> **v3.1 新增**：本章节对齐 ADS v6.0 §12 算法版本管理与《关键算法设计说明》§3.3，定义算法结果表中的版本字段规范。

### 6.1 算法版本字段汇总

v3.1 在以下表中新增或明确 `algorithm_version` 字段，用于算法结果追溯：

| 表名 | 字段 | 类型 | 说明 |
|---|---|---|---|
| `kpi_snapshot_hourly` | `algorithm_version` | VARCHAR(50) | KPI 计算算法版本（如 `KPI_CALC_v1.0`、`SCORE_CALC_v1.0`） |
| `diagnosis_result` | `algorithm_version` | VARCHAR(50) | 诊断算法版本（如 `OSC_IAE_v1.0`、`STICTION_CH_v1.0`） |
| `tuning_record` | `algorithm_version` | VARCHAR(50) | 整定算法版本（如 `FOPDT_ID_v1.0`、`IMC_TUNE_v1.0`） |

### 6.2 版本号格式

算法版本号格式：`<algorithm_name>v<major>.<minor>`（对齐 ADS v6.0 §12.1）

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
   │                       │
   │                       ├── (N) kpi_snapshot_hourly
   │                       ├── (N) diagnosis_result
   │                       ├── (N) action_tracker
   │                       ├── (N) tuning_record
   │                       ├── (N) kpi_snapshot_custom
   │                       └── (N) loop_mode_mapping [v6.0 新增]
   │
   ├── (N) kpi_node_snapshot_hourly [v6.0 新增]
   ├── (N) kpi_node_snapshot_daily [v6.0 新增]
   ├── (N) kpi_node_snapshot_monthly [v6.0 新增]
   └── (N) unit_kpi_summary

diagnosis_tag (N) ──── (1) loop_ledger [v6.0 关系明确]

metric_config (独立配置表)          diagnosis_config (独立配置表)
engine_rule (独立配置表)            report_record (独立记录表)
sys_audit_log (独立日志表)          sys_config (独立键值配置表) [v6.0 新增]
loop_type_weight (独立配置表) [v6.0 新增]   loop_level_weight (独立配置表) [v6.0 新增]
sys_user (独立用户表) [v6.0 新增]    report_config (独立报表配置表) [v6.0 新增]
clpm_metric_data_requirement (独立契约表)
```

**外键关系**：
* `loop_ledger.unit_id` → `plant_node.id`
* `loop_tag_mapping.loop_id` → `loop_ledger.id`
* `loop_tag_mapping.tag_id` → `tag_registry.id`
* `kpi_snapshot_hourly.loop_id` → `loop_ledger.id`
* `kpi_snapshot_custom.loop_id` → `loop_ledger.id`
* `diagnosis_result.loop_id` → `loop_ledger.id`
* `diagnosis_tag.loop_id` → `loop_ledger.id`
* `action_tracker.loop_id` → `loop_ledger.id`
* `tuning_record.loop_id` → `loop_ledger.id`
* `loop_mode_mapping.loop_id` → `loop_ledger.id` [v6.0 新增]
* `unit_kpi_summary.node_id` → `plant_node.id`
* `kpi_node_snapshot_hourly.plant_node_id` → `plant_node.id` [v6.0 新增]
* `kpi_node_snapshot_daily.plant_node_id` → `plant_node.id` [v6.0 新增]
* `kpi_node_snapshot_monthly.plant_node_id` → `plant_node.id` [v6.0 新增]

### 7.4 v6.0 新增表汇总

> **v6.0 新增**：本章节汇总 v6.0 对齐代码事实补全的 9 张表及其关系结构。

| 表名 | 关联实体 | 关系类型 | 说明 |
|---|---|---|---|
| `kpi_node_snapshot_hourly` | `plant_node` | N:1 | 节点级每小时 KPI 快照，按节点聚合 |
| `kpi_node_snapshot_daily` | `plant_node` | N:1 | 节点级日 KPI 快照，按 `loop_count` 加权聚合 |
| `kpi_node_snapshot_monthly` | `plant_node` | N:1 | 节点级月 KPI 快照，按 `loop_count` 加权聚合 |
| `sys_user` | — | 独立 | 系统用户表，角色以枚举存储 |
| `loop_mode_mapping` | `loop_ledger` | N:1 | 回路 MODE 值到控制模式映射 |
| `loop_type_weight` | — | 独立 | 4 类回路类型权重模板 |
| `loop_level_weight` | — | 独立 | 3 级回路级别权重 |
| `sys_config` | — | 独立 | 运行时键值配置 |
| `report_config` | — | 独立 | 报表生成配置（替代 `report_schedule`） |

**v6.0 关系结构影响评估**：
* 新增 9 张表，其中 4 张与 `plant_node`/`loop_ledger` 建立外键关系（`kpi_node_snapshot_*` × 3 + `loop_mode_mapping` × 1）。
* 5 张为独立配置/记录表（`loop_type_weight`/`loop_level_weight`/`sys_config`/`report_config`/`sys_user`），不涉及外键。
* 新增表不影响 v3.1/v4.0/v4.1 已有的关系结构，仅做扩展。
