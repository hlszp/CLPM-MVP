# CLPM P0 契约主干设计

日期：2026-06-16
版本：v0.1
关联 PRS：`product-requirements-specification-v0.4-2026-06-16.md`
文档定位：P0 研发契约参考，面向后端、前端、算法、测试和原型协同

## 1. 设计结论

P0 不按“数据接入、台账、KPI、诊断、审核、报表各做一块”验收，而按一条可复现证据链验收：

`SampleBatch -> LoopLedger -> KpiResult -> DiagnosisFinding -> Review/Reevaluation -> EvidencePackage`

本文件补齐 P0 契约主干的字段级、状态级、接口级规格，重点覆盖 5 个核心对象：

| 对象 | P0 职责 |
|---|---|
| `SampleBatch` | 定义样本批次、来源、时间窗、导入与验证状态 |
| `LoopLedger` | 固化样本回路台账、点位映射、统计口径相关版本 |
| `KpiResult` | 保存核心 KPI 结果、口径版本、质量状态和可追溯输入 |
| `DiagnosisFinding` | 保存诊断结论、证据链、置信度、建议动作和复核状态 |
| `EvidencePackage` | 以 manifest 方式引用本次验证全部证据，不直接打包原始大数据 |

P0 契约主干的核心原则：

- 结果对象必须引用版本，不允许只存展示值。
- 数据不足、不可判定、部分可用是一等状态，不允许静默写成 `0` 或空白。
- 台账、映射、公式、阈值、质量规则、模式映射变更后，旧结果不得静默漂移。
- EvidencePackage 是 manifest，不是截图汇总，也不是原始时序数据导出包。
- P0 只支持样本窗口和结果快照查询，不承诺 5 年任意查询性能。

## 2. 契约边界

### 2.1 P0 范围

| 范围 | 说明 |
|---|---|
| 样本规模 | 单装置或代表性装置 `50-100` 回路 |
| 数据来源 | 真实 OPC、historian 导出、CSV 或模拟数据 |
| 必备点位 | `PV/SP/OP/MODE` |
| 核心 KPI | 自控率、有效自控率、平稳率、性能评分、报警次数、操作频次、好值率 |
| 核心输出 | 低性能清单、数据可用性与可整定性状态、单回路证据链、审核/实施/复评记录、EvidencePackage |
| 整定样例 | `1` 个专家确认的 TuningCase 附件，不进入治理主链依赖 |

### 2.2 P0 不做

| 不做项 | 说明 |
|---|---|
| 5 年任意查询 | P2 做冷热分层、预聚合、异步查询和缓存 |
| 批量模型辨识 | P1 做可信整定工作台 |
| 批量交互式整定 | P1/P2 |
| 完整项目交付驾驶舱 | P2 |
| 自动写 DCS | 永不作为默认能力 |

## 3. 通用字段约定

### 3.1 ID 与时间

| 字段 | 类型 | 说明 |
|---|---|---|
| `*_id` | string | 全局唯一 ID，建议采用带前缀的 UUID 或 ULID，如 `sb_...` |
| `created_at` | datetime | ISO 8601，带时区 |
| `updated_at` | datetime | ISO 8601，带时区 |
| `created_by` | string | 用户 ID 或系统任务 ID |
| `updated_by` | string | 用户 ID 或系统任务 ID |

### 3.2 版本引用

任何可发布结论必须至少携带适用的版本引用。

| 字段 | 说明 |
|---|---|
| `ledger_version` | 回路台账版本 |
| `mapping_version` | 点位映射版本 |
| `formula_version` | KPI 公式版本 |
| `threshold_version` | KPI 阈值版本 |
| `quality_rule_version` | 质量规则版本 |
| `mode_mapping_version` | MODE 映射版本 |
| `rule_version` | 诊断规则版本 |

### 3.3 生成与有效性

| 字段 | 类型 | 说明 |
|---|---|---|
| `generated_by_run` | string | 生成该结果的 `CalculationRun` 或 `JobRun` |
| `input_hash` | string | 生成输入摘要，用于判断结果是否过期 |
| `validity_status` | enum | `current`、`stale`、`superseded`、`invalid` |
| `superseded_by` | string/null | 被哪个新对象替代 |
| `invalid_reason` | string/null | 结果无效原因 |

### 3.4 值状态

P0 所有结果对象都必须区分“没有值”和“值为 0”。

| 状态 | 含义 |
|---|---|
| `available` | 已成功计算并可用于展示或证据包 |
| `partial` | 部分回路、部分窗口或部分证据可用 |
| `unavailable` | 输入不足，不能计算 |
| `inconclusive` | 输入存在但不足以形成可信结论 |
| `error` | 系统或配置错误导致失败 |

## 4. 对象关系

```text
SampleBatch
  |
  | defines sample scope and source windows
  v
LoopLedger
  |
  | freezes loop identity and point mapping versions
  v
KpiResult
  |
  | feeds priority and diagnosis
  v
DiagnosisFinding
  |
  | reviewed and optionally closed by action records
  v
ReviewRecord / Reevaluation
  |
  | referenced by manifest
  v
EvidencePackage
```

Supporting references:

- `TimeWindow`：KPI、诊断、证据链使用的数据窗口。
- `SignalSeries`：PV/SP/OP/MODE 的窗口数据或数据引用。
- `QualityMask`：good/bad/missing/frozen 质量叠层。
- `ModeSegment`：AUTO/manual/APC/unknown 状态段。
- `ExceptionRecord`：排除原因和审批记录。
- `ReviewRecord`：审核、人工实施、专家判断。
- `Reevaluation`：前后对比复评。
- `TuningCase`：P0 单个整定可信度样例附件。

本文件对 supporting references 只定义引用方式，不展开完整字段。完整字段可在后续专项设计中细化。

## 5. SampleBatch

### 5.1 作用

`SampleBatch` 定义一次 P0 验证使用的样本范围、数据来源、时间窗、导入状态和数据就绪状态。它是所有 P0 结果的根对象。

### 5.2 字段规格

| 字段 | 类型 | 必填 | 说明 | P0 校验 |
|---|---|---:|---|---|
| `sample_batch_id` | string | 是 | 样本批次 ID | 唯一 |
| `batch_name` | string | 是 | 批次名称 | 非空 |
| `batch_purpose` | enum | 是 | `p0_validation`、`desktop_review`、`demo` | P0 默认 `p0_validation` |
| `site_name` | string | 否 | 厂区或企业名称 | 可为空 |
| `unit_name` | string | 是 | 装置或代表性单元名称 | 非空 |
| `sample_scope_desc` | string | 是 | 样本范围说明 | 需说明是否代表装置 |
| `loop_count_declared` | integer | 是 | 导入前声明回路数 | 建议 `50-100` |
| `loop_count_imported` | integer | 是 | 实际导入回路数 | 可为 0，但状态不能 ready |
| `source_type` | enum | 是 | `opc`、`historian_export`、`csv`、`simulated` | 非空 |
| `source_name` | string | 是 | 数据源名称 | 必须可追溯 |
| `source_version` | string | 是 | 数据源版本或导出版本 | 非空 |
| `source_files` | array | 否 | 文件名、hash、行数、时间范围 | CSV/historian 必填 |
| `import_run_id` | string | 是 | 导入任务 ID | 非空 |
| `sample_start_time` | datetime | 是 | 样本开始时间 | 小于结束时间 |
| `sample_end_time` | datetime | 是 | 样本结束时间 | 大于开始时间 |
| `timezone` | string | 是 | 时区 | 默认 `Asia/Shanghai` |
| `expected_signals` | array | 是 | 预期点位，如 `PV/SP/OP/MODE` | P0 必含四项 |
| `event_availability` | enum | 是 | `available`、`imported`、`simulated`、`unavailable` | 不可空 |
| `quality_mark_availability` | enum | 是 | `native_quality`、`derived_rules`、`manual_rules`、`none` | `none` 时不可高可信 |
| `batch_status` | enum | 是 | 见 5.3 | 系统维护 |
| `readiness_status` | enum | 是 | `ready`、`partial`、`insufficient`、`unknown` | 系统计算 |
| `validation_summary` | object | 是 | 样本数、映射率、好值率、问题数量 | 验证后生成 |
| `validation_issues` | array | 否 | 校验问题列表 | 可为空 |
| `frozen_at` | datetime/null | 否 | 样本冻结时间 | frozen 后必填 |
| `frozen_by` | string/null | 否 | 样本冻结人 | frozen 后必填 |
| `notes` | string | 否 | 备注 | 可为空 |
| `created_at` | datetime | 是 | 创建时间 | 自动 |
| `created_by` | string | 是 | 创建人 | 自动 |

### 5.3 状态机

| 状态 | 含义 | 可进入条件 | 可执行动作 |
|---|---|---|---|
| `draft` | 已创建但未导入数据 | 创建后默认状态 | 上传/配置来源 |
| `importing` | 正在导入 | 发起导入任务 | 等待任务完成 |
| `import_failed` | 导入失败 | 文件、来源或解析失败 | 修正来源后重新导入 |
| `imported` | 已导入但未验证 | 导入成功 | 执行验证 |
| `validating` | 正在验证 | 发起验证任务 | 等待任务完成 |
| `validation_failed` | 验证失败 | 缺关键字段或时间窗非法 | 修正样本 |
| `insufficient` | 样本不足 | 0 回路、时间窗不足、关键点位缺失严重 | 作为失败样本保留 |
| `ready` | 通过验证，待冻结 | 样本、时间窗、来源和基本字段通过 | 冻结样本 |
| `frozen` | 样本范围已固化 | ready 后人工冻结 | 生成 LoopLedger |
| `archived` | 已归档 | 人工归档 | 只读 |

### 5.4 接口规格

基础路径：`/api/p0/sample-batches`

| 方法 | 路径 | 用途 | 输入 | 输出 |
|---|---|---|---|---|
| `POST` | `/api/p0/sample-batches` | 创建样本批次 | 批次基础信息 | `SampleBatch` |
| `POST` | `/api/p0/sample-batches/{id}/import` | 导入数据 | source 配置、文件引用 | `JobRun` |
| `POST` | `/api/p0/sample-batches/{id}/validate` | 验证样本 | 可选验证规则版本 | 验证任务 |
| `POST` | `/api/p0/sample-batches/{id}/freeze` | 固化样本范围 | freeze reason | 固化后的 `SampleBatch` |
| `GET` | `/api/p0/sample-batches/{id}` | 获取详情 | 无 | `SampleBatch` |
| `GET` | `/api/p0/sample-batches/{id}/readiness` | 获取就绪摘要 | 无 | readiness summary |
| `GET` | `/api/p0/sample-batches/{id}/loops` | 获取样本回路列表 | 分页参数 | loop refs |

### 5.5 校验规则

- `sample_end_time` 必须晚于 `sample_start_time`。
- P0 成功验证建议 `loop_count_imported` 在 `50-100` 范围内；小于 50 不阻止导入，但 `readiness_status` 应标记为 `partial` 或 `insufficient`。
- `source_type=csv` 或 `historian_export` 时，`source_files` 必须包含文件 hash。
- 下游 `LoopLedger`、KPI 和诊断建议只引用 `batch_status=frozen` 的样本。
- `quality_mark_availability=none` 时，所有 KPI 和诊断不得输出高可信状态。
- `event_availability=unavailable` 时，诊断证据链必须标注“事件未纳入本次判断”。

## 6. LoopLedger

### 6.1 作用

`LoopLedger` 固化样本批次中的回路身份、层级、控制类型、点位映射、统计状态和版本引用。它是 KPI、诊断和 EvidencePackage 的身份基础。

### 6.2 Header 字段规格

| 字段 | 类型 | 必填 | 说明 | P0 校验 |
|---|---|---:|---|---|
| `ledger_id` | string | 是 | 台账 ID | 唯一 |
| `sample_batch_id` | string | 是 | 所属样本批次 | 必须 ready/frozen |
| `ledger_version` | string | 是 | 台账版本 | 冻结后不可变 |
| `mapping_version` | string | 是 | 点位映射版本 | 修正映射后递增 |
| `mode_mapping_version` | string | 是 | MODE 映射版本 | KPI 依赖 |
| `quality_rule_version` | string | 是 | 质量规则版本 | KPI 依赖 |
| `ledger_status` | enum | 是 | 见 6.4 | 系统维护 |
| `loop_count_total` | integer | 是 | 台账回路总数 | >=0 |
| `mapping_complete_count` | integer | 是 | PV/SP/OP/MODE 完整回路数 | 自动计算 |
| `mapping_complete_rate` | number | 是 | 映射完整率 | P0 目标 `>=95%` |
| `manual_correction_count` | integer | 是 | 人工校核次数 | 可为 0 |
| `frozen_at` | datetime/null | 否 | 冻结时间 | frozen 后必填 |
| `frozen_by` | string/null | 否 | 冻结人 | frozen 后必填 |
| `supersedes_ledger_id` | string/null | 否 | 替代旧台账 | 可为空 |

### 6.3 LoopRecord 字段规格

`LoopLedger` 包含多个 `LoopRecord`。P0 最少需要以下字段。

| 字段 | 类型 | 必填 | 说明 | P0 校验 |
|---|---|---:|---|---|
| `loop_id` | string | 是 | 回路 ID | 批次内唯一 |
| `loop_tag` | string | 是 | 回路位号 | 非空 |
| `loop_name` | string | 否 | 回路名称 | 可为空 |
| `unit_name` | string | 是 | 所属装置 | 非空 |
| `loop_group` | string | 否 | 回路组 | 可为空 |
| `control_type` | enum | 是 | `flow`、`pressure`、`temperature`、`level`、`composition`、`other` | unknown 需标注 |
| `loop_priority` | enum | 否 | `critical`、`major`、`normal` | 可为空 |
| `participation_status` | enum | 是 | `included`、`excluded`、`conditional` | excluded 必须有关联原因 |
| `exception_record_id` | string/null | 否 | 排除记录 | excluded 必填 |
| `pv_tag` | string/null | 是 | PV 点位 | 为空则不可评估 |
| `sp_tag` | string/null | 是 | SP 点位 | 为空则不可评估 |
| `op_tag` | string/null | 是 | OP 点位 | 为空则不能判断饱和和动作 |
| `mode_tag` | string/null | 是 | MODE 点位 | 为空则不可计算自控率 |
| `pid_param_tags` | object/null | 否 | P/I/D 参数点位 | 仅整定样例必需 |
| `engineering_units` | object | 否 | PV/SP/OP 单位 | 单位异常会降低可信度 |
| `range_limits` | object | 否 | PV/SP/OP 量程 | 缺失时部分指标不可算 |
| `control_direction` | enum | 否 | `direct`、`reverse`、`unknown` | unknown 阻止整定样例高可信 |
| `mapping_status` | enum | 是 | `complete`、`partial`、`missing_required`、`invalid` | 自动计算 |
| `data_readiness_status` | enum | 是 | `evaluable`、`diagnosable`、`tunable_candidate`、`needs_field_check`、`data_insufficient`、`undetermined` | 数据雷达使用 |
| `mapping_issues` | array | 否 | 缺失点位、单位异常、方向未知等 | 可为空 |
| `corrected_by` | string/null | 否 | 最近校核人 | 人工修正后必填 |
| `corrected_at` | datetime/null | 否 | 最近校核时间 | 人工修正后必填 |

### 6.4 状态机

| 状态 | 含义 | 可进入条件 | 可执行动作 |
|---|---|---|---|
| `draft` | 自动识别或导入后的初始台账 | 从 SampleBatch 生成 | 人工校核 |
| `mapping_review` | 正在校核映射 | 存在部分或错误映射 | 修改点位、单位、方向 |
| `ready_to_freeze` | 达到冻结条件 | 映射完整率、必要字段通过 | 冻结 |
| `frozen` | 台账已固化 | 人工确认冻结 | 下游 KPI/诊断可引用 |
| `superseded` | 已被新版本替代 | 新台账冻结 | 只读 |
| `invalid` | 台账不可用 | 严重结构错误 | 重建 |

### 6.5 接口规格

基础路径：`/api/p0/loop-ledgers`

| 方法 | 路径 | 用途 | 输入 | 输出 |
|---|---|---|---|---|
| `POST` | `/api/p0/sample-batches/{id}/loop-ledger` | 从样本生成台账 | 模板/识别规则版本 | `LoopLedger` |
| `GET` | `/api/p0/loop-ledgers/{ledger_id}` | 获取台账 | 无 | `LoopLedger` |
| `GET` | `/api/p0/loop-ledgers/{ledger_id}/loops` | 查询回路 | 分页、过滤 | `LoopRecord[]` |
| `PATCH` | `/api/p0/loop-ledgers/{ledger_id}/loops/{loop_id}` | 修改回路映射 | 修改字段、原因 | 新 mapping draft |
| `POST` | `/api/p0/loop-ledgers/{ledger_id}/freeze` | 冻结台账 | 冻结原因 | frozen ledger |
| `GET` | `/api/p0/loop-ledgers/{ledger_id}/coverage` | 映射覆盖率 | 无 | coverage summary |

### 6.6 校验规则

- frozen 的 `LoopLedger` 不允许原地修改；任何修改必须生成新 `mapping_version`。
- 旧 `KpiResult`、`DiagnosisFinding`、`EvidencePackage` 必须继续引用旧版本，不随新映射漂移。
- `PV/SP/OP/MODE` 缺任一项时，该回路不能进入完整 KPI 状态。
- `MODE` 缺失时，自控率和有效自控率必须为 `unavailable`，不能填 0。
- `participation_status=excluded` 时必须有关联 `ExceptionRecord`，否则不能冻结。

## 7. KpiResult

### 7.1 作用

`KpiResult` 保存样本窗口、回路或聚合对象的 KPI 计算结果。P0 页面和诊断不直接读原始时序数据生成结论，而应读 `KpiResult` 和其引用的结果快照。

### 7.2 P0 KPI 枚举

| `metric_name` | 中文名 | P0 |
|---|---|---|
| `auto_rate` | 自控率 | 必做 |
| `effective_auto_rate` | 有效自控率 | 必做 |
| `stability_rate` | 平稳率 | 必做 |
| `performance_score` | 性能评分 | 必做 |
| `alarm_count` | 报警次数 | 必做，可导入/模拟 |
| `operation_count` | 操作频次 | 必做，可导入/模拟 |
| `good_value_rate` | 好值率 | 必做 |

### 7.3 字段规格

| 字段 | 类型 | 必填 | 说明 | P0 校验 |
|---|---|---:|---|---|
| `kpi_result_id` | string | 是 | KPI 结果 ID | 唯一 |
| `sample_batch_id` | string | 是 | 样本批次 | 非空 |
| `loop_id` | string/null | 否 | 回路 ID，聚合结果可为空 | 回路级必填 |
| `aggregate_scope` | enum | 是 | `loop`、`loop_group`、`unit`、`sample_batch` | 非空 |
| `window_id` | string | 是 | 统计窗口 | 不可变 |
| `metric_name` | enum | 是 | KPI 枚举 | 必须在公式库中存在 |
| `metric_value` | number/null | 否 | KPI 值 | `value_status=available` 时必填 |
| `metric_unit` | string | 是 | `%`、`count`、`score` 等 | 非空 |
| `value_status` | enum | 是 | `available`、`partial`、`unavailable`、`inconclusive`、`error` | 非空 |
| `pass_status` | enum | 否 | `pass`、`fail`、`warning`、`not_applicable` | 可为空 |
| `numerator` | number/null | 否 | 分子或统计量 | 有公式时建议填 |
| `denominator` | number/null | 否 | 分母或统计基数 | 有公式时建议填 |
| `threshold_value` | number/null | 否 | 阈值 | 有阈值时填 |
| `threshold_direction` | enum/null | 否 | `gte`、`lte`、`range` | 有阈值时填 |
| `formula_version` | string | 是 | 公式版本 | 必须 frozen |
| `threshold_version` | string | 是 | 阈值版本 | 必须 frozen |
| `ledger_version` | string | 是 | 台账版本 | 非空 |
| `mapping_version` | string | 是 | 映射版本 | 非空 |
| `quality_rule_version` | string | 是 | 质量规则版本 | 非空 |
| `mode_mapping_version` | string | 是 | MODE 映射版本 | 自控率必填 |
| `quality_summary` | object | 是 | 好值率、缺失率、冻结率 | 非空 |
| `input_refs` | array | 是 | 输入窗口、信号、质量 mask 引用 | 至少 1 个 |
| `exception_refs` | array | 否 | 排除记录引用 | 可为空 |
| `reason_codes` | array | 否 | 不可用、部分可用或失败原因 | `available` 外必填 |
| `result_snapshot_ref` | string | 是 | 结果快照引用 | 非空 |
| `generated_by_run` | string | 是 | 计算任务 | 非空 |
| `validity_status` | enum | 是 | `current`、`stale`、`superseded`、`invalid` | 非空 |
| `superseded_by` | string/null | 否 | 替代结果 | 可为空 |

### 7.4 状态机

| 状态 | 含义 | 可进入条件 | 可执行动作 |
|---|---|---|---|
| `pending` | 等待计算 | KPI run 已创建 | 开始计算 |
| `calculating` | 正在计算 | 任务运行中 | 等待 |
| `available` | 计算成功 | 输入、公式、阈值均有效 | 发布/被诊断引用 |
| `partial` | 部分可用 | 部分回路或窗口可算 | 展示覆盖率 |
| `unavailable` | 输入不足 | 缺 MODE、质量码或窗口 | 显示原因 |
| `inconclusive` | 结果不足以判断 | 数据质量低或样本不支持结论 | 展示“不可判定” |
| `error` | 计算失败 | 配置或系统错误 | 修复后重算 |
| `stale` | 输入版本变化 | 台账/公式/规则更新 | 阻止证据包纳入 |
| `superseded` | 被新结果替代 | 新结果发布 | 只读 |

### 7.5 接口规格

基础路径：`/api/p0/kpi`

| 方法 | 路径 | 用途 | 输入 | 输出 |
|---|---|---|---|---|
| `POST` | `/api/p0/kpi/runs` | 发起 KPI 计算 | `sample_batch_id`、`ledger_version`、`window_id`、metric list | `JobRun` |
| `GET` | `/api/p0/kpi/runs/{run_id}` | 查询计算任务 | 无 | run status |
| `GET` | `/api/p0/kpi/results` | 查询 KPI 结果 | batch、loop、metric、status、分页 | `KpiResult[]` |
| `GET` | `/api/p0/kpi/results/{id}` | KPI 详情 | 无 | `KpiResult` |
| `GET` | `/api/p0/kpi/summary` | 样本/装置摘要 | batch、scope | summary snapshot |
| `POST` | `/api/p0/kpi/results/{id}/invalidate` | 标记失效 | reason | invalidated result |

### 7.6 校验规则

- KPI 计算前必须存在 `ledger_status=frozen` 的 `LoopLedger`。
- `formula_version` 和 `threshold_version` 必须已固化。
- 缺 `MODE` 时，自控率相关 KPI 必须 `unavailable`。
- 缺 `OP` 时，有效自控率、OP 饱和相关判断必须 `partial` 或 `unavailable`。
- `metric_value=0` 只能表示真实 0，不得代表不可算。
- 异常排除必须通过 `ExceptionRecord` 引用，不允许在 KPI 中直接硬删数据。

## 8. DiagnosisFinding

### 8.1 作用

`DiagnosisFinding` 保存低性能或不可判定回路的诊断结论。它将 KPI、趋势窗口、事件、规则命中和建议动作连接起来，是工程师工作台的核心对象。

### 8.2 诊断类型

| `finding_type` | 中文名 | P0 |
|---|---|---|
| `pid_suspect` | 疑似 PID 参数问题 | 必做样例 |
| `valve_instrument_suspect` | 疑似阀门/仪表问题 | 必做样例 |
| `data_or_operating_issue` | 数据或运行状态问题 | 必做样例 |
| `process_disturbance` | 过程扰动 | 可选/弱化 |
| `loop_coupling` | 回路耦合 | 可选/弱化 |
| `distributed_oscillation` | 分布式振荡 | P1 |
| `no_issue` | 未发现问题 | P0 可用 |
| `inconclusive` | 不可判定 | 必做 |

### 8.3 字段规格

| 字段 | 类型 | 必填 | 说明 | P0 校验 |
|---|---|---:|---|---|
| `finding_id` | string | 是 | 诊断 ID | 唯一 |
| `sample_batch_id` | string | 是 | 样本批次 | 非空 |
| `loop_id` | string | 是 | 回路 ID | 非空 |
| `evidence_window_id` | string | 是 | 证据窗口 | 必须存在 |
| `finding_type` | enum | 是 | 诊断类型 | 非空 |
| `severity` | enum | 是 | `critical`、`high`、`medium`、`low`、`info` | 非空 |
| `confidence_score` | number/null | 否 | 0-100 | 不可判定可为空 |
| `confidence_level` | enum | 是 | `high`、`medium`、`low`、`none` | 非空 |
| `diagnosis_status` | enum | 是 | 见 8.4 | 非空 |
| `kpi_result_refs` | array | 是 | 相关 KPI | 至少 1 个，除非数据错误 |
| `evidence_refs` | array | 是 | 趋势、事件、规则、质量、MODE 引用 | 至少 1 个 |
| `rule_hits` | array | 否 | 规则命中列表 | 可为空 |
| `rule_version` | string | 是 | 诊断规则版本 | 必须 frozen |
| `reason_codes` | array | 是 | 诊断原因码 | 非空 |
| `explanation` | string | 是 | 面向工程师的解释 | 非空 |
| `suggested_action` | enum | 是 | `tune_candidate`、`field_check`、`process_review`、`data_fix`、`observe`、`no_action` | 非空 |
| `next_owner_role` | enum | 是 | `instrument`、`process`、`operation`、`ot`、`safety`、`expert` | 非空 |
| `tunability_status` | enum | 是 | `tunable_candidate`、`not_tunable`、`needs_interactive_review`、`data_insufficient`、`undetermined` | 非空 |
| `review_required` | boolean | 是 | 是否需要专家/工程师复核 | 非空 |
| `review_refs` | array | 否 | 审核记录 | 可为空 |
| `generated_by_run` | string | 是 | 诊断任务 | 非空 |
| `validity_status` | enum | 是 | `current`、`stale`、`superseded`、`invalid` | 非空 |
| `superseded_by` | string/null | 否 | 替代诊断 | 可为空 |

### 8.4 状态机

| 状态 | 含义 | 可进入条件 | 可执行动作 |
|---|---|---|---|
| `candidate` | 候选结论 | 规则或排序生成 | 生成证据 |
| `generated` | 已生成 | 证据链组装完成 | 进入复核 |
| `needs_review` | 需要复核 | 置信度中低或建议动作涉及实施 | 专家/工程师审核 |
| `accepted` | 已认可 | 审核通过 | 可进入闭环记录 |
| `rejected` | 已驳回 | 审核否定 | 记录原因 |
| `inconclusive` | 不可判定 | 证据不足或数据冲突 | 展示原因，不进入实施 |
| `stale` | 结果过期 | KPI/台账/规则版本变化 | 重算 |
| `superseded` | 被替代 | 新诊断发布 | 只读 |

### 8.5 接口规格

基础路径：`/api/p0/diagnosis`

| 方法 | 路径 | 用途 | 输入 | 输出 |
|---|---|---|---|---|
| `POST` | `/api/p0/diagnosis/runs` | 发起诊断 | sample、ledger、KPI refs、rule version | `JobRun` |
| `GET` | `/api/p0/diagnosis/findings` | 查询诊断 | batch、loop、type、status、severity、分页 | `DiagnosisFinding[]` |
| `GET` | `/api/p0/diagnosis/findings/{id}` | 获取诊断详情 | 无 | `DiagnosisFinding` |
| `GET` | `/api/p0/diagnosis/findings/{id}/evidence` | 获取证据链 | 无 | evidence bundle |
| `POST` | `/api/p0/diagnosis/findings/{id}/review` | 提交复核结论 | decision、reason、confidence override | `ReviewRecord` |
| `POST` | `/api/p0/diagnosis/findings/{id}/invalidate` | 标记失效 | reason | invalidated finding |

### 8.6 校验规则

- `DiagnosisFinding` 必须引用至少一个 `KpiResult`，除非诊断类型是数据导入错误。
- `evidence_window_id` 必须指向不可变窗口。
- `confidence_level=high` 时必须有规则命中、KPI 支撑和趋势/事件/质量证据之一。
- `suggested_action=tune_candidate` 时，`tunability_status` 不能是 `not_tunable` 或 `data_insufficient`。
- `inconclusive` 不是失败，必须展示原因和下一步动作。
- P0 至少应形成 3 类典型诊断样例：PID 疑似、阀门/仪表疑似、数据/工况问题。

## 9. EvidencePackage

### 9.1 作用

`EvidencePackage` 是 P0 验收证据包的 manifest。它引用样本范围、台账版本、KPI 结果、诊断结论、审核/实施/复评记录、例外剔除和整定样例，不直接内嵌长时序原始数据。

### 9.2 字段规格

| 字段 | 类型 | 必填 | 说明 | P0 校验 |
|---|---|---:|---|---|
| `package_id` | string | 是 | 证据包 ID | 唯一 |
| `package_name` | string | 是 | 证据包名称 | 非空 |
| `package_type` | enum | 是 | `sample_validation`、`desktop_review`、`complete_loop_closure` | P0 可三选一 |
| `sample_batch_id` | string | 是 | 样本批次 | 必须存在 |
| `package_status` | enum | 是 | 见 9.3 | 非空 |
| `sample_scope_summary` | object | 是 | 装置、回路数、时间范围 | 非空 |
| `ledger_version` | string | 是 | 台账版本 | 非空 |
| `mapping_version` | string | 是 | 映射版本 | 非空 |
| `formula_version` | string | 是 | KPI 公式版本 | 非空 |
| `threshold_version` | string | 是 | 阈值版本 | 非空 |
| `quality_rule_version` | string | 是 | 质量规则版本 | 非空 |
| `mode_mapping_version` | string | 是 | MODE 映射版本 | 非空 |
| `rule_version` | string | 是 | 诊断规则版本 | 非空 |
| `included_kpi_results` | array | 是 | 纳入的 KPI 结果 ID | 至少包含 P0 核心 KPI |
| `included_findings` | array | 是 | 纳入的诊断结论 ID | 至少 3 类样例或说明不足 |
| `included_reviews` | array | 否 | 审核/实施记录 ID | 可为空，但状态降级 |
| `included_reevaluations` | array | 否 | 复评记录 ID | 可为空，但状态降级 |
| `included_exceptions` | array | 否 | 例外剔除记录 ID | 可为空 |
| `included_tuning_cases` | array | 否 | P0 整定样例 ID | 可为空或 1 条 |
| `coverage_metrics` | object | 是 | 映射率、好值率、低性能数量、闭环率 | 非空 |
| `risk_summary` | array | 是 | 当前样本不能证明什么 | 可为空数组 |
| `conclusion` | string | 是 | Sponsor 可读结论 | 非空 |
| `manifest_hash` | string | 是 | manifest 摘要 | 生成时计算 |
| `export_assets` | array | 否 | PDF/JSON/图片等导出物 | 可为空 |
| `generated_by_run` | string | 是 | 组包任务 | 非空 |
| `validity_status` | enum | 是 | `current`、`stale`、`superseded`、`invalid` | 非空 |
| `created_at` | datetime | 是 | 创建时间 | 自动 |
| `created_by` | string | 是 | 创建人 | 自动 |

### 9.3 状态机

| 状态 | 含义 | 可进入条件 | 用户可见文案 |
|---|---|---|---|
| `draft` | 已创建但未组装 | 创建后默认 | 证据包草稿 |
| `assembling` | 正在组装 | 发起 assemble | 正在组装证据包 |
| `ready` | 完整可用 | 必备结果均 current | 可导出 |
| `partial` | 部分可用 | 缺实施或复评、缺专家复核等 | 桌面评审版/模拟版 |
| `blocked` | 阻止生成 | 引用了 stale/superseded/invalid 结果 | 结果过期，请重算 |
| `stale` | 已过期 | 上游版本变化 | 需重新生成 |
| `exported` | 已导出 | 导出成功 | 已导出 |
| `archived` | 已归档 | 人工归档 | 只读 |

### 9.4 接口规格

基础路径：`/api/p0/evidence-packages`

| 方法 | 路径 | 用途 | 输入 | 输出 |
|---|---|---|---|---|
| `POST` | `/api/p0/evidence-packages` | 创建证据包 | sample、package type、included refs | draft package |
| `POST` | `/api/p0/evidence-packages/{id}/assemble` | 组装证据包 | 可选模板版本 | `JobRun` |
| `POST` | `/api/p0/evidence-packages/{id}/validate` | 校验证据包 | 无 | validation result |
| `GET` | `/api/p0/evidence-packages/{id}` | 获取 manifest | 无 | `EvidencePackage` |
| `GET` | `/api/p0/evidence-packages/{id}/summary` | Sponsor 摘要 | 无 | summary view model |
| `GET` | `/api/p0/evidence-packages/{id}/export` | 导出 | `format=pdf/json` | export asset |
| `POST` | `/api/p0/evidence-packages/{id}/archive` | 归档 | reason | archived package |

### 9.5 校验规则

- EvidencePackage 不能纳入 `validity_status != current` 的核心结果。
- EvidencePackage 不直接扫描长时序原始数据，只引用快照、窗口和导出资产。
- 缺 `ReviewRecord` 或 `Reevaluation` 时，证据包状态最多为 `partial`，不能标记 `ready`。
- 如果样本没有低性能回路，证据包必须写明“样本不能证明低性能排序能力”，而不是失败。
- `manifest_hash` 必须覆盖 included refs、版本字段、结论、风险摘要和导出模板版本。

## 10. 接口通用规范

### 10.1 API 风格

P0 建议采用 REST 风格接口，所有接口挂在 `/api/p0` 下。后续可再映射为 OpenAPI。

通用响应结构：

```json
{
  "request_id": "req_xxx",
  "data": {},
  "warnings": [],
  "errors": [],
  "meta": {
    "generated_at": "2026-06-16T10:00:00+08:00",
    "api_version": "p0.v1"
  }
}
```

### 10.2 异步任务

导入、KPI 计算、诊断、证据包组装都应使用 `JobRun`。

| 字段 | 类型 | 说明 |
|---|---|---|
| `job_run_id` | string | 任务 ID |
| `job_type` | enum | `import`、`validate`、`kpi_compute`、`diagnosis`、`package_assemble`、`export` |
| `input_refs` | array | 输入对象引用 |
| `status` | enum | `queued`、`running`、`succeeded`、`failed`、`cancelled` |
| `started_at` | datetime/null | 开始时间 |
| `finished_at` | datetime/null | 结束时间 |
| `failure_reason` | string/null | 失败原因 |
| `result_refs` | array | 输出对象引用 |

### 10.3 错误码

| 错误码 | 触发条件 | 用户提示 |
|---|---|---|
| `SAMPLE_SOURCE_INVALID` | 样本来源无法解析 | 样本批次解析失败，请检查来源与时间窗 |
| `REQUIRED_SIGNAL_MISSING` | 缺 PV/SP/OP/MODE | 数据不足，不可高可信评估 |
| `FORMULA_VERSION_MISSING` | KPI 公式未固化 | 指标口径未固化 |
| `QUALITY_RULE_MISSING` | 质量规则缺失 | 质量码或质量规则无法解析 |
| `EVIDENCE_WINDOW_STALE` | 证据窗口过期 | 证据窗口失效，请重新生成 |
| `RESULT_SUPERSEDED` | 引用了被替代结果 | 结果已过期，请重新生成 |
| `PACKAGE_INCOMPLETE` | 缺审核或复评记录 | 当前证据包为桌面评审版/模拟版 |

### 10.4 幂等与审计

- 写接口必须支持 `Idempotency-Key`。
- 人工修改、冻结、审核、归档必须写审计日志。
- 系统生成对象必须记录 `generated_by_run`。
- 人工复核必须记录 `actor_id`、`role`、`decision`、`reason`、`timestamp`。

## 11. 跨对象状态传播

| 变更 | 影响 |
|---|---|
| `LoopLedger` 新版本冻结 | 旧版本生成的 KPI、诊断和证据包保持只读；新计算必须引用新版本 |
| `mapping_version` 变化 | 旧 KPI 与诊断标记为 `stale` 或被新结果 `superseded` |
| `formula_version` 变化 | 相关 KPI 必须重算，引用旧 KPI 的诊断和证据包变 stale |
| `threshold_version` 变化 | KPI pass/fail 和低性能排序必须重算 |
| `quality_rule_version` 变化 | 好值率、可评估状态、证据链可信度必须重算 |
| `rule_version` 变化 | 诊断结果必须重算或标记为旧规则 |
| `DiagnosisFinding` 被驳回 | EvidencePackage 中应显示专家驳回原因，不能作为已认可证据 |
| `ReviewRecord` 或 `Reevaluation` 新增 | EvidencePackage 闭环完成率可更新，但需重新 assemble |

## 12. 契约测试

### 12.1 SampleBatch 测试

| 用例 | 输入 | 期望 |
|---|---|---|
| happy | 50-100 回路，PV/SP/OP/MODE 来源完整 | 状态 `ready` |
| empty | 0 回路 | 状态 `insufficient`，提示样本不可验证 |
| missing source | source 文件不存在或 hash 不匹配 | `import_failed` |
| partial source | 回路数不足 50 | `partial/insufficient`，但可保留批次 |
| no quality mark | 无质量码也无人工规则 | 不允许高可信输出 |

### 12.2 LoopLedger 测试

| 用例 | 输入 | 期望 |
|---|---|---|
| complete mapping | 95% 以上回路具备 PV/SP/OP/MODE | 可进入 `ready_to_freeze` |
| missing MODE | 部分回路缺 MODE | 回路 `data_insufficient`，自控率不可算 |
| manual correction | 人工修正点位 | 生成新 `mapping_version` |
| frozen update | 修改 frozen 台账 | 阻止原地修改 |
| excluded without reason | 回路 excluded 但无 ExceptionRecord | freeze 失败 |

### 12.3 KpiResult 测试

| 用例 | 输入 | 期望 |
|---|---|---|
| formula locked | 公式和阈值版本已固化 | KPI `available` |
| formula missing | 无公式版本 | KPI 发布阻止 |
| missing MODE | 自控率计算缺 MODE | `unavailable`，不是 0 |
| partial OP | 缺 OP | 有效自控率 `partial/unavailable` |
| exception applied | 有审批排除窗口 | KPI 引用 `exception_refs` |

### 12.4 DiagnosisFinding 测试

| 用例 | 输入 | 期望 |
|---|---|---|
| PID suspect | KPI 异常且趋势证据存在 | 生成 `pid_suspect` |
| valve/instrument suspect | OP/PV 证据支持 | 生成 `valve_instrument_suspect` |
| data issue | 好值率低或 MODE 缺失 | 生成 `data_or_operating_issue` |
| inconclusive | 证据不足 | 生成 `inconclusive`，显示下一步 |
| stale KPI | 引用过期 KPI | 诊断阻止发布或标记 stale |

### 12.5 EvidencePackage 测试

| 用例 | 输入 | 期望 |
|---|---|---|
| complete | 样本、KPI、诊断、审核、复评均 current | 状态 `ready` |
| no review | 缺审核记录 | 状态 `partial` |
| superseded KPI | 引用被替代 KPI | 状态 `blocked` |
| no low performance | 没有低性能回路 | 状态可 partial，但结论说明样本限制 |
| export | manifest ready | 生成 PDF/JSON 导出资产 |

## 13. 前端 ViewModel 建议

P0 页面不应直接消费原始契约对象，而应由后端提供面向页面的 view model。

| 页面 | ViewModel | 来源对象 |
|---|---|---|
| 样本验证仪表盘 | `SampleValidationSummary` | `SampleBatch`、`LoopLedger`、`KpiResult`、`EvidencePackage` |
| 数据雷达 | `ReadinessRadarView` | `LoopLedger.LoopRecord`、`KpiResult` |
| 低性能清单 | `LowPerformanceListItem[]` | `KpiResult`、`DiagnosisFinding` |
| 单回路证据链 | `LoopEvidenceView` | `DiagnosisFinding`、`KpiResult`、TimeWindow refs |
| 审核/实施/复评 | `ClosureWorkflowView` | `DiagnosisFinding`、`ReviewRecord`、`Reevaluation` |
| 证据包摘要 | `EvidencePackageSummary` | `EvidencePackage` |

## 14. 实现顺序

| 顺序 | 工作 | 输出 |
|---:|---|---|
| 1 | 定义枚举、公共字段、版本引用 | 契约基础类型 |
| 2 | 实现 `SampleBatch` 导入和验证 | 可形成样本批次 |
| 3 | 实现 `LoopLedger` 映射和冻结 | 可固化台账版本 |
| 4 | 实现 `KpiResult` 计算和快照 | 可支撑低性能清单 |
| 5 | 实现 `DiagnosisFinding` 和证据引用 | 可支撑单回路证据链 |
| 6 | 实现审核/复评轻量记录 | 可支撑闭环 |
| 7 | 实现 `EvidencePackage` manifest | 可支撑 sponsor 汇报和导出 |
| 8 | 补齐契约测试 | 防止版本、状态和证据链断裂 |

## 15. 开放问题

| 问题 | 影响 | 建议处理 |
|---|---|---|
| `formula_version` 和 `threshold_version` 是否独立管理 | 影响 KPI 重算边界 | 建议独立，便于阈值调整不改公式 |
| 质量规则是否允许人工修正 | 影响好值率可信度 | P0 允许人工规则，但必须版本化 |
| `ReviewRecord` 是否纳入本文件完整字段 | 影响闭环接口 | 本文件先作为引用，后续可单独补审核/复评契约 |
| 诊断规则是否采用规则引擎 | 影响实现复杂度 | P0 可先用版本化规则配置，不引入复杂规则引擎 |
| 证据包 PDF 是否必须 P0 完成 | 影响交付 | P0 至少支持摘要页和 JSON manifest，PDF 可按原型需求决定 |
