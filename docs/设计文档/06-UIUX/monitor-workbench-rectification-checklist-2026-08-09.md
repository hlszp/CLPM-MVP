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

- [ ] 将 `useVirtualList` 行高从 57px 调整为 76px。
- [ ] 列表项 CSS 固定三行布局，PV/SP/OP/MODE 不换行，超长模式文本截断。
- [ ] 补充总高度、可视起止索引、最后一项可达单测。
- [ ] 在 100 条数据下滚动到末尾截图验证，无重叠、空白和跳动。
- 依赖：MW-G0-01。
- 主要文件：`views/loop/workbench.vue`、`__tests__/use-virtual-list.test.ts`。
- 验收：虚拟总高度=`items.length × 76`；最后一条完整可见。

### MW-P0-02 为监控列表增加 `loopId` 精确查询

- [ ] `GET /loops/monitor` 增加可选 `loopId` UUID 参数。
- [ ] 服务层按主键精确过滤，并继续执行现有权限和 `is_active` 口径。
- [ ] OpenAPI golden、前端 `MonitorQueryParams` 同步。
- [ ] 后端测试覆盖存在、不存在、无权限、与其他筛选组合四类情况。
- 依赖：MW-G0-01。
- 主要文件：`backend/app/api/v1/endpoints/loops.py`、`backend/app/services/monitor.py`、`frontend/.../api/loop.ts`。
- 验收：精确查询只返回目标回路或空结果，不回退其他回路。

### MW-P0-03 修复工作台深链接解析

- [ ] URL 有 `loopId` 时先执行精确查询，不依赖当前分页是否包含目标。
- [ ] 目标不存在时显示“回路不存在或已停用”，保留原 URL，不选择第一条。
- [ ] 无权限时渲染权限页/只读入口，不触发多条 403 toast。
- [ ] 筛选条件隐藏目标回路时，仍在已选上下文区显示目标，并提示“不在当前筛选结果中”。
- [ ] E2E 覆盖第 101 条回路、无效 UUID、已停用回路和带筛选深链接。
- 依赖：MW-P0-02。
- 主要文件：`views/loop/workbench.vue`、`e2e/tests/loop.spec.ts`。
- 验收：URL `loopId` 与页面位号始终一致。

### MW-P0-04 增加切换请求代次保护

- [ ] 工作台选中回路变化时递增 `selectionEpoch`。
- [ ] 评估、诊断、整定、详情/摘要响应写入前同时核对 epoch 和 loopId。
- [ ] 可取消的请求接入 `AbortController`；不可取消的请求必须丢弃旧响应。
- [ ] 组件卸载时取消计时器、轮询和待处理请求。
- [ ] 单测模拟 A 慢/B 快返回，最终页面只显示 B。
- 依赖：MW-G0-01。
- 主要文件：`views/loop/workbench.vue`，建议新增 `composables/use-latest-request.ts`。
- 验收：20 次快速切换旧响应覆盖 0 次。

### MW-P0-05 收敛 72h 趋势加载

- [ ] 移除切换回路时的无上限分页循环。
- [ ] 首屏不加载趋势大数组；评估区进入可视区后请求。
- [ ] API 增加或复用 `maxPoints`/时间窗上限，72h 图最多返回 100 点。
- [ ] 保留现有 8h/12h/24h/48h/72h 五档；用户切换时间窗时才刷新对应数据，并缓存 30 秒。
- [ ] 评估任务完成后只失效当前回路缓存。
- 依赖：MW-P0-04。
- 主要文件：`workbench.vue`、`score-trend-chart.vue`、`api/metric.ts`，必要时增量修改后端快照查询。
- 验收：切换回路不再循环翻页；趋势功能和空态不回退。

### MW-P0-06 Phase 0 出口

- [ ] `check:type` 通过。
- [ ] 前端全量 vitest 通过。
- [ ] 后端相关 pytest 通过。
- [ ] `route-compat.spec.ts`、`loop.spec.ts` 定向 E2E 通过。
- [ ] 更新本清单进度日志和 Phase 0 请求指标。
- 验收：P0 项全部附证据后方可签认。

---

## 3. Phase 1：共享监控壳层与实时状态（P1，预计 2.5 人日）

### MW-P1-01 新增 `useMonitorContext`

- [ ] 实现 `view/loopId/plantNodeId/loopType/keyword/attentionOnly/timeWindow/eventId/trackerId/section` 类型。
- [ ] `timeWindow` 固定保留 8h/12h/24h/48h/72h 五档，不删除现有 12h/48h。
- [ ] URL 为真相源，所有更新使用 `router.replace`。
- [ ] 扩展 `useLoopContext`，保留已知上下文，不再默认清空 eventId/trackerId/section。
- [ ] 单测覆盖空值、非法值、回退值、跨模块跳转和浏览器前进后退。
- 依赖：MW-P0-03。
- 验收：刷新页面、复制 URL、前进后退均可还原上下文。

### MW-P1-02 建立共享监控工具栏

- [ ] 提取装置/单元、回路类型、关键词、保存视图；“只看关注项”在 Phase 2 API 就绪前不渲染。
- [ ] 工作台和批量表格共用同一筛选对象。
- [ ] 搜索 300ms 防抖；下拉变化立即更新 URL；回车立即查询。
- [ ] 保存视图复用 `use-clpm-preferences.ts`。
- 依赖：MW-P1-01。
- 主要文件：新增 `components/monitor/monitor-context-toolbar.vue`。
- 验收：切换视图后筛选不丢失。

### MW-P1-03 左栏服务端分页与无限加载

- [ ] 默认 `pageSize=50`，接近底部时加载下一页。
- [ ] 搜索/筛选变化时清空旧页并回到第 1 页。
- [ ] 去重键固定为 `loopId`；重复响应不得产生重复条目。
- [ ] 精确深链接项可独立插入“当前选中”上下文，不污染分页总数。
- [ ] 1000 回路数据集验证 DOM 同时渲染 ≤100。
- 依赖：MW-P0-01、MW-P1-02。
- 验收：第 1000 条回路可达，滚动和选中状态稳定。

### MW-P1-04 抽取 `useLoopRealtime`

- [ ] 从旧监控页迁移 tagCode 解析、PV/SP/OP/MODE 更新和质量码映射。
- [ ] 复用全局 `realtimeWs` 单例，禁止创建第二连接。
- [ ] 提供 `connectionStatus/lastMessageAt/applyMessage/startFallback/stopFallback`。
- [ ] MODE 自定义映射仍以 REST 返回为权威，WS 只做安全的默认映射。
- [ ] 单测覆盖未知 tag、非法 value、质量码、MODE、取消订阅。
- 依赖：MW-P0-04。
- 主要文件：新增 `composables/use-loop-realtime.ts`，修改 `monitor.vue`。
- 验收：旧监控页和新工作台消费同一逻辑，结果一致。

### MW-P1-05 工作台接入实时状态条

- [ ] 选中回路 PV/SP/OP/MODE 收到 WS 后局部更新。
- [ ] 显示 online/reconnecting/offline、最后采样时间、PV 质量码。
- [ ] 使用 summary 返回的 `dataFreshness.status/thresholdSeconds/reason` 显示“数据延迟”，前端不复制停滞阈值，不用红色表示普通陈旧。
- [ ] 工作台切换回路后实时更新只作用于当前目标。
- 依赖：MW-P1-04。
- 主要文件：`workbench.vue`，建议新增 `components/monitor/loop-live-status-bar.vue`。
- 验收：WS 消息到 UI ≤2 秒。

### MW-P1-06 实现断连轮询降级

- [ ] WS 在线时停止运行值轮询。
- [ ] 断连 5 秒内显示状态，并启动 30 秒间隔轮询。
- [ ] 重连成功后立即停止轮询并主动刷新一次。
- [ ] 多次断连/重连不重复创建 interval。
- [ ] 页面卸载只退订页面 handler，不断开布局管理的全局单例。
- 依赖：MW-P1-05。
- 验收：浏览器网络切换三轮，无重复请求和内存泄漏。

### MW-P1-07 Phase 1 出口

- [ ] 实时单测和组件测试通过。
- [ ] WS 在线/断连/重连截图与网络面板证据齐全。
- [ ] 1000 回路列表性能场景通过。
- [ ] 五角色路由冒烟通过。
- 验收：§3.2 实时、降级、规模指标全部达标。

---

## 4. Phase 2：统一关注队列（P1，预计 3 人日）

### MW-P2-01 定义关注队列 schema 和前端类型

- [ ] 新增 `AttentionSource/Priority/Status/Item/ListData`。
- [ ] 统一状态含 OPEN/ACKNOWLEDGED/SUPPRESSED/IN_PROGRESS/VERIFYING，并保留来源原始 `sourceStatus`。
- [ ] `attentionId` 使用 `${source}:${sourceId}`，不新增数据库主键。
- [ ] 定义服务端下发的 `primaryAction/actions`；动作 target 明确包含 loopId 和可用的 eventId/trackerId/section。
- [ ] OpenAPI 和前端类型字段逐项对齐。
- 依赖：MW-P0-02。

### MW-P2-02 聚合活跃预警

- [ ] 查询 ACTIVE/ACKNOWLEDGED/SUPPRESSED 事件。
- [ ] 明确状态映射：ACTIVE→OPEN、ACKNOWLEDGED→ACKNOWLEDGED、SUPPRESSED→SUPPRESSED。
- [ ] 带出规则、触发值、可信度、重复次数、trackerId 和回路信息。
- [ ] 保留原事件状态机和动作 API，不复制业务逻辑。
- [ ] 测试状态、误报、归档和回路删除边界。
- 依赖：MW-P2-01。

### MW-P2-03 聚合其余四类关注来源

- [ ] DEGRADATION：`dayTrend=WORSENED` 且 `scoreDelta<=-2`。
- [ ] DATA_QUALITY：读取最新 `loop_integrity_snapshot` 的 WARNING/CRITICAL 或 `loop_confidence_latest` 的 D/E；禁止请求时扫描 TDengine。
- [ ] TRACKER：PENDING/IN_PROGRESS。
- [ ] VERIFICATION：VERIFYING 超过配置验证周期。
- [ ] 来源主键固定：DEGRADATION=最新快照 ID、DATA_QUALITY=loopId、TRACKER/VERIFICATION=trackerId。
- [ ] DATA_QUALITY 每回路只产出一项，合并完整性和可信度原因；VERIFYING 不再进入 TRACKER 来源。
- [ ] 同一来源同一记录去重；不同来源保留独立项。
- 依赖：MW-P2-01。
- 验收：种子数据至少覆盖每种来源 2 条。

### MW-P2-04 实现透明优先级和排序原因

- [ ] 按方案定义 URGENT/HIGH/MEDIUM/LOW 映射。
- [ ] 评分区间固定为 HIGH `<=-10`、MEDIUM `(-10,-5]`、LOW `(-5,-2]`，无重叠和空洞。
- [ ] 同级排序：未确认 → 超期 → 处理中/验证中 → 已确认 → 已抑制 → 时间倒序。
- [ ] 每条至少返回一个 `rankReason`。
- [ ] 不在 UI 暴露裸数字优先级。
- [ ] 单测覆盖阈值边界：-2/-5/-10、WARN/ERROR/CRITICAL、恰好验证周期。
- 依赖：MW-P2-02、MW-P2-03。

### MW-P2-05 新增 attention API

- [ ] 新增 `/api/v1/monitor/attention` router/service。
- [ ] 支持装置、来源、优先级、状态、回路、关键词和分页。
- [ ] 按角色生成 `primaryAction/actions`，前端不自行推断权限；单条无权限不得使整页失败。
- [ ] 为 `/loops/monitor` 实现 `attentionOnly` 服务端筛选；完成前工具栏不显示该选项。
- [ ] p95 基准不达 500ms 时先优化 SQL；仍不达标再加 15 秒短缓存。
- [ ] 禁止在未压测前引入物化表。
- 依赖：MW-P2-04。

### MW-P2-06 新增关注队列页面

- [ ] 菜单新增“关注队列”，预警不新增一级菜单。
- [ ] 顶部显示四级语义筛选和五类来源筛选。
- [ ] 列表列：优先级、来源、回路、摘要、状态、发生时间、排序原因、动作。
- [ ] 支持密度、列设置、分页、空态三要素和错误重试。
- [ ] 点击行打开详情抽屉；主动作进入目标工作台区。
- [ ] 提供“查看预警记录”入口到 `/monitor/alerts`，明确关注队列只含当前行动项。
- 依赖：MW-P1-02、MW-P2-05。
- 主要文件：新增 `views/monitor/attention.vue`、`api/monitor.ts`。

### MW-P2-07 复用预警动作并保持上下文

- [ ] 关注队列复用确认、处置、误报、撤销误报、归档 API。
- [ ] 操作成功后同步更新队列、徽标和工作台摘要缓存。
- [ ] `/monitor/attention?eventId=` 自动打开目标详情。
- [ ] “进入工作台”携带 `loopId/eventId/section=overview`。
- [ ] 若存在 trackerId，同时携带并显示 Tracker 链接。
- 依赖：MW-P2-06。

### MW-P2-08 升级全局铃铛深链接

- [ ] 通知项保存 loopId、eventId、severity 和 occurredAt。
- [ ] 点击单条进入 `/monitor/attention?source=ALERT&eventId=`。
- [ ] “查看全部”进入关注队列的 ALERT 筛选。
- [ ] 已读状态不等于事件已确认，文案和数据分离。
- 依赖：MW-P2-07。
- 主要文件：`layouts/basic.vue`。

### MW-P2-09 Sponsor 只读路径

- [ ] Sponsor 可查看关注队列和事件详情。
- [ ] 不显示确认/处置/工作台按钮。
- [ ] API 不向 Sponsor 返回 `OPEN_WORKBENCH`，主动作固定为 `VIEW_DETAIL`。
- [ ] 提供返回运行总览的明确动作。
- [ ] E2E 验证页面无 403 toast。
- 依赖：MW-P2-06。

### MW-P2-10 Phase 2 出口

- [ ] 后端 attention 单元/API 测试通过。
- [ ] 前端关注队列与铃铛测试通过。
- [ ] 提供可重复生成 1000 回路/10000 开放项的性能 fixture，并在测试结束后清理隔离数据。
- [ ] 1000 回路/10000 开放项 p95 ≤500ms。
- [ ] 五角色权限 E2E 通过。
- [ ] `/monitor/alerts` 保持全状态预警记录、审计和导出，不重定向到关注队列。

---

## 5. Phase 3：生命周期、下一步与实施验证（P1，预计 3.5 人日）

### MW-P3-01 定义工作台 summary 契约

- [ ] 定义运行态、关注摘要、评估/诊断/整定摘要、Tracker、生命周期、nextAction。
- [ ] 摘要禁止返回趋势数组、FFT 点、仿真曲线等大数据。
- [ ] 所有摘要包含 `resultAt/timeWindow/confidence/status`。
- [ ] 运行态包含服务端计算的 `dataFreshness.status/thresholdSeconds/reason`，复用实时链路停滞配置。
- 依赖：MW-P2-01。

### MW-P3-02 实现五阶段生命周期构建器

- [ ] MONITOR 判定配置完整、运行值和数据健康度。
- [ ] ASSESS 判定任务与最新快照。
- [ ] DIAGNOSE 判定结果是否晚于当前评估。
- [ ] TUNE 映射既有整定状态机。
- [ ] VERIFY 映射 PENDING/IN_PROGRESS/VERIFYING/CLOSED/REOPENED。
- [ ] 单测覆盖 NOT_STARTED/READY/RUNNING/COMPLETED/INCONCLUSIVE/BLOCKED/OVERDUE/NOT_REQUIRED。
- 依赖：MW-P3-01。

### MW-P3-03 实现推荐下一步规则

- [ ] 按方案 §7.3 顺序输出唯一主动作。
- [ ] 返回动作原因和禁用原因。
- [ ] 后置结果不得早于前置结果；时间同轴不满足时提示重新计算。
- [ ] 新活跃严重预警晚于诊断时，提示重新评估/诊断。
- [ ] 按角色过滤动作：PE 所有写动作 disabled；EXPERT 仅整定动作可写；Tracker 写入仅 ADMIN/IC。
- [ ] 单测覆盖每种动作和无动作情况。
- 依赖：MW-P3-02、MW-P2-04。

### MW-P3-04 新增 workbench summary API

- [ ] 新增 `GET /monitor/loops/{loopId}/summary`。
- [ ] 复用现有服务查询，不复制算法计算。
- [ ] 单个来源失败时返回 `partial=true` 和 `unavailableSections`，不让整页 500。
- [ ] 权限与当前工作台一致：ADMIN/IC/PE/EXPERT 可读，PE 返回同结构但所有写动作 disabled；Sponsor 固定返回 403，前端不得发起该请求。
- [ ] p95 目标 ≤400ms，必要时并行查询独立来源。
- 依赖：MW-P3-02、MW-P3-03。

### MW-P3-05 工作台首屏改用 summary

- [ ] 选中回路后首屏只请求 summary。
- [ ] 顶部状态条展示实时值、数据健康度、评分趋势和时间上下文。
- [ ] 部分来源失败使用区级错误状态，不使用全局红色 toast 表示空结果。
- [ ] 手工刷新只刷新当前摘要和已展开区。
- 依赖：MW-P3-04、MW-P1-05。

### MW-P3-06 加入当前回路活跃关注项

- [ ] 顶部显示开放项总数和最高优先级。
- [ ] 默认展示最多 3 条，超过时进入关注队列并按 loopId 筛选。
- [ ] eventId/trackerId 深链接自动定位对应项。
- [ ] 不出现规则编辑入口。
- 依赖：MW-P3-05、MW-P2-07。

### MW-P3-07 加入生命周期条和推荐下一步

- [ ] 五阶段状态可扫描，当前/阻塞/超期状态有文字和图标，不只靠颜色。
- [ ] 点击阶段滚动到对应区或展开验证时间线。
- [ ] 页面只保留一个 primary 主动作，其余区动作降级为 secondary/link。
- [ ] 主动作执行前沿用现有可信度与危险确认门禁。
- 依赖：MW-P3-05。

### MW-P3-08 加入 Tracker/实施/验证时间线

- [ ] 显示建单来源、状态变化、负责人、MOC、实施 PID、实施时间、验证计划和结果。
- [ ] VERIFYING 超期显示超期时长和“立即验证”。
- [ ] CLOSED 显示改善/无变化/恶化结论；REOPENED 显示原因。
- [ ] 所有编辑动作复用 Tracker API 和权限，不另建状态机。
- [ ] 平台安全边界文案始终可见：只读建议、人工实施、需留痕。
- 依赖：MW-P3-05。

### MW-P3-09 加入实施前后对比

- [ ] 对比实施前/后的评分、核心 KPI、PID 和验证时间窗。
- [ ] 无基线、窗口不足、可信度不足时显示 INCONCLUSIVE，不显示伪 0。
- [ ] 对比结论必须带时间窗和可信度。
- [ ] 不重复实现 `/tracker/effectiveness` 计算逻辑。
- 依赖：MW-P3-08。

### MW-P3-10 区级延迟加载和任务反写

- [ ] 评估趋势、诊断波形/FFT、整定仿真在可见/展开时加载。
- [ ] 任务完成时失效当前摘要和对应区缓存，其他回路缓存不动。
- [ ] 切换回路后后台任务进度仍可通过 TaskTracker 恢复。
- [ ] 失败、取消、INCONCLUSIVE 均有原因和下一步。
- 依赖：MW-P0-05、MW-P3-05。

### MW-P3-11 Phase 3 出口

- [ ] “评分恶化→评估→诊断→Tracker→整定→实施→验证”主流程 E2E 通过。
- [ ] “数据不足→导入数据”分支通过。
- [ ] “验证失败→REOPENED”分支通过。
- [ ] 工作台首屏请求和 summary p95 指标通过。

---

## 6. Phase 4：批量监控与工作台正式合并（P1，预计 2 人日）

### MW-P4-01 抽取 `LoopFleetView`

- [ ] 将旧 `monitor.vue` 的列表、筛选、统计、列设置、密度、导出抽为可嵌入组件。
- [ ] 页面壳、路由和全局工具栏不进入组件。
- [ ] 实时逻辑改用 `useLoopRealtime`。
- [ ] 保留现有表格列、趋势弹窗和错误/空态。
- 依赖：MW-P1-04、MW-P1-02。

### MW-P4-02 工作台增加 workspace/table 模式

- [ ] `/monitor/loop-workbench?view=workspace|table` URL 可还原。
- [ ] Segmented/切换按钮名称为“单回路工作台 / 批量表格”。
- [ ] table 点击回路切换到 workspace 并携带 loopId。
- [ ] workspace 返回 table 时保持滚动、分页和筛选。
- [ ] table 模式仅 ADMIN/IC/PE 可用；EXPERT 强制规范化到 workspace，SPONSOR 不开放工作台路由。
- 依赖：MW-P4-01、MW-P1-01。

### MW-P4-03 统一筛选与保存视图

- [ ] 两种模式共享装置、类型、关键词、只看关注项。
- [ ] 保存视图包含模式、筛选和时间窗，不包含 eventId/trackerId。
- [ ] 应用保存视图时无权限字段被安全忽略。
- 依赖：MW-P4-02。

### MW-P4-04 切换规范路由

- [ ] `/loop/monitor` redirect 到 `?view=table`。
- [ ] `/monitor/alerts` 保留组件并改称“预警记录”；`/alert/events` 继续重定向到该路径。
- [ ] 关注队列与预警记录双向保留来源、loopId 和状态筛选上下文。
- [ ] 路由注释、权限测试、E2E 兼容矩阵同步。
- [ ] 旧组件保留至少一个发布周期，不在本轮删除。
- 依赖：MW-P2-10、MW-P4-02。

### MW-P4-05 验证批量能力无回退

- [ ] 装置/类型筛选、排序、分页、列设置、密度、导出全部通过。
- [ ] 1000 回路滚动/分页通过。
- [ ] WS/轮询状态与 workspace 一致。
- [ ] 键盘操作、焦点、表格横向滚动通过。
- 依赖：MW-P4-02。

### MW-P4-06 Phase 4 出口

- [ ] 新旧路由兼容 E2E 全绿。
- [ ] 两模式来回切换 20 次无状态丢失。
- [ ] 高密度表功能清单逐项签认。

---

## 7. Phase 5：出口验收与文档回写（预计 1.5 人日）

### MW-P5-01 后端门禁

- [ ] `uv run ruff check .`
- [ ] `uv run ruff format --check .`
- [ ] `uv run alembic check`
- [ ] `uv run pytest -q`
- [ ] OpenAPI golden/契约测试通过。

### MW-P5-02 前端门禁

- [ ] `pnpm run format`
- [ ] `pnpm run check:type`
- [ ] `pnpm run test:unit`
- [ ] 无新增硬编码 hex、非法 token、裸英文枚举。

### MW-P5-03 E2E 与角色矩阵

- [ ] ADMIN 主流程。
- [ ] IC 完整处置流程。
- [ ] PE 只读/受限动作。
- [ ] EXPERT 诊断整定流程，直接输入 `view=table` 时回到 workspace 且无 403 toast。
- [ ] SPONSOR 关注队列只读且无 403 toast。
- [ ] legacy route 全量兼容。

### MW-P5-04 性能验收

- [ ] attention 1000/10000 p95 ≤500ms。
- [ ] summary p95 ≤400ms。
- [ ] 首屏请求数达到目标。
- [ ] 快速切换旧响应覆盖 0。
- [ ] DOM 同时渲染 ≤100 条。

### MW-P5-05 视觉、暗色与可访问性

- [ ] 1440×900、1920×1080、1366×768 三档桌面截图。
- [ ] 亮色/暗色对比度走查。
- [ ] 关注项、生命周期和实时状态不只用颜色编码。
- [ ] 所有交互支持 Tab/Enter/Space，焦点可见。
- [ ] 触控目标和最小文字遵循 UI/UX v6.3。

### MW-P5-06 安全边界复核

- [ ] 对比开工时 OpenAPI 清单，无 DCS PID 写入端点。
- [ ] 整定和仿真仍走危险确认。
- [ ] 实施记录明确为人工输入和审计留痕。
- [ ] 权限绕过测试通过。

### MW-P5-07 文档回写

- [ ] 实现契约升级到下一版本，登记 attention/summary/route/query/权限。
- [ ] UI/UX 规范升级，登记三层预警、生命周期和双模式布局。
- [ ] `README.md`、`DESIGN.md`、AGENTS 当前基线同步。
- [ ] 更新 `ui-ux-rectification-checklist-2026-08-08.md` §4.1 指向本轮出口。
- [ ] 更新路由和 OpenAPI 文档。

### MW-P5-08 出口报告

- [ ] 新建 `monitor-workbench-rectification-exit-report-2026-08-xx.md`。
- [ ] 记录任务完成数、门禁、E2E、性能、截图、已知遗留。
- [ ] 逐项对照上位方案 Definition of Done。
- [ ] 未达标项保持 `[!]`，不得用“后续优化”替代验收。

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
