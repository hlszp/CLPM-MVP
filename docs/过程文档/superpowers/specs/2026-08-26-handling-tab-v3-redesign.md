# 处置 Tab V3 重构设计（Handling Tab V3 Redesign）

- **批次**：M2 阶段 · G-处置批次
- **范围**：P0 功能 F-OP-01（OpsKanban×4 LaneCol）/ F-OP-02（StaffHBar+od-dot）/ F-OV-05 漏斗联动
- **基准**：1:1 复刻整定 V3 范式 + 原型 `renderOps()` L1054-L1098 视觉
- **原则**：0 后端改动、不删处置专属文件、整体一页不滚动、SVG 优先、HelpBubble 复用
- **日期**：2026-08-26
- **分支**：macbook（双机策略，push 需 `git push -u github macbook` 备份）

---

## 1. 上下文与现状

工作台共 5 Tab（总览/评估/诊断/整定/处置），前 4 Tab 已完成 V3 重构并合入（commit 91d7c176 + 13d04b6c）。整定 Tab 是最近完成的复刻模板，其范式（上下主结构 45/55 + 外壳 overflow-hidden 严格链 + 分区内滚动 + 单源 selectedRow + HelpBubble 复用 + SVG 图表优先 + 0 后端改动）必须 1:1 套用到处置 Tab。

### 已实装现状（不重写）

- 后端 `handling.py` 全端点实装（建议侧/工单侧/聚合侧，状态机强校验）
- 后端 `workbench.py` 的 A-05/A-08/A-09 为 TODO stub（返回空结构，本阶段不动）
- 前端 `api/handling.ts` 17 个 API 函数全实装
- 前端 `views/handling/workbench.vue + archive.vue + handling-detail-drawer.vue + order-detail-drawer.vue` 全实装
- 前端 `router/routes/modules/handling.ts` 5 段式路由实装
- 前端 `views/workbench/index.vue` 已挂载 `<Handling v-show="store.activeTab === 'handling'"/>`
- 前端 `views/workbench/tabs/handling.vue` 为 M1/M2 占位骨架（本阶段重写）
- 前端 `api/workbench.ts` 的 `WorkbenchApi.HandlingResult` 为 `unknown` 占位（本阶段补全类型契约）

---

## 2. 决策（已与用户确认锁定）

| 决策 | 选择 | 说明 |
|---|---|---|
| A · A-05 后端实装策略 | **方案 B 前端拼装** | 0 后端。`getHandlingOrdersApi` 分 4 次（PENDING/EXECUTING/VERIFYING/CLOSED）并行 + `getHandlingStatisticsApi` 取 SLA + `getHandlingLoopsApi` 取重开，前端 computed 聚合 kanban/staff_load。与整定 V3 单源+computed 范式一致 |
| B · 任务详情呈现 | **选项 2 内嵌紧凑卡 + 抽屉兜底** | 工作台右侧 ROW 嵌 `TaskDetailCard`（参考 TuningLoopDetail 模式），单源 selectedTask 联动；卡内「查看完整工单」按钮开既有 `order-detail-drawer.vue`（props: open/orderId/canOperate，深链接 orderId）做全量字段 + 流转操作 |
| C · 漏斗联动 | **新增 store.handlingLaneFilter** | `store/workbench.ts` 加 `handlingLaneFilter: ref<OrderStatus\|null>` + `setHandlingLaneFilter(lane)` action。总览 FunnelStat 点击 → `setActiveTab('handling')` + `setHandlingLaneFilter(lane)`；handling.vue watch 该值高亮/滚到对应泳道 + 顶部清除 chip |
| D · SLA 警示色阈值 | **默认 24h 临期** | 超期 `due<now` 红 `#FF4D4F` / 临期 `due<now+24h` 橙 `#FA8C16` / 正常 `due≥now+24h` 绿 `#52C41A` / `due==null` 显「无排程」灰。`due` 代理 = `task.plannedAt` |
| E · 阶段验收 + 合并 main | **阶段末再定** | 本阶段完成 + 用户显式批准后，届时再定 `--no-ff` 合并 / 保留分支 / 暂不合并 |

### 两个数据缺口（方案 B 代价，已告知用户）

1. **staff_load 无专用端点**：`handling.ts` 无 staff-load 函数（A-08 后端 stub 包 MV-01，前端无对应）。→ 由 pending+executing+verifying 三态 orders 按 `handler` 聚合派生（各状态在办数 + overdue 计数）。原型"及时率/人"无数据源 → StaffHBar 该列留灰占位 + HelpBubble 说明"及时率需 A-08 后端落地"。
2. **OrderItem 无 sla_deadline_at 字段**（A-E1 增强未做）：→ SLA 警示色用 `plannedAt`（排程时间）作 due 代理。CLOSED 卡片不套 SLA 色（统一绿系只读）。

### 泳道数偏差（已决策）

原型 `renderOps` L1060 实际只渲染 3 道（待处理/处理中/验证中），"已闭环"放在"闭环质量"卡作计数。但任务书与 A-05 契约 `kanban:{pending,executing,verifying,done}` 均为 4 道。**按任务书走 4 道**：CLOSED 作第 4 泳道只读绿系卡；原型"闭环质量"卡平移为 `HandlingSlaSummary`。

---

## 3. 架构与布局（方案 A · 看板优先）

V3 上下主结构 45/55，1:1 套整定范式，外壳 `overflow-hidden` 严格链 + 分区内 `overflow-y-auto` + 整体一页不滚动：

```
┌─ tabs/handling.vue  (h-full flex-col overflow-hidden) ──────────────┐
│ 上 45%  flex-[0.9]  min-h-0                                          │
│  ├ U1 断言带  flex-none ~32px（黄框 ⚠ 在办/闭环/超期/临期/SLA 一句话 + scope + HelpBubble）
│  └ U2 flex-1 flex-row gap-2 min-h-0                                  │
│     ├ SLA 窄边栏  flex-none ~150px  → HandlingSlaSummary（SVG 环+图例）
│     └ 4 泳道看板  flex-1 flex-row    → OpsKanban（4× LaneCol 等宽并排）
│ 下 55%  flex-[1.15]  min-h-0                                          │
│  ├ 行动栏  flex-none ~26px 深蓝（● + "行动区 · 人员负载 × 重开列表 × 任务详情" + HelpBubble）
│  └ LOW flex-1 flex-row gap-2 min-h-0                                  │
│     ├ StaffHBar          flex-1                                       │
│     ├ HandlingReopenList flex-1                                       │
│     └ TaskDetailCard     flex-[1.4] bg-[#F7F9FC]（单源 selectedTask 联动）
└─ <OrderDetailDrawer v-model:open v-model:orderId /> 兜底（既有组件，不重写本体）
```

### 范式约束

- **单源 selectedTask**：1 个 `ref<OrderItem|null>`，TaskCard@click 与 LaneCol@select 都写同 ref；TaskDetailCard watch 它刷新。
- **漏斗联动**：`store.handlingLaneFilter` watch → 高亮对应 LaneCol 头 + 滚动到该列 + 顶部显清除 chip（× 清除）；切 tab / 选任务卡时清除。
- **HelpBubble 复用**：断言带、行动栏、SLA 区、TaskDetailCard 的说明性文字一律转 `?` 图符弹窗（复用既有 `components/HelpBubble.vue`，不另建）。
- **SVG 优先**：SLA 环（HandlingSlaSummary）借鉴 `TuningFitnessCard` SVG donut；重开条借鉴 `TuningRootCauseDist` 反向色阶；不用 ECharts。

---

## 4. 组件清单与契约

新建组件均建于 `frontend/apps/web-antd/src/views/workbench/components/handling/`（与整定 `components/tuning/` 平级；若既有 tuning 组件直接在 `components/` 根，则处置同级建 `components/handling/` 子目录）。

| 组件 | props | emits | 职责 |
|---|---|---|---|
| **OpsKanban.vue** | `lanes:{pending,executing,verifying,closed:OrderItem[]}`, `laneFilter:OrderStatus\|null`, `selectedTaskId:string\|null` | `select(task:OrderItem)` | 4 泳道容器，渲染 4×LaneCol 等宽；laneFilter 命中列传 active=true |
| **LaneCol.vue** | `laneKey,title,color,count:number`, `tasks:OrderItem[]`, `selectedTaskId:string\|null`, `active:boolean` | `select(task)` | LaneHeader（色点+名+计数徽章）+ 卡片列 `overflow-y-auto` |
| **TaskCard.vue** | `task:OrderItem`, `selected:boolean`, `sla:'overdue'\|'near'\|'normal'\|'none'` | `select(task)` | 字段 orderNo/title/handler/due(plannedAt 倒计时)/od/loopTagName/reopen；SLA 警示色边框 |
| **StaffHBar.vue** | `staff:{handler,pending,executing,verifying,overdue:number}[]`, `selectedHandler:string\|null` | `select(handler)` | 横向 hbar（在办数=宽，按状态分色段）+ od-dot 超期标记；点击过滤（高亮该人卡片，其余降透明） |
| **HandlingSlaSummary.vue** | `statistics:{closeRate,avgCycleHours,ineffectiveRate,avgKpiDelta?}`, `sla:{normal,near,overdue:number}` | — | SVG 环（及时率=normal 占比）+ 图例数量百分比；底部 mini bars 近6周闭环数（取 statistics.series，无则降级静态） |
| **HandlingReopenList.vue** | `loops:{loopId,loopTagName,reopened,kpiDelta?}[]` | `select(loop)` | 反向色阶条（reopened 降序 top N，默认 8）；点击 → 可联动 TaskDetailCard 或预留 |
| **TaskDetailCard.vue** | `task:OrderItem\|null`, `kpiCompare?:{before,after}` | `open-drawer(orderId:string)` | 流转时间线（创建/认领/处理/验证 4 节点）+ 处置前后 KPI 对比 + 关键字段；「📦 查看完整工单」→ emit open-drawer |
| **tabs/handling.vue** | — | — | 容器：数据加载 + computed 聚合 + selectedTask + laneFilter watch + 上下布局 + drawer 挂载 |

### 复用既有

- `components/HelpBubble.vue`：通用 `?` 图符 + Modal.info 弹窗，处置 tab 复用不另建。
- `views/handling/components/order-detail-drawer.vue`：props `canOperate:boolean; open:boolean; orderId:null|string`。TaskDetailCard「完整工单」→ 容器置 `drawerOpen=true; drawerOrderId=task.id`。

### 补全

- **`store/workbench.ts`**：加 `handlingLaneFilter: ref<OrderStatus|null>(null)` + `setHandlingLaneFilter(lane:OrderStatus|null)` action；`setActiveTab` 切到非 handling 时清空 `handlingLaneFilter`。
- **`api/workbench.ts`**：`WorkbenchApi.HandlingResult` 由 `unknown` 补为具体类型契约（kanban/staff_load/reopen_list/sla 结构）。**容器实际不调 `fetchWorkbenchHandling`**（A-05 stub 返回空），改调 `handling.ts` 的 6 个函数；类型契约补全仅为对齐契约 + 后续 A-05 落地即换。

---

## 5. 数据流（方案 B 前端拼装 · 0 后端）

```
onMounted / store.scope 变化 →
const plantNodeId = (store.scopeParams as { plantNodeId?: string }).plantNodeId;  // 作用域
Promise.all([
  getHandlingOrdersApi({status:'PENDING',   pageSize:200, plantNodeId, sortBy:'plannedAt'}),
  getHandlingOrdersApi({status:'REOPENED',  pageSize:200, plantNodeId, sortBy:'plannedAt'}),  // REOPENED 独立状态
  getHandlingOrdersApi({status:'EXECUTING', pageSize:200, plantNodeId, sortBy:'plannedAt'}),
  getHandlingOrdersApi({status:'VERIFYING', pageSize:200, plantNodeId, sortBy:'plannedAt'}),
  getHandlingOrdersApi({status:'CLOSED',    pageSize:50,  plantNodeId, sortBy:'updatedAt'}),  // 近期闭环
  getHandlingStatisticsApi(months=1),         // SLA summary（全局，无 plantNodeId 参数 → SLA 汇总为全局口径，已知限制）
  getHandlingLoopsApi({plantNodeId, sort:'reopened', pageSize:8}),  // 重开列表（作用域）
])
↓
computed:
  kanban        = {pending:[...r0.items, ...r1.items], executing:r2.items, verifying:r3.items, closed:r4.items}
                  // REOPENED 并入待办道，TaskCard 据 status==='REOPENED' 打「重开」标
  staff_load   = aggregate(pending+executing+verifying by handler)   ← 缺口1 派生
  sla_breakdown= count(pending+executing+verifying by sla(plannedAt))← 缺口2 派生
  reopen_list  = loops.filter(l=>l.orderCounts.reopened>0).slice(8)
  assert_text  = 在办Σ / 闭环Σ / 超期Σ / 临期Σ / SLA 及时率
↓
selectedTask.value = task                 (单源，TaskCard@click / LaneCol@select 都写它)
store.handlingLaneFilter ← 总览漏斗点击   (watch → 高亮+滚到对应泳道+清除 chip)
TaskDetailCard "完整工单" → drawerOpen=true; drawerOrderId=task.id  (既有 order-detail-drawer)
```

### SLA 计算（前端）

- `due = task.plannedAt`（OrderItem 排程时间）
- `overdue`：`plannedAt < now`（红 #FF4D4F）
- `near`：`now ≤ plannedAt < now+24h`（橙 #FA8C16）
- `normal`：`plannedAt ≥ now+24h`（绿 #52C41A）
- `none`：`plannedAt == null`（灰，显「无排程」，不计入超期/临期统计）
- CLOSED 卡片不套 SLA 色（统一绿系只读）
- 断言带超期/临期数仅计有 plannedAt 的在办（pending+executing+verifying）工单

### KPI 对比数据源（TaskDetailCard）

- `selectedTask` 变化时，容器按需调用 `getOrderKpiComparisonApi(selectedTask.id)` 单次拉取处置前后 KPI 对比，传给 TaskDetailCard 的 `kpiCompare` prop。
- 失败或无数据 → TaskDetailCard 隐藏 KPI 对比区（不阻断流转时间线展示）。
- 非批量：仅当前选中任务拉一次，切换任务时取消上一次未完成请求。

---

## 6. 错误与状态处理

- **加载态**：每泳道 LaneCol 显骨架卡（3 行 shimmer）；SLA/staff/reopen 区显骨架；断言带显「加载中…」。
- **空态**：泳道空 → "暂无 [待办] 工单"居中灰字；staff 空 → "近30天无在办人员"；reopen 空 → "无重开工单 ✅"。
- **错误态**：单个 status 请求失败 → 该泳道显"加载失败 重试"按钮（仅重试该请求），不阻断其他泳道；statistics/loops 失败 → toast 非阻断 + 该区降级为空态；全部失败 → 断言带显"数据加载失败，请检查 17101 后端"。
- **plannedAt==null**：TaskCard 显"无排程"灰，不计入超期/临期统计。
- **CLOSED 过多**：仅取近 50 条（updatedAt desc），徽章数显 `statistics.summary.closedCount` 真实总数，卡片列底部"查看全部 →"跳处置档案 `archive.vue`（既有路由）。

---

## 7. 不做（防越界）

- P1/P2 增强（方案 §5.5 L547-L579）：SLA 策略管理页 / 多视图切换 / 泳道容量告警阈值配置 → 不做
- 处置档案页 `archive.vue` 改造 → 不做（已实装）
- 处置路由 `handling.ts` 改造 → 不做（已实装）
- 工单详情抽屉 `order-detail-drawer.vue` 本体重写 → 不做（仅做 TaskDetailCard 行点击→抽屉衔接）
- WebSocket 铃铛实时推送（G-WS 批次）→ 不做
- E2E S 场景编写（G-E2E 批次）→ 不做
- 不删除任何处置专属前后端文件（AGENTS.md 红线）

---

## 8. 测试与门禁

### 手动 7 项功能验证（完成度核验映射）

1. **F-OP-01**：4 泳道渲染，各态计数与后端一致；超期卡红边框、临期橙、正常绿。
2. **F-OP-02**：StaffHBar hbar 显示在办数分色段 + od-dot 超期人员标记；点击过滤生效。
3. **原型复刻**：任务卡 7 字段（no/title/by/due/od/loop/reopen）齐全；断言带一句话摘要。
4. **V3 视觉**：上下 45/55 + 外壳不滚动 + 分区内滚动 + HelpBubble 接入 + SVG 环/色阶。
5. **漏斗联动**：总览 FunnelStat 点击 → 切处置 tab + 对应泳道高亮 + 清除 chip。
6. **任务详情**：点任务卡 → TaskDetailCard 联动；「完整工单」→ order-detail-drawer 打开正确 orderId。
7. **越界核查**：archive.vue / 路由 / order-detail-drawer 本体 / P1P2 / WebSocket / E2E 均未改。

### 门禁（提交前必跑，本地即门禁）

- `cd frontend && pnpm run check:type` → 0 新增报错（允许既有 14 条 Dg 遗留）
- `cd frontend && pnpm exec eslint apps/web-antd --cache` → 0 error，warning 不增
- `node scripts/check-hex-whitelist.mjs --diff` → 本轮文件全部入白名单/基线

### 提交口径（仅用户显式要求时执行）

- Conventional Commits `<type>(workbench/handling): <subject>`，subject ≤50 字符祈使句
- 按逻辑单元拆分，单 commit ≤500 行
- `git push -u github macbook` 备份（双机策略）
- 不主动提交、不同步等待 CI（报告"已触发"即可）

---

## 9. 完成度核验（逐项，全过才算完成）

- [ ] 方案 §5.5 L530-L545 的 F-OP-01（OpsKanban×4 LaneCol）和 F-OP-02（StaffHBar+od-dot）逐条核对有证据
- [ ] 原型 `renderOps()` L1054-L1098 视觉 1:1 复刻（4 泳道 / 任务卡字段 / 人员负载 hbar / SLA 警示色 / 漏斗 lane 过滤联动）
- [ ] 工作台 V3 视觉对齐（上下 45/55 / overflow-hidden 严格链 / 分区内滚动 / 整体一页不滚动 / HelpBubble 接入 / SVG 图表用于 SLA 汇总与重开分布）
- [ ] 门禁全通过（check:type 0 新增 / ESLint 0 error / hex-baseline 本轮文件入基线）
- [ ] 无越界改动（P1/P2/archive/路由/WebSocket/E2E 均未触碰）
- [ ] `WorkbenchApi.HandlingResult` TypeScript 类型契约补全
- [ ] 任务详情抽屉衔接就绪（点任务卡 → TaskDetailCard 联动 → 「完整工单」开既有 order-detail-drawer.vue，不重写抽屉本体）
- [ ] 验收中发现的 bug 是否已修复

---

## 10. 后续阶段（本批次不做，仅记录边界）

- A-05/A-08/A-09 后端实装（方案 A）：填充 workbench.py 真实聚合，补 sla_deadline_at / 及时率，补单测 + openapi_baseline
- P1/P2 增强：SLA 策略管理页 / 多视图切换 / 泳道容量告警阈值配置
- WebSocket 铃铛实时推送（G-WS 批次）
- E2E S 场景编写（G-E2E 批次）
