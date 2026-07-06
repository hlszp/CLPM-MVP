# IDS v4.0 差距分析

**生成日期**：2026-07-06
**分析对象**：`docs/设计文档/05-IDS/IDS.md`（当前版本 v4.0，2026-06-26 发布）
**基准来源**：`docs/过程文档/superpowers/plans/v6-baseline-extract.md`（FDS v5.1 / DDS v4.1 / 实现契约 v1.0 / UIUX v5.3）

---

## 1. 版本号声明差距

### 差距 1: IDS 当前版本号声明与基准期望存在版本代差
- **来源文档**：IDS
- **差距类型**：版本号
- **当前值**：IDS 声明 `v4.0`（2026-06-26 发布）；设计依据引用 `PRD (v3.0), FDS (v3.0), ADS (v3.0), DDS (v3.0), 关键算法设计说明 (v1.0), 关键算法设计说明 v2.0`
- **基准值**：v6.0 升级目标要求对齐 FDS v5.1（2026-07-04）、DDS v4.1（2026-07-04）、ADS v4.0（2026-06-26）、PRD v3.1、UIUX v5.3、实现契约 v1.0
- **修复方案**：将 IDS 升级至 v6.0，并将设计依据更新为 `PRD (v3.1), FDS (v5.1), ADS (v4.0), DDS (v4.1), UIUX (v5.3), 实现契约 (v1.0), 关键算法设计说明 v2.0`

### 差距 2: 设计依据引用文档版本严重过期
- **来源文档**：IDS
- **差距类型**：引用
- **当前值**：IDS 头部声明引用 `PRD v3.0 / FDS v3.0 / ADS v3.0 / DDS v3.0`
- **基准值**：FDS 实际为 v5.1（落后 2 个大版本），DDS 实际为 v4.1（落后 1 个大版本），ADS 实际为 v4.0（落后 1 个大版本），PRD 实际为 v3.1（落后 1 个小版本）
- **修复方案**：同步更新所有引用版本号；§0 变更记录追加 v5.0/v6.0 升级条目，记录"对齐 FDS v5.1 的 3+1+8 体系、DDS v4.1 的 17 张表/字段、实现契约 v1.0 的 6 大 API 领域路径"

---

## 2. API 端点差距

### 差距 3: 用户管理 API 路径不一致
- **来源文档**：IDS
- **差距类型**：API
- **当前值**：IDS §2.6.1/§2.6.2 使用 `/api/v1/system/users` 和 `/api/v1/system/users/{userId}/role`
- **基准值**：实现契约 v1.0 §4.4 规定用户管理领域路径为 `/api/v1/users/*`，并明确"不强制改为 `/api/v1/system/users`"——但 IDS 当前实现恰好是基线反对的写法
- **修复方案**：将 `/api/v1/system/users` 路径统一改为 `/api/v1/users`；§2.6 系统管理 API 组说明改为"用户管理 API 路径遵循实现契约 v1.0 §4.4，使用 `/api/v1/users/*`"

### 差距 4: 审计日志 API 路径不一致
- **来源文档**：IDS
- **差距类型**：API
- **当前值**：IDS §2.6.3 使用 `/api/v1/system/audit`
- **基准值**：实现契约 v1.0 §4.4 规定审计日志领域路径为 `/api/v1/audit-logs/*`
- **修复方案**：将 `/api/v1/system/audit` 改为 `/api/v1/audit-logs`

### 差距 5: 报表管理 API 路径不一致
- **来源文档**：IDS
- **差距类型**：API
- **当前值**：IDS §2.6.4/§2.6.5 使用 `/api/v1/system/reports` 和 `/api/v1/system/reports/{reportId}/retry`
- **基准值**：实现契约 v1.0 §4.4 规定报表管理领域路径为 `/api/v1/reports/*`
- **修复方案**：将 `/api/v1/system/reports` 改为 `/api/v1/reports`，相应子路径同步调整

### 差距 6: IDS 包含实现契约明确禁止的 `/api/v1/configs/metrics` 聚合接口
- **来源文档**：IDS
- **差距类型**：API
- **当前值**：IDS §2.8.1/§2.8.2 定义了 `GET/PUT /api/v1/configs/metrics` 批量聚合接口
- **基准值**：实现契约 v1.0 §4.4 明确"性能配置与看板"领域路径为 `/api/v1/performance/*`，且"不新增 `/api/v1/configs/metrics` 聚合接口"
- **修复方案**：删除 §2.8 整节，将批量指标配置能力合并到 §2.3 性能评估 API 组下（如 `GET/PUT /api/v1/performance/metrics:batch`）；或在实现契约 v2.0 中追认此聚合接口的存在

### 差距 7: IDS 包含实现契约明确禁止的 `/api/v1/configs/diagnosis` 聚合接口
- **来源文档**：IDS
- **差距类型**：API
- **当前值**：IDS §2.9.1/§2.9.2 定义了 `GET/PUT /api/v1/configs/diagnosis` 批量聚合接口
- **基准值**：实现契约 v1.0 §4.4 明确"诊断配置与跟踪"领域路径为 `/api/v1/diagnosis/*`，且"不新增 `/api/v1/configs/diagnosis` 聚合接口"
- **修复方案**：删除 §2.9 整节，将批量诊断配置能力合并到 §2.4 诊断中心 API 组下（如 `GET/PUT /api/v1/diagnosis/config:batch`）；或在实现契约 v2.0 中追认

### 差距 8: Action Tracker 路径未归并到诊断领域
- **来源文档**：IDS
- **差距类型**：API
- **当前值**：IDS §2.4.6/§2.4.7 使用独立路径 `/api/v1/tracker/{loopId}/status` 和 `/api/v1/tracker/{loopId}/export`
- **基准值**：实现契约 v1.0 §4.4 明确"诊断配置与跟踪"统一归入 `/api/v1/diagnosis/*`，Action Tracker 是诊断中心的子模块
- **修复方案**：将 `/api/v1/tracker/{loopId}/status` 改为 `/api/v1/diagnosis/tracker/{loopId}/status`；将 `/api/v1/tracker/{loopId}/export` 改为 `/api/v1/diagnosis/tracker/{loopId}/export`

### 差距 9: IDS 拥有实现契约 6 大领域之外的多个 API 领域
- **来源文档**：IDS
- **差距类型**：API
- **当前值**：IDS 包含 `/api/v1/dashboard/*`、`/api/v1/plant-nodes/*`、`/api/v1/aas/*`、`/api/v1/loops/*`、`/api/v1/timeseries/*`、`/api/v1/algorithms/*`、`/api/v1/tasks/*` 等基线 6 大领域（performance/diagnosis/tuning/users/audit-logs/reports）之外的路径
- **基准值**：实现契约 v1.0 §4.4 仅明确 6 大领域路径；这些支撑性 API 未被基线显式列举
- **修复方案**：在实现契约 v2.0 中追认这些支撑性 API 领域；或将 `/api/v1/dashboard/*` 归入 `/api/v1/performance/*`，`/api/v1/timeseries/*` 归入 `/api/v1/diagnosis/*`，`/api/v1/algorithms/*` 与 `/api/v1/tasks/*` 作为内部算法服务域单独说明

---

## 3. 请求/响应 Schema 差距

### 差距 10: §2.2.8 scoreWeights 字段结构与 3+1+8 体系不一致
- **来源文档**：IDS
- **差距类型**：Schema
- **当前值**：IDS §2.2.8/§2.2.9/§2.2.10 的 `scoreWeights` 字段含 6 个 key：`good_value_rate`/`auto_mode_rate`/`steady_rate`/`accuracy_rate`/`oscillation_rate`/`saturation_rate`
- **基准值**：FDS v5.1 与 IDS 自身 §2.8 的 3+1+8 体系规定：仅 3 项核心指标（`accuracy_rate`/`fast_rate`/`steady_rate`）参与权重配置；投用指标 `effective_auto_rate` 作为折扣因子；辅助诊断指标权重固定为 `null`
- **修复方案**：将 §2.2.8/§2.2.9/§2.2.10 的 `scoreWeights` 改为 `{ "accuracy_rate": 40, "fast_rate": 30, "steady_rate": 30 }`；新增 `commissioningMetric` 字段引用 `effective_auto_rate`；`ERR_METRIC_WEIGHT_SUM` 校验仅针对 3 项核心指标

### 差距 11: 缺少 `grading_thresholds` 字段（5 级性能定级）
- **来源文档**：IDS
- **差距类型**：Schema
- **当前值**：IDS §2.3.3/§2.8.1 的指标配置响应仅含 `threshold`（`{min, max, alert}`）字段
- **基准值**：DDS v4.1 §3.6 明确 `metric_config` 表新增 `grading_thresholds` 字段，对应 FDS v5.1 §1.3 的 5 级性能定级（EXCELLENT/GOOD/FAIR/WARNING/POOR）
- **修复方案**：在 §2.3.3/§2.8.1 的响应 Schema 中增加 `gradingThresholds` 字段（JSONB 对象，含 EXCELLENT/GOOD/FAIR/WARNING/POOR 5 级阈值）

### 差距 12: 缺少 `loop_ledger` 新增字段 `control_type`/`importance_level`/`include_in_evaluation`
- **来源文档**：IDS
- **差距类型**：Schema
- **当前值**：IDS §2.2.7/§2.2.8/§2.2.9 的回路列表/创建/详情响应未包含 `controlType`、`importanceLevel`、`includeInEvaluation` 字段
- **基准值**：DDS v4.1 §3.6 明确 `loop_ledger` 表新增 `control_type`（控制类型，对应 STABLE/SLOW/FAST/LOGIC）、`importance_level`（重要性等级）、`include_in_evaluation`（是否参与评估）三个字段
- **修复方案**：在 §2.2.7/§2.2.8/§2.2.9 的响应 Schema 中增加 `controlType`/`importanceLevel`/`includeInEvaluation` 字段；§2.2.8 创建回路请求体支持这三个字段

### 差距 13: 缺少 `unit_kpi_summary` 的 `excluded_loops`/`status` 字段
- **来源文档**：IDS
- **差距类型**：Schema
- **当前值**：IDS §2.3.1/§2.3.7 的装置级 KPI 响应未包含 `excludedLoops` 和 `status` 字段
- **基准值**：DDS v4.1 §3.6 明确 `unit_kpi_summary` 表新增 `excluded_loops`（排除的回路列表）和 `status`（汇总状态）字段
- **修复方案**：在装置级 KPI 响应 Schema 中增加 `excludedLoops`（数组）和 `status` 字段

### 差距 14: 数据血缘字段结构与 DDS v4.1 不一致
- **来源文档**：IDS
- **差距类型**：Schema
- **当前值**：IDS §2.7.1/§2.7.4 将 `data_lineage` 描述为含 3 个子字段（`sampling_freq`/`quality_policy`/`tag_group`）的对象；回路级 `data_lineage` 增加 `valid_rate`/`source_metrics` 共 5 个子字段
- **基准值**：DDS v4.1 §3.5 明确 `kpi_snapshot_hourly` 与 `kpi_snapshot_custom` 均包含 5 个独立字段：`sampling_freq`/`quality_policy`/`valid_rate`/`confidence_level`/`data_lineage`（JSONB），其中 `data_lineage` JSONB 内部包含 6 个子字段
- **修复方案**：将 `sampling_freq`/`quality_policy`/`valid_rate`/`confidence_level` 提升为与 `data_lineage` 平级的独立字段（对齐 DDS v4.1 表结构）；`data_lineage` 作为 JSONB 容器仅承载派生元数据（如 `tag_group`/`source_metrics`/`aggregation_policy` 等 6 个子字段）

---

## 4. 错误码差距

### 差距 15: `ERR_METRIC_WEIGHT_SUM` 错误描述与 3+1+8 体系不一致
- **来源文档**：IDS
- **差距类型**：错误码
- **当前值**：IDS §3.2 描述为"更新性能指标配置时，6 项指标权重总和 ≠ 100%"；§3.4 错误响应示例"当前 6 项指标权重总和为 95%"
- **基准值**：3+1+8 体系下仅 3 项核心指标（`accuracy_rate`/`fast_rate`/`steady_rate`）参与权重校验
- **修复方案**：将描述改为"更新性能指标配置时，3 项核心指标权重总和 ≠ 100%"；错误响应示例改为"当前 3 项核心指标权重总和为 95%"

### 差距 16: 缺少投用指标与辅助诊断指标相关错误码
- **来源文档**：IDS
- **差距类型**：错误码
- **当前值**：IDS §3 仅定义了 `ERR_METRIC_WEIGHT_SUM`/`ERR_CONTROL_TYPE_INVALID` 等针对 6 大 KPI 体系的错误码
- **基准值**：3+1+8 体系下投用指标（折扣因子）与辅助诊断指标有独立的校验规则（如折扣因子不可修改、辅助指标权重必须为 null）
- **修复方案**：新增 `ERR_DISCOUNT_FACTOR_READONLY`（投用指标折扣因子不可修改）、`ERR_AUXILIARY_METRIC_WEIGHT_FORBIDDEN`（辅助诊断指标不允许配置权重）等错误码

---

## 5. 状态机差距

### 差距 17: Action Tracker 状态枚举与实现契约冲突
- **来源文档**：IDS
- **差距类型**：状态机
- **当前值**：IDS §2.4.1/§2.4.6/§2.4.10/§2.4.12 多处使用 `status` 枚举值 `PENDING`/`IN_PROGRESS`/`RESOLVED`/`IGNORED`；§2.4.6 说明"标记为 `RESOLVED` 后系统自动截取..."
- **基准值**：实现契约 v1.0 §4.6 明确 Action Tracker 标准枚举为 `PENDING` → `IN_PROGRESS` → `IMPLEMENTED` / `IGNORED`，并强调"历史文档中的 `RESOLVED` 统一视为旧命名；当前代码与后续文档使用 `IMPLEMENTED`"
- **修复方案**：全文替换 `RESOLVED` 为 `IMPLEMENTED`；§2.4.6 说明改为"标记为 `IMPLEMENTED` 后系统自动截取实施前后数据窗口生成 A/B 对比视图"

### 差距 18: Loop 状态枚举大小写不一致且混用 KPI 快照状态
- **来源文档**：IDS
- **差距类型**：状态机
- **当前值**：IDS §2.2.7 使用 `status` 枚举值 `Ready`/`Partial`/`INCONCLUSIVE`（首字母大写 + 全大写混用）
- **基准值**：实现契约 v1.0 §4.6 明确 Loop 标准枚举为 `READY`/`PARTIAL`/`INACTIVE`（全大写）；`INCONCLUSIVE` 是 KPI 快照状态，不是 Loop 状态
- **修复方案**：将 §2.2.7 等处 Loop 的 `status` 枚举统一改为 `READY`/`PARTIAL`/`INACTIVE`（全大写）；移除对 `INCONCLUSIVE` 作为 Loop 状态的误用；§2.2.14 的 KPI 快照状态保持 `INCONCLUSIVE`

### 差距 19: Tuning 状态枚举与实现契约完全不同
- **来源文档**：IDS
- **差距类型**：状态机
- **当前值**：IDS §2.5.1 使用 `status` 枚举值 `PENDING`/`IN_PROGRESS`/`COMPLETED`/`FAILED`
- **基准值**：实现契约 v1.0 §4.6 明确 Tuning 标准枚举为 `DRAFT`/`RUNNING`/`COMPLETED`/`ROLLED_BACK`
- **修复方案**：将 §2.5.1 的 `status` 枚举改为 `DRAFT`/`RUNNING`/`COMPLETED`/`ROLLED_BACK`；`PENDING` → `DRAFT`，`IN_PROGRESS` → `RUNNING`，`FAILED` → `ROLLED_BACK`（注意：`FAILED` 在 IDS 中表示失败，但基线用 `ROLLED_BACK` 表示已回退，失败应使用其他枚举如 `FAILED` 保留或在实现契约 v2.0 中追认）

---

## 6. 权限差距

### 差距 20: 角色枚举名与实现契约不一致
- **来源文档**：IDS
- **差距类型**：权限
- **当前值**：IDS §2.6.1 `role` 枚举值为 `EXECUTOR`（仪控工程师）/`COLLABORATOR`（工艺/设备工程师）/`VIEWER`（Sponsor）/`ADMIN`/`EXPERT`；§4.3 权限层级使用中文描述（查看层/协同层/执行层/管理层/服务层）
- **基准值**：实现契约 v1.0 §4.5 明确 5 角色为 `ADMIN`/`IC_ENGINEER`/`PE_ENGINEER`/`EXPERT`/`SPONSOR`
- **修复方案**：将 `EXECUTOR` 改为 `IC_ENGINEER`；`COLLABORATOR` 改为 `PE_ENGINEER`；`VIEWER` 改为 `SPONSOR`；§4.3 权限层级表的角色列同步更新

### 差距 21: 各端点的权限角色标注需统一为基线 5 角色口径
- **来源文档**：IDS
- **差距类型**：权限
- **当前值**：IDS 各端点权限描述使用"查看层及以上""执行层及以上""管理层"等层级化描述，未明确对应到 `IC_ENGINEER`/`PE_ENGINEER`/`SPONSOR`/`EXPERT`/`ADMIN` 角色
- **基准值**：实现契约 v1.0 §4.5 给出 5 角色的精确权限矩阵（如 SPONSOR 不可进入单回路诊断详情、波形证据或异常跟踪编辑）
- **修复方案**：在每个端点的"权限"字段补充精确的角色枚举（如"IC_ENGINEER, ADMIN"），并保留层级化描述作为辅助说明；§2.4.2 诊断详情端点需补充 SPONSOR 不可访问的明确限制

---

## 7. 数据模型差距

### 差距 22: IDS 引用的表名与 DDS v4.1 的 17 张表清单存在差异
- **来源文档**：IDS
- **差距类型**：数据模型
- **当前值**：IDS 仅显式引用 `tag_registry` 表（§2.2.6），其余接口响应字段未明确对应到 DDS v4.1 的表名
- **基准值**：DDS v4.1 §3.2 定义 17 张表（`loop_ledger`/`tag_registry`/`loop_tag_mapping`/`plant_node`/`metric_config`/`kpi_snapshot_hourly`/`kpi_snapshot_custom`/`unit_kpi_summary`/`clpm_metric_data_requirement`/`action_tracker`/`tuning_record`/`report_record`/`sys_user`/`sys_role`/`sys_user_role`/`sys_audit_log`/`report_schedule`）
- **修复方案**：在 IDS §4 通用约定中新增"数据模型映射"小节，列出各 API 响应字段与 DDS v4.1 表/字段的对应关系

### 差距 23: `metric_config` 表字段映射不完整
- **来源文档**：IDS
- **差距类型**：数据模型
- **当前值**：IDS §2.3.3/§2.8.1 响应字段含 `metricId`/`metricKey`/`metricName`/`formula`/`weight`/`threshold`/`controlType`/`isEnabled`/`description`/`algorithmVersion`
- **基准值**：DDS v4.1 的 `metric_config` 表新增 `category`（CORE/COMMISSIONING/AUXILIARY_DIAGNOSTIC）、`is_discount_factor`、`grading_thresholds` 字段
- **修复方案**：在响应 Schema 中显式增加 `category`/`isDiscountFactor`/`gradingThresholds` 字段（`category` 已在 §2.8.1 出现，需补到 §2.3.3）

### 差距 24: `kpi_snapshot_custom` 字段名 `stability_rate`/`steady_rate` 区分未在 IDS 体现
- **来源文档**：IDS
- **差距类型**：数据模型
- **当前值**：IDS §2.2.14/§2.3.1 等处统一使用 `steady_rate`（稳定率）字段名
- **基准值**：DDS v4.1 §3.7 明确术语统一：loop-level 字段名为 `steady_rate`，unit-level 聚合字段名为 `stability_rate`；§3.6 提到 `kpi_snapshot_custom.stability_rate` 已修正为 `steady_rate`
- **修复方案**：在装置级 KPI 响应（如 §2.3.1 `kpiSummary`、§2.3.7 `unitRanking`）中将 `steady_rate` 改为 `stability_rate`；回路级 KPI 响应保持 `steady_rate`

### 差距 25: TDengine 超级表 `st_loop_data` 的字段定义未在 IDS 体现
- **来源文档**：IDS
- **差距类型**：数据模型
- **当前值**：IDS 未提及 TDengine 时序数据存储结构
- **基准值**：DDS v4.1 §3.3 定义 1 个超级表 `st_loop_data`，2 个 Tag 列（`loop_id`/`plant_node_id`），9 个 Field 列（`ts`/`pv`/`sp`/`op`/`mode`/`quality`/`pid_p`/`pid_i`/`pid_d`），子表命名规则 `loop_{loop_id}`
- **修复方案**：在 IDS §4 通用约定中新增"时序数据存储"小节，说明 TDengine 超级表结构与 §2.4.5 波形 API 的对应关系

---

## 8. KPI 指标差距

### 差距 26: IDS 内部 KPI 体系口径自相矛盾（§2.3 vs §2.8）
- **来源文档**：IDS
- **差距类型**：KPI
- **当前值**：IDS §2.3.1/§2.3.2/§2.3.3 仍使用"6 大 KPI（good_value_rate/auto_mode_rate/steady_rate/accuracy_rate/oscillation_rate/saturation_rate）"口径；§2.8 已升级为 3+1+8 体系（3 核心+1 投用+8 辅助诊断）
- **基准值**：FDS v5.1 §1.3 明确 3+1+8 体系；实现契约 v1.0 §4.7 说明"对外口径仍可强调 6 大核心 KPI，但实现需保留 2 个扩展派生指标"
- **修复方案**：将 §2.3.1/§2.3.2/§2.3.3 的 KPI 卡片响应改为 3 核心指标（`accuracy_rate`/`fast_rate`/`steady_rate`）+ 综合评分；§2.3.7 `metricKey` 枚举值同步更新；保留 §2.8 的 3+1+8 完整结构作为配置接口

### 差距 27: 置信度等级阈值与 FDS v5.1 不一致
- **来源文档**：IDS
- **差距类型**：KPI
- **当前值**：IDS §2.7.1 定义 `confidence_level` 等级规则：A（valid_rate ≥ 0.95）/B（0.90-0.95）/C（0.80-0.90）/D（0.60-0.80）/E（< 0.60）
- **基准值**：FDS v5.1 §1.3 与 AGENTS.md 明确 A/B/C/D/E 五级可信度阈值为 95/80/60/20%（即 A≥95% / B≥80% / C≥60% / D≥20% / E<20%）
- **修复方案**：将 §2.7.1 的阈值改为 A（valid_rate ≥ 0.95）/B（0.80-0.95）/C（0.60-0.80）/D（0.20-0.60）/E（< 0.20）

### 差距 28: KPI 状态枚举混用性能定级与快照状态
- **来源文档**：IDS
- **差距类型**：KPI
- **当前值**：IDS §2.3.1/§2.3.2 等处 KPI 卡片 `status` 枚举值为 `GOOD`/`WARNING`/`POOR`/`INCONCLUSIVE`
- **基准值**：FDS v5.1 §1.3 定义 5 级性能定级（`EXCELLENT`/`GOOD`/`FAIR`/`WARNING`/`POOR`）；实现契约 v1.0 §4.6 定义 KPI 快照状态（`SUCCESS`/`PARTIAL`/`INCONCLUSIVE`）——这是两个不同概念
- **修复方案**：将 KPI 卡片的性能定级 `status` 改为 5 级（`EXCELLENT`/`GOOD`/`FAIR`/`WARNING`/`POOR`）；新增 `snapshotStatus` 字段表示快照计算状态（`SUCCESS`/`PARTIAL`/`INCONCLUSIVE`）；移除 `INCONCLUSIVE` 作为性能定级的误用

### 差距 29: 缺少投用指标 `effective_auto_rate` 作为折扣因子的端点暴露
- **来源文档**：IDS
- **差距类型**：KPI
- **当前值**：IDS §2.3.1 的 `kpiCards` 仅返回 6 大 KPI + 综合评分，未包含投用指标 `effective_auto_rate`
- **基准值**：FDS v5.1 §1.3 明确 1 投用指标（有效自控率 R）作为综合评分折扣因子
- **修复方案**：在 §2.3.1 的 `kpiCards` 中增加 `effective_auto_rate` 卡片（标记为 `COMMISSIONING` 类别）；`kpiSummary` 中增加 `effective_auto_rate` 字段与 `discountFactor` 字段

### 差距 30: §2.7.1 触发 KPI 计算的 `metrics` 参数枚举未对齐 3+1+8
- **来源文档**：IDS
- **差距类型**：KPI
- **当前值**：IDS §2.7.1 请求体 `metrics` 枚举值为 `good_value_rate`/`auto_mode_rate`/`steady_rate`/`accuracy_rate`/`oscillation_rate`/`saturation_rate`（6 项）
- **基准值**：3+1+8 体系下应支持 3 核心 + 1 投用 + 8 辅助诊断指标的全部 key
- **修复方案**：将 §2.7.1 `metrics` 枚举扩展为 12 项指标 key（含 `effective_auto_rate`/`stiction_index`/`overaggressive_index`/`overconservative_index`/`disturbance_index`/`quality_abnormal_rate`）；说明"为空数组时计算全部 12 项指标"

---

## 9. 引用文档差距

### 差距 31: 引用的《关键算法设计说明》版本号标注混乱
- **来源文档**：IDS
- **差距类型**：引用
- **当前值**：IDS 头部设计依据同时引用"关键算法设计说明 (v1.0)"和"关键算法设计说明 v2.0"，未明确以哪个为准
- **基准值**：FDS v5.1/DDS v4.1 均明确引用"关键算法设计说明 v2.0"
- **修复方案**：删除 v1.0 引用，统一为"关键算法设计说明 v2.0"

### 差距 32: 缺少对实现契约 v1.0 的引用
- **来源文档**：IDS
- **差距类型**：引用
- **当前值**：IDS 头部设计依据未引用实现契约 v1.0
- **基准值**：实现契约 v1.0 是重构后 IA/路由/API/权限/状态机/KPI 的事实来源，AGENTS.md 明确要求所有文档引用实现契约
- **修复方案**：在头部设计依据中新增"实现契约 (v1.0)"引用；在 §0 变更记录中说明"v6.0 对齐实现契约 v1.0 的 6 大 API 领域路径与 5 角色权限矩阵"

### 差距 33: 缺少对 UIUX v5.3 的引用
- **来源文档**：IDS
- **差距类型**：引用
- **当前值**：IDS 头部设计依据未引用 UIUX 设计规范
- **基准值**：UIUX v5.3（2026-07-04）是当前 UI/UX 事实来源，定义 32 个路由 path 与 5 角色权限矩阵
- **修复方案**：在头部设计依据中新增"UIUX (v5.3)"引用；在端点说明中补充对应的前端路由 path（如 §2.1 工作台 API 对应 `/dashboard/workbench`）

### 差距 34: 缺少对 GB/T 44693.2-2024 国标与 ADS v4.0 的引用
- **来源文档**：IDS
- **差距类型**：引用
- **当前值**：IDS 头部设计依据未引用 GB/T 44693.2-2024 国标与 ADS 设计规范
- **基准值**：FDS v5.1/DDS v4.1 均引用"GB/T 44693.2-2024"与"ADS v4.0"
- **修复方案**：在头部设计依据中新增"ADS (v4.0)"与"GB/T 44693.2-2024"引用

---

差距总数：34 条
