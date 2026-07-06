# v6.0 文档统一升级 — 代码事实清单

**提取日期**：2026-07-06
**执行分支**：`mb/doc-v6`
**目的**：作为文档升级的代码事实依据，确保文档与代码完全一致

---

## 1. 后端 API 端点清单

### 1.1 API 路由前缀清单（28 个 router）

| # | prefix | tags | 文件 |
|---|---|---|---|
| 1 | `/auth` | auth | `endpoints/auth.py` |
| 2 | `/users` | users | `endpoints/users.py` |
| 3 | `/loops` | loop | `endpoints/loops.py` |
| 4 | `/loops` | loop-mode-mapping | `endpoints/loop_mode_mapping.py` |
| 5 | `/tags` | tag | `endpoints/tags.py` |
| 6 | `/timeseries` | timeseries | `endpoints/tags.py` |
| 7 | `/plant-nodes` | plant-node | `endpoints/plant_nodes.py` |
| 8 | `/performance` | performance | `endpoints/performance.py` |
| 9 | `/performance/nodes` | performance-node | `endpoints/node_performance.py` |
| 10 | `/diagnosis` | diagnosis | `endpoints/diagnosis.py` |
| 11 | `/timeseries` | timeseries | `endpoints/diagnosis.py` |
| 12 | `/tracker` | tracker | `endpoints/diagnosis.py` |
| 13 | `/diagnosis/tags` | diagnosis-tags | `endpoints/diagnosis.py` |
| 14 | `/tuning` | tuning | `endpoints/tuning.py` |
| 15 | `/tasks` | tasks | `endpoints/tasks.py` |
| 16 | `/dashboard` | dashboard | `endpoints/dashboard.py` |
| 17 | `/realtime` | 实时数据 | `endpoints/realtime.py` |
| 18 | `/ws` | WebSocket实时推送 | `endpoints/ws_realtime.py` |
| 19 | `/audit-logs` | audit-logs | `endpoints/audit_logs.py` |
| 20 | `/reports` | reports | `endpoints/reports.py` |
| 21 | `/aas` | aas | `endpoints/aas.py` |
| 22 | `/configs` | configs | `endpoints/configs.py` |
| 23 | `/configs/metrics` | (在 configs 下) | `endpoints/configs.py` |
| 24 | `/configs/diagnosis` | (在 configs 下) | `endpoints/configs.py` |
| 25 | `/configs/loop-type-weights` | loop-type-weight | `endpoints/loop_type_weight.py` |
| 26 | `/configs/loop-level-weights` | loop-level-weight | `endpoints/loop_level_weight.py` |
| 27 | `/configs/weight-templates` | weight-config | `endpoints/weight_config.py` |
| 28 | `/configs/grading-thresholds` | grading-config | `endpoints/grading_config.py` |
| 29 | `/algorithms` | algorithms | `endpoints/algorithms.py` |
| 30 | `/algorithms/dataplanner` | dataplanner | `endpoints/dataplanner.py` |
| 31 | `/health` | health | `endpoints/health.py` |

### 1.2 完整 API 端点清单（120+ 端点）

#### auth 领域（`/api/v1/auth/*`）
- `POST /login` — 登录
- `POST /refresh` — 刷新 token
- `POST /logout` — 登出
- `GET /me` — 获取当前用户信息
- `PUT /password` — 修改密码
- `GET /rbac-test` — RBAC 测试

#### users 领域（`/api/v1/users/*`）
- `GET /` — 用户列表
- `POST /` — 创建用户
- `PUT /{user_id}` — 更新用户
- `DELETE /{user_id}` — 删除用户
- `PUT /{user_id}/reset-password` — 重置密码

#### loops 领域（`/api/v1/loops/*`）
- `GET /` — 回路列表
- `POST /` — 创建回路
- `POST /batch` — 批量创建
- `GET /monitor` — 监控列表
- `GET /export` — 导出
- `POST /import` — 导入
- `GET /{loop_id}` — 回路详情
- `PUT /{loop_id}` — 更新回路
- `DELETE /{loop_id}` — 删除回路
- `GET /{loop_id}/tags` — 回路 Tag 关联
- `PUT /{loop_id}/tags` — 更新 Tag 关联
- `GET /{loop_id}/monitor` — 单回路监控

#### loop_mode_mapping 领域（`/api/v1/loops/*`，loop_mode_mapping.py）
- `GET /mode-mapping` — 模式映射查询
- `PUT /mode-mapping` — 更新模式映射

#### tags 领域（`/api/v1/tags/*`）
- `GET /` — Tag 列表
- `GET /export` — 导出
- `POST /import` — 导入
- `POST /batch-delete` — 批量删除
- `GET /match-loop` — 匹配回路
- `GET /{tag_id}` — Tag 详情
- `PUT /{tag_id}` — 更新 Tag
- `DELETE /{tag_id}` — 删除 Tag

#### plant-nodes 领域（`/api/v1/plant-nodes/*`）
- `GET /` — 工厂树
- `POST /` — 创建节点
- `GET /export` — 导出
- `POST /import` — 导入
- `PUT /{node_id}` — 更新节点
- `DELETE /{node_id}` — 删除节点

#### performance 领域（`/api/v1/performance/*`）
- `GET /metrics` — 指标配置列表
- `PUT /metrics/{metric_id}` — 更新指标配置
- `GET /rules` — 引擎规则列表
- `PUT /rules/{rule_id}` — 更新引擎规则
- `GET /board` — KPI 看板
- `GET /ranking` — 排行
- `GET /analytics` — 分析
- `POST /analytics/export` — 导出分析
- `GET /realtime-auto-rate` — 实时自控率
- `GET /loops/snapshots` — 回路快照

#### performance/nodes 领域（`/api/v1/performance/nodes/*`）
- `GET /{node_id}/snapshot` — 节点快照
- `GET /{node_id}/trend` — 节点趋势
- `GET /ranking` — 节点排行
- `POST /compare` — 节点对比
- `GET /overview` — 节点总览
- `GET /{node_id}/monitor` — 节点监控

#### diagnosis 领域（`/api/v1/diagnosis/*`）
- `GET /metrics` — 诊断指标配置
- `PUT /metrics/{diag_id}` — 更新诊断指标
- `GET /list` — 诊断列表
- `GET /analytics` — 诊断分析
- `POST /analytics/export` — 导出诊断分析
- `GET /statistics/export` — 统计导出
- `GET /ab-compare` — A/B 对比
- `GET /{loop_id}` — 单回路诊断
- `GET /{loop_id}/waveform` — 波形
- `POST /{loop_id}/report` — 报告

#### tracker 领域（`/api/v1/tracker/*`，diagnosis.py 内）
- `GET /` — 异常跟踪列表
- `POST /` — 创建异常跟踪
- `GET /{tracker_id}` — 详情
- `PUT /{tracker_id}` — 更新
- `PATCH /{tracker_id}/status` — 状态流转

#### diagnosis/tags 领域（`/api/v1/diagnosis/tags/*`）
- 诊断标签管理端点

#### tuning 领域（`/api/v1/tuning/*`）
- `GET /methods` — 整定方法列表
- `POST /identify` — 模型辨识
- `POST /tune` — 整定计算
- `POST /simulate` — 闭环仿真
- `GET /tasks` — 整定任务列表
- `GET /tasks/{task_id}` — 整定任务详情
- `POST /tasks` — 创建整定任务
- `GET /history` — 整定历史

#### tasks 领域（`/api/v1/tasks/*`）
- `POST /standard/evaluate` — 标准评估任务
- `POST /custom/evaluate` — 自定义评估任务
- `POST /backfill` — 历史重算任务
- `GET /notifications` — 任务通知
- `POST /notifications/{task_id}/read` — 标记已读
- `GET /{task_id}` — 任务详情
- `GET /` — 任务列表
- `POST /{task_id}/cancel` — 取消任务
- `DELETE /{task_id}` — 删除任务
- `GET /{task_id}/results` — 任务结果

#### dashboard 领域（`/api/v1/dashboard/*`）
- `GET /overview` — 工作台总览
- `GET /board` — 工作台看板
- `GET /auto-rate-rt` — 实时自控率

#### realtime 领域（`/api/v1/realtime/*`）
- `GET /` — 实时数据查询

#### ws 领域（`/api/v1/ws/*`）
- WebSocket 实时推送

#### audit-logs 领域（`/api/v1/audit-logs/*`）
- `GET /` — 审计日志列表

#### reports 领域（`/api/v1/reports/*`）
- `GET /configs` — 报表配置列表
- `POST /configs` — 创建报表配置
- `PUT /configs/{config_id}` — 更新报表配置
- `POST /generate` — 生成报表
- `GET /tasks/{task_id}` — 报表任务状态

#### aas 领域（`/api/v1/aas/*`）
- `GET /config` — AAS 配置
- `PUT /config` — 更新 AAS 配置
- `POST /config/test` — 测试 AAS 配置
- `POST /sync` — 触发 AAS 同步
- `GET /tags` — AAS Tag 列表
- `GET /sync-status` — 同步状态
- `GET /sync-logs` — 同步日志

#### configs 领域（`/api/v1/configs/*`）
- `GET /metrics` — 指标配置批量
- `PUT /metrics` — 更新指标配置批量
- `GET /diagnosis` — 诊断配置批量
- `PUT /diagnosis` — 更新诊断配置批量

#### configs/loop-type-weights 领域
- `GET /` — 类型权重列表
- `PUT /{loop_type}` — 更新类型权重

#### configs/loop-level-weights 领域
- `GET /` — 级别权重列表
- `PUT /{level}` — 更新级别权重

#### configs/weight-templates 领域
- `GET /` — 权重模板
- `POST /` — 创建权重模板
- `GET /history` — 历史版本
- `POST /{version}/rollback` — 回滚
- `POST /restore-defaults` — 恢复默认

#### configs/grading-thresholds 领域
- `GET /` — 性能定级阈值
- `POST /` — 更新性能定级阈值

#### algorithms 领域（`/api/v1/algorithms/*`）
- `POST /kpi/calculate` — KPI 计算
- `POST /diagnosis/analyze` — 诊断分析
- `POST /tuning/calculate` — 整定计算
- `GET /tasks/{task_id}` — 算法任务状态

#### algorithms/dataplanner 领域
- `POST /plan` — 数据计划
- `POST /bundle` — 数据包
- `GET /cache/stats` — 缓存统计
- `DELETE /cache/{loop_id}` — 清除缓存

#### health 领域
- `GET /health` — 健康检查
- `GET /health/ready` — 就绪检查

---

## 2. 后端 ORM 模型清单（26 张表）

| # | 类名 | __tablename__ | 文件 | 用途 |
|---|---|---|---|---|
| 1 | `LoopLedger` | `loop_ledger` | `models/loop.py` | 回路台账 |
| 2 | `LoopTagMapping` | `loop_tag_mapping` | `models/loop.py` | 回路-Tag 关联 |
| 3 | `TagRegistry` | `tag_registry` | `models/tag.py` | AAS Tag 注册表 |
| 4 | `PlantNode` | `plant_node` | `models/plant_node.py` | 工厂层级 |
| 5 | `MetricConfig` | `metric_config` | `models/metric.py` | 指标配置 |
| 6 | `KpiSnapshotHourly` | `kpi_snapshot_hourly` | `models/metric.py` | KPI 小时快照 |
| 7 | `KpiSnapshotCustom` | `kpi_snapshot_custom` | `models/metric.py` | KPI 自定义快照 |
| 8 | `ClpmMetricDataRequirement` | `clpm_metric_data_requirement` | `models/metric_data_requirement.py` | 指标数据需求 |
| 9 | `UnitKpiSummary` | `unit_kpi_summary` | `models/unit_kpi_summary.py` | 装置级 KPI 汇总 |
| 10 | `KpiNodeSnapshotHourly` | `kpi_node_snapshot_hourly` | `models/node_kpi.py` | 节点 KPI 小时快照 |
| 11 | `KpiNodeSnapshotDaily` | `kpi_node_snapshot_daily` | `models/node_kpi.py` | 节点 KPI 日快照 |
| 12 | `KpiNodeSnapshotMonthly` | `kpi_node_snapshot_monthly` | `models/node_kpi.py` | 节点 KPI 月快照 |
| 13 | `EngineRule` | `engine_rule` | `models/engine.py` | 引擎规则 |
| 14 | `DiagnosisConfig` | `diagnosis_config` | `models/diagnosis.py` | 诊断配置 |
| 15 | `DiagnosisResult` | `diagnosis_result` | `models/diagnosis.py` | 诊断结果 |
| 16 | `DiagnosisTag` | `diagnosis_tag` | `models/diagnosis.py` | 诊断标签 |
| 17 | `ActionTracker` | `action_tracker` | `models/tracker.py` | 异常跟踪 |
| 18 | `TuningRecord` | `tuning_record` | `models/tuning.py` | 整定记录 |
| 19 | `LoopModeMapping` | `loop_mode_mapping` | `models/loop_config.py` | 回路模式映射 |
| 20 | `LoopTypeWeight` | `loop_type_weight` | `models/loop_config.py` | 回路类型权重 |
| 21 | `LoopLevelWeight` | `loop_level_weight` | `models/loop_config.py` | 回路级别权重 |
| 22 | `SysUser` | `sys_user` | `models/sys_user.py` | 系统用户 |
| 23 | `SysAuditLog` | `sys_audit_log` | `models/audit.py` | 审计日志 |
| 24 | `SysConfig` | `sys_config` | `models/sys_config.py` | 系统配置 |
| 25 | `ReportRecord` | `report_record` | `models/report.py` | 报表记录 |
| 26 | `ReportConfig` | `report_config` | `models/report_config.py` | 报表配置 |

### 2.1 与 DDS v4.1 的对比

| DDS v4.1 表名 | 代码 ORM 模型 | 状态 |
|---|---|---|
| `loop_ledger` | `LoopLedger` | ✅ 一致 |
| `tag_registry` | `TagRegistry` | ✅ 一致 |
| `loop_tag_mapping` | `LoopTagMapping` | ✅ 一致 |
| `plant_node` | `PlantNode` | ✅ 一致 |
| `metric_config` | `MetricConfig` | ✅ 一致 |
| `kpi_snapshot_hourly` | `KpiSnapshotHourly` | ✅ 一致 |
| `kpi_snapshot_custom` | `KpiSnapshotCustom` | ✅ 一致 |
| `unit_kpi_summary` | `UnitKpiSummary` | ✅ 一致 |
| `clpm_metric_data_requirement` | `ClpmMetricDataRequirement` | ✅ 一致 |
| `action_tracker` | `ActionTracker` | ✅ 一致 |
| `tuning_record` | `TuningRecord` | ✅ 一致 |
| `report_record` | `ReportRecord` | ✅ 一致 |
| `sys_user` | `SysUser` | ✅ 一致 |
| `sys_audit_log` | `SysAuditLog` | ✅ 一致 |
| `report_schedule` | — | ❌ DDS 有，代码无（实际为 `report_config`） |
| `sys_role` | — | ❌ DDS 有，代码无 |
| `sys_user_role` | — | ❌ DDS 有，代码无 |

**代码额外有的表**（DDS 未列出）：
- `kpi_node_snapshot_hourly` / `kpi_node_snapshot_daily` / `kpi_node_snapshot_monthly`（节点级 KPI 快照）
- `engine_rule`（引擎规则）
- `diagnosis_config` / `diagnosis_result` / `diagnosis_tag`（诊断三件套）
- `loop_mode_mapping` / `loop_type_weight` / `loop_level_weight`（回路配置三件套）
- `sys_config`（系统配置）
- `report_config`（报表配置）

---

## 3. 前端路由清单（45 条）

**详细文件见**：`v6-frontend-routes.md`（117 行）

### 3.1 路由模块统计

| 模块 | 文件 | 路由数 |
|---|---|---|
| dashboard | `dashboard.ts` | 2 |
| loop | `loop.ts` | 8 |
| metric | `metric.ts` | 12 |
| diagnosis | `diagnosis.ts` | 8 |
| tuning | `tuning.ts` | 6 |
| task | `task.ts` | 3 |
| system | `system.ts` | 5 |
| vben | `vben.ts` | 1 |
| **合计** | — | **45** |

### 3.2 关键发现

1. **权限字段名**：代码使用 `meta.authority`（非文档中常出现的 `meta.roles`）
2. **隐藏路由 7 条**：`LoopFactory`、`LoopLedger`、`LoopDetail`、`DiagnosisDetail`、`DiagnosisABCompare`、`TaskDetail`、`Profile` 均设置 `hideInMenu: true`
3. **重定向路由 6 条**：`Dashboard`、`LoopFactory`、`LoopLedger`、`Metric`、`MetricConfigGroup`、`Task`
4. **动态参数路由 3 条**：`LoopDetail`(:id)、`DiagnosisDetail`(:loopId)、`TaskDetail`(:taskId)
5. **跨模块路由**：`TagList` 路径为 `/tag/list`（属 loop 模块但前缀非 `/loop`）
6. **order 仅在父级声明**：1/2/3/3.5/4/5/6，其中 `Task` 用 3.5（与 Metric 同属"性能评估执行体系"）
7. **Tuning 模块带 Beta 徽章**，标注为 Phase 2 原型先行

---

## 4. 代码与文档一致性关键发现

### 4.1 实现契约 v1.0 vs 代码

| 实现契约声明 | 代码实际 | 状态 |
|---|---|---|
| 6 模块+1门户 | 8 个路由模块（含 vben） | ✅ 一致（vben 是 vben-admin 框架路由） |
| `/metric/*` 路由 | metric.ts 有 12 条路由 | ✅ 一致 |
| `/diagnosis/*` 路由 | diagnosis.ts 有 8 条路由 | ✅ 一致 |
| `/api/v1/users/*` | `/api/v1/users/*` | ✅ 一致 |
| `/api/v1/audit-logs/*` | `/api/v1/audit-logs/*` | ✅ 一致 |
| `/api/v1/reports/*` | `/api/v1/reports/*` | ✅ 一致 |
| 不新增 `/api/v1/configs/metrics` | **代码存在** `/api/v1/configs/metrics` | ❌ 不一致 |
| 不新增 `/api/v1/configs/diagnosis` | **代码存在** `/api/v1/configs/diagnosis` | ❌ 不一致 |

### 4.2 DDS v4.1 vs 代码

| DDS 声明 | 代码实际 | 状态 |
|---|---|---|
| 17 张表 | 26 张 ORM 模型 | ⚠️ 代码多 9 张，DDS 多 2 张 |
| `report_schedule` 表 | 代码无，有 `report_config` | ❌ 不一致 |
| `sys_role` / `sys_user_role` 表 | 代码无（角色用枚举） | ❌ 不一致 |

### 4.3 FDS v5.1 vs 代码

| FDS 声明 | 代码实际 | 状态 |
|---|---|---|
| 12 个指标计算器 | 12 个 MetricCalculator | ✅ 一致 |
| 预处理 Pipeline 路径 `app/services/preprocessing/` | 需验证 | 待验证 |
| 4 类权重模板 | `weight_config.py` 存在 | ✅ 一致 |
| 5 级性能定级 | `grading_config.py` 存在 | ✅ 一致 |

---

## 5. v6.0 文档升级的代码事实依据

基于以上代码事实，v6.0 文档升级需要：

1. **PRD v6.0**：引用 26 张 ORM 表（非 DDS 的 17 张）、45 条前端路由、120+ API 端点
2. **ADS v6.0**：补全节点级 KPI 表（3 张）、诊断三件套表（3 张）、回路配置三件套表（3 张）
3. **IDS v6.0**：补全 120+ API 端点的完整 Schema 定义
4. **实现契约 v2.0**：追认 `/api/v1/configs/metrics` 和 `/api/v1/configs/diagnosis` 的存在（与 v1.0 声明冲突）
5. **DDS v6.0**：补全代码特有的 9 张表，或确认 `report_schedule`/`sys_role`/`sys_user_role` 是否为计划中的表
