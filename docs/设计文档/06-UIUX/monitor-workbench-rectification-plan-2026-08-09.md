# 监控—回路工作台闭环整改实施计划

> 文档状态：proposed-for-implementation
> 日期：2026-08-09
> 适用基线：实现契约 v2.9、UI/UX 规范 v6.3、UI/UX Phase 2 出口基线
> 配套任务清单：`monitor-workbench-rectification-checklist-2026-08-09.md`
> 智能体交接提示词：`monitor-workbench-agent-handoff-prompt-2026-08-09.md`
> 生效规则：本文件是待实施方案，不覆盖当前实现契约；全部出口门禁通过后，再回写实现契约与 UI/UX 规范。

## 1. 结论与冻结决策

本轮不再调整一级模块数量。继续维持 6 个一级模块：**监控 / 评估 / 诊断 / 整定 / 配置 / 系统**。

冻结以下产品决策，实施过程中不得重新分叉：

1. **预警不设一级模块**：预警规则继续归 `/config/alert-rules`；预警结果进入监控模块。
2. **预警结果采用三层呈现**：全局通知负责到达，关注队列负责分诊，回路工作台负责单回路上下文处置。
3. **监控与工作台合并壳层和上下文，不强行合并认知任务**：跨回路扫视使用总览/表格模式，单回路深度处置使用工作台模式。
4. **回路工作台闭环扩展为**：监控 → 评估 → 诊断 → 整定 → 人工实施/MOC → 效果验证 → 持续监控。
5. **安全边界不变**：平台不增加任何 DCS PID 参数下写能力，只生成建议、证据、风险、回退方案和实施留痕。
6. **兼容性优先**：旧路由、现有 API 和权限只做增量扩展；不在本轮删除兼容入口。
7. **原则上不新增业务表**：关注队列和生命周期摘要聚合现有 `alert_event`、KPI 快照、诊断、整定、`action_tracker` 数据。只有性能压测证明实时聚合不能达标时，才另立物化表方案。

## 2. 为什么现在整改

### 2.1 受影响用户

| 角色 | 当前主要任务 | 当前断点 |
|---|---|---|
| ADMIN | 查看全局态势、配置规则、监督闭环 | 规则与事件已归位，但缺统一关注优先级和闭环汇总 |
| IC_ENGINEER | 巡检、确认预警、评估、诊断、整定、验证 | 需要在事件、监控、工作台、Tracker 间来回切换 |
| PE_ENGINEER | 查看生产影响、参与异常跟踪 | 可进入工作台，但处置能力和只读边界缺少统一表达 |
| EXPERT | 单回路诊断与整定 | 缺少跨模块时间上下文和实施后验证信息 |
| SPONSOR | 查看运行态势和预警结果 | 无权进入工作台，关注项只能停留在事件列表，缺只读解释层 |

### 2.2 已核实的当前状态

| 现状 | 证据 | 影响 |
|---|---|---|
| IA 已收敛为 6 个一级模块 | `implementation-contract.md` §2 | 菜单方向正确，不再重做 |
| 预警事件在监控、规则在配置 | `router/routes/modules/monitor.ts`、`config.ts`、`alert.ts` | 归属正确，但事件仍是孤立列表 |
| 工作台只并列加载概览、评估、诊断、整定 | `views/loop/workbench.vue` | 缺实施/验证、生命周期状态和推荐下一步 |
| 工作台没有消费 `realtimeWs` | `views/loop/workbench.vue` 对比 `views/loop/monitor.vue` | “监控”主要是列表快照，不是选中回路实时态 |
| 旧高密度监控页有装置/类型筛选、保存筛选、WS 与轮询降级 | `views/loop/monitor.vue` | 隐藏旧页导致批量巡检能力没有进入新主入口 |
| 工作台每次切换发起 4 路加载，并全量获取 72h 快照 | `views/loop/workbench.vue`、工作台专项评估 W-10 | 快速切换存在请求风暴和旧响应覆盖风险 |
| 左栏固定 `pageSize=100`，深链接不在首屏时回退第一回路 | `views/loop/workbench.vue` | 大规模部署和精确深链接存在信任风险 |
| 虚拟列表配置仍按 57px/两行，模板已变成三行 | `views/loop/workbench.vue` | 可能出现重叠、裁切或滚动总高度错误 |
| 预警事件跳工作台只传 `loopId` | `views/alert/events.vue` | `eventId`、处置状态、证据定位丢失 |
| Tracker 已具备 VERIFYING/CLOSED/REOPENED 和验证超期计数 | `models/tracker.py`、`services/diagnosis.py` | 闭环基础已存在，可直接接入工作台 |

## 3. 整改目标与量化指标

### 3.1 产品目标

1. 用户从任何关注项进入工作台后，能够理解“发生了什么、证据是否可信、当前处于哪一步、下一步做什么”。
2. 用户在同一回路上下文内完成监控、评估、诊断、整定、实施留痕和效果验证。
3. 跨回路巡检和单回路深度分析共享筛选、回路选择、时间窗和深链接，不再形成两个割裂产品。
4. 预警结果与评分恶化、数据质量、Tracker、验证超期进入同一关注队列。

### 3.2 出口指标

| 指标 | 出口目标 |
|---|---|
| 选中回路实时值延迟 | 收到 WS 消息后 2 秒内更新 PV/SP/OP/MODE 和采样时间 |
| WS 断连降级 | 连接断开后 5 秒内显示状态，30 秒内启动首次轮询刷新 |
| 快速切换一致性 | 连续切换 20 次，最终页面所有摘要均属于 URL 中的 `loopId`，旧响应覆盖 0 次 |
| 深链接准确性 | 回路不在列表首屏、带筛选、超过 100 条时仍 100% 打开目标回路；无权限/不存在时不回退其他回路 |
| 工作台首屏请求 | 除回路列表外，首屏最多 1 个摘要请求；图表/详情按可见区或用户动作延迟加载 |
| 关注队列性能 | 1000 回路、10000 条开放事件测试集下，接口 p95 ≤ 500ms；若不达标再评估缓存/物化 |
| 关注项可解释性 | 每条关注项都有来源、优先级、发生时间、当前状态、排序原因和目标动作 |
| 处置跳转 | 关注队列到目标工作台区不超过 1 次点击，并保留 `loopId/eventId/trackerId/section` |
| 闭环可见性 | 存在 Tracker 的回路 100% 显示实施/验证状态；VERIFYING 超期明确标记 |
| 批量规模 | 1000 回路可分页/无限加载，DOM 同时渲染条目 ≤ 100 |
| 权限回归 | 5 个角色路由与动作 E2E 全通过，无新增 403 toast |
| 安全边界 | OpenAPI 静态扫描确认无 DCS PID 写入端点 |

## 4. 目标信息架构

```text
监控
├── 运行总览                       /dashboard/workbench
├── 关注队列                       /monitor/attention
│   ├── 全部
│   ├── 活跃预警
│   ├── 评分恶化
│   ├── 数据质量
│   ├── 待处置 Tracker
│   └── 验证超期
├── 预警记录                       /monitor/alerts
│   └── 全状态查询、处置审计与导出（由关注队列提供入口）
└── 回路工作台                     /monitor/loop-workbench
    ├── 工作台模式                 ?view=workspace&loopId=
    └── 批量表格模式               ?view=table

配置
└── 预警规则                       /config/alert-rules
```

兼容策略：

- `/monitor/alerts` 保留并改称“预警记录”，承载 RESOLVED/ARCHIVED 等历史查询、审计与导出；关注队列只承载当前行动项，不能替代历史记录。
- `/alert/events` 继续重定向到 `/monitor/alerts`；关注队列的“查看预警记录”链接到该路径并保留回路/状态筛选。
- `/loop/monitor` → `/monitor/loop-workbench?view=table`
- `/loop/workbench` 与 `/loop/detail/:id` 继续重定向到工作台模式。
- 旧路由至少保留一个正式发布周期，E2E 持续验证。

## 5. 关键用户流程

### 5.1 预警处置

```text
预警 WS 到达
  → 全局铃铛出现未读项
  → 点击进入关注队列并定位 eventId
  → 查看触发值、可信度、重复次数、状态和排序原因
  → 点击“进入回路工作台”
  → 工作台打开目标 loopId，并展开活跃预警/对应 section
  → 确认、处置、转 Tracker 或标记误报
  → 状态同步回铃铛、队列和工作台
```

### 5.2 回路治理闭环

```text
跨回路扫视
  → 选择评分恶化/数据质量异常回路
  → 工作台顶部看到实时状态和推荐下一步
  → 发起评估
  → 结果不足时补数据；结果异常时发起诊断
  → 根据诊断创建 Tracker 或进入整定
  → 完成辨识、PID 建议和只读仿真
  → 人工实施并记录 MOC/PID/实施时间
  → VERIFYING 周期到达后查看前后对比
  → 通过则 CLOSED，失败则 REOPENED
  → 回到持续监控
```

### 5.3 批量巡检

```text
工作台切换到批量表格模式
  → 复用装置/单元、类型、关键词、只看关注项、保存视图
  → 排序/导出/批量扫视
  → 点击任一回路切回工作台模式并保留筛选上下文
```

## 6. 预警结果三层呈现

| 层 | 组件 | 信息上限 | 动作 |
|---|---|---|---|
| 全局通知 | 通知铃铛 | 最新 10 条：严重度、回路、规则、触发值、时间 | 打开对应关注项；标记已读 |
| 关注队列 | MonitorAttentionQueue | 分页全量：来源、优先级、状态、排序原因、上下文 ID | 确认、处置、误报、转 Tracker、进入工作台 |
| 单回路上下文 | WorkbenchActiveAttention | 当前回路最多 3 条开放项 + 汇总数 | 展开详情、处理、跳 Tracker；不承载规则编辑 |

预警规则 CRUD、订阅、抑制、试运行和规则审计只允许出现在配置模块。已处置/已归档事件的运行记录继续由监控模块的“预警记录”承载；关注队列不得吞掉历史检索与 CSV 导出能力。

## 7. 回路工作台目标结构

### 7.1 页面结构

```text
┌ 共享监控工具栏：装置/类型/搜索/保存视图/工作台·表格模式 ┐
├ 回路状态条：位号、单元、AUTO/MAN、PV/SP/OP、实时连接、采样时间 ┤
├ 可信与风险条：数据健康度、评分趋势、活跃关注项、选定时间窗       ┤
├ 生命周期：监控 → 评估 → 诊断 → 整定 → 实施与验证               ┤
├ 推荐下一步：一个主动作 + 原因 + 前置条件                         ┤
├ 性能评估摘要：结论、时间、可信度、主图、动作                     ┤
├ 回路诊断摘要：结论、证据、时间、可信度、动作                     ┤
├ 回路整定摘要：当前/推荐 PID、模型、仿真、风险、动作              ┤
└ 闭环时间线：Tracker、MOC、实施参数、验证结果、重开记录           ┘
```

### 7.2 生命周期状态

统一状态枚举：

`NOT_STARTED | READY | RUNNING | COMPLETED | INCONCLUSIVE | BLOCKED | OVERDUE | NOT_REQUIRED`

| 阶段 | 状态来源 | 完成判定 |
|---|---|---|
| MONITOR | 回路配置态、实时 `readAt`、数据健康度 | 必填 Tag 完整且存在运行态数据 |
| ASSESS | 评估任务 + 最新 KPI 快照 | 最新任务成功且快照落在当前时间上下文内 |
| DIAGNOSE | 诊断任务 + 最新诊断结果 | 诊断时间不早于当前评估结果 |
| TUNE | 整定任务状态机 | 达到 IDENTIFIED/SIMULATED/COMPLETED；INCONCLUSIVE 单独表达 |
| VERIFY | Action Tracker | CLOSED 完成；VERIFYING 超期为 OVERDUE；无整改项为 NOT_REQUIRED |

### 7.3 推荐下一步规则

服务端返回一条可解释的 `nextAction`，优先级从上到下：

1. 回路配置不完整或无本地数据 → 修复 Tag 关联/导入历史数据。
2. 评估缺失、过期或评分明显恶化 → 发起评估。
3. 评估异常且无同轴诊断结果 → 发起诊断。
4. 诊断存在可执行问题且无开放 Tracker → 创建 Tracker。
5. 诊断指向可整定问题且无有效整定记录 → 回路辨识/参数整定。
6. 已生成建议但未实施 → 记录人工实施和 MOC。
7. Tracker 为 VERIFYING → 进入效果验证；超期时提升关注优先级。
8. 无开放问题 → 持续监控。

每条动作必须返回 `label`、`reason`、`actionType`、`target`、`disabledReason`，不得仅返回按钮文案。

## 8. 后端增量契约

### 8.1 `GET /api/v1/monitor/attention`

查询参数：

```text
plantNodeId?: UUID
source?: ALERT|DEGRADATION|DATA_QUALITY|TRACKER|VERIFICATION（可重复）
priority?: URGENT|HIGH|MEDIUM|LOW（可重复）
status?: OPEN|ACKNOWLEDGED|SUPPRESSED|IN_PROGRESS|VERIFYING
loopId?: UUID
keyword?: string
page: int = 1
pageSize: int = 20，最大 100
```

响应项：

```ts
interface AttentionItem {
  attentionId: string;       // `${source}:${sourceId}`
  source: 'ALERT' | 'DEGRADATION' | 'DATA_QUALITY' | 'TRACKER' | 'VERIFICATION';
  sourceId: string;
  loopId: string;
  tagName: string;
  unitName?: string;
  title: string;
  summary: string;
  priority: 'URGENT' | 'HIGH' | 'MEDIUM' | 'LOW';
  sourceSeverity?: string;
  status: 'OPEN' | 'ACKNOWLEDGED' | 'SUPPRESSED' | 'IN_PROGRESS' | 'VERIFYING';
  sourceStatus: string;      // 保留 ACTIVE/PENDING 等来源原始状态，便于审计解释
  rankReasons: string[];
  occurredAt: string;
  updatedAt?: string;
  confidenceLevel?: 'A' | 'B' | 'C' | 'D' | 'E';
  score?: number;
  scoreDelta?: number;
  eventId?: string;
  trackerId?: string;
  taskId?: string;
  primaryAction: AttentionAction;
  actions: AttentionAction[];
}

interface AttentionAction {
  type:
    | 'VIEW_DETAIL'
    | 'OPEN_WORKBENCH'
    | 'ACKNOWLEDGE'
    | 'RESOLVE'
    | 'MARK_FALSE_POSITIVE'
    | 'CREATE_TRACKER'
    | 'VIEW_ALERT_HISTORY'
    | 'BACK_TO_OVERVIEW';
  label: string;
  enabled: boolean;
  disabledReason?: string;
  target?: {
    route: '/monitor/loop-workbench' | '/monitor/alerts' | '/dashboard/workbench';
    query: Record<string, string>;
  };
}
```

动作由服务端按角色和来源状态生成，前端不得只靠隐藏按钮实现权限。ADMIN/IC 可获得预警处置动作；PE/EXPERT 只有详情和其获准的工作台入口；Sponsor 的主动作固定为 `VIEW_DETAIL`，可返回 `BACK_TO_OVERVIEW`，不得返回 `OPEN_WORKBENCH`。

聚合来源：

- `ALERT`：`alert_event.status IN (ACTIVE, ACKNOWLEDGED, SUPPRESSED)`；分别映射为 OPEN/ACKNOWLEDGED/SUPPRESSED。
- `DEGRADATION`：最新 `dayTrend=WORSENED`，评分下降达到 2 分以上。
- `DATA_QUALITY`：最新 `loop_integrity_snapshot` 为 WARNING/CRITICAL，或 `loop_confidence_latest` 为 D/E；不在请求时实时扫描 TDengine。
- `TRACKER`：PENDING/IN_PROGRESS 的 Action Tracker。
- `VERIFICATION`：VERIFYING 且超过配置的验证周期。

来源主键和归并规则固定如下，避免实现时产生重复项：

| 来源 | `sourceId` | 统一状态 | 归并规则 |
|---|---|---|---|
| ALERT | `alert_event.id` | 按事件状态映射 | 一条事件一项 |
| DEGRADATION | 最新 `kpi_snapshot_hourly.id` | OPEN | 每回路只取最新有效快照 |
| DATA_QUALITY | `loopId` | OPEN | 每回路一项，合并完整性与可信度原因，时间取两者较新值 |
| TRACKER | `action_tracker.id` | PENDING→OPEN；IN_PROGRESS→IN_PROGRESS | 一条 Tracker 一项 |
| VERIFICATION | `action_tracker.id` | VERIFYING | 只纳入已超过验证周期的 Tracker，不与 TRACKER 重复 |

优先级规则必须透明：

- URGENT：CRITICAL 活跃预警。
- HIGH：ERROR 活跃预警、验证超期、完整性 CRITICAL、`scoreDelta <= -10`。
- MEDIUM：WARN、开放 Tracker、完整性 WARNING、`-10 < scoreDelta <= -5`。
- LOW：INFO、`-5 < scoreDelta <= -2`、可信度 D/E 但无安全预警。
- 同级排序：未确认在前 → 超期在前 → 处理中/验证中 → 已确认 → 已抑制 → 发生时间倒序。
- `rankReasons` 至少包含一条可读原因，例如“严重预警未确认”“验证已超期 9 小时”。

### 8.2 `GET /api/v1/monitor/loops/{loopId}/summary`

首屏摘要一次返回：

- 回路基本信息、PV/SP/OP/MODE、`readAt` 和质量码；浏览器 WS 连接状态由前端 `useLoopRealtime` 提供，不伪装成服务端摘要字段。
- 服务端计算的数据新鲜度 `dataFreshness.status=FRESH|DELAYED|UNKNOWN`、`thresholdSeconds` 和可读原因；阈值复用实时链路停滞配置，不由前端复制常量。
- 数据健康度和最新评分/较昨日趋势。
- 当前回路开放关注项汇总和最多 3 条明细。
- 最新评估、诊断、整定摘要，不返回大数组和图表点。
- 最新开放 Tracker、实施参数、MOC、验证状态。
- 生命周期五阶段状态。
- 推荐下一步 `nextAction`。

详细趋势、诊断图、FFT、仿真结果继续使用既有 API，按可见区延迟加载。

### 8.3 `GET /api/v1/loops/monitor` 增量参数

新增：

```text
loopId?: UUID       // 精确返回指定回路，供深链接解析
attentionOnly?: bool // Phase 2 随关注队列聚合服务启用；Phase 1 工具栏不提前暴露
```

保持现有分页结构和字段兼容；旧客户端不受影响。

## 9. 前端技术设计

### 9.1 共享监控上下文

新增 `use-monitor-context.ts`，URL 为真相源：

```ts
interface MonitorContext {
  view: 'workspace' | 'table';
  loopId: string | null;
  plantNodeId: string | null;
  loopType: string | null;
  keyword: string;
  attentionOnly: boolean;
  timeWindow: '8h' | '12h' | '24h' | '48h' | '72h';
  eventId: string | null;
  trackerId: string | null;
  section: 'overview' | 'assessment' | 'diagnosis' | 'tuning' | 'verification' | null;
}
```

- `router.replace` 更新筛选和回路，避免新增标签页。
- 保存视图继续复用 `use-clpm-preferences.ts`，不新增第二套持久化。
- 扩展 `use-loop-context.ts`，跨模块跳转保留已知上下文，不再默认丢弃其他 query。

### 9.2 实时数据

- 工作台复用全局 `realtimeWs` 单例，不创建第二连接。
- 抽取 `use-loop-realtime.ts`，从现有监控页迁移 tagCode 解析、质量码映射和局部更新逻辑。
- 只更新匹配回路；所有实时字段同时更新 `readAt`。
- WS 在线时不轮询运行值；断连后使用 30 秒轮询；恢复后立即停止轮询。
- 顶部始终显示 online/reconnecting/offline、最后采样时间和质量码。

### 9.3 请求一致性

- 每次选择回路递增 `selectionEpoch`；所有异步返回写入前校验 epoch 和 loopId。
- 支持时使用 `AbortController` 取消旧请求；不支持取消的接口仍必须通过 epoch 防旧响应覆盖。
- 首屏只请求 `/summary`；区级图表在进入可视区、展开或用户切换时间窗时加载。
- 摘要按 `loopId + timeWindow` 做 30 秒前端缓存；任务完成、WS 状态变化、手工刷新时失效。

### 9.4 列表与批量模式

- 左栏改为服务端分页 + 无限加载，默认每页 50。
- 固定行高改为 76px，并通过 CSS 保证三行不换行；虚拟滚动单测覆盖总高度和最后一项可达。
- 工作台模式左栏仅显示扫视信息；批量表格模式复用旧监控页的筛选、列设置、密度、导出和保存视图。
- 旧 `monitor.vue` 先抽取为可嵌入 `LoopFleetView`，确认 E2E 后再把旧路由改为重定向。

## 10. 权限设计

| 能力 | ADMIN | IC | PE | EXPERT | SPONSOR |
|---|---:|---:|---:|---:|---:|
| 查看运行总览/关注队列 | ✅ | ✅ | ✅ | ✅ | ✅ |
| 查看单回路工作台 | ✅ | ✅ | ✅ 只读 | ✅ | ❌ |
| 查看批量表格模式 | ✅ | ✅ | ✅ | ❌ | ❌ |
| 确认/处置预警 | ✅ | ✅ | ❌ | ❌ | ❌ |
| 在工作台发起评估/诊断 | ✅ | ✅ | ❌ | ❌ | ❌ |
| 发起整定/仿真 | ✅ | ✅ | ❌ | ✅ | ❌ |
| 记录实施/验证 | ✅ | ✅ | ❌ | ❌ | ❌ |
| 配置预警规则 | ✅ | ❌ | ❌ | ❌ | ❌ |

写权限按当前后端执行口径冻结，不在本轮顺带扩展：PE 的工作台保持只读；EXPERT 可只读查看生命周期/验证结论并执行整定相关动作，但不开放当前旧实时表未授权的批量模式；Tracker 状态写入仅 ADMIN/IC。服务端 `nextAction` 必须按角色返回 enabled/disabledReason，不能向无权限角色返回可执行写动作。Sponsor 点击关注项时只打开关注队列内的事件详情抽屉，不调用工作台 summary，也不渲染工作台入口，避免 403 toast。

## 11. 实施阶段与依赖

```text
Phase 0 正确性护栏
   ├──> Phase 1 共享壳层与实时监控 ───┐
   └──> Phase 2 统一关注队列 ─────────┼──> Phase 3 生命周期与实施验证
                                      └──> Phase 4 批量视图正式合并
Phase 0~4 ─────────────────────────────────> Phase 5 出口验收与文档回写
```

| 阶段 | 内容 | 预计工程量 | 出口条件 |
|---|---|---:|---|
| Phase 0 | 行高、深链接、请求竞态、72h 拉取瘦身、基线测试 | 1.5 人日 | 快速切换、深链接、虚拟列表单测通过 |
| Phase 1 | 共享上下文、实时 WS、轮询降级、列表分页/筛选 | 2.5 人日 | 实时延迟与断连恢复指标达标 |
| Phase 2 | 关注队列 API、排序解释、页面、预警三层联动 | 3 人日 | 五类来源、权限、深链接 E2E 通过 |
| Phase 3 | summary BFF、生命周期、推荐下一步、Tracker/验证时间线 | 3.5 人日 | 完整治理流程 E2E 通过 |
| Phase 4 | 批量表格嵌入、模式切换、旧路由重定向 | 2 人日 | 批量能力无回退，旧路由兼容通过 |
| Phase 5 | 性能、可访问性、暗色、全门禁、契约回写 | 1.5 人日 | Definition of Done 全部签认 |
| **合计** |  | **约 14 人日** | 前后端并行时预计 8~10 个工作日 |

## 12. 测试策略

| 层 | 新增重点 | 最低数量 |
|---|---|---:|
| 后端单元 | 关注来源映射、优先级、生命周期、nextAction、权限过滤 | +25 |
| 后端 API | attention 分页/筛选、summary、loopId 精确查询、空数据 | +12 |
| 前端单元 | monitor context、selectionEpoch、WS reducer、虚拟列表行高、深链接 | +20 |
| 组件测试 | 关注队列、生命周期条、活跃关注项、验证时间线 | +12 |
| E2E | 预警链路、快速切换、断连降级、批量/工作台切换、五角色 | +10 |
| 性能 | 1000 回路/10000 关注项、连续切换 20 次、DOM 数量 | +3 场景 |

每个阶段必须先跑定向测试，再跑：

```bash
cd backend && uv run ruff check . && uv run ruff format --check .
cd backend && uv run alembic check && uv run pytest -q
cd frontend && pnpm run check:type && pnpm run test:unit
cd e2e && pnpm exec playwright test tests/route-compat.spec.ts tests/loop.spec.ts
```

Phase 5 再跑全量 E2E，并对失败逐例归因，不以总失败数代替回归判定。

## 13. 发布与回退

### 13.1 发布顺序

1. 先发布增量后端 API，旧前端继续可用。
2. 再发布前端 Phase 0~3，保留 `/monitor/alerts` 作为预警历史/审计入口，并保留 `/loop/monitor` 原组件作为回退路径。
3. Phase 4 验证后只把 `/loop/monitor` 改为统一批量模式重定向；`/monitor/alerts` 不重定向到关注队列。
4. 稳定一个发布周期后，再评估删除旧组件；删除不属于本轮。

### 13.2 回退

- Phase 0/1：回退前端提交，既有 API 无破坏。
- Phase 2/3：回退独立的关注队列菜单/路由提交；`/monitor/alerts` 和旧工作台始终保留，后端新增端点可保留。
- Phase 4：把旧路由 redirect 恢复为旧组件加载。
- 本轮无数据迁移；无需数据库回滚。

本轮不新增运行时 feature flag 或 `sys_config` 键。菜单、路由和页面切换必须拆成独立提交，使回退只涉及前端路由提交，不与后端 API、数据模型或业务状态绑定。

## 14. 主要文件范围

| 文件/目录 | 计划变更 |
|---|---|
| `frontend/apps/web-antd/src/views/loop/workbench.vue` | 改为统一监控壳层、摘要首屏、生命周期和延迟加载 |
| `frontend/apps/web-antd/src/views/loop/monitor.vue` | 抽取批量视图和实时 composable，最终转兼容入口 |
| `frontend/apps/web-antd/src/views/alert/events.vue` | 保留全状态预警记录、审计与导出；动作 API 与关注队列复用 |
| `frontend/apps/web-antd/src/layouts/basic.vue` | 铃铛深链接携带 eventId/loopId |
| `frontend/apps/web-antd/src/composables/use-loop-context.ts` | 保留扩展上下文 |
| `frontend/apps/web-antd/src/composables/use-monitor-context.ts` | 新增统一监控 URL 状态 |
| `frontend/apps/web-antd/src/composables/use-loop-realtime.ts` | 新增 WS/轮询复用逻辑 |
| `frontend/apps/web-antd/src/views/monitor/attention.vue` | 新增关注队列页面 |
| `frontend/apps/web-antd/src/api/monitor.ts` | 新增 attention/summary 类型与请求 |
| `backend/app/api/v1/endpoints/monitor.py` | 新增关注队列与工作台摘要端点 |
| `backend/app/services/monitor_attention.py` | 五类关注来源聚合和排序 |
| `backend/app/services/workbench_summary.py` | 生命周期与推荐下一步聚合 |
| `backend/app/api/v1/endpoints/loops.py` | `loopId/attentionOnly` 增量参数 |
| `frontend/apps/web-antd/src/__tests__/` | 新增上下文、实时、竞态、组件测试 |
| `backend/tests/`、`e2e/tests/` | API、权限、闭环和兼容回归 |

## 15. 明确不改

- 不修改预警规则 DSL、巡检周期、抑制和去重语义。
- 不新增 DCS 参数写入、自动实施或自动回退能力。
- 不重写评估、诊断、整定算法。
- 不删除评估/诊断/整定职能轴模块。
- 不删除旧路由和旧 API。
- 不实施 BL-9 评价周期字段。
- 不把全部图表和证据塞入首屏。

## 16. Definition of Done

1. 任务清单所有 P0/P1 项勾选并附验证证据。
2. 监控菜单包含运行总览、关注队列、预警记录、回路工作台；预警和回路不恢复一级模块。
3. 关注队列覆盖五类来源，排序原因可解释，权限正确。
4. 工作台显示实时状态、五阶段生命周期、推荐下一步和验证时间线。
5. 从预警/Tracker/评估/诊断/整定进入工作台时上下文不丢失。
6. 批量表格能力正式进入统一监控壳层，旧入口兼容通过。
7. 所有出口指标达到 §3.2 目标，未达标项不得以“后续优化”关闭。
8. 后端 ruff/format/alembic/pytest、前端 check:type/vitest、定向及全量 E2E 完成并归因。
9. 实现契约、UI/UX 规范、README、DESIGN 和 E2E 路由基线同步回写。
10. 安全扫描确认无 DCS PID 下写能力新增。
