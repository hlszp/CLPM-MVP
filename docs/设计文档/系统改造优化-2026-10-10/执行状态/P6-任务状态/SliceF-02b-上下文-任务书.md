# Slice F 任务书：P6-02b 上下文双端接通（O02-CONTEXT/PL）

Owner：子代理F ｜ 工作树 `/Users/zhangping/DEV/clpm-p6-kcode-20261014`（只在此树）｜ `F/`=`frontend/apps/web-antd/src/`

## 必读（按节）
1. `执行状态/P6-01a-契约与文件Owner-20261014.md` **§4.1/§4.2（上下文契约与路由）+ §6 O02-CONTEXT/PL 行 + §8 PL-01/PL-02/PL-06 行**
2. `09-工程师任务导向IA与工作台组件增补-2026-10-11.md` §2（mode 契约）
3. Slice C 已冻结：`F/views/loop/workbench360/workbench-context.ts`（WorkbenchContext/resolveWb360Route/wb360QueryFromContext/wb360ContextKey——**消费方以此为准，不改**）、index.vue Adapter 消费协议
4. 根 AGENTS.md「下钻契约」（目标页必须消费全部携带参数+回显+可单项清除）

## 范围（独占）
- 修改：`F/views/cockpit/wb-comps/{EvalRankTable.vue,use-drill.ts}`、`F/views/cockpit/components/{loop-detail-panel.vue,node-radar-panel.vue}`、`F/views/metric/{pid-dashboard.vue,indicator-analysis.vue}`、`F/views/metric/components/loop-confidence-content.vue`、`F/views/reports/performance.vue`、`F/api/{dashboard.ts,reports.ts}`
- **禁止**：改 workbench360 全目录（C 已冻结）、use-loop-realtime/realtime-ws/basic.vue（RT 冻结）、后端、其他旧页；commit/push
- 注意 loop-detail-panel.vue 已被 Slice D 接过实时——保留其 registerInterest 用法不破坏

## 契约要点
- 下钻发送端携带完整上下文：稳定 nodeId（装置/单元）/loopId、**精确 tsStart/tsEnd/timezone**（禁止近似滚动窗替换）、指标 identity、status/category、recordId/profile/configRevision（可得时）、from 来源
- 消费端（workbench360 已由 C 实现消费/回显/单项清除）——你的职责是发送端构造 `wb360QueryFromContext` 兼容的 query，及本组页面间互跳的同等消费/回显/返回恢复
- 装置排行（EvalRankTable）下钻到**该节点**回路清单（稳定 nodeId scope，非全厂）；行内解释该节点
- 历史可信度按指定快照（recordId/profile），昨天成功/今天不足/迟到旧成功语义不混（A09/A10）
- R01–R03 页面接线不在本 Slice（O-R-UI 后续），但 loop-confidence-content 按真实服务字段按值判空原则先立好（null 判空，不以键存在制造警告）
- 无效/越权对象报原因；统计数字与明细对账口径不丢参数

## 验证
- 新增/更新 vitest：发送端 query 构造完整性（精确起止/时区/nodeId）、消费回显+单项清除、返回恢复筛选、recordId 指定快照
- `pnpm run check:type`、eslint scoped 0；相关 vitest 全绿；全量 vitest 回归 exit 0
- 浏览器端到端证据由主协调 Phase 4 统一补，本 Slice 作者自验+单测层

## 完成判据
01a §7 与 §8 PL-01/02 相关判据逐条对照；移交记录写本文件末尾标"待验收"。水位>50% 即 WIP 停手写断点。

## 移交记录

**状态：待验收**（2026-10-14，实施者：子代理F，工作树 `/Users/zhangping/DEV/clpm-p6-kcode-20261014`，分支 `codex/p6-kcode-20261014`，未 commit/push）

### 改动清单（9 源文件 + 1 新测试；`F/api/reports.ts` 评估后无需变更，见偏差说明）

| 文件 | 改动 |
|---|---|
| `F/views/cockpit/wb-comps/use-drill.ts` | 新增发送端纯函数构造点（vitest 直覆）：`wb360DrillQuery`（wb360QueryFromContext 受管键 + section 一次性键显式携带 + from 返回来源）、`preciseCockpitWindow`（24h/7d/30d→精确起止+IANA 时区，禁滚动窗枚举顶替）、`localTimezone`、`canOpenWorkbench360`（对齐 loop-workbench authority 四角色）、`nodeLoopsDrillTarget`（无稳定 nodeId 返回 null，不回退全厂）；`DrillTarget` loops/metric 增 `nodeName`，loadLoops/loadMetric description 回显「范围：节点「X」（nodeId=N）/全厂」（对账不丢参数） |
| `F/views/cockpit/wb-comps/EvalRankTable.vue` | 行下钻全厂口径 → **该节点回路清单**（稳定 nodeId scope，标题去「全厂口径」）；无 nodeId 行（如「未挂载单元」兜底组）置灰禁用 + 行内 tooltip 报原因（无效对象报原因，不静默降级） |
| `F/views/cockpit/components/loop-detail-panel.vue` | 态二头部新增「工作台」入口 → wb360 single（loopId + 驾驶舱时间窗精确起止/tz + 左树 nodeId scope + from）；SPONSOR 不渲染。**Slice D registerInterest 块未触碰**（diff 可证） |
| `F/views/cockpit/components/node-radar-panel.vue` | 新增「节点回路 →」入口 → wb360 `mode=all` + nodeId scope（FACTORY 不携带）+ from；SPONSOR 不渲染 |
| `F/views/metric/pid-dashboard.vue` | `goIndicatorAnalysis` 近似窗（nearestIndicatorWindow，已删）→ 精确 tsStart/tsEnd/tz + plantNodeId/plantNodeName + from；待治理 TOP10/阀门越限位号 ClpmLoopLink → 本页上下文深链（wb360 diagnosis/tuning 剖面 + 精确窗口 + 节点 scope + from），无权角色降级纯文本（同 LoopLink 口径） |
| `F/views/metric/indicator-analysis.vue` | 消费端：新增 tsStart/tsEnd/tz 精确窗消费（优先于 window 枚举）、from 返回来源按钮、无效参数 notices 逐条回显（不静默丢弃）；精确窗回显于窗口下拉首项 + allow-clear **单项清除**回退原预设；环比=等长窗口 [start-Δ, start]（PL-02）。发送端：B 区条形/D 区清单位号 → wb360（loopId + 生效窗精确起止/tz + 节点 scope + from=fullPath 恢复本页筛选现场）。纯函数 `parseIndicatorQuery/buildIndicatorQuery/equalLengthPrevRange` 独立 `<script>` 块导出供测 |
| `F/views/metric/components/loop-confidence-content.vue` | 评估历史新增「指定快照」列：`snapshotDrillQuery`（recordKind=kpi_snapshot + recordId + recordVersion/profile + **该快照自身窗口** + from；A09/A10——无 resultRecordId 返回 null 不顶替 latest）；`snapshotTraceNote` 按值判空（BROKEN 才警告、NOT_TRACEABLE/UNLINKED 中性、键缺失/AVAILABLE 无提示）；无 pinia/router 注入直挂场景降级不崩（getActivePinia 守卫 + 路由注入可选） |
| `F/views/reports/performance.vue` | 回路报表位号 → wb360 assess 剖面（loopId + 报表精确窗口/tz + 节点 scope + **profile=聚合行 algorithmVersion** + from；聚合行跨多记录无单一 recordId，不编造） |
| `F/api/dashboard.ts` | `getBoardTreeApi` 补 startTime/endTime 类型（后端本已接收，此前靠展开绕类型——custom 窗缺失时后端回退 today 口径数字对不上） |
| `F/__tests__/p6-02b-drill-context.test.ts`（新增） | 24 例：发送端 query 完整性（精确起止/tz/nodeId/record/profile/from）、消费回显+单项清除（解析/回写/往返不漂移）、返回恢复（from→resolveWb360Route returnRef）、recordId 指定快照（含无身份不顶替、无版本不编造、窗口缺失省略）、等长环比、节点 scope 下推 API + description 回显、wb360DrillQuery→Slice C 冻结 resolveWb360Route 双端往返 |

### 验证（真实退出码）

- `cd frontend/apps/web-antd && pnpm exec vitest run src/__tests__/p6-02b-drill-context.test.ts` → **exit 0**（24/24 过）
- 相关存量回归 7 文件（cockpit-drill-honesty / cockpit-assessment-rank-tree / cockpit-score-rank / wb360-route-adapter / workbench-ranking-empty / cockpit-loops-mode-label / cockpit-p5-04-server-filtering）→ **exit 0**（58/58 过）
- `cd frontend && pnpm run check:type` → **exit 0**（2/2 tasks）
- `pnpm exec eslint <本 Slice 10 文件 + 测试>` → **exit 0**（--fix 仅重排 import/联合类型，无语义变更）
- `cd frontend && pnpm run test:unit`（全量）→ **exit 0**（94 文件 / **807/807 通过**）

### 偏差与说明

1. **`F/api/reports.ts` 未改动**：reports/performance.vue 的数据链走 `getBoardAggregateApi`（dashboard.ts）与 `getLoopSnapshotsApi`（metric.ts），窗口/plantNodeId 参数已齐备；reports.ts 内端点本页均未消费，无 scope/window/profile 缺口可补。
2. **ClpmLoopLink 替换**：共享组件 `components/clpm/loop-link.vue` 不在本 Slice 范围，pid-dashboard/indicator-analysis 三处行内位号改为本页上下文深链（原下拉多目标菜单随之去掉——其 performance 目标为旧页无消费协议；wb360 剖面覆盖 diagnosis/tuning 主任务），角色降级口径与 LoopLink 一致。
3. **reports/performance 未做 query 消费端**：本组互跳无任何发送端以该页为目标（驾驶舱 D3 禁跳管理后台；指标页互跳目标是 indicator-analysis/wb360），下钻契约不约束无入口页；其筛选状态本由控件自回显。
4. **返回恢复依赖 KeepAlive**：pid-dashboard/cockpit 筛选为组件/pinia 态，wb360 经 from 返回时由 tabbar KeepAlive 恢复现场（与 indicator-analysis 既有 fullPathKey=false 机制同口径）；indicator-analysis 的完整筛选（metric/精确窗/节点/fitness）经 from=fullPath 精确恢复。
5. **预存环境问题（非本 Slice 引入，已举证）**：`loop-confidence-content-error-states.test.ts` 单文件独立运行时 12 例全挂 `window.matchMedia is not a function`（jsdom 未 polyfill，全量运行时因同 worker 其他测试 polyfill 泄漏而通过）——在主目录干净基线（6c92cf69，未含本 Slice 任何代码）单跑该文件复现同样 12 挂；全量回归两轮佐证与本 Slice 无关。建议后续由测试基建 Owner 为该文件补 matchMedia stub（本 Slice 未动该测试文件）。
6. R01–R03 页面接线未做（任务书明确属 O-R-UI 后续）；loop-confidence-content 仅立按值判空原则。
7. 全量 vitest 收尾有 "something prevents Vite server from exiting" 提示（主目录全量同样存在，预存 teardown 残留，退出码为 0）。

### 01a §8 对照（PL-01/02 相关）

- 装置→单元→回路稳定 scope：EvalRankTable 节点下钻（nodeId）、pid-dashboard/indicator-analysis/reports 发送端 nodeId、node-radar-panel all 模式 scope ✓（单测覆盖）
- 精确非整日/跨日/历史起止+时区：全部发送端精确 ISO+IANA tz，禁滚动窗枚举 ✓；指标 identity：metric 键 pid→indicator 消费 ✓；profile/证据：recordId/profile 可得时携带、不可得（聚合行）不编造 ✓；筛选往返：from 全链路 ✓；等长比较：indicator-analysis 环比等长 ✓；最新快照另标：loop-confidence latest 与历史分列原有语义保持，指定快照独立入口 ✓
- A09/A10（02b 部分）：recordId 指定快照 + 昨天成功/今天不足/迟到旧成功不混（无身份不顶替）单测覆盖 ✓

### G-N2/G-N1 返修段

**状态：待复验**（2026-10-14 晚，02b Owner 返修；依据 Slice N 验收报告 §5 G-N2/G-N1 证据与修复建议；未 commit/push，未碰后端——ranking 端点 400 守卫归另一 Owner）

**G-N2（严重，02b 新链引入）装置排行→节点回路清单 500：int/UUID 身份空间不一致**

- 根因确认：`plant_node.id`=UUID PK 与 `source_node_id`=BIGINT 双身份；EvalRankTable 行 `id`=int source_node_id，ranking 端点 `plantNodeId` 过滤 UUID 列 → int 直推触发后端 500（Slice N API+浏览器实证）。
- 修复（前端主修，`use-drill.ts` + `EvalRankTable.vue`）：
  - 新增 `NodeDualIdentity`（id int + nodeId UUID 双身份最小结构）、`buildSourceNodeUuidIndex(tree)`（int→UUID 集合索引，同 UUID 去重，多映射可检出；树缺失=空索引）；
  - `nodeLoopsDrillTarget(row, uuidIndex)` 改判别式返回 `{ok:true,target}|{ok:false,reason}`：行 int id 经树解析为 UUID 下推；无稳定行 id / 无映射（含节点树整体缺失）/ 多映射（>1 UUID）三档**显式失败原因，不回退全厂、不静默丢弃**；
  - `DrillTarget` loops/metric 的 `plantNodeId` 类型放宽 `null | number | string`（UUID 字符串；loadLoops 既有 `String()` 直通）；EvalRankTable 以 `props.nodeTree` 建索引，不可下钻行置灰 + tooltip 报原因（复用既有禁用位）。
- 测试：`p6-02b-drill-context.test.ts` 该 describe 全量重写（索引构建/双身份去重、int→UUID 正确下推、无映射/树缺失/多映射各档显式失败、drill 集成 UUID 下推 + 范围回显 nodeId=uuid）；`p6-pl-reconciliation.test.ts` PL-01 树 mock 补 `nodeId` 双身份，链跳 1/1补充/3 断言由 int `'101'/'12'` 改为 `'uuid-unit-101'/'uuid-area-12'`，G-N2 注记改已返修口径。

**G-N1（一般，预存 66419bbd 非 P6）驾驶舱指标清单率值 ×100 显示**

- 核消费方全口径：`pct()` 唯一消费方为 loadMetric 的 metricText（METRIC_FIELDS 7 个 RankingItem 率字段，0–100 口径，API 实测 steadyRate=65.42/goodValueRate=100.0；indicator-analysis/pid-dashboard/reports 他端均直拼 %）；`fittingScore`（0–1 口径）×100 在 loadTuneTasks 调用处内联、不经 pct，不受影响。
- 修复（`use-drill.ts`）：`pct` 不再 ×100，0–100 直接 `toFixed(2)%`——'8100.00%'→'81.00%'。
- 测试：`p6-pl-reconciliation.test.ts` G-N1 用例断言 '8100.00%'/'6325.00%' → **'81.00%'/'63.25%'**（用例更名「已返修」，文件头登记段同步）；`cockpit-drill-honesty.test.ts` IA-02 fixture 由 0–1 口径（钉住错误 ×100 行为）改 0–100 真实口径（42/99/85/7），排序/缺值末位/'99.00%' 断言意图不变。

**验证（真实退出码）**

- scoped vitest（pl-reconciliation + p6-02b-drill-context + cockpit-drill-honesty + cockpit-assessment-rank-tree + cockpit-score-rank）→ **exit 0**（59/59）
- `cd frontend && pnpm run check:type` → **exit 0**（2/2 tasks；曾暴露测试 mock 缺 name 字段，NodeDualIdentity 补可选 name 后通过）
- `pnpm exec eslint <返修 2 源文件 + 3 测试文件>` → **exit 0**（--fix 仅 import/联合类型排序）
- `cd frontend && pnpm run test:unit`（全量）→ **exit 0**（103 文件 / **964/964 通过**，工作树含他 Slice 新增测试）

**未做**：后端 ranking 端点非 UUID 输入 400 参数校验（另一 Owner）；G-N3（参评 N>M 后端口径，Slice N 已登记，非本 Slice 范围）。
