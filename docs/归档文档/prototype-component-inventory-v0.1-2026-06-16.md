# CLPM Prototype Component Inventory

日期：2026-06-16
版本：v0.1
用途：定义 prototype 首批组件边界，防止页面各自散做。

## Layout 组件

| 组件 | 职责 | 服务页面 |
|---|---|---|
| `AppShell` | 整体壳层：左导航、顶栏、内容区 | 全部 |
| `SideNav` | 11 个一级菜单、二级入口、版本标签 | 全部 |
| `TopBar` | 产品标题、样本批次、风险摘要、导出入口、用户入口 | 全部 |
| `PageHeader` | 页面标题、定位说明、版本标签 | 全部 |

## Navigation 组件

| 组件 | 职责 |
|---|---|
| `VersionBadge` | 显示 P0 / P1 / P2 / P3 |
| `RouteBreadcrumb` | 显示当前路径 |
| `SubMenuList` | 当前一级菜单下的二级导航 |

## Status 组件

| 组件 | 职责 |
|---|---|
| `StatusBadge` | 展示可评估 / 可诊断 / 可整定 / 数据不足 / 不可判定 |
| `RiskBadge` | 展示 high / medium / low 风险 |
| `ReadinessBadge` | 展示样本就绪状态 |
| `StateBlock` | 呈现 Loading / Empty / Error / Success / Partial |

## Data & List 组件

| 组件 | 职责 |
|---|---|
| `MetricCard` | KPI 摘要卡 |
| `KpiGrid` | 指标总览栅格 |
| `LoopTable` | 回路清单 / 低效排行 / 台账表格 |
| `PriorityList` | 工程首页低性能优先级清单 |
| `DetailSummaryPanel` | 当前选中回路摘要 |

## Evidence 组件

| 组件 | 职责 |
|---|---|
| `TrendChart` | 展示 PV / SP / OP 趋势 |
| `ModeBand` | 展示 MODE 分段 |
| `EventTimeline` | 展示事件线 |
| `RuleHitList` | 展示规则命中与阈值 |
| `EvidenceSummaryPanel` | 汇总当前判断、建议动作和证据完整度 |
| `EvidenceManifestCard` | 展示 EvidencePackage 摘要和版本引用 |

## Workflow 组件

| 组件 | 职责 |
|---|---|
| `ActionReviewPanel` | 工程首页右侧动作与待办 |
| `ReviewStepper` | 审核流转步骤 |
| `ReviewDecisionCard` | 单条审核意见 |
| `ImplementationRecordCard` | 单条实施记录 |
| `ClosureTimeline` | 审核/实施/复评时间线 |
| `BeforeAfterComparison` | 前后 KPI / 趋势对比 |
| `RollbackPanel` | 回退条件与原始参数 |

## Sponsor 组件

| 组件 | 职责 |
|---|---|
| `ValidationDashboardCard` | 样本可信度、映射率、闭环率 |
| `RiskSummaryPanel` | Sponsor 风险与不可证明事项 |
| `RepresentativeCaseSwitcher` | 切换代表性样例 |
| `ExportActionBar` | 导出完整证据包 |

## Placeholder 组件

| 组件 | 职责 |
|---|---|
| `RoadmapPlaceholder` | P1/P2/P3 结构展示页模板 |
| `CapabilityList` | 展示未来能力列表 |
