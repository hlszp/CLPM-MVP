# 前端路由清单（代码事实）

> 来源：`frontend/apps/web-antd/src/router/routes/modules/` 下 8 个路由模块文件
> 提取日期：2026-07-06
> 说明：权限字段在代码中使用 `meta.authority`（非 `meta.roles`）；`meta.order` 仅在父级路由声明。

## 1. 工作台（dashboard.ts）

| name | path | component | title | icon | authority | order | 隐藏 |
|---|---|---|---|---|---|---|---|
| Dashboard | /dashboard | —（redirect: /dashboard/workbench） | 工作台 | lucide:layout-dashboard | — | 1 | 否 |
| DashboardWorkbench | /dashboard/workbench | #/views/dashboard/workbench.vue | 性能总览 | lucide:layout-dashboard | — | — | 否（affixTab: true） |

## 2. 回路管理（loop.ts）

| name | path | component | title | icon | authority | order | 隐藏 |
|---|---|---|---|---|---|---|---|
| Loop | /loop | — | 回路管理 | lucide:network | ['ADMIN','IC_ENGINEER','PE_ENGINEER'] | 2 | 否 |
| LoopManage | /loop/manage | #/views/loop/manage.vue | 回路管理 | lucide:network | ['ADMIN','IC_ENGINEER','PE_ENGINEER'] | — | 否 |
| LoopFactory | /loop/factory | —（redirect: /loop/manage） | 工厂模型 | — | ['ADMIN','IC_ENGINEER','PE_ENGINEER'] | — | hideInMenu: true（已废弃） |
| LoopLedger | /loop/ledger | —（redirect: /loop/manage） | 回路台账 | — | ['ADMIN','IC_ENGINEER','PE_ENGINEER'] | — | hideInMenu: true（已废弃） |
| TagList | /tag/list | #/views/tag/list.vue | 测点清单 | lucide:list | ['ADMIN','IC_ENGINEER','PE_ENGINEER'] | — | 否 |
| LoopMonitor | /loop/monitor | #/views/loop/monitor.vue | 回路监控 | lucide:gauge | ['ADMIN','IC_ENGINEER','PE_ENGINEER'] | — | 否 |
| LoopAasSync | /loop/aas-sync | #/views/loop/aas.vue | AAS 同步状态 | lucide:refresh-cw | ['ADMIN','IC_ENGINEER','PE_ENGINEER'] | — | 否 |
| LoopDetail | /loop/detail/:id | #/views/loop/detail.vue | 回路详情 | — | ['ADMIN','IC_ENGINEER','PE_ENGINEER'] | — | hideInMenu: true（activePath: /loop/monitor） |

## 3. 性能评估（metric.ts）

| name | path | component | title | icon | authority | order | 隐藏 |
|---|---|---|---|---|---|---|---|
| Metric | /metric | —（redirect: /metric/dashboard） | 性能评估 | lucide:gauge | — | 3 | 否 |
| MetricDashboard | /metric/dashboard | #/views/metric/dashboard.vue | 性能看板 | lucide:layout-dashboard | — | — | 否 |
| MetricRanking | /metric/ranking | #/views/metric/ranking.vue | 低效排行 | lucide:arrow-down-narrow-wide | — | — | 否 |
| MetricStatistics | /metric/statistics | #/views/metric/statistics.vue | 统计报表 | lucide:bar-chart-3 | — | — | 否 |
| MetricSnapshots | /metric/snapshots | #/views/metric/snapshots.vue | 指标明细 | lucide:table-properties | — | — | 否 |
| MetricRecompute | /metric/recompute | #/views/metric/recompute.vue | 历史重算 | lucide:history | ['ADMIN','IC_ENGINEER'] | — | 否 |
| MetricConfigGroup | /metric/config-group | —（redirect: /metric/config） | 指标配置 | lucide:settings | ['ADMIN'] | — | 否 |
| MetricConfig | /metric/config | #/views/metric/config.vue | 指标定义 | lucide:settings-2 | ['ADMIN'] | — | 否 |
| MetricWeightConfig | /metric/weight-config | #/views/metric/weight-config.vue | 权重配置 | lucide:scale | ['ADMIN'] | — | 否 |
| MetricEngineConfig | /metric/engine-config | #/views/metric/engine-config.vue | 引擎规则 | lucide:cog | ['ADMIN'] | — | 否 |
| MetricTaskStrategy | /metric/task-strategy | #/views/metric/task-strategy.vue | 任务策略 | lucide:calendar-clock | ['ADMIN'] | — | 否 |
| MetricTaskRecords | /metric/tasks | #/views/task/list.vue | 执行记录 | lucide:list-checks | ['ADMIN','IC_ENGINEER','PE_ENGINEER'] | — | 否 |

## 4. 诊断中心（diagnosis.ts）

| name | path | component | title | icon | authority | order | 隐藏 |
|---|---|---|---|---|---|---|---|
| Diagnosis | /diagnosis | — | 诊断中心 | lucide:stethoscope | ['ADMIN','EXPERT','IC_ENGINEER','PE_ENGINEER','SPONSOR'] | 4 | 否 |
| DiagnosisList | /diagnosis/list | #/views/diagnosis/list.vue | 诊断列表 | lucide:list | ['ADMIN','EXPERT','IC_ENGINEER','PE_ENGINEER','SPONSOR'] | — | 否 |
| DiagnosisDetail | /diagnosis/detail/:loopId | #/views/diagnosis/detail.vue | 诊断详情 | — | ['ADMIN','EXPERT','IC_ENGINEER','PE_ENGINEER'] | — | hideInMenu: true |
| DiagnosisWaveform | /diagnosis/waveform | #/views/diagnosis/waveform.vue | 波形分析 | lucide:activity | ['ADMIN','EXPERT','IC_ENGINEER','PE_ENGINEER'] | — | 否 |
| DiagnosisTracker | /diagnosis/tracker | #/views/diagnosis/tracker-page.vue（稳定 DOM 路由根；内容为 tracker.vue） | 异常跟踪 | lucide:clipboard-check | ['ADMIN','IC_ENGINEER','PE_ENGINEER','EXPERT'] | — | 否 |
| DiagnosisABCompare | /diagnosis/ab-compare | #/views/diagnosis/ab-compare.vue | A/B 对比 | lucide:git-compare | ['ADMIN','IC_ENGINEER','EXPERT'] | — | hideInMenu: true |
| DiagnosisStatistics | /diagnosis/statistics | #/views/diagnosis/statistics.vue | 统计报表 | lucide:bar-chart-3 | ['ADMIN','EXPERT','IC_ENGINEER','PE_ENGINEER','SPONSOR'] | — | 否 |
| DiagnosisConfig | /diagnosis/config | #/views/diagnosis/config.vue | 诊断配置 | lucide:settings-2 | ['ADMIN'] | — | 否 |

## 5. 回路整定（tuning.ts，Phase 2 原型先行）

| name | path | component | title | icon | authority | order | 隐藏 |
|---|---|---|---|---|---|---|---|
| Tuning | /tuning | — | 回路整定 | lucide:settings-2 | ['ADMIN','IC_ENGINEER','EXPERT'] | 5 | 否（badge: Beta） |
| TuningWorkbench | /tuning/workbench | #/views/tuning/workbench.vue | 整定工作台 | lucide:settings-2 | ['ADMIN','IC_ENGINEER','EXPERT'] | — | 否 |
| TuningModel | /tuning/model | #/views/tuning/model.vue | 模型辨识 | lucide:git-branch | ['ADMIN','IC_ENGINEER','EXPERT'] | — | 否 |
| TuningAlgorithm | /tuning/algorithm | #/views/tuning/algorithm.vue | 整定算法 | lucide:cpu | ['ADMIN','IC_ENGINEER','EXPERT'] | — | 否 |
| TuningSimulation | /tuning/simulation | #/views/tuning/simulation.vue | 闭环仿真 | lucide:play-circle | ['ADMIN','IC_ENGINEER','EXPERT'] | — | 否 |
| TuningStats | /tuning/stats | #/views/tuning/stats.vue | 效果统计 | lucide:file-bar-chart | ['ADMIN','IC_ENGINEER','EXPERT'] | — | 否 |

## 6. 评估任务（task.ts）

| name | path | component | title | icon | authority | order | 隐藏 |
|---|---|---|---|---|---|---|---|
| Task | /tasks | —（redirect: /tasks/list） | 评估任务 | lucide:list-checks | ['ADMIN','IC_ENGINEER','PE_ENGINEER'] | 3.5 | 否 |
| TaskList | /tasks/list | #/views/task/list.vue | 任务列表 | lucide:list-checks | — | — | 否 |
| TaskDetail | /tasks/:taskId | #/views/task/detail.vue | 任务详情 | — | — | — | hideInMenu: true |

## 7. 系统管理（system.ts）

| name | path | component | title | icon | authority | order | 隐藏 |
|---|---|---|---|---|---|---|---|
| System | /system | — | 系统管理 | lucide:settings | ['ADMIN','IC_ENGINEER'] | 6 | 否 |
| SystemUsers | /system/users | #/views/system/users.vue | 用户管理 | lucide:users | ['ADMIN'] | — | 否 |
| SystemAudit | /system/audit | #/views/system/audit.vue | 审计日志 | lucide:scroll-text | ['ADMIN'] | — | 否 |
| SystemPermissions | /system/permissions | #/views/system/permissions.vue | 权限矩阵 | lucide:shield-check | —（全角色可见） | — | 否 |
| SystemReports | /system/reports | #/views/system/reports.vue | 自动报表 | lucide:file-text | ['ADMIN','IC_ENGINEER'] | — | 否 |

## 8. 内置/Vben 框架路由（vben.ts）

| name | path | component | title | icon | authority | order | 隐藏 |
|---|---|---|---|---|---|---|---|
| Profile | /profile | #/views/_core/profile/index.vue | $t('page.auth.profile')（用户资料） | lucide:user | — | — | hideInMenu: true |

## 统计汇总

| 模块 | 路由数（含父级） |
|---|---|
| dashboard.ts | 2 |
| loop.ts | 8 |
| metric.ts | 12 |
| diagnosis.ts | 8 |
| tuning.ts | 6 |
| task.ts | 3 |
| system.ts | 5 |
| vben.ts | 1 |
| **合计** | **45** |

## 关键观察

1. **权限字段名**：代码中实际使用 `meta.authority`，而非 UI/UX 文档中常出现的 `meta.roles`。后续基线文档统一时应使用 `authority`。
2. **隐藏路由**：`LoopFactory`、`LoopLedger`、`LoopDetail`、`DiagnosisDetail`、`DiagnosisABCompare`、`TaskDetail`、`Profile` 均设置 `hideInMenu: true`，其中 `LoopFactory`、`LoopLedger` 为已废弃重定向路由。
3. **重定向路由**：`Dashboard`→`/dashboard/workbench`、`LoopFactory`→`/loop/manage`、`LoopLedger`→`/loop/manage`、`Metric`→`/metric/dashboard`、`MetricConfigGroup`→`/metric/config`、`Task`→`/tasks/list`。
4. **带动态参数路由**：`LoopDetail`（:id）、`DiagnosisDetail`（:loopId）、`TaskDetail`（:taskId）。
5. **order 字段**：仅在父级路由声明，分别为 1（Dashboard）、2（Loop）、3（Metric）、3.5（Task）、4（Diagnosis）、5（Tuning）、6（System）；vben.ts 的 Profile 无 order。
6. **跨模块路由**：`TagList` 路径为 `/tag/list`（属于 loop 模块但路径前缀不是 `/loop`）；`MetricTaskRecords` 路径为 `/metric/tasks`（复用 `#/views/task/list.vue` 组件，与 task.ts 的 `TaskList` 同组件）。
7. **Tuning 模块带 Beta 徽章**：父级 `Tuning` 路由设置 `badge: 'Beta'`、`badgeVariants: 'default'`，标注为 Phase 2 原型先行。
8. **affixTab**：仅 `DashboardWorkbench` 设置 `affixTab: true`（标签栏固定）。
9. **activePath**：仅 `LoopDetail` 设置 `activePath: /loop/monitor`（菜单高亮锚点）。
10. **国际化**：仅 vben.ts 的 `Profile` 标题使用 `$t('page.auth.profile')`，其余均为中文字面量。
