# 阶段任务提示词：工作台 Tab5「问题处置」内容设计与实现（M2 · G-处置批次）

> 生成日期：2026-08-26
> 来源：按 `docs/过程文档/staged-implementation-workflow-2026-08-24.md` §3 骨架填空
> 上游完成锚点：G-整定 V3 已合入（commit 91d7c176 + 13d04b6c，整定 Tab 上下主结构 + HelpBubble + SVG 图表模式已立）
> 任务性质：M2 阶段 G-处置批次（P0 功能 F-OP-01~02 + 后端 A-05 填充决策 + 前端 OpsKanban×LaneCol / StaffHBar 组件）

## 1. 必读清单（按序阅读，禁止跳读）

1. **AGENTS.md**（仓库根）：MVP 覆盖说明（处置 v2.0 双实体 loop_action_item + handling_order 已落地）+ 关键注意事项行为红线 + 提交前本地检查 + Git 工作流
2. **改造方案 v2.0**：`docs/设计文档/CLPM工作台改进方案-v2.0.md`
   - §5.5 处置 Tab P0 功能项（L530-L545，F-OP-01 OpsKanban×LaneCol / F-OP-02 StaffHBar+od-dot / F-OV-05 漏斗联动）
   - §5.5 P1/P2 增强清单（L547-L579，本阶段不做，仅了解边界）
   - §后端接口 A-05/A-08/A-09 定义（L411-L421，kanban/staff_load/sla_summary/reopen_list 字段契约）
   - §数据模型字段新增（L350-L357，disposition_state/SLA/reopen_count/lane_capacity）
   - §业务逻辑联动（L384-L394，SLA 两级升级 / 工单重开闭环 / 事件归一推送）
   - §E2E 验收场景（L611-L616，超期红闪烁 / SLA 升级 / 缓存命中）
3. **MVP 处置模块设计**：`docs/MVP设计/08-处置模块设计方案.md`
   - §业务流全景（L10-L22）/ §核心决策（L33-L49，双实体/CLOSED 不可重开/安全边界）/ §双实体概念（L57-L68）/ §数据模型（L91-L145）/ §状态机（L159-L203，建议5态 PENDING/ACCEPTED/CONVERTED/REJECTED/IGNORED + 工单6态 PENDING/EXECUTING/VERIFYING/CLOSED/REOPENED/CANCELLED）/ §API 端点（L220-L260）
4. **实施计划 G-处置批次**：`docs/过程文档/工作台v2实施计划-2026-08-25.md` §4 M2 L138（G-处置：F-OP-01~02 / A-05 全字段 + A-09 / OpsKanban×4 LaneCol + StaffHBar+od-dot / 预计 3 天）
5. **原型 1:1 复刻基准**：`/Users/zhangping/Downloads/Kimi_Agent_CLPM工作台设计/app/index.html`
   - `renderOps()` L1054-L1098（4 泳道看板 / 闭环统计 / 超期临期清单 / 人员负载 hbar）
   - `taskCard()` L1099-L1113（任务卡片模板 + bindOps 交互）
   - `TASKS` mock L504-L518（字段：no/title/lane/by/due/od/loop/reopen）
   - `openTaskDrawer()` L1159-L1185（任务详情抽屉，状态/责任人/SLA/前后评分/重开/进展/延期/关闭）
   - `openLoopDrawer()` L1128-L1158（回路 360 抽屉"生成处置任务"按钮，体现诊断→处置衔接）
   - 漏斗联动 L760-L769（switchTab('ops') + lane 过滤）
6. **整定 V3 实施范式（必读，作为复刻模板）**：
   - `docs/过程文档/superpowers/specs/2026-08-26-tuning-tab-v3-redesign.md`（§0 主干 10 行规则 / §4 0 后端改动数据流表 / §5 门禁 4 节）
   - `frontend/apps/web-antd/src/views/workbench/tabs/tuning.vue`（V3 上下主结构 45/55 + 外壳 overflow-hidden 链 + 单源 selectedRow + HelpBubble 接入 6 处）
   - `frontend/apps/web-antd/src/views/workbench/components/HelpBubble.vue`（通用 ? 图符 + Modal.info 弹窗，处置 tab 复用此组件，不另建）
   - `frontend/apps/web-antd/src/views/workbench/components/TuningFitnessCard.vue` / `TuningRootCauseDist.vue`（SVG 环形图/饼图 + 图例数量百分比模式，处置 SLA 汇总/重开分布可借鉴）
   - `frontend/apps/web-antd/src/views/workbench/components/TuningLoopDetail.vue`（ECharts 接入既有 `/tuning/verification/data` 模式，处置任务详情抽屉可借鉴 h() 渲染 Modal.confirm）

## 2. 阶段范围（做什么 / 不做什么）

### 2.1 现状盘点（先核验，避免重复造轮子）

**已实装可复用（不重写）**：
- 后端 `backend/app/api/v1/endpoints/handling.py` 全端点实装（建议侧/工单侧/聚合侧，状态机强校验）
- 后端模型 `handling_order.py` + `loop_action_item.py` 双实体字段齐全
- 前端 `frontend/apps/web-antd/src/api/handling.ts` 17 个 API 函数全实装（getHandlingSuggestionsApi / getHandlingOrdersApi / getHandlingStatisticsApi / getHandlingLoopsApi / convertSuggestionsApi / startOrderApi / feedbackOrderApi / submitOrderApi / verifyOrderApi / cancelOrderApi / getOrderKpiComparisonApi 等）
- 前端 `frontend/apps/web-antd/src/views/handling/workbench.vue` + `archive.vue` + `components/handling-detail-drawer.vue` + `order-detail-drawer.vue` 全实装（建议审核/工单执行/统计卡/筛选/抽屉流转）
- 前端路由 `router/routes/modules/handling.ts` 5 段式实装
- 工作台 `views/workbench/index.vue` 已挂载 `<Handling v-show="store.activeTab === 'handling'"/>`

**待新建/改造（本阶段重点）**：
- `frontend/apps/web-antd/src/views/workbench/tabs/handling.vue` 当前为 M1/M2 占位骨架，需重写为 V3 上下主结构视觉
- `frontend/apps/web-antd/src/api/workbench.ts` 中 `WorkbenchApi.HandlingResult` 类型为 unknown 占位，需补全 TypeScript 类型契约
- 新建组件（参考整定 V3 命名风格）：
  - `OpsKanban.vue` 或 `HandlingKanban.vue`（4 泳道看板：待办/处理中/验证中/已关闭，每泳道 LaneCol 列）
  - `LaneCol.vue`（单泳道列，含 LaneHeader + TaskCard 列表 + 滚动）
  - `TaskCard.vue`（任务卡片，字段 no/title/by/due/od/loop/reopen，SLA 警示色）
  - `StaffHBar.vue`（人员负载横向 hbar + od-dot 超期标记）
  - `HandlingSlaSummary.vue`（SLA 汇总环形图，借鉴 TuningFitnessCard SVG 模式）
  - `HandlingReopenList.vue`（重开列表，可借鉴 TuningTopWorst 反向色阶条模式）

### 2.2 本阶段任务清单（G-处置批次 P0）

按实施计划 §4 L138：
- F-OP-01：OpsKanban×4 LaneCol（4 泳道看板 + 任务卡片 + 选中联动 + 漏斗 lane 过滤）
- F-OP-02：StaffHBar+od-dot（人员负载 hbar + 超期 od 标记 + 点击人员过滤）
- F-OV-05：处置漏斗联动（从总览 tab 漏斗点击切到处置 tab + lane 过滤）
- 工作台 V3 视觉对齐：上下主结构 + 外壳不滚动 + 分区内滚动 + HelpBubble 接入 + SVG 图表优先

### 2.3 明确不做（防越界）

- P1/P2 增强（方案 §5.5 L547-L579）：SLA 策略管理页、多视图切换、泳道容量告警阈值配置 → 不做
- 处置档案页 `archive.vue` 改造 → 不做（已实装）
- 处置路由 `handling.ts` 改造 → 不做（已实装）
- 工单详情抽屉 `order-detail-drawer.vue` 重写 → 不做（已实装，仅在工作台 tab 内做"行点击 → 抽屉"衔接）
- WebSocket 铃铛实时推送（G-WS 批次）→ 不做
- E2E S 场景编写（G-E2E 批次）→ 不做
- 不删除任何处置专属前后端文件（AGENTS.md 红线）

### 2.4 依赖确认

- G-整定 V3 已合入（HelpBubble / WorkbenchShell / V3 容器范式已立）✓
- 后端 `handling.py` 全端点实装 ✓
- 工作台 store / 路由 / Tab 注册已就绪 ✓

## 3. 执行纪律

- **提交/推送/CI 仅在用户显式要求时执行**（AGENTS.md Git 纪律）；commit 按 Conventional Commits `<type>(workbench/handling): <subject>`，subject ≤50 字符祈使句，按逻辑单元拆分单 commit ≤500 行
- **当前分支**：`macbook`（双机分支策略，可直接小步提交，push 需 `git push -u github macbook` 备份）
- **DB 迁移与 ORM 改动同批**（如本阶段决策后端实装 A-05，则 service + 任何模型索引改动同批提交，alembic check 退出码 0）
- **门禁**（提交前必跑，本地即门禁）：`cd frontend && pnpm run check:type` + `pnpm exec eslint apps/web-antd --cache` + `node scripts/check-hex-whitelist.mjs --diff`；后端若有改动追加 `cd backend && uv run ruff check . && uv run ruff format --check . && uv run pytest -q && uv run alembic check`
- **0 后端改动原则**（强建议）：参考整定 V3 范式，前端用既有 `getHandlingOrdersApi` + `getHandlingStatisticsApi` + `getHandlingLoopsApi` 拼装看板，避免触碰 A-05 stub；如必须后端实装，需用户显式授权（见 §5 决策点 A）
- **视觉复刻**：1:1 还原原型 `renderOps()` L1054-L1098 视觉（4 泳道 / 任务卡片字段 / 人员负载 hbar / SLA 警示色），但布局对齐整定 V3 上下主结构 + 等高对齐 + 外壳不滚动
- **HelpBubble 复用**：所有说明性文字（如"泳道容量告警""SLA 升级规则""重开闭环"等）一律用既有 `HelpBubble.vue` 转 ? 图符弹窗，不在区域内堆文字描述（参考整定 tab 6 处接入模式）
- **SVG 图表优先**：SLA 汇总 / 重开分布等用 SVG 环形图/饼图（借鉴 TuningFitnessCard / TuningRootCauseDist 模式），不滥用 ECharts
- **单源 selectedTask**：参考整定 selectedRow 模式，1 个 ref selectedTask，清单 @select + 任务卡 @click 都更新同 ref，避免联动错位
- **暂停/恢复**：阶段进度以本文件"修订记录"为状态锚点（见文末），恢复时先读最新修订行

## 4. 完成度核验问题（逐项回答，全部通过才算完成）

- [ ] 方案 §5.5 L530-L545 的 F-OP-01（OpsKanban×4 LaneCol）和 F-OP-02（StaffHBar+od-dot）是否逐条核对并有证据（截图/命令输出）？
- [ ] 原型 `renderOps()` L1054-L1098 的视觉是否 1:1 复刻（4 泳道 / 任务卡字段 no·title·by·due·od·loop·reopen / 人员负载 hbar / SLA 警示色 / 漏斗 lane 过滤联动）？
- [ ] 工作台 V3 视觉对齐是否达成（上下主结构 45/55 / 外壳 overflow-hidden 严格链 / 分区内滚动 / 整体一页不滚动 / HelpBubble 接入所有说明性文字 / SVG 图表用于 SLA 汇总与重开分布）？
- [ ] 门禁命令是否全部通过？（check:type 0 新增报错，允许既有 14 条 Dg 遗留；ESLint 0 error，warning 不增；hex-baseline 本轮文件全部入白名单/基线）
- [ ] 是否引入了越界改动？（P1/P2 增强 / archive 页 / 路由 / WebSocket / E2E 均未触碰）
- [ ] `WorkbenchApi.HandlingResult` TypeScript 类型契约是否补全（字段名与后端 A-05 返回对齐，或与既有 handling API 拼装结构对齐）？
- [ ] 任务详情抽屉衔接是否就绪（工作台 tab 内点任务卡 → 打开既有 `order-detail-drawer.vue`，不重写抽屉本体）？
- [ ] 本文件"修订记录"是否已追加本阶段完成同步行？
- [ ] 是否有验收中发现的 bug 需在进入下阶段前修复？

## 5. 人工决策点（出现即暂停等待用户）

### 决策点 A：A-05 后端实装策略（最关键，启动前必须确认）

工作台 A-05 `GET /workbench/handling` 当前为 TODO stub（返回空 kanban/staff_load/sla_summary/reopen_list），`GET /staff-load` + `GET /lane-more` 也 stub。三种策略择一：

- **方案 A（后端实装）**：在 `backend/app/services/` 新建 `workbench_handling.py`，写聚合 service 查 handling_order + loop_action_item + KpiSnapshotHourly 拼 kanban/staff_load/sla_summary/reopen_list，A-05/A-08/A-09 三端点全实装。优点：数据真实；缺点：违反 0 后端改动原则，工作量 +1.5 天，需补单测 + openapi_baseline 更新
- **方案 B（前端拼装，0 后端改动，强推荐）**：参考整定 V3 范式，前端 tabs/handling.vue 用既有 `getHandlingOrdersApi({status: 'PENDING|EXECUTING|VERIFYING|CLOSED'})` 分 4 次拉取 + `getHandlingStatisticsApi` 取 SLA 汇总 + `getHandlingLoopsApi` 取重开列表，前端 computed 聚合 kanban/staff_load。优点：0 后端改动，复用既有端点；缺点：4 次请求 + 前端聚合逻辑稍重
- **方案 C（demo fallback，P0.5 后端补）**：A-05 stub 不动，前端用 demo 数据 + 标注「（示例）」，参考整定 V3 TuningFitnessCard level_counts fallback 模式。优点：最快交付；缺点：非真实数据

**推荐方案 B**（与整定 V3 一致，0 后端改动，复用既有 17 个 API 函数）

### 决策点 B：任务详情抽屉复用 vs 工作台内嵌

原型 `openTaskDrawer()` L1159-L1185 是全屏抽屉。工作台 tab 内点任务卡时：
- 选项 1：复用既有 `order-detail-drawer.vue`（已实装，深链接 focus=orderId）
- 选项 2：在工作台右侧 ROW 详情区嵌一个紧凑详情卡（参考整定 TuningLoopDetail 模式）

**推荐选项 2**（与整定 V3 上下主结构对齐：左 4 泳道看板 / 右 任务详情卡 + 趋势/SLA 快照），既有抽屉作为"全量字段查看"入口保留

### 决策点 C：漏斗联动契约

总览 tab 漏斗点击 → 切处置 tab + lane 过滤。需确认 `useWorkbenchStore` 是否已有 `laneFilter` 状态，若无则需补 store 字段（属工作台 store 改动，非处置专属）。参考原型 L760-L769 `switchTab('ops')` + lane 过滤后 `renderOps()` 重新渲染逻辑

### 决策点 D：SLA 警示色阈值

原型任务卡 `od`（overdue）字段 + `due` 截止时间。SLA 警示色阈值（超期红 / 临期橙 / 正常绿）需用户确认具体阈值（默认建议：超期 = due < now / 临期 = due < now+24h / 正常 = due ≥ now+24h）

### 决策点 E：阶段验收结论 + 合并 main

本阶段完成 + 用户显式批准后，是否合并 macbook 分支回 main（`--no-ff`）？还是继续在 macbook 分支累积？

## 6. 建议执行步骤（参考整定 V3 实施链）

1. 先做 A/B 方案线框（brainstorming skill + Visual Companion），用户批准方向后再写 spec
2. 写 spec：`docs/过程文档/superpowers/specs/2026-08-26-handling-tab-v3-redesign.md`（参考整定 V3 spec 骨架）
3. 写 plan：`docs/过程文档/superpowers/plans/2026-08-26-handling-tab-v3.md`（writing-plans skill，3 Checkpoint）
4. Checkpoint 1：OpsKanban + LaneCol + TaskCard（ESLint 0）
5. Checkpoint 2：StaffHBar + HandlingSlaSummary + HandlingReopenList（ESLint 0）
6. Checkpoint 3：tabs/handling.vue 容器重写 + WorkbenchApi.HandlingResult 类型补全 + 3 道门禁（ESLint / check:type / hex-baseline）+ 手动 7 项功能验证
7. 全绿后报告用户，等待显式授权 commit（不主动提交）

## 修订记录

| 日期 | 修订 | 说明 |
|---|---|---|
| 2026-08-26 | v1.0 | 初版生成，按 staged-implementation-workflow §3 骨架填空；待新任务接收并执行 |
