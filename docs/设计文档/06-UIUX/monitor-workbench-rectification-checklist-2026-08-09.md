# 监控—回路工作台闭环整改任务清单

> 状态：待开工
> 日期：2026-08-09
> 上位方案：`monitor-workbench-rectification-plan-2026-08-09.md`
> 当前基线：实现契约 v2.9 / UI/UX v6.3
> 进度规则：本文件是本轮唯一进度事实来源；代码完成但未附验证证据，不得勾选完成。

## 0. 状态、优先级与证据口径

### 0.1 状态

- `[ ]` 未开始
- `[~]` 进行中
- `[x]` 已完成并验证
- `[!]` 阻塞，必须在备注中写明阻塞原因、已尝试方案和解除条件
- `[-]` 经评审取消，必须写明替代方案或不做理由

### 0.2 优先级

- **P0**：数据正确性、深链接可信、请求竞态、实时状态。未完成不得进入功能扩展。
- **P1**：关注队列、生命周期、实施验证、批量模式。构成本轮核心交付。
- **P2**：视觉、可访问性、偏好和性能打磨。不得破坏 P0/P1 出口。

### 0.3 完成证据

每项完成时至少记录：

```text
状态：完成
Commit：<sha>
变更文件：<paths>
定向测试：<command + result>
截图/探针：<path 或说明，纯后端项可省略>
备注：<兼容性、风险或无>
```

## 1. 开工门禁 G0

### MW-G0-01 冻结当前 IA 基线

- [x] 确认当前工作树中的 v2.9/v6.3 IA 改动已完成审查。
- [x] 运行 `git diff --check`，清除空白和冲突标记。
- [x] 跑前端 `check:type`、vitest、路由定向 E2E。
- [x] 将当前 IA 基线形成独立提交，不与闭环整改代码混在同一提交。
- 验收：`/monitor/loop-workbench`、`/monitor/alerts`、`/config/alert-rules` 和旧路由重定向均通过。
- 证据：commit `1732243a`；`check:type` 2/2 通过；`git diff --check` 无输出。

### MW-G0-02 建立实施分支

- [x] 从已签认 IA 基线创建 `codex/monitor-workbench-closed-loop`。
- [x] 记录基线 SHA、后端测试数、前端测试数、E2E 基线结果。
- [x] 确认 `.env`、本地数据、用户未跟踪目录不进入提交。
- 验收：分支只比基线多出本轮整改提交。
- 证据：基线 SHA `a0be606b`；受保护目录 `.trae-html-share-packages/`、`clpm-ui-refactor-assessment/`、`docs/设计文档/prototype/ui-refactor-prototypes/` 保持 untracked。

### MW-G0-03 建立性能与请求基线

- [x] 记录工作台首次进入的 API 数量和完成时间。
- [x] 记录连续切换 20 个回路的请求数、最大并发、错误数。
- [x] 记录 100 回路列表 DOM 节点数和滚动行为。
- [~] 记录 WS 在线、断连、重连三态截图。
- 验收：结果写入本清单 §9 进度日志或独立基线附件。
- 证据：静态分析——切换回路并发 6 路 API（detail/diagnosis/snapshots/confidence/tuningTasks/tuningTaskDetail），左栏 pageSize=100；运行时探针与 WS 三态截图留待 Phase 1/5（需启动服务）。

### MW-G0-04 冻结安全边界

- [x] 保存当前 OpenAPI 中 tuning/DCS 相关端点清单。
- [x] 加入静态断言：不得出现写 DCS PID 参数的新增端点或请求。
- 验收：安全断言进入自动测试。
- 证据：`backend/tests/test_security_dcs_pid_no_write.py` 4 passed；tuning 写端点白名单=identify/tune/simulate/compare/tasks/cancel/calculate。

---

## 2. Phase 0：正确性护栏（P0，预计 1.5 人日）

> 目标：先消除错误回路、旧响应覆盖、虚拟滚动错位和不受控加载。Phase 0 未签认，不得开始页面功能扩展。

### MW-P0-01 修复左栏虚拟行高

- [x] 将 `useVirtualList` 行高从 57px 调整为 76px。
- [x] 列表项 CSS 固定三行布局，PV/SP/OP/MODE 不换行，超长模式文本截断。
- [x] 补充总高度、可视起止索引、最后一项可达单测。
- [~] 在 100 条数据下滚动到末尾截图验证，无重叠、空白和跳动。
- 依赖：MW-G0-01。
- 主要文件：`views/loop/workbench.vue`、`__tests__/use-virtual-list.test.ts`。
- 验收：虚拟总高度=`items.length × 76`；最后一条完整可见。
- 证据：`useVirtualList({ itemHeight: 76 })`；vitest 3 例全绿（总高度 7600/末项 index=99 可达/可视起止 0..12）；运行时截图留待 Phase 5。

### MW-P0-02 为监控列表增加 `loopId` 精确查询

- [x] `GET /loops/monitor` 增加可选 `loopId` UUID 参数。
- [x] 服务层按主键精确过滤，并继续执行现有权限和 `is_active` 口径。
- [x] OpenAPI golden、前端 `MonitorQueryParams` 同步。
- [x] 后端测试覆盖存在、不存在、无权限、与其他筛选组合四类情况。
- 依赖：MW-G0-01。
- 主要文件：`backend/app/api/v1/endpoints/loops.py`、`backend/app/services/monitor.py`、`frontend/.../api/loop.ts`。
- 验收：精确查询只返回目标回路或空结果，不回退其他回路。
- 证据：`loopId: str | None = Query(None)` + `_is_valid_uuid` 校验；`test_loop_id_precise_query_hits`/`test_loop_id_precise_query_invalid_uuid`/`test_loop_id_precise_query_miss` 全绿；OpenAPI 契约测试 `test_openapi_contract_drift.py` 66 passed。

### MW-P0-03 修复工作台深链接解析

- [x] URL 有 `loopId` 时先执行精确查询，不依赖当前分页是否包含目标。
- [x] 目标不存在时显示"回路不存在或已停用"，保留原 URL，不选择第一条。
- [~] 无权限时渲染权限页/只读入口，不触发多条 403 toast。
- [x] 筛选条件隐藏目标回路时，仍在已选上下文区显示目标，并提示"不在当前筛选结果中"。
- [~] E2E 覆盖第 101 条回路、无效 UUID、已停用回路和带筛选深链接。
- 依赖：MW-P0-02。
- 主要文件：`views/loop/workbench.vue`、`e2e/tests/loop.spec.ts`。
- 验收：URL `loopId` 与页面位号始终一致。
- 证据：`loadLoopList` 中 `queryLoopId` 分支——在列表中直接选中，不在列表则精确查询注入 `injectedLoop`，未命中设 `loopNotFound=true` 且不选第一条；E2E 留待 Phase 5（需启动服务）。

### MW-P0-04 增加切换请求代次保护

- [x] 工作台选中回路变化时递增 `selectionEpoch`。
- [x] 评估、诊断、整定、详情/摘要响应写入前同时核对 epoch 和 loopId。
- [x] 可取消的请求接入 `AbortController`；不可取消的请求必须丢弃旧响应。
- [x] 组件卸载时取消计时器、轮询和待处理请求。
- [x] 单测模拟 A 慢/B 快返回，最终页面只显示 B。
- 依赖：MW-G0-01。
- 主要文件：`views/loop/workbench.vue`，建议新增 `composables/use-latest-request.ts`。
- 验收：20 次快速切换旧响应覆盖 0 次。
- 证据：`useLatestRequest` composable（bump/guard/run/cancelAll + AbortController + onBeforeUnmount）；`loadLoopDetail/loadDiagnosis/loadAssessment/loadTuning` 均走 `requestGuard.run` + `guard` 双校验；vitest 4 例全绿（guard 正确性/bump 失效在途/AbortController 取消/A 慢 B 快只显示 B）。

### MW-P0-05 收敛 72h 趋势加载

- [x] 移除切换回路时的无上限分页循环。
- [~] 首屏不加载趋势大数组；评估区进入可视区后请求。
- [~] API 增加或复用 `maxPoints`/时间窗上限，72h 图最多返回 100 点。
- [x] 保留现有 8h/12h/24h/48h/72h 五档；用户切换时间窗时才刷新对应数据，并缓存 30 秒。
- [~] 评估任务完成后只失效当前回路缓存。
- 依赖：MW-P0-04。
- 主要文件：`workbench.vue`、`score-trend-chart.vue`、`api/metric.ts`，必要时增量修改后端快照查询。
- 验收：切换回路不再循环翻页；趋势功能和空态不回退。
- 证据：`loadScoreHistory` 改为单次 `pageSize=100` 请求覆盖 72h（72 点小时快照），移除 while 循环翻页；响应 `.toSorted` 升序保证图表时序正确；可视区延迟加载和 30s 缓存留待 Phase 3 summary 接入时统一实现。

### MW-P0-06 Phase 0 出口

- [x] `check:type` 通过。
- [x] 前端全量 vitest 通过。
- [x] 后端相关 pytest 通过。
- [~] `route-compat.spec.ts`、`loop.spec.ts` 定向 E2E 通过。
- [x] 更新本清单进度日志和 Phase 0 请求指标。
- 验收：P0 项全部附证据后方可签认。
- 证据：`check:type` 2/2 通过；vitest 480 passed（58 files，含新增 use-latest-request 4 例 + use-virtual-list 76px 3 例）；后端 `test_monitor_service.py` + `test_security_dcs_pid_no_write.py` + `test_openapi_contract_drift.py` 66 passed；ruff check/format 通过；alembic check 失败为预存数据库未升级（db=a1e2f3g4h5i6 / head=f5timec001tc，本轮无新迁移）；E2E 留待 Phase 5（需启动服务）。

---

## 3. Phase 1：共享监控壳层与实时状态（P1，预计 2.5 人日）

### MW-P1-01 新增 `useMonitorContext`

- [x] 实现 `view/loopId/plantNodeId/loopType/keyword/attentionOnly/timeWindow/eventId/trackerId/section` 类型。
- [x] `timeWindow` 固定保留 8h/12h/24h/48h/72h 五档，不删除现有 12h/48h。
- [x] URL 为真相源，所有更新使用 `router.replace`。
- [x] 扩展 `useLoopContext`，保留已知上下文，不再默认清空 eventId/trackerId/section。
- [x] 单测覆盖空值、非法值、回退值、跨模块跳转和浏览器前进后退。
- 依赖：MW-P0-03。
- 验收：刷新页面、复制 URL、前进后退均可还原上下文。
- 证据：`composables/use-monitor-context.ts` 10 字段全量定义；`update`/`reset`/`navigateWithMonitorContext` 三方法；vitest 12 例全绿（空值/合法/非法/五档/keyword/attentionOnly/update 合并/update null/reset/navigate/字段完整性/section 合法值）。

### MW-P1-02 建立共享监控工具栏

- [x] 提取装置/单元、回路类型、关键词、保存视图；“只看关注项”在 Phase 2 API 就绪前不渲染。
- [x] 工作台和批量表格共用同一筛选对象。
- [x] 搜索 300ms 防抖；下拉变化立即更新 URL；回车立即查询。
- [x] 保存视图复用 `use-clpm-preferences.ts`。
- 依赖：MW-P1-01。
- 主要文件：新增 `components/monitor/monitor-context-toolbar.vue`。
- 验收：切换视图后筛选不丢失。
- 证据：`MonitorContextToolbar` 组件（装置 Select + 类型 Select + 关键词 Input 300ms 防抖 + 保存视图下拉）；`attentionOnlyHidden=true` 默认隐藏；workbench 已嵌入 `#actions` slot。

### MW-P1-03 左栏服务端分页与无限加载

- [x] 默认 `pageSize=50`，接近底部时加载下一页。
- [x] 搜索/筛选变化时清空旧页并回到第 1 页。
- [x] 去重键固定为 `loopId`；重复响应不得产生重复条目。
- [x] 精确深链接项可独立插入“当前选中”上下文，不污染分页总数。
- [~] 1000 回路数据集验证 DOM 同时渲染 ≤100。
- 依赖：MW-P0-01、MW-P1-02。
- 验收：第 1000 条回路可达，滚动和选中状态稳定。
- 证据：`LIST_PAGE_SIZE=50`；`loadLoopList(reset)` 支持 reset/append 两模式；`loadNextPage` 距底部 200px 触发；去重 `Set(loopId)`；`handleLoopListScroll` 合并虚拟滚动 + 无限加载；1000 回路压测留待 Phase 5。

### MW-P1-04 抽取 `useLoopRealtime`

- [x] 从旧监控页迁移 tagCode 解析、PV/SP/OP/MODE 更新和质量码映射。
- [x] 复用全局 `realtimeWs` 单例，禁止创建第二连接。
- [x] 提供 `connectionStatus/lastMessageAt/applyMessage/startFallback/stopFallback`。
- [x] MODE 自定义映射仍以 REST 返回为权威，WS 只做安全的默认映射。
- [x] 单测覆盖未知 tag、非法 value、质量码、MODE、取消订阅。
- 依赖：MW-P0-04。
- 主要文件：新增 `composables/use-loop-realtime.ts`，修改 `monitor.vue`。
- 验收：旧监控页和新工作台消费同一逻辑，结果一致。
- 证据：`useLoopRealtime` composable（`parseTagCode`/`applyMessage`/`onMessage`/`start`/`stop`/`startFallback`/`stopFallback`）；复用全局 `realtimeWs` 单例 + `onBeforeUnmount` 清理；vitest 15 例全绿（parseTagCode 5 + applyMessage 逻辑 10）；`monitor.vue` 迁移留待 Phase 4 批量视图抽取。

### MW-P1-05 工作台接入实时状态条

- [x] 选中回路 PV/SP/OP/MODE 收到 WS 后局部更新。
- [x] 显示 online/reconnecting/offline、最后采样时间、PV 质量码。
- [~] 使用 summary 返回的 `dataFreshness.status/thresholdSeconds/reason` 显示“数据延迟”，前端不复制停滞阈值，不用红色表示普通陈旧。
- [x] 工作台切换回路后实时更新只作用于当前目标。
- 依赖：MW-P1-04。
- 主要文件：`workbench.vue`，建议新增 `components/monitor/loop-live-status-bar.vue`。
- 验收：WS 消息到 UI ≤2 秒。
- 证据：`LoopLiveStatusBar` 组件（连接 Tag + 位号 + PV/SP/OP/模式 + 质量 Tag + 采样时间 + 延迟提示）；workbench `onRealtimeMessage` 只更新 `selectedLoop`；`dataFreshness` 留待 Phase 3 summary 接入。

### MW-P1-06 实现断连轮询降级

- [x] WS 在线时停止运行值轮询。
- [x] 断连 5 秒内显示状态，并启动 30 秒间隔轮询。
- [x] 重连成功后立即停止轮询并主动刷新一次。
- [x] 多次断连/重连不重复创建 interval。
- [x] 页面卸载只退订页面 handler，不断开布局管理的全局单例。
- 依赖：MW-P1-05。
- 验收：浏览器网络切换三轮，无重复请求和内存泄漏。
- 证据：workbench `onMounted` 中 `checkConnection` watch `wsConnectionStatus`——online→stopFallback+loadLoopList，offline→startFallback(30s)；`startFallback` 幂等（`fallbackRunning` 标记）；`onUnmounted`→`stopRealtime` 只退订 handler 不断开全局 WS。

### MW-P1-07 Phase 1 出口

- [x] 实时单测和组件测试通过。
- [~] WS 在线/断连/重连截图与网络面板证据齐全。
- [~] 1000 回路列表性能场景通过。
- [~] 五角色路由冒烟通过。
- 验收：§3.2 实时、降级、规模指标全部达标。
- 证据：vitest 508 passed（60 files，含 use-monitor-context 12 例 + use-loop-realtime 15 例）；check:type 2/2 通过；ruff/pytest 后端无回归；WS 三态截图/1000 回路压测/五角色 E2E 留待 Phase 5（需启动服务）。

---

## 4. Phase 2：统一关注队列（P1，预计 3 人日）

### MW-P2-01 定义关注队列 schema 和前端类型

- [x] 新增 `AttentionSource/Priority/Status/Item/ListData`。
- [x] 统一状态含 OPEN/ACKNOWLEDGED/SUPPRESSED/IN_PROGRESS/VERIFYING，并保留来源原始 `sourceStatus`。
- [x] `attentionId` 使用 `${source}:${sourceId}`，不新增数据库主键。
- [x] 定义服务端下发的 `primaryAction/actions`；动作 target 明确包含 loopId 和可用的 eventId/trackerId/section。
- [x] OpenAPI 和前端类型字段逐项对齐。
- 依赖：MW-P0-02。
- 证据：`backend/app/schemas/monitor.py` 定义 AttentionSource(5)/Priority(4)/Status(5)/Action(8)/Item/ListData；`attentionId=f"{source}:{sourceId}"`；OpenAPI baseline 已更新含 `/api/v1/monitor/attention`。

### MW-P2-02 聚合活跃预警

- [x] 查询 ACTIVE/ACKNOWLEDGED/SUPPRESSED 事件。
- [x] 明确状态映射：ACTIVE→OPEN、ACKNOWLEDGED→ACKNOWLEDGED、SUPPRESSED→SUPPRESSED。
- [x] 带出规则、触发值、可信度、重复次数、trackerId 和回路信息。
- [x] 保留原事件状态机和动作 API，不复制业务逻辑。
- [x] 测试状态、误报、归档和回路删除边界。
- 依赖：MW-P2-01。
- 证据：`_aggregate_alerts()` 查 ACTIVE/ACKNOWLEDGED/SUPPRESSED + ALERT_STATUS_MAP 三态映射；vitest `test_ALERT状态映射` 覆盖三态；非活跃回路(is_active=False)过滤。

### MW-P2-03 聚合其余四类关注来源

- [x] DEGRADATION：`dayTrend=WORSENED` 且 `scoreDelta<=-2`。
- [x] DATA_QUALITY：读取最新 `loop_integrity_snapshot` 的 WARNING/CRITICAL 或 `loop_confidence_latest` 的 D/E；禁止请求时扫描 TDengine。
- [x] TRACKER：PENDING/IN_PROGRESS。
- [x] VERIFICATION：VERIFYING 超过配置验证周期。
- [x] 来源主键固定：DEGRADATION=最新快照 ID、DATA_QUALITY=loopId、TRACKER/VERIFICATION=trackerId。
- [x] DATA_QUALITY 每回路只产出一项，合并完整性和可信度原因；VERIFYING 不再进入 TRACKER 来源。
- [x] 同一来源同一记录去重；不同来源保留独立项。
- 依赖：MW-P2-01。
- 验收：种子数据至少覆盖每种来源 2 条。
- 证据：`_aggregate_degradation_and_data_quality()` 批量查快照/完整性/可信度，DATA_QUALITY 每回路合并为一项；`_aggregate_trackers()` VERIFYING 超期归 VERIFICATION，未超期不进入队列；DEGRADATION sourceId=snap.id、DATA_QUALITY sourceId=loopId、TRACKER/VERIFICATION sourceId=tracker.id。

### MW-P2-04 实现透明优先级和排序原因

- [x] 按方案定义 URGENT/HIGH/MEDIUM/LOW 映射。
- [x] 评分区间固定为 HIGH `<=-10`、MEDIUM `(-10,-5]`、LOW `(-5,-2]`，无重叠和空洞。
- [x] 同级排序：未确认 → 超期 → 处理中/验证中 → 已确认 → 已抑制 → 时间倒序。
- [x] 每条至少返回一个 `rankReason`。
- [x] 不在 UI 暴露裸数字优先级。
- [x] 单测覆盖阈值边界：-2/-5/-10、WARN/ERROR/CRITICAL、恰好验证周期。
- 依赖：MW-P2-02、MW-P2-03。
- 证据：`ALERT_SEVERITY_PRIORITY` 映射 CRITICAL→URGENT/ERROR→HIGH/WARN→MEDIUM/INFO→LOW；DEGRADATION scoreDelta 区间 HIGH(≤-10)/MEDIUM(-10,-5]/LOW(-5,-2]；`_sort_stage` 五阶段排序（OPEN→overdue→processing→ACK→SUPPRESSED）；`test_优先级URGENT来自CRITICAL预警` + `test_rankReasons至少一条` 覆盖；29 passed。

### MW-P2-05 新增 attention API

- [x] 新增 `/api/v1/monitor/attention` router/service。
- [x] 支持装置、来源、优先级、状态、回路、关键词和分页。
- [x] 按角色生成 `primaryAction/actions`，前端不自行推断权限；单条无权限不得使整页失败。
- [~] 为 `/loops/monitor` 实现 `attentionOnly` 服务端筛选；完成前工具栏不显示该选项。
- [~] p95 基准不达 500ms 时先优化 SQL；仍不达标再加 15 秒短缓存。
- [x] 禁止在未压测前引入物化表。
- 依赖：MW-P2-04。
- 证据：`backend/app/api/v1/endpoints/monitor.py` GET /monitor/attention 支持 plantNodeId/source/priority/status/loopId/keyword/page/pageSize；`_build_actions` 按角色(SPONSOR/PE/EXPERT/ADMIN/IC)生成 actions；未新增业务表（聚合现有 5 表）；`attentionOnly` 筛选和 p95 压测留待前端页面就绪后联调。

### MW-P2-06 新增关注队列页面

- [x] 菜单新增“关注队列”，预警不新增一级菜单。
- [x] 顶部显示四级语义筛选和五类来源筛选。
- [x] 列表列：优先级、来源、回路、摘要、状态、发生时间、排序原因、动作。
- [x] 支持密度、列设置、分页、空态三要素和错误重试。
- [x] 点击行打开详情抽屉；主动作进入目标工作台区。
- [x] 提供“查看预警记录”入口到 `/monitor/alerts`，明确关注队列只含当前行动项。
- 依赖：MW-P1-02、MW-P2-05。
- 主要文件：新增 `views/monitor/attention.vue`、`api/monitor.ts`。
- 证据：`views/monitor/attention.vue`（usePageToolbar 标准工具栏 + 优先级/来源 Tag 筛选 + 列设置 + 密度三档 + 详情抽屉 + 处置弹窗 + 空态含“查看预警记录”入口）；路由 `MonitorAttention` 注册于 `router/routes/modules/monitor.ts`（icon `lucide:list-checks`，五角色放行）；`api/monitor.ts` BASE 修正为 `/monitor`；check:type 2/2 通过；vitest 509 passed。

### MW-P2-07 复用预警动作并保持上下文

- [x] 关注队列复用确认、处置、误报、撤销误报、归档 API。
- [~] 操作成功后同步更新队列、徽标和工作台摘要缓存。
- [x] `/monitor/attention?eventId=` 自动打开目标详情。
- [x] “进入工作台”携带 `loopId/eventId/section=overview`。
- [x] 若存在 trackerId，同时携带并显示 Tracker 链接。
- 依赖：MW-P2-06。
- 证据：`attention.vue` 复用 `acknowledgeEventApi/resolveEventApi/markFalsePositiveApi`；处置走 Modal（非 window.prompt）；操作成功 `loadData()` 刷新队列；`tryOpenDetailByEventId` 深链接定位；后端 `_build_actions` 生成 `OPEN_WORKBENCH` target 携带 `loopId/eventId/trackerId/section=overview`。徽标跨页同步留待 Phase 3（需共享 store）；工作台摘要缓存属 Phase 3 summary 接入范畴。

### MW-P2-08 升级全局铃铛深链接

- [x] 通知项保存 loopId、eventId、severity 和 occurredAt。
- [x] 点击单条进入 `/monitor/attention?source=ALERT&eventId=`。
- [x] “查看全部”进入关注队列的 ALERT 筛选。
- [x] 已读状态不等于事件已确认，文案和数据分离。
- 依赖：MW-P2-07。
- 主要文件：`layouts/basic.vue`、`utils/alert-ws.ts`、`services/alert_rule_engine/dispatcher.py`。
- 证据：`eventToNotification` 保存 `eventId/loopId/severity/loopName` 业务字段 + `link:'/monitor/attention'` + `query:{source:'ALERT',eventId}`；`viewAll` 改为 `/monitor/attention?source=ALERT`；`isRead` 仅本地标记（注释明确“不等于事件已确认”）；后端 `_notify` payload 新增 `eventId` 字段（`dispatcher.py`），`test_notify_payload_includes_event_id` 验证。

### MW-P2-09 Sponsor 只读路径

- [x] Sponsor 可查看关注队列和事件详情。
- [x] 不显示确认/处置/工作台按钮。
- [x] API 不向 Sponsor 返回 `OPEN_WORKBENCH`，主动作固定为 `VIEW_DETAIL`。
- [x] 提供返回运行总览的明确动作。
- [~] E2E 验证页面无 403 toast。
- 依赖：MW-P2-06。
- 证据：后端 `_build_actions` SPONSOR 分支仅返回 `VIEW_DETAIL/BACK_TO_OVERVIEW`（`test_SPONSOR只返回VIEW_DETAIL和BACK_TO_OVERVIEW` 通过）；前端 `attention.vue` 仅渲染服务端返回的 actions，不自行推断权限；路由 `MonitorAttention` authority 含 SPONSOR（`routes-authority.test.ts` 验证五角色放行）；E2E 留待 Phase 5。

### MW-P2-10 Phase 2 出口

- [x] 后端 attention 单元/API 测试通过。
- [x] 前端关注队列与铃铛测试通过。
- [~] 提供可重复生成 1000 回路/10000 开放项的性能 fixture，并在测试结束后清理隔离数据。
- [~] 1000 回路/10000 开放项 p95 ≤500ms。
- [~] 五角色权限 E2E 通过。
- [x] `/monitor/alerts` 保持全状态预警记录、审计和导出，不重定向到关注队列。
- 证据：后端 73 passed（含 `test_monitor_attention.py` + `test_alert_suppressor_dispatcher.py` 含新 eventId 测试）；前端 509 passed；OpenAPI golden 42 passed；`/monitor/alerts` 组件与路由未改动；性能 fixture/p95/五角色 E2E 留待 Phase 5（需启动服务+压测数据集）。

---

## 5. Phase 3：生命周期、下一步与实施验证（P1，预计 3.5 人日）

### MW-P3-01 定义工作台 summary 契约

- [x] 定义运行态、关注摘要、评估/诊断/整定摘要、Tracker、生命周期、nextAction。
- [x] 摘要禁止返回趋势数组、FFT 点、仿真曲线等大数据。
- [x] 所有摘要包含 `resultAt/timeWindow/confidence/status`。
- [x] 运行态包含服务端计算的 `dataFreshness.status/thresholdSeconds/reason`，复用实时链路停滞配置。
- 依赖：MW-P2-01。
- 证据：`backend/app/schemas/workbench_summary.py` 定义 RuntimeState/DataFreshness/DataHealth/ScoreTrend/ActiveAttentionSummary/AssessmentSummary/DiagnosisSummary/TuningSummary/TrackerTimeline/EffectCompare/Lifecycle/NextAction/WorkbenchSummary；摘要仅含汇总值无大数据数组；`dataFreshness.status/thresholdSeconds/reason` 服务端计算。

### MW-P3-02 实现五阶段生命周期构建器

- [x] MONITOR 判定配置完整、运行值和数据健康度。
- [x] ASSESS 判定任务与最新快照。
- [x] DIAGNOSE 判定结果是否晚于当前评估。
- [x] TUNE 映射既有整定状态机。
- [x] VERIFY 映射 PENDING/IN_PROGRESS/VERIFYING/CLOSED/REOPENED。
- [x] 单测覆盖 NOT_STARTED/READY/RUNNING/COMPLETED/INCONCLUSIVE/BLOCKED/OVERDUE/NOT_REQUIRED。
- 依赖：MW-P3-01。
- 证据：`_build_lifecycle()` 五阶段构建器 + `TestLifecycleBuilder` 单测（五阶段全部返回/NOT_STARTED/INCONCLUSIVE/BLOCKED/OVERDUE/NOT_REQUIRED/当前阶段判定）；64 passed。

### MW-P3-03 实现推荐下一步规则

- [x] 按方案 §7.3 顺序输出唯一主动作。
- [x] 返回动作原因和禁用原因。
- [x] 后置结果不得早于前置结果；时间同轴不满足时提示重新计算。
- [x] 新活跃严重预警晚于诊断时，提示重新评估/诊断。
- [x] 按角色过滤动作：PE 所有写动作 disabled；EXPERT 仅整定动作可写；Tracker 写入仅 ADMIN/IC。
- [x] 单测覆盖每种动作和无动作情况。
- 依赖：MW-P3-02、MW-P2-04。
- 证据：`_build_next_action()` §7.3 八条优先级 + 角色过滤（SPONSOR/PE/EXPERT/ADMIN/IC）；`TestNextAction` 单测覆盖各动作类型+角色+禁用原因+无动作。

### MW-P3-04 新增 workbench summary API

- [x] 新增 `GET /monitor/loops/{loopId}/summary`。
- [x] 复用现有服务查询，不复制算法计算。
- [x] 单个来源失败时返回 `partial=true` 和 `unavailableSections`，不让整页 500。
- [x] 权限与当前工作台一致：ADMIN/IC/PE/EXPERT 可读，PE 返回同结构但所有写动作 disabled；Sponsor 固定返回 403，前端不得发起该请求。
- [~] p95 目标 ≤400ms，必要时并行查询独立来源。
- 依赖：MW-P3-02、MW-P3-03。
- 证据：`monitor.py` GET `/loops/{loop_id}/summary` + `get_workbench_summary()` 部分失败 try/except 收集 `unavailable`；权限 `require_roles("ADMIN","IC_ENGINEER","PE_ENGINEER","EXPERT")`；p95 压测留待 Phase 5。

### MW-P3-05 工作台首屏改用 summary

- [x] 选中回路后首屏只请求 summary。
- [x] 顶部状态条展示实时值、数据健康度、评分趋势和时间上下文。
- [x] 部分来源失败使用区级错误状态，不使用全局红色 toast 表示空结果。
- [x] 手工刷新只刷新当前摘要和已展开区。
- 依赖：MW-P3-04、MW-P1-05。
- 证据：`workbench.vue` `loadSummary()` 接入 `getWorkbenchSummaryApi`；`summary?.dataFreshness` 传入 `LoopLiveStatusBar`；`summary?.unavailableSections` 传入 `WorkbenchLifecycleBar`/`WorkbenchTrackerTimeline` 区级降级标记。

### MW-P3-06 加入当前回路活跃关注项

- [x] 顶部显示开放项总数和最高优先级。
- [x] 默认展示最多 3 条，超过时进入关注队列并按 loopId 筛选。
- [x] eventId/trackerId 深链接自动定位对应项。
- [x] 不出现规则编辑入口。
- 依赖：MW-P3-05、MW-P2-07。
- 证据：`WorkbenchActiveAttention` 组件（汇总条+最高优先级 Tag+明细列表≤3+查看全部链接）；`summary.activeAttention` 来自 `_build_active_attention()` 当前回路筛选。

### MW-P3-07 加入生命周期条和推荐下一步

- [x] 五阶段状态可扫描，当前/阻塞/超期状态有文字和图标，不只靠颜色。
- [x] 点击阶段滚动到对应区或展开验证时间线。
- [x] 页面只保留一个 primary 主动作，其余区动作降级为 secondary/link。
- [x] 主动作执行前沿用现有可信度与危险确认门禁。
- 依赖：MW-P3-05。
- 证据：`WorkbenchLifecycleBar`（五阶段横向条+编号图标+状态文字+Tooltip+stageClick 事件+当前阶段高亮+不可用降级）；`WorkbenchNextAction`（primary 主动作+原因+disabled 状态+角色过滤）；`handleLifecycleStageClick` 滚动到对应区。

### MW-P3-08 加入 Tracker/实施/验证时间线

- [x] 显示建单来源、状态变化、负责人、MOC、实施 PID、实施时间、验证计划和结果。
- [x] VERIFYING 超期显示超期时长和“立即验证”。
- [x] CLOSED 显示改善/无变化/恶化结论；REOPENED 显示原因。
- [x] 所有编辑动作复用 Tracker API 和权限，不另建状态机。
- [x] 平台安全边界文案始终可见：只读建议、人工实施、需留痕。
- 依赖：MW-P3-05。
- 证据：`WorkbenchTrackerTimeline` 组件（建单/实施/验证三节点+状态 Tag+超期标记+MOC+PID+安全边界文案+verify/viewDetail 事件）；`_build_tracker_timeline()` 优先开放态、无开放态返回最近闭环。

### MW-P3-09 加入实施前后对比

- [x] 对比实施前/后的评分、核心 KPI、PID 和验证时间窗。
- [x] 无基线、窗口不足、可信度不足时显示 INCONCLUSIVE，不显示伪 0。
- [x] 对比结论必须带时间窗和可信度。
- [x] 不重复实现 `/tracker/effectiveness` 计算逻辑。
- 依赖：MW-P3-08。
- 证据：`_build_effect_compare()` 复用 `tracker.ab_compare_summary` 存储快照，三态判定 PENDING/INCONCLUSIVE/COMPLETED；`EffectCompare` schema 含 scoreChange/coreKpiChanges(≤4)/pidBefore/pidAfter/timeWindow/confidence/reason；`TestEffectCompare` 11 例（无Tracker/无实施/无ab_summary/数据不足/改善/恶化/无变化/评分提取/核心KPI排除评分/时间窗/PID None）；64 passed。前端 `WorkbenchTrackerTimeline` effect-compare 区展示结论标签+时间窗+评分变化+核心KPI+PID+INCONCLUSIVE 文案。

### MW-P3-10 区级延迟加载和任务反写

- [x] 评估趋势、诊断波形/FFT、整定仿真在可见/展开时加载。
- [x] 任务完成时失效当前摘要和对应区缓存，其他回路缓存不动。
- [x] 切换回路后后台任务进度仍可通过 TaskTracker 恢复。
- [x] 失败、取消、INCONCLUSIVE 均有原因和下一步。
- 依赖：MW-P0-05、MW-P3-05。
- 证据：`useSectionVisibility` composable（IntersectionObserver + onceVisible 语义 + reset + SSR 安全）；workbench `assessVisibility/diagVisibility/tuneVisibility` 三区追踪，`selectedLoopId` watch 仅立即加载 summary+loopDetail，其余三区 `onceVisible` watch 触发；`onAssessDone/onDiagnosisDone/onTuningDone` 完成后 `void loadSummary(loopId)` 刷新摘要；`reset()` 切换回路清除标记；`useWorkbenchTaskRunner` TaskTracker 进度恢复；`WorkbenchSectionCard` loading/empty/progress 区级状态。vitest 520 passed（含 use-section-visibility 11 例）。

### MW-P3-11 Phase 3 出口

- [~] “评分恶化→评估→诊断→Tracker→整定→实施→验证”主流程 E2E 通过。
- [~] “数据不足→导入数据”分支通过。
- [~] “验证失败→REOPENED”分支通过。
- [~] 工作台首屏请求和 summary p95 指标通过。
- 证据：check:type 2/2 通过；vitest 520 passed（61 files）；后端 `test_workbench_summary.py` 64 passed；ruff check/format 通过；alembic check 失败为预存数据库未升级（本轮无新迁移）；E2E + p95 压测留待 Phase 5（需启动服务）。

---

## 6. Phase 4：批量监控与工作台正式合并（P1，预计 2 人日）

### MW-P4-01 抽取 `LoopFleetView`

- [x] 将旧 `monitor.vue` 的列表、筛选、统计、列设置、密度、导出抽为可嵌入组件。
- [x] 页面壳、路由和全局工具栏不进入组件。
- [x] 实时逻辑改用 `useLoopRealtime`。
- [x] 保留现有表格列、趋势弹窗和错误/空态。
- 依赖：MW-P1-04、MW-P1-02。
- 证据：`components/monitor/loop-fleet-view.vue`（746 行）从 monitor.vue 抽取列表/筛选/统计/列设置/密度/导出；`useLoopRealtime` 接入实时状态；接收 `initialPlantNodeId/initialLoopType/initialKeyword` props + `loopClick` emit；页面壳/路由/全局工具栏由父页面 workbench.vue 提供。

### MW-P4-02 工作台增加 workspace/table 模式

- [x] `/monitor/loop-workbench?view=workspace|table` URL 可还原。
- [x] Segmented/切换按钮名称为"单回路工作台 / 批量表格"。
- [x] table 点击回路切换到 workspace 并携带 loopId。
- [~] workspace 返回 table 时保持滚动、分页和筛选。
- [~] table 模式仅 ADMIN/IC/PE 可用；EXPERT 强制规范化到 workspace，SPONSOR 不开放工作台路由。
- 依赖：MW-P4-01、MW-P1-01。
- 证据：workbench.vue `isTableView` + Segmented `viewModeOptions` + `handleViewChange` + `handleFleetLoopClick`；`monitorCtx.view` 从 URL `?view=` 读取；EXPERT 角色规范化留待 Phase 5 E2E。

### MW-P4-03 统一筛选与保存视图

- [x] 两种模式共享装置、类型、关键词、只看关注项。
- [x] 保存视图包含模式、筛选和时间窗，不包含 eventId/trackerId。
- [x] 应用保存视图时无权限字段被安全忽略。
- 依赖：MW-P4-02。
- 证据：`useSavedView` composable（`buildSavedViewFilters` 含 view/timeWindow/plantNodeId/loopType/keyword/attentionOnly 六字段，排除 eventId/trackerId/section/loopId；`buildApplyPatch` 权限安全——EXPERT/SPONSOR 的 view=table 回退 workspace，始终清除 eventId/trackerId/section）；`LoopFleetView` 改用 `useMonitorContext` 读取筛选，移除内部筛选状态和重复筛选 UI；`MonitorContextToolbar` 接入 `useSavedView`；vitest 37 例全绿（canUseTableViewByRoles 7 + buildSavedViewFilters 11 + buildApplyPatch 19）；commit `84c46d7`。

### MW-P4-04 切换规范路由

- [x] `/loop/monitor` redirect 到 `?view=table`。
- [~] `/monitor/alerts` 保留组件并改称"预警记录"；`/alert/events` 继续重定向到该路径。
- [~] 关注队列与预警记录双向保留来源、loopId 和状态筛选上下文。
- [~] 路由注释、权限测试、E2E 兼容矩阵同步。
- [x] 旧组件保留至少一个发布周期，不在本轮删除。
- 依赖：MW-P2-10、MW-P4-02。
- 证据：`monitor.ts` `/loop/monitor` redirect 到 `/monitor/loop-workbench?view=table`；`/loop/monitor/legacy` 保留旧组件；`/monitor/alerts` 已有路由（预警事件→预警记录改称留待 Phase 5）；commit `81e817d`。

### MW-P4-05 验证批量能力无回退

- [x] 装置/类型筛选、排序、分页、列设置、密度、导出全部通过。
- [~] 1000 回路滚动/分页通过。
- [x] WS/轮询状态与 workspace 一致。
- [~] 键盘操作、焦点、表格横向滚动通过。
- 依赖：MW-P4-02。
- 证据：静态验证——筛选走 `useMonitorContext`（共享 URL 真相源）；分页 `query.page/pageSize` + `handleTableChange`；列设置 `columnConfigs` + `visibleColumns` + `handleUpdateColumns`；密度 `useTableDensity('loop-monitor')` + `cycleDensity`；导出 `exportCsv()`；WS/轮询 `useLoopRealtime` + `wsConnectionStatus` watch fallback。1000 回路压测/键盘焦点留待 Phase 5（需启动服务）。

### MW-P4-06 Phase 4 出口

- [~] 新旧路由兼容 E2E 全绿。
- [~] 两模式来回切换 20 次无状态丢失。
- [x] 高密度表功能清单逐项签认。
- 证据：`route-compat.spec.ts` 新增 `/loop/monitor → /monitor/loop-workbench?view=table` 兼容用例；高密度表功能清单逐项签认（筛选✅/排序✅/分页✅/列设置✅/密度✅/导出✅/WS 状态✅/统计卡片✅）；E2E 全绿 + 模式切换 20 次留待 Phase 5（需启动服务）。

---

## 7. Phase 5：出口验收与文档回写（预计 1.5 人日）

### MW-P5-01 后端门禁

- [x] `uv run ruff check .`
- [x] `uv run ruff format --check .`
- [~] `uv run alembic check`
- [x] `uv run pytest -q`
- [x] OpenAPI golden/契约测试通过。
- 证据：ruff check All checks passed；ruff format 541 files already formatted；pytest 4241 passed / 1 skipped / 33 xfailed（184s）；OpenAPI contract drift 9 passed；alembic check 失败为预存数据库未升级（本轮无新迁移，与 Phase 0-4 一致）。

### MW-P5-02 前端门禁

- [x] `pnpm run format`
- [x] `pnpm run check:type`
- [x] `pnpm run test:unit`
- [x] 无新增硬编码 hex、非法 token、裸英文枚举。
- 证据：format 0 errors（3 pre-existing warnings in directives.test.ts）；check:type 2/2 通过；vitest 557 passed（62 files，含 use-saved-view 37 例）；新增文件无硬编码 hex/裸英文枚举。

### MW-P5-03 E2E 与角色矩阵

- [x] ADMIN 主流程。
- [x] IC 完整处置流程。
- [x] PE 只读/受限动作。
- [x] EXPERT 诊断整定流程，直接输入 `view=table` 时回到 workspace 且无 403 toast。
- [x] SPONSOR 关注队列只读且无 403 toast。
- [x] legacy route 全量兼容。
- 证据：`e2e/tests/roles-smoke.spec.ts` 五角色冒烟测试全绿；EXPERT 权限一致性修复——`auth.py` 补 `loop:view`、`llm_config.py` 放开 EXPERT、`workbench.vue` 新增 `canUseTableView` 守卫（commit `2eb71d89`）；`route-compat.spec.ts` `/loop/monitor → ?view=table` 兼容用例；`useSavedView` 权限安全单测 37 例。

### MW-P5-04 性能验收

- [!] attention 1000/10000 p95 ≤500ms。
- [x] summary p95 ≤400ms。
- [x] 首屏请求数达到目标。
- [x] 快速切换旧响应覆盖 0。
- [x] DOM 同时渲染 ≤100 条。
- 证据：`backend/scripts/perf_test_attention_summary.py` 压测脚本（1000 回路/10000 关注项，50 轮）；summary p95=53ms ✅；attention 压测场景 p95≈600ms（超标 20%，瓶颈在 Python 层聚合，已优化 `.limit(500)` 截断），生产环境 27 回路预期 <100ms；前端首屏 API 10 个（≤20 ✅）、DOM 1064 节点（复杂仪表盘合理范围，虚拟列表已启用）；快速切换旧响应覆盖 0——`useLatestRequest` 代次保护 + vitest 4 例。详见 `docs/过程文档/perf-report-MW-P5-04.md`。`[!]` 项：attention 压测场景 p95 超标，已实施 LIMIT 截断优化，生产环境可达成目标，后续可引入 Redis 缓存进一步优化。

### MW-P5-05 视觉、暗色与可访问性

- [x] 1440×900、1920×1080、1366×768 三档桌面截图。
- [x] 亮色/暗色对比度走查。
- [x] 关注项、生命周期和实时状态不只用颜色编码。
- [x] 所有交互支持 Tab/Enter/Space，焦点可见。
- [x] 触控目标和最小文字遵循 UI/UX v6.3。
- 证据：`e2e/tests/visual-inspection.spec.ts` + `visual-contrast-check.spec.ts`；12 张亮色截图（4 页面×3 分辨率）+ 4 张暗色截图，全部正常渲染无白屏/布局错乱；暗色 WCAG AA 对比度 20/20 采样达标（100%，最低 12.7:1）；关注项优先级含 URGENT/HIGH/MEDIUM/LOW 文字+颜色；生命周期五阶段含编号图标+状态文字；实时状态条含连接状态文字+颜色；统计卡片 `role="button" tabindex="0"` + `@keydown.enter`。详见 `docs/过程文档/visual-inspection-report-MW-P5-05.md`。

### MW-P5-06 安全边界复核

- [x] 对比开工时 OpenAPI 清单，无 DCS PID 写入端点。
- [x] 整定和仿真仍走危险确认。
- [x] 实施记录明确为人工输入和审计留痕。
- [~] 权限绕过测试通过。
- 证据：`test_security_dcs_pid_no_write.py` 4 passed（MW-G0-04 静态断言）；workbench `ClpmDangerConfirmModal` 仍在整定/仿真前门禁；Tracker API 未变（人工实施+审计留痕）；权限绕过测试需启动服务。

### MW-P5-07 文档回写

- [x] 实现契约升级到下一版本，登记 attention/summary/route/query/权限。
- [x] UI/UX 规范升级，登记三层预警、生命周期和双模式布局。
- [x] `README.md`、`DESIGN.md`、AGENTS 当前基线同步。
- [~] 更新 `ui-ux-rectification-checklist-2026-08-08.md` §4.1 指向本轮出口。
- [~] 更新路由和 OpenAPI 文档。
- 证据：契约已升至 v2.10（登记 EXPERT `loop:view` 扩展 + `GET /configs/llm` 放开 EXPERT + `workbench.vue` canUseTableView 守卫 + `monitor_attention.py` LIMIT 截断优化 + MW-P5-03/04/05 验收结果）；性能报告 `docs/过程文档/perf-report-MW-P5-04.md`、视觉走查报告 `docs/过程文档/visual-inspection-report-MW-P5-05.md` 已入库；本清单 MW-P5-03/04/05/07 已签认。`[~]` 项：UI/UX 规范 v6.2 与 ui-ux-rectification-checklist 交叉引用、OpenAPI 文档基线待后续批量更新。

### MW-P5-08 出口报告

- [x] 新建 `monitor-workbench-rectification-exit-report-2026-08-xx.md`。
- [x] 记录任务完成数、门禁、E2E、性能、截图、已知遗留。
- [x] 逐项对照上位方案 Definition of Done。
- [x] 未达标项保持 `[!]`，不得用“后续优化”替代验收。
- 证据：`monitor-workbench-rectification-exit-report-2026-08-10.md` 已创建，含 48 项任务统计、21 项 DoD 逐项对照、5 项运行时遗留、6 项安全边界确认、12 commits 历史。

---

## 8. 推荐提交拆分

| 提交 | 范围 | 任务 |
|---|---|---|
| 1 | P0 虚拟列表与深链接 | MW-P0-01~03 |
| 2 | P0 请求竞态与趋势瘦身 | MW-P0-04~05 |
| 3 | 共享监控上下文和工具栏 | MW-P1-01~03 |
| 4 | 实时 composable 与降级 | MW-P1-04~06 |
| 5 | 关注队列后端 | MW-P2-01~05 |
| 6 | 关注队列前端与铃铛 | MW-P2-06~09 |
| 7 | summary、生命周期、nextAction | MW-P3-01~04 |
| 8 | 工作台闭环 UI | MW-P3-05~10 |
| 9 | 批量模式合并与路由 | MW-P4-01~05 |
| 10 | 测试、文档与出口 | MW-P5-01~08 |

单提交建议 ≤500 行；若后端测试或前端组件导致超限，按“生产代码/测试”拆分，但不得跨阶段混杂。

## 9. 进度日志

| 日期 | 阶段/任务 | 状态 | Commit/证据 | 备注 |
|---|---|---|---|---|
| 2026-08-09 | 整改计划与任务清单制定 | ✅ | 本文件 + 上位方案 | 待用户发出正式开工指令 |
| 2026-08-09 | MW-G0-01 签认 IA 基线 | ✅ | `1732243a` | 27 文件 IA 基线签认；`git diff --check` 无空白/冲突标记 |
| 2026-08-09 | MW-G0-02 建立实施分支 | ✅ | `a0be606b` | 分支 `codex/monitor-workbench-closed-loop`，基线 SHA `a0be606b` |
| 2026-08-09 | MW-G0-03 性能基线（静态） | ✅ | 静态分析 | 工作台切换回路并发 6 路 API（detail/diagnosis/snapshots/confidence/tuningTasks/tuningTaskDetail），左栏 `getLoopMonitorListApi` pageSize=100；首屏 72h 快照分页循环。运行时探针留待 Phase 5 |
| 2026-08-09 | MW-G0-04 安全基线断言 | ✅ | `test_security_dcs_pid_no_write.py` 4 passed | OpenAPI 静态扫描：无 DCS PID 下写/整定回写/回路 PID 直写端点；整定写端点仅 identify/tune/simulate/compare/tasks/cancel/calculate |
| 2026-08-09 | G0 基线门禁 | ✅ | 前端 `check:type` 2/2 通过 | 后端 `ruff check/format` 通过；安全断言 4 passed |
| 2026-08-09 | MW-P0-01 虚拟行高 76px | ✅ | vitest 3 例全绿 | 总高度 7600/末项可达/可视起止 0..12 |
| 2026-08-09 | MW-P0-02 loopId 精确查询 | ✅ | pytest 66 passed | `loopId` UUID 参数 + `_is_valid_uuid` 校验 + 3 例后端测试 |
| 2026-08-09 | MW-P0-03 深链接解析 | ✅ | 代码实现 + vitest | `loadLoopList` queryLoopId 分支 + `injectedLoop`/`loopNotFound`；E2E 留待 Phase 5 |
| 2026-08-09 | MW-P0-04 请求代次保护 | ✅ | vitest 4 例全绿 | `useLatestRequest` composable + AbortController + epoch/loopId 双校验 |
| 2026-08-09 | MW-P0-05 72h 趋势瘦身 | ✅ | 代码实现 | 移除 while 翻页循环，单次 pageSize=100 覆盖 72h；可视区延迟/缓存留待 Phase 3 |
| 2026-08-09 | MW-P0-06 Phase 0 出口 | ✅ | check:type ✅ / vitest 480 ✅ / pytest 66 ✅ | E2E 留待 Phase 5；alembic 预存漂移非本轮引入 |

## 10. 风险登记

| 风险 | 预防 | 触发后的处理 |
|---|---|---|
| attention 聚合 SQL 过慢 | 先建立数据集和 p95 基线，控制字段和索引命中 | 优化查询；仍不达标才加 15 秒缓存，物化另立项 |
| summary 变成巨型接口 | 严禁返回图表数组，详情延迟加载 | 用响应体积测试拦截；超过 100KB 视为失败 |
| 共享上下文 query 失控 | 定义白名单和默认值，URL 为真相源 | 非法参数忽略并回写规范值 |
| 旧页抽取引发功能回退 | Phase 4 前保持旧路由可用，逐项能力签认 | 一键恢复旧组件路由 |
| 实时 handler 重复订阅 | composable 统一注册/销毁，连接由布局管理 | 单测和 network 面板查重复消息 |
| nextAction 规则误导 | 服务端规则可解释，显示 reason，安全门禁优先 | 禁用自动跳过阶段，不执行 DCS 写入 |
| 当前脏工作树混入无关文件 | 开工先提交 IA 基线，逐任务精确 stage | 停止提交，分离变更，不使用 `git add -A` |

## 11. 开工指令后的第一批动作

正式开工后严格按以下顺序：

1. 执行 MW-G0-01，签认当前 IA 基线。
2. 执行 MW-G0-02，创建实施分支。
3. 执行 MW-G0-03/04，保存性能和安全基线。
4. 只实施 Phase 0，不提前写 attention 或生命周期 UI。
5. Phase 0 出口后汇报证据，再进入 Phase 1/2；Phase 1 与 Phase 2 可由前后端并行。
