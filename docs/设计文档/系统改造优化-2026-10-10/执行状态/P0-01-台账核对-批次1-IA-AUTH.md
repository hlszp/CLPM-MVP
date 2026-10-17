# P0-01 台账核对报告·批次1：IA-01~13 + AUTH-01~10

- 核对子智能体：独立只读代理（未参与执行包编写）
- 开工/收工 HEAD：`2c817248fc1d55e85dfd413ecfd9b4f01b9efbad`（一致，工作区无已跟踪文件改动）
- 日期：2026-10-10（P0 执行期）
- 判定统计：✅确认存在 23 ｜ ⚠️漂移 0 ｜ ❌已修复/不可复现 0 ｜ ❓无法核验 0
- 行号微漂（按符号定位核对一致，不构成描述漂移）：IA-04 use-journey-summary 75→80-84；AUTH-03 tasks.py 1349→1351；AUTH-06 tuning.py 242→237-246

## §1 IA、上下文与显示事实（13 条）

- **IA-01 ｜ ✅确认存在 ｜** `frontend/apps/web-antd/src/views/cockpit/wb-comps/use-drill.ts:206-212` 舱内 E1/E2 清单调 `getRankingApi({ limit: 200 })`（前端 `api/metric.ts:1013-1017` 直透传不钳制）；`backend/app/api/v1/endpoints/performance.py:168` ranking 端点 `limit: Query(20, ge=1, le=100)`，200 超上限必 422；`use-drill.ts:246-247/293-294` `catch { list.rows = [] }` 清空吞错；`views/cockpit/components/modals/list-modal.vue:55-58` 空态固定显示"暂无数据"，请求失败与空数据不可区分。同文件 `:317` E4 预警清单 `limit: 50` 合法但静默截断，即"部分清单首批截断"。

- **IA-02 ｜ ✅确认存在 ｜** `use-drill.ts:271-277` 指标清单候选先 `sortBy:'score', sortOrder:'desc', limit:200` 按综合分取（非按该指标全范围排序）；`:283` `metricText = pct(...)` 生成 `"85.00%"` 字符串；`:289-291` 排序 `Number(metricText)` 对百分号字符串恒 NaN，`Number.isFinite` 回退 -1，指标值降序比较实际全部失效（`:266` 描述却宣称"按该指标值降序"）。

- **IA-03 ｜ ✅确认存在 ｜** `views/loop/workbench360/composables/use-assess-history.ts:111-113` `latest = rows.find(r => r.source !== 'MANUAL_CUSTOM')`，rows 为**当前页**数据（`:144-146`），翻页/切来源档（`:142` SOURCE_QUERY + `:158` watch）即改变 latest；`views/loop/workbench360/index.vue:376-385` `assessSummary` 中 `kpiStatus/score/scoreDelta` 取自清单行 `loop.current` 而 `lossSummary` 取自翻页变动的 `latest`，头部新旧事实混用。

- **IA-04 ｜ ✅确认存在 ｜** `backend/app/services/monitor_attention.py:623-631` VERIFYING 工单超 24h 会生成 HIGH 关注项（VERIFYING 语义已进关注后端）；`views/monitor/attention.vue:113-125` `STATUS_LABEL/STATUS_COLOR` 仅 OPEN/ACKNOWLEDGED/SUPPRESSED/IN_PROGRESS 四态（`api/monitor.ts:23-27` AttentionStatus 类型同样四态，无 VERIFYING）；`use-journey-summary.ts:79-93` 在途口径 statuses 仅 `['PENDING','EXECUTING','REOPENED']`；归档侧含 VERIFYING：`views/handling/archive.vue:158` 状态分组含 verifying，`api/governance.ts:15` 注释口径 `status ∈ PENDING/EXECUTING/VERIFYING/REOPENED`。

- **IA-05 ｜ ✅确认存在 ｜** `views/cockpit/wb-comps/EvalRankTable.vue:40-43` 行点击 `drill({kind:'loops', ...})`，代码注释自证"迁移版无范围选择器（恒 GLOBAL，scopeTree 为空，原 resolvePlantNodeId 恒解析不到），清单为全厂口径"——点击具体装置行无法解释该节点；`views/cockpit/performance.vue:97-101` `onLoseClick(_tag)` 参数被 `void _tag` 显式丢弃，仅切 `/cockpit/diagnosis` 页签不带失分类别。

- **IA-06 ｜ ✅确认存在 ｜** `backend/app/schemas/performance.py:198/438/493` 三处声明 `fitnessLevel: str | None`；`backend/app/api/v1/endpoints/performance.py:601-650` 快照列表组装 `KpiSnapshotListItem(...)` 全段无任何 fitnessLevel 赋值（grep 全文件仅 fitnessFilter 出现），字段恒 None；`workbench360/index.vue:399-405` 前端读 `latest.fitnessLevel` 作接入位，注释明示"G1 落地后自动点亮、未落地显式提示"。

- **IA-07 ｜ ✅确认存在 ｜** `views/loop/workbench360/components/JourneyRail.vue:84-90` `nextSection` 推荐规则：`diag.categoryLabel !== null && (tuning.total ?? 0) === 0` 即推荐整定，不区分类别；`backend/app/services/diagnosis_system_actions.py:30-39` `CATEGORY_LABELS` 八类含 VALVE（阀门）/INSTRUMENT（仪表）/DATA_INSUFFICIENT（数据不足）——阀门/仪表/数据原因同样触发"推荐整定"。

- **IA-08 ｜ ✅确认存在 ｜** `views/handling/components/handling-archive-drawer.vue:69-79` 建议段与工单段各 `page:1, pageSize:100` 固定首页，`:81-86` 仅用 items 排序，`res.total` 从未消费；模板无分页控件、无"仅前 100 条（共 M 条）"截断提示。

- **IA-09 ｜ ✅确认存在 ｜** `views/loop/workbench360/components/sections/AssessSection.vue:93-98` `gapRateText = formatRatio(1 - validRate)`，`:294` 渲染 `断流 {{ gapRateText }}`——validRate 缺失含质量 Bad/Uncertain 与异常剔除，1-validRate ≠ 纯断流；`views/reports/data-quality.vue:66/74` 回路级区块透传 `plantNodeId` 装置筛选，`:144-156` 位号级体检 `loadAudit` 请求参数仅 lastHours/minDensityRatio/includeHeld/issueType/分页——无任何装置参数，恒全厂范围。

- **IA-10 ｜ ✅确认存在 ｜** `views/metric/components/loop-confidence-content.vue:70` 历史入口调 `getLoopConfidenceLatestApi` → `backend/app/api/v1/endpoints/loops.py:640-672` 读 `LoopConfidenceLatest` 单行最新记录（与被点击历史行的时点无关）；`workbench360/composables/use-diag-prefill.ts:10-16` `DiagPrefill` 仅 from/tsStart/tsEnd 三字段，无快照 ID/任务 ID 等证据身份；`views/tuning/verification.vue:52-56` 查询仅 loopId/pointTime/windowHours，`:95-113` 时点反查"最近提交的 TUNING 工单 submittedAt"（非按记录绑定），验证不携带决策证据身份。

- **IA-11 ｜ ✅确认存在 ｜** `views/metric/tasks.vue:134-137` 任务列表 `exclude-task-types: ['DIAGNOSIS','TUNING','REPORT']` 排除其他模块任务；`:62-69` `loadActiveTaskCount` 调 `getTaskListApi({status:'RUNNING'})` 无 taskType 过滤统计所有类型，`:128` Tab 角标直接消费——列表口径与角标口径不一致。

- **IA-12 ｜ ✅确认存在 ｜** `backend/app/tasks/report_generator.py:144-158` `_do_generate` 调占位 PDF 生成后 `file_url = f"/reports/{record_id}.pdf"`（`:152-154` 注释自认 "use a placeholder path"，无真实文件落盘），`:156-158` `record.status="COMPLETED"` 并 commit；`backend/app/api/v1/endpoints/reports.py:186-199` `POST /reports/generate` 手动触发链路保留；前端 `views/reports/subscription.vue:308/323/334` UI 明确"当前为预配置、自动生成暂未开放"——UI 语义与后端可保存占位完成态并存。

- **IA-13 ｜ ✅确认存在（落点路径注记）｜** 台账落点 `views/loop/manage` 为目录简写，实际圈选功能在 **`views/loop/manage.vue`**（回路配置页主页面，2319 行；`manage/` 子目录仅 LoopEditDrawer.vue 与 use-loop-changes.ts）。`manage.vue:213/855` "2026-10-10 运维圈选：当前控制模式筛选 / 全选当前筛选结果"，`:776-823` 批量配置——运维圈选确已在回路配置页落地，日常治理动作与组态管理同页混合，"无需再建圈选"与现状一致。

## §2 角色权限（10 条）

- **AUTH-01 ｜ ✅确认存在 ｜** `backend/app/models/sys_user.py:37+49-53` 单 `role` 列 + CheckConstraint 五预设角色；`backend/app/services/auth.py:58-104` `ROLE_PERMISSIONS` 为代码内静态 dict；`backend/app/api/deps.py:106-124` `require_roles` 硬编码角色白名单与 require_perms 权限码两套并存；`frontend/apps/web-antd/src/views/system/permissions.vue:102-160` `PERMISSION_MATRIX` 纯前端静态展示，不驱动任何鉴权。

- **AUTH-02 ｜ ✅确认存在 ｜** `backend/app/api/v1/endpoints/tuning.py:192-196` `POST /tuning/identify/history` 白名单含 PE；`services/auth.py:69-83` PE_ENGINEER 权限码无 `tuning:view`；`tuning.py:553/578/588` 任务列表/详情/进度端点均 `require_perms("tuning:view")` → PE 提交成功后轮询/读记录 403；`use-tuning-workbench.ts:339` `getTuningTaskStatusApi(taskId)` 轮询即触发。SPONSOR 侧：`router/routes/modules/tuning.ts:22/39/65/81` 查看 authority 含 SPONSOR，后端 SPONSOR 无 tuning:view → 模块可进、数据必 403。

- **AUTH-03 ｜ ✅确认存在 ｜** `views/task/list.vue:595-602` 批量删除按钮仅按选中数、`:781-791` 取消按钮仅按状态、`:793-805` 删除按钮仅按终态显示——三处均无角色/权限码控制；`backend/app/api/v1/endpoints/tasks.py:120` `_TASK_CREATOR_ROLES=("IC_ENGINEER","PE_ENGINEER","ADMIN")`，cancel（:1348-1351）与 delete（:1419-1422）端点均 `require_roles(*_TASK_CREATOR_ROLES)`——EXPERT/SPONSOR 可见按钮但 API 必 403。

- **AUTH-04 ｜ ✅确认存在 ｜** `router/routes/modules/reports.ts:23-27` `/reports` 父路由固定 `redirect: '/reports/performance'` 且 authority 为子路由并集含 EXPERT；`:39` performance 子路由 authority 不含 EXPERT——EXPERT 进父路径必被重定向到无权子页；`frontend/packages/effects/access/src/accessible.ts:141-152` 框架对父路由的固定 redirect 机制（子路由排序变化即可能改变落地页）。

- **AUTH-05 ｜ ✅确认存在 ｜** `services/auth.py:60-67` IC_ENGINEER 权限 `"loop:*"` 通配（语义覆盖 loop:delete）；`endpoints/loops.py:527-531` `DELETE /loops/{loop_id}` `require_roles("ADMIN")` 真实删除仅 ADMIN（`:533` docstring"仅 ADMIN，硬删除"）——权限码删除语义与 API 角色门禁不一致，机械迁移通配符确会扩权。

- **AUTH-06 ｜ ✅确认存在 ｜** `composables/use-clpm-roles.ts:38-39` `ADVANCED_PARAM_ROLES = ['ADMIN','EXPERT']` 前端高级参数仅两角色可见；`tuning.py:196+237-246` 历史辨识端点白名单含 IC/PE，`thetaEstimate`/`candidateModelTypes` 等高级参数从 body 直传任务，无字段级角色守卫——前端隐藏不构成后端约束。

- **AUTH-07 ｜ ✅确认存在 ｜** `backend/app/api/deps.py:86-96` 每请求读库校验角色/is_active（后端实时）；`router/guard.ts:89-92` `accessStore.isAccessChecked` 后路由/菜单不再重查（前端缓存）；`services/user.py:257-291` `reset_password` 仅改 hash+审计+commit，无任何令牌撤销调用——对照 `services/auth.py:533-564` `change_password`（自助改密）调用了 `_revoke_all_user_tokens`，管理侧重置未复用该能力。

- **AUTH-08 ｜ ✅确认存在 ｜** `models/sys_user.py:25-57` SysUser 模型全字段无装置/单元 scope 字段；`api/deps.py:35-103` `get_current_user` 不注入任何对象范围；`endpoints/loops.py:86-119` `plantNodeId` 等均为可选查询筛选参数，无用户→对象授权裁剪——装置 scope 目前纯是筛选。

- **AUTH-09 ｜ ✅确认存在 ｜** `backend/app/main.py:1231-1234` realtime/ws_realtime/ws_alert 三 router 无条件注册；`endpoints/realtime.py:35-38` `GET /realtime` 接受任意 `tagCodes` 列表仅 get_current_user；`endpoints/ws_realtime.py:91-92` `filter_tags=None` 旧客户端全量转发、`:119-143` `_verify_token` 仅验 JWT 签名/类型/黑名单（无角色/scope）、`:293-314` `_validate_subscribe_tags` 仅查 tag_registry 存在性；`endpoints/ws_alert.py:66-110` `/ws/alerts` 认证后 `pubsub.listen()` 全频道消息直接 `send_text`（无按回路过滤）。

- **AUTH-10 ｜ ✅确认存在 ｜** `backend/app/tasks/tuning.py:179` 注释自证 created_by_id 仅"传入 init_progress 以桥接 TaskTracker"（跟踪用途），`:256` 长计算 `identify_model_from_history`，`:341` 计算完成后直接 `await db.commit()` 落库（无操作者权限复核/提交栅栏）；`tasks/diagnosis_v2.py:86` 批诊断逐回路 for 循环执行落库；`services/diagnosis_orchestrator.py:1080-1083` `db.add(run); await db.commit()` 诊断结果直接提交并即时生成 SYSTEM 处置建议——撤权语义若新增，须在这些 commit/发送点加栅栏。

## 补充观察（供 P1/P4 任务卡参考，不扩大 P0 范围）

1. 多处端点 docstring 角色口径落后于白名单：`tuning.py:198/262`、`tasks.py:613/636` 文档均写 "ADMIN/IC_ENGINEER/EXPERT"，实际已含 PE_ENGINEER，后续改造勿按注释读权限。
2. `use-drill.ts:317` E4 预警事件清单 `limit: 50` 静默截断，弹窗无"仅前 N 条"提示，与 IA-08 同类诚实化缺口。
3. `auth.py:561-564` change_password 先 commit 密码再撤销令牌：撤销步骤失败时新密码已生效而旧 access token 仍有效（次序问题）。
4. `reports.ts:26` 注释只提"避免 IC/PE/SPONSOR 看不到菜单"，但并集同时带入了 EXPERT，而 EXPERT 对 redirect 目标 performance 无权且无可用回退（AUTH-04 的直接体验后果）。
