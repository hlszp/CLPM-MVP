# ADS v4.0 差距分析

**生成日期**：2026-07-06
**分析对象**：`docs/设计文档/03-ADS/ADS.md`（当前版本 v4.0，发布日期 2026-06-26）
**基准来源**：`docs/过程文档/superpowers/plans/v6-baseline-extract.md`（FDS v5.1 / UIUX v5.3 / DDS v4.1 / 实现契约 v1.0）
**对比维度**：版本号声明、架构组件、数据流、数据模型、API 端点、路由、权限、状态机、KPI 指标、模块清单、引用文档

---

## 1. 版本号声明差距

### 差距 1: ADS 当前版本号落后于基准文档群
- **来源文档**：ADS（§0 文档变更记录、文档头）
- **差距类型**：版本号
- **当前值**：ADS 声明 v4.0，发布日期 2026-06-26
- **基准值**：FDS v5.1（2026-07-04）、UIUX v5.3（2026-07-04）、DDS v4.1（2026-07-04）、实现契约 v1.0（2026-06-25）均已在 2026-07-04 完成新一轮升级
- **修复方案**：ADS 升级至 v6.0，发布日期更新为 2026-07-06，并在变更记录中标注"v6.0：对齐 FDS v5.1/DDS v4.1/UIUX v5.3/实现契约 v1.0"

### 差距 2: ADS 引用的 PRD 版本号过期
- **来源文档**：ADS（文档头"设计依据"行）
- **差距类型**：引用
- **当前值**：`**设计依据**: PRD (v3.0)、关键算法设计说明 v2.0、CLPM后端研发智能体系统提示词与开发指导文档`
- **基准值**：基准摘要 §5.1 指出 PRD 实际版本为 v4.0（2026-06-25），落后 1 个大版本
- **修复方案**：将"PRD (v3.0)"改为"PRD (v4.0)"

### 差距 3: ADS 缺少对 FDS/UIUX/DDS/实现契约 的引用
- **来源文档**：ADS（文档头"设计依据"行）
- **差距类型**：引用
- **当前值**：ADS 仅引用 PRD、关键算法设计说明、CLPM 后端研发智能体系统提示词，未引用 FDS、UIUX、DDS、实现契约
- **基准值**：基准文档群中 FDS v5.1、UIUX v5.3、DDS v4.1、实现契约 v1.0 均为 ADS 的下游约束，应被显式引用
- **修复方案**：在"设计依据"行补充"FDS v5.1、DDS v4.1、UIUX v5.3、实现契约 v1.0"

---

## 2. 架构组件差距

### 差距 4: ADS 内部"6 大 KPI"与"3+1+8"口径自相矛盾
- **来源文档**：ADS（§8.2 算法服务划分表 vs §13.1 合规映射表）
- **差距类型**：架构/KPI
- **当前值**：
  - §8.2 表格描述"KPI 计算服务"职责为"6 大 KPI 计算（好值率/自控率/稳定率/准确率/振荡率/饱和率）+ 综合评分"
  - §13.1 表格声明"3+1+8 指标体系"（3 核心+1 折扣+8 扩展）
  - §10.2 实际列出 10 个独立指标计算接口
  - §12.2 表格描述 `KPI_CALC` v1.0 为"6 大 KPI 计算"
- **基准值**：FDS v5.1 明确 3+1+8 体系 = 12 项指标计算器；实现契约 v1.0 §4.7 描述"6 大核心 KPI + 2 扩展派生指标"
- **修复方案**：统一术语为"3+1+8 体系（12 项指标计算器）"，对外合规口径仍可强调"6 大核心 KPI"，但 §8.2/§12.2 内部表述须与 §13.1 对齐，避免"6 大 KPI"与"3+1+8"并存

### 差距 5: ADS 未描述 12 项指标计算器的完整清单
- **来源文档**：ADS（§8.2、§10.2）
- **差距类型**：架构/KPI
- **当前值**：§10.2 仅列出 10 个独立指标接口（好值率/自控率/有效自控率/准确率/快速率/稳定率/振荡率/饱和率/粘滞系数/输出行程指数）
- **基准值**：FDS v5.1 §1.3 明确 12 项指标计算器，包含稳态时间、理想稳态时间等（与 §13.1 国标映射表中"附录 F.4 稳态时间"对应）
- **修复方案**：§10.2 表格补全至 12 项指标计算器，明确区分"3 核心质量指标 / 1 折扣因子 / 8 扩展指标"

### 差距 6: ADS 未给出预处理 Pipeline 的代码路径与模块组成
- **来源文档**：ADS（§2、§8.2、§10.7.1、§14.1-§14.6）
- **差距类型**：架构
- **当前值**：ADS 多处提到"8 步预处理 Pipeline"，但仅描述概念步骤（质量码识别 → 有效性标记 → 量程归一化 → 异常值识别 → 缺失率统计 → 连续性检查 → Metric Mask 生成 → Quality Summary 生成），未给出代码模块路径
- **基准值**：AGENTS.md 明确预处理 Pipeline 路径为 `app/services/` 下的 quality_code/thresholds/outlier_detection/validity_mask/quality_summary/pipeline 等模块；实现契约 v1.0 也指向 `app/services/` 而非 `app/services/preprocessing/`
- **修复方案**：§8.2 或 §14 补充"预处理 Pipeline 实现路径：`app/services/` 下的 quality_code/thresholds/outlier_detection/validity_mask/quality_summary/pipeline 模块，共 8 步 + 8 类异常值检测"

### 差距 7: ADS 未描述 TaskTracker 组件
- **来源文档**：ADS（§3 核心服务职责划分表）
- **差距类型**：架构
- **当前值**：ADS §3 描述 "Action Tracker Service" 为"异常跟踪子模块服务"，归入 Diagnosis Service 体系，但未提及 TaskTracker 组件
- **基准值**：AGENTS.md 与实现契约明确 TaskTracker（`app/services/task_tracker.py`）作为任务全生命周期跟踪组件（create/update_status + Redis 状态存储 + 通知）
- **修复方案**：§3 表格补充 TaskTracker 组件行，说明其位于 `app/services/task_tracker.py`，负责任务全生命周期跟踪（create/update_status + Redis 状态存储 + 通知）

### 差距 8: ADS 未描述 ConfidenceEvaluator 组件
- **来源文档**：ADS（§3、§14.9）
- **差距类型**：架构
- **当前值**：§14.9 定义了"指标可信度（Confidence Level）"概念与判定规则（A/B/C/D/E 五级，valid_rate 阈值 95/80/60/20%），但未提及实现该判定的代码组件
- **基准值**：AGENTS.md 明确 ConfidenceEvaluator（`app/services/confidence_evaluator.py`）为可信度评估 A/B/C/D/E（valid_rate 阈值 95/80/60/20%），含 INCONCLUSIVE 处理
- **修复方案**：§3 或 §14.9 补充"ConfidenceEvaluator 实现路径：`app/services/confidence_evaluator.py`，按 valid_rate 自动判定 A/B/C/D/E 五级，E 级时标记 INCONCLUSIVE"

### 差距 9: ADS 未给出 DataPlanner 的代码路径
- **来源文档**：ADS（§14.1）
- **差距类型**：架构
- **当前值**：§14.1 描述 DataPlanner 的定义、职责、约束、部署形态（独立容器化服务 `clpm/algo-dataplanner:v1.0`，HPA 2~10 副本），但未给出代码路径
- **基准值**：AGENTS.md 明确 DataPlanner 代码路径为 `app/services/data_planner.py`
- **修复方案**：§14.1 补充"代码实现路径：`app/services/data_planner.py`"

---

## 3. 数据流差距

### 差距 10: ADS 未描述 DataPlanner 按控制类型自动降采样
- **来源文档**：ADS（§14.4 tagGroup 定义表）
- **差距类型**：数据流
- **当前值**：§14.4 描述 BASE tagGroup"按控制类型（FC=1s/PC=2s/TC=5s/CC=10s）"采样，但未明确说明这是 DataPlanner 的自动降采样行为
- **基准值**：AGENTS.md 与 FDS v5.1 明确 DataPlanner"按控制类型自动降采样，分发 MetricDataBundle"
- **修复方案**：§14.1 或 §14.4 补充"DataPlanner 根据 loop_ledger.control_type 自动选择 BASE 采样率（FC=1s/PC=2s/TC=5s/CC=10s），无需用户配置"

### 差距 11: ADS 数据流描述与 FDS v5.1 不完全对齐
- **来源文档**：ADS（§8.2 DataPlanner 与 KPI Calculation Service 关系图）
- **差距类型**：数据流
- **当前值**：ADS §8.2 给出 7 步数据流（提交需求契约 → 合并查询 → 查询 DataBlock Cache → 回源+8步预处理 → 组装 Bundle → 返回 → 消费+输出数据血缘），描述详细
- **基准值**：FDS v5.1 §1.4 描述"DataPlanner：统一历史数据读取，按控制类型自动降采样"，强调"分发 MetricDataBundle"
- **修复方案**：ADS 数据流描述已较为完整，仅需在 §8.2 流程图开头补充"DataPlanner 在合并查询计划时按控制类型自动降采样"一句，与 FDS v5.1 对齐

---

## 4. 数据模型差距

### 差距 12: ADS PostgreSQL 表清单严重缺失
- **来源文档**：ADS（§4.1 关系型存储表清单）
- **差距类型**：数据模型
- **当前值**：ADS §4.1 列出 13 张表（plant_node/tag_registry/loop_ledger/loop_tag_mapping/metric_config/diagnosis_config/engine_rule/kpi_snapshot_hourly/diagnosis_result/action_tracker/tuning_record/report_record/sys_audit_log）
- **基准值**：DDS v4.1 §3.2 明确 17 张表，ADS 缺失以下 4 张：
  - `kpi_snapshot_custom`（KPI 自定义快照）
  - `unit_kpi_summary`（装置级 KPI 汇总）
  - `clpm_metric_data_requirement`（指标数据需求表）
  - `report_schedule`（报表计划表）
  - `sys_user`（系统用户）
  - `sys_role`（系统角色）
  - `sys_user_role`（用户-角色关联）
  - 实际缺失 7 张表（13 vs 17+sys 三表）
- **修复方案**：§4.1 表格补齐 7 张缺失表，并对齐 DDS v4.1 的字段定义

### 差距 13: ADS 缺失 sys_user/sys_role/sys_user_role 用户权限表
- **来源文档**：ADS（§4.1、§3 Auth & Audit Service）
- **差距类型**：数据模型
- **当前值**：ADS §3 描述 Auth & Audit Service 提供"统一认证、RBAC 权限管理"，但 §4.1 数据模型表中无 sys_user/sys_role/sys_user_role 三张核心权限表
- **基准值**：DDS v4.1 §3.2 明确列出 sys_user/sys_role/sys_user_role 三张表，是 RBAC 的数据基础
- **修复方案**：§4.1 补充 sys_user/sys_role/sys_user_role 三张表的字段说明

### 差距 14: ADS 缺失 unit_kpi_summary 装置级 KPI 汇总表
- **来源文档**：ADS（§4.1、§10.3）
- **差距类型**：数据模型
- **当前值**：ADS §10.3 描述 `aggregate_unit_score` 接口输出 `unit_kpis` + `unit_score`，但 §4.1 未列出装置级 KPI 汇总表
- **基准值**：DDS v4.1 §3.2 明确 `unit_kpi_summary` 表，含 `excluded_loops`/`status` 等 v4.1 新增字段
- **修复方案**：§4.1 补充 `unit_kpi_summary` 表，对齐 DDS v4.1 字段定义

### 差距 15: ADS 缺失 clpm_metric_data_requirement 指标数据需求表
- **来源文档**：ADS（§14.5 Metric Data Requirement）
- **差距类型**：数据模型
- **当前值**：§14.5 详细描述 Metric Data Requirement 契约结构（metric_code/tag_group/tags/sampling_strategy/quality_policy/mask_expression/aggregation_policy/depends_on），但 §4.1 未列出对应的持久化表
- **基准值**：DDS v4.1 §3.2 明确 `clpm_metric_data_requirement` 表
- **修复方案**：§4.1 补充 `clpm_metric_data_requirement` 表，说明契约结构持久化方式

### 差距 16: ADS 缺失 kpi_snapshot_custom 自定义快照表
- **来源文档**：ADS（§4.1）
- **差距类型**：数据模型
- **当前值**：ADS §4.1 仅列出 `kpi_snapshot_hourly` 一张快照表
- **基准值**：DDS v4.1 §3.2 明确 `kpi_snapshot_custom` 表用于自定义时间窗口快照，且 v4.1 修正了 `stability_rate` → `steady_rate` 字段名
- **修复方案**：§4.1 补充 `kpi_snapshot_custom` 表，并对齐 DDS v4.1 的字段名修正（`steady_rate` 而非 `stability_rate`）

### 差距 17: ADS 缺失 report_schedule 报表计划表
- **来源文档**：ADS（§4.1）
- **差距类型**：数据模型
- **当前值**：ADS §4.1 仅列出 `report_record`（报表生成记录），无报表计划表
- **基准值**：DDS v4.1 §3.2 明确 `report_schedule` 表用于周期报表计划配置
- **修复方案**：§4.1 补充 `report_schedule` 表

### 差距 18: ADS loop_ledger 表缺少 v4.1 新增字段
- **来源文档**：ADS（§4.1）
- **差距类型**：数据模型
- **当前值**：ADS §4.1 描述 loop_ledger 仅含"回路基础信息（位号/描述/所属单元/评分权重/启用状态/备注等扩展配置）"
- **基准值**：DDS v4.1 §3.6 明确 loop_ledger 新增 `control_type` / `importance_level` / `include_in_evaluation` 三个关键字段，control_type 直接决定 DataPlanner 的采样率选择
- **修复方案**：§4.1 loop_ledger 行补充"v4.1 新增字段：control_type（控制类型 FC/PC/TC/CC，决定 DataPlanner 采样率）、importance_level（装置级聚合权重）、include_in_evaluation（是否纳入评估）"

### 差距 19: ADS metric_config 表缺少 grading_thresholds 字段
- **来源文档**：ADS（§4.1）
- **差距类型**：数据模型
- **当前值**：ADS §4.1 描述 metric_config 含"国标评分核 R/A/F/S 的公式、权重、阈值及项目展示/诊断指标元数据"
- **基准值**：DDS v4.1 §3.6 明确 metric_config 新增 `grading_thresholds`（5 级性能定级 EXCELLENT/GOOD/FAIR/WARNING/POOR）
- **修复方案**：§4.1 metric_config 行补充"v4.1 新增 `grading_thresholds`（5 级性能定级 EXCELLENT/GOOD/FAIR/WARNING/POOR）"

### 差距 20: ADS 数据血缘字段数量与 DDS v4.1 不一致
- **来源文档**：ADS（§10.2、§14.8 数据血缘字段定义）
- **差距类型**：数据模型
- **当前值**：ADS §14.8 定义数据血缘 8 个字段：sampling_freq / aggregation_policy / quality_policy / tag_group / data_block_ids / valid_rate / data_policy_version / algorithm_version
- **基准值**：DDS v4.1 §3.5 明确 `kpi_snapshot_hourly` 与 `kpi_snapshot_custom` 均包含 5 个独立字段（sampling_freq/quality_policy/valid_rate/confidence_level/data_lineage），其中 data_lineage（JSONB）内部包含 6 个子字段。基准明确指出"原计划提到的'8 字段'实际不存在，文档明确为 5 字段"
- **修复方案**：§14.8 重构数据血缘字段定义：5 独立字段（sampling_freq/quality_policy/valid_rate/confidence_level/data_lineage JSONB）+ data_lineage 内部 6 子字段（aggregation_policy/tag_group/data_block_ids/data_policy_version/algorithm_version/sampling_freq），与 DDS v4.1 对齐

### 差距 21: ADS TDengine 超级表字段命名与 DDS v4.1 不一致
- **来源文档**：ADS（§4.2 字段设计表）
- **差距类型**：数据模型
- **当前值**：ADS §4.2 列出 TDengine 字段为 ts/pv/sp/op/mode/pv_quality/pid_p/pid_i/pid_d，其中质量码字段名为 `pv_quality`
- **基准值**：DDS v4.1 §3.3 明确 TDengine 超级表 `st_loop_data` 的 9 个 Field 列为 ts/pv/sp/op/mode/quality/pid_p/pid_i/pid_d，质量码字段名为 `quality`（非 `pv_quality`）
- **修复方案**：§4.2 将 `pv_quality` 重命名为 `quality`，与 DDS v4.1 对齐

### 差距 22: ADS TDengine 缺少超级表与子表命名规则
- **来源文档**：ADS（§4.2）
- **差距类型**：数据模型
- **当前值**：ADS §4.2 仅说明"一回路一表 (Table per Loop)"、"同模型一超级表 (Super Table)"的建表策略
- **基准值**：DDS v4.1 §3.3 明确超级表名 `st_loop_data`，2 个 Tag 列（loop_id, plant_node_id），子表命名规则 `loop_{loop_id}`
- **修复方案**：§4.2 补充超级表名 `st_loop_data`、2 个 Tag 列（loop_id, plant_node_id）、子表命名规则 `loop_{loop_id}`

---

## 5. API 端点差距

### 差距 23: ADS 完全缺失 HTTP REST API 端点描述
- **来源文档**：ADS（§10 算法服务接口定义、§8.3 服务间通信）
- **差距类型**：API
- **当前值**：ADS §10 仅描述算法服务的 gRPC 接口（get_timeseries_data/get_batch_timeseries_data/batch_calc_metrics/aggregate_unit_score/diagnose/identify_model/tune_pid/simulate 等），§8.3 提到"算法服务 ↔ 第三方系统（对外）REST"但未给出具体路径
- **基准值**：实现契约 v1.0 §4.4 明确 6 大 REST API 领域：`/api/v1/performance/*`、`/api/v1/diagnosis/*`、`/api/v1/tuning/*`、`/api/v1/users/*`、`/api/v1/audit-logs/*`、`/api/v1/reports/*`
- **修复方案**：新增章节或在 §10 补充"业务服务 REST API 端点"小节，列出实现契约 v1.0 的 6 大 API 领域路径

### 差距 24: ADS 未描述任务管理/通知相关 API
- **来源文档**：ADS（§10）
- **差距类型**：API
- **当前值**：ADS §10 未提及任务跟踪与通知相关 API
- **基准值**：AGENTS.md Phase 5 明确"17 端点 + 任务跟踪/通知 + OpenAPI 文档"
- **修复方案**：§10 补充任务跟踪与通知相关 API 端点描述

### 差距 25: ADS 算法服务接口签名与实现代码路径未对应
- **来源文档**：ADS（§10.1、§10.2）
- **差距类型**：API/架构
- **当前值**：ADS §10.1/§10.2 描述的接口（如 `get_timeseries_data`、`calc_good_value_rate`）以算法服务 gRPC 形式定义，未说明是否在 FastAPI 中暴露为 REST 端点
- **基准值**：实现契约 v1.0 §4.4 明确 API 路径为 REST 形式（`/api/v1/performance/*` 等），AGENTS.md Phase 5 提到"17 端点"
- **修复方案**：§10 补充说明"gRPC 算法服务接口通过 FastAPI 适配层暴露为 REST 端点 `/api/v1/performance/*`"

---

## 6. 路由差距

### 差距 26: ADS 完全缺失前端路由清单
- **来源文档**：ADS（§2 逻辑分层架构）
- **差距类型**：路由
- **当前值**：ADS §2 仅在分层架构图中提及"6 大功能模块 + 1 个门户"，未列出任何具体路由 path
- **基准值**：实现契约 v1.0 §4.2 明确 32 个路由 path，按 6 模块+1门户分组（工作台 `/dashboard/workbench`、回路管理 `/loop/*`、性能评估 `/metric/*`、诊断中心 `/diagnosis/*`、回路整定 `/tuning/*`、系统管理 `/system/*`）
- **修复方案**：新增"前端路由架构"小节或在 §2 补充 32 个路由 path 清单，引用实现契约 v1.0 §4.2

### 差距 27: ADS 路由口径与 FDS v5.1 模块结构不一致
- **来源文档**：ADS（§2）
- **差距类型**：路由/模块
- **当前值**：ADS §2 声明"对齐 PRD v3.0 的 6 大功能模块 + 1 个门户"
- **基准值**：FDS v5.1 §5 实际为 5 业务模块 + 1 门户（任务管理归入性能评估子节），实现契约 v1.0 与 UIUX v5.3 为 6 模块 + 1 门户
- **修复方案**：ADS §2 改为"对齐实现契约 v1.0 与 UIUX v5.3 的 6 模块 + 1 门户口径"，并在脚注说明"FDS v5.1 §5 章节结构为 5+1，任务管理作为性能评估子模块"

---

## 7. 权限差距

### 差距 28: ADS 完全缺失权限矩阵描述
- **来源文档**：ADS（§3 核心服务职责划分表）
- **差距类型**：权限
- **当前值**：ADS §3 仅在 Auth & Audit Service 行说明"统一认证、RBAC 权限管理、操作审计日志"，未列出具体角色与权限矩阵
- **基准值**：实现契约 v1.0 §4.5 明确 5 角色（ADMIN/IC_ENGINEER/PE_ENGINEER/EXPERT/SPONSOR）及各角色设计口径；UIUX v5.3 §6 定义 14 项操作权限矩阵
- **修复方案**：新增"权限矩阵"小节，列出 5 角色及其权限范围，引用实现契约 v1.0 §4.5

### 差距 29: ADS 未描述角色权限与数据模型的映射
- **来源文档**：ADS（§3、§4.1）
- **差距类型**：权限/数据模型
- **当前值**：ADS §3 仅概念性描述 RBAC，未说明角色与 sys_role/sys_user_role 表的映射
- **基准值**：DDS v4.1 §3.2 明确 sys_role/sys_user_role 表为 RBAC 数据基础
- **修复方案**：在权限矩阵小节中说明"角色通过 sys_role/sys_user_role 表持久化，详见 DDS v4.1 §3.2"

---

## 8. 状态机差距

### 差距 30: ADS 缺失 Loop 状态机描述
- **来源文档**：ADS（§3、§4.1）
- **差距类型**：状态机
- **当前值**：ADS 未提及 Loop 状态机（READY/PARTIAL/INACTIVE）
- **基准值**：实现契约 v1.0 §4.6 明确 Loop 状态枚举为 `READY` / `PARTIAL` / `INACTIVE`（就绪/部分配置/已停用），并明确"历史文档中的 `ACTIVE`/`PAUSED`/`DECOMMISSIONED` 统一视为旧命名"
- **修复方案**：新增"状态机定义"小节或在 §3/§4.1 补充 Loop 状态机枚举

### 差距 31: ADS 缺失 KPI 快照状态机描述
- **来源文档**：ADS（§10.2 输出 status 字段、§14.9）
- **差距类型**：状态机
- **当前值**：ADS §10.2 输出 status 字段值为 `OK` / `INCONCLUSIVE` / `ERROR`；§14.9 提到 E 级时标记 INCONCLUSIVE
- **基准值**：实现契约 v1.0 §4.6 明确 KPI 快照状态枚举为 `SUCCESS` / `PARTIAL` / `INCONCLUSIVE`（成功/部分有效/数据不足）
- **修复方案**：§10.2 将 status 字段值改为 `SUCCESS` / `PARTIAL` / `INCONCLUSIVE`，与实现契约 v1.0 对齐

### 差距 32: ADS 缺失 Tuning 状态机描述
- **来源文档**：ADS（§3 Tuning Service 行、§10.5 整定计算服务接口）
- **差距类型**：状态机
- **当前值**：ADS §3 描述 Tuning Service 为"Phase 2"，但未列出整定任务状态机
- **基准值**：实现契约 v1.0 §4.6 明确 Tuning 状态枚举为 `DRAFT` / `RUNNING` / `COMPLETED` / `ROLLED_BACK`（草稿/运行中/已完成/已回退）
- **修复方案**：§3 或 §10.5 补充 Tuning 状态机枚举

### 差距 33: ADS Action Tracker 状态机描述与实现契约一致但缺少枚举值
- **来源文档**：ADS（§3 Action Tracker Service 行、§4.1 action_tracker 表）
- **差距类型**：状态机
- **当前值**：ADS §3 描述状态标签为"待处理/处理中/已实施/已忽略"（中文），§4.1 描述"状态标签：待处理/处理中/已实施/已忽略"
- **基准值**：实现契约 v1.0 §4.6 明确 Action Tracker 状态枚举为 `PENDING` → `IN_PROGRESS` → `IMPLEMENTED` / `IGNORED`，并强调"历史文档中的 `RESOLVED` 统一视为旧命名"
- **修复方案**：§3 与 §4.1 补充英文枚举值（PENDING/IN_PROGRESS/IMPLEMENTED/IGNORED），并标注"旧命名 RESOLVED 已废弃"

### 差距 34: ADS 缺失 PV Quality 状态机
- **来源文档**：ADS（§4.2、§7）
- **差距类型**：状态机
- **当前值**：ADS §4.2 与 §7 多处提及 PV 质量码 Good/Bad/Uncertain，但未作为状态机正式定义
- **基准值**：实现契约 v1.0 §4.6 明确 PV Quality 状态枚举为 `GOOD` / `BAD` / `UNCERTAIN`（好值/坏值/不确定）
- **修复方案**：在状态机定义小节中正式列出 PV Quality 状态枚举

---

## 9. KPI 指标差距

### 差距 35: ADS 指标体系口径与实现契约不完全一致
- **来源文档**：ADS（§8.2、§13.1）
- **差距类型**：KPI
- **当前值**：ADS §13.1 声明"3+1+8 指标体系"（3 核心+1 折扣+8 扩展），§8.2 又称"6 大 KPI"，§12.2 称 `KPI_CALC` v1.0 为"6 大 KPI 计算"
- **基准值**：FDS v5.1 §1.3 明确 3+1+8 体系 = 12 项指标计算器；实现契约 v1.0 §4.7 描述"6 大核心 KPI + 2 扩展派生指标"，并说明"PRD 对外合规口径仍强调 6 大核心 KPI；实现可保留 2 个扩展派生指标用于算法增强、排序与内部诊断"
- **修复方案**：统一口径为"3+1+8 体系（12 项指标计算器），对外合规口径强调 6 大核心 KPI（好值率/自控率/平稳率/准确率/振荡率/饱和率）"，删除 §8.2/§12.2 中"6 大 KPI"的内部表述矛盾

### 差距 36: ADS 缺失 5 级性能定级描述
- **来源文档**：ADS（§13.1）
- **差距类型**：KPI
- **当前值**：ADS §13.1 国标映射表仅一行"附录 D 性能定级 | 一级至五级 | 评分映射等级 | ✅ 合规"，未详细描述 5 级定级
- **基准值**：FDS v5.1 §1.3 明确 5 级性能定级（EXCELLENT/GOOD/FAIR/WARNING/POOR），DDS v4.1 §3.6 明确 metric_config 新增 `grading_thresholds`（5 级性能定级）
- **修复方案**：§13.1 补充 5 级性能定级枚举（EXCELLENT/GOOD/FAIR/WARNING/POOR）及对应阈值配置字段

### 差距 37: ADS 缺失 4 类权重模板描述
- **来源文档**：ADS（§13.1）
- **差距类型**：KPI
- **当前值**：ADS §13.1 国标映射表仅一行"附录 C 权重系数 | 稳定型/慢速型/快速型/逻辑型 | 默认权重配置（4 类控制类型）"
- **基准值**：FDS v5.1 §1.3 明确 4 类权重模板（STABLE/SLOW/FAST/LOGIC）
- **修复方案**：§13.1 补充 4 类权重模板枚举（STABLE/SLOW/FAST/LOGIC）及与 control_type 的映射关系

### 差距 38: ADS 缺失装置级三大 KPI 描述
- **来源文档**：ADS（§10.3 组合调用服务接口）
- **差距类型**：KPI
- **当前值**：ADS §10.3 仅描述 `aggregate_unit_score` 接口输出 `unit_kpis` + `unit_score` + `inconclusive_count`，未具体列出装置级 KPI
- **基准值**：FDS v5.1 §1.3 明确装置级三大 KPI（自控率/好值率/平稳率）
- **修复方案**：§10.3 补充"装置级三大 KPI：自控率、好值率、平稳率（stability_rate）"

### 差距 39: ADS loop-level 与 unit-level 字段名未区分
- **来源文档**：ADS（§10.3、§14.8）
- **差距类型**：KPI/数据模型
- **当前值**：ADS 未明确区分 loop-level 与 unit-level 的"平稳率"字段名
- **基准值**：DDS v4.1 §3.7 明确"loop-level 字段名：`steady_rate`；unit-level 聚合字段名：`stability_rate`"
- **修复方案**：§10.3 或新增术语小节，明确"loop-level 用 `steady_rate`，unit-level 聚合用 `stability_rate`"

---

## 10. 模块清单差距

### 差距 40: ADS 模块口径与 FDS v5.1 章节结构不一致
- **来源文档**：ADS（§2）
- **差距类型**：模块
- **当前值**：ADS §2 声明"服务层划分对齐 PRD v3.0 的 6 大功能模块 + 1 个门户"
- **基准值**：基准摘要 §5.2 指出 FDS v5.1 §5 章节结构实为 5 业务模块 + 1 门户（任务管理归入性能评估子节），但实现契约 v1.0 与 UIUX v5.3 为 6 模块 + 1 门户
- **修复方案**：ADS §2 改为"对齐实现契约 v1.0 与 UIUX v5.3 的 6 模块 + 1 门户口径"，并在脚注说明"FDS v5.1 §5 章节结构为 5+1，任务管理作为性能评估子模块"

### 差距 41: ADS 引用 PRD v3.0 而非 v4.0 作为模块口径来源
- **来源文档**：ADS（§2）
- **差距类型**：模块/引用
- **当前值**：ADS §2 引用"PRD v3.0 的 6 大功能模块"
- **基准值**：基准摘要 §5.1 明确 PRD 实际版本为 v4.0（2026-06-25）
- **修复方案**：将"PRD v3.0"改为"PRD v4.0"，或改为"实现契约 v1.0"

---

## 11. 引用文档差距

### 差距 42: ADS 引用的关键算法设计说明版本正确但需补充对齐文档
- **来源文档**：ADS（文档头"设计依据"行、§0 变更记录）
- **差距类型**：引用
- **当前值**：ADS 引用"关键算法设计说明 v2.0"，与基准一致
- **基准值**：基准摘要 §1.5 列出 FDS v5.1 引用文档包括"PRD v3.1、DDS v4.1、关键算法设计说明 v2.0、CLPM 后端研发指导文档、GB/T 44693.2-2024"
- **修复方案**：保持关键算法设计说明 v2.0 引用，补充 FDS v5.1、DDS v4.1、UIUX v5.3、实现契约 v1.0

### 差距 43: ADS 引用的 CLPM 后端研发智能体系统提示词未在基准清单中
- **来源文档**：ADS（文档头"设计依据"行）
- **差距类型**：引用
- **当前值**：ADS 引用"CLPM后端研发智能体系统提示词与开发指导文档"
- **基准值**：基准文档清单（FDS v5.1/UIUX v5.3/DDS v4.1/实现契约 v1.0）中未包含此文档作为正式引用源
- **修复方案**：保留该引用但标注为"内部参考文档"，或将其内容已固化的部分引用至正式基准文档

### 差距 44: ADS §0 变更记录未提及对实现契约的对齐
- **来源文档**：ADS（§0 文档变更记录表）
- **差距类型**：引用/版本号
- **当前值**：ADS §0 变更记录仅列出 v3.0/v3.1/v4.0 三次变更，均未提及对实现契约 v1.0 的对齐
- **基准值**：实现契约 v1.0（2026-06-25）是 IA/路由/API/权限/状态机/KPI 的事实来源，ADS 应在 v6.0 变更中明确对齐
- **修复方案**：在 v6.0 变更记录中新增"对齐实现契约 v1.0 的 API/路由/权限/状态机/KPI 口径"

---

## 差距总数：44 条

**统计分类**：
| 差距类型 | 数量 |
|---|---|
| 版本号 | 2 |
| 架构 | 6 |
| 数据流 | 2 |
| 数据模型 | 11 |
| API | 3 |
| 路由 | 2 |
| 权限 | 2 |
| 状态机 | 5 |
| KPI | 5 |
| 模块 | 2 |
| 引用 | 4 |

**优先级建议**：
1. **P0（必须修复）**：差距 12-22（数据模型 11 条）、差距 23-25（API 3 条）、差距 26-27（路由 2 条）
2. **P1（强烈建议）**：差距 4-9（架构 6 条）、差距 28-29（权限 2 条）、差距 30-34（状态机 5 条）
3. **P2（建议修复）**：差距 1-3（版本号 2 条）、差距 35-39（KPI 5 条）、差距 40-44（模块/引用 6 条）
