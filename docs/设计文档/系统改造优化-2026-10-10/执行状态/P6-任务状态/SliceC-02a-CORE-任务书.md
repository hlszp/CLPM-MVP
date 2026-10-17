# Slice C 任务书：P6-02a 工作台核心拆分（O02-CORE）

Owner：子代理C ｜ 基线 6c92cf69 ｜ 工作树 `/Users/zhangping/DEV/clpm-p6-kcode-20261014`（**只在此树工作**）｜ `F/` = `frontend/apps/web-antd/src/`

## 必读（按节，禁全量通读）
1. `docs/设计文档/系统改造优化-2026-10-10/执行状态/P6-01a-契约与文件Owner-20261014.md` **§4 全文 + §5 相关行 + §6 O02-CORE 行**
2. `docs/设计文档/系统改造优化-2026-10-10/09-工程师任务导向IA与工作台组件增补-2026-10-11.md` **§2（路由建议）+ §3（组件设计）**
3. 工作树源码：`F/views/loop/workbench360/` 全目录（index.vue、composables/、components/ 现状）、`F/views/diagnosis/overview.vue` 的已有抽屉（第二宿主接入点）、`F/api/` 中 loop 相关四 API 封装
4. **Slice B 移交记录**（`SliceB-02a-RT-任务书.md` 末尾）中冻结的实时注册 Interface——签名以该记录为准

## 范围（独占文件，越界即停）
- 修改：`F/views/loop/workbench360/index.vue`、`composables/use-wb360-loop.ts`、`composables/use-diag-prefill.ts`、`composables/use-tuning-prefill.ts`、`components/LoopSpine.vue`
- 拟建：`F/views/loop/workbench360/components/SingleLoopWorkbench.vue`、`components/AllLoopWorkbenchHost.vue`、`composables/use-wb360-session.ts`、`composables/use-wb360-directory.ts`、`workbench-context.ts`
- 第二宿主：`F/views/diagnosis/overview.vue` 的抽屉接入（仅此文件串行改，其他旧页一律禁改）
- 实时兴趣：经 Slice B 冻结 Interface 注册/释放（owner=实例会话），不直接调 subscribe
- **禁止**：改 use-loop-realtime.ts/realtime-ws.ts（O02-RT 文件，发现问题报告主协调）；改其他 8 个实时消费文件；分叉整定/诊断业务实现（复用 use-tuning-workbench、diagnosis-result-panel）；commit/push

## 契约要点（01a §4 与 09§2 为准）
- **路由 Adapter**：优先级 有效显式 mode → embed=1 → 无 mode 非空 loopId → 默认 all；非法显式 mode 直接提示；`section=diagnosis`→内部 diag；非法 section/禁用剖面/缺必需对象给出原因不静默换；全模式选中写 `mode=all&loopId=`；旧 loopId 深链规范为 single；携带 window/record/work/scope 必须消费回显、单项清除更新上下文
- **拆分**：index.vue=路由 Adapter；AllLoopWorkbenchHost=树宿主（稳定 nodeId 装置-单元-回路，按需加载，不预载全厂）；SingleLoopWorkbench=单回路核心（props=context+presentation+readonly，**不读 route/query/hash/window.location，不写宿主 URL**，事件 result-changed/navigate-request 由宿主构造路由）
- **单对象读取**：复用 getLoopDetailApi/getLoopMonitorDetailApi/getLoopTagsApi/getLoopModeMappingApi；single 模式**禁止**拉全厂 monitor 列表；对象不存在/删除/越权明确失败展示原因，**不回退第一条**；SPONSOR 等被 monitor 硬角色拒绝时如实显示不可用，不靠 readonly prop 绕过
- **实例会话**：诊断/整定 prefill 从模块级 reactive 迁入 use-wb360-session 实例；每次对象/证据变化递增请求代次；趋势/量程/历史迟到响应同时核上下文身份+代次，不符丢弃；切换前处理未保存输入；尺寸/主题变化不重建业务会话
- **readonly** 只限制不授予；模块禁用剖面显示不可用原因
- 关闭组件只释放本实例读请求/handler/定时器/兴趣，不假称取消已投计算

## 验证
- 新增/更新 vitest：Adapter 优先级矩阵、single 无全目录请求（mock API 计数）、无效对象不回退、代次丢弃迟到响应、双实例 prefill 隔离
- `pnpm run check:type` 通过；eslint scoped 自己文件通过
- 浏览器证据由 Phase 4 统一取，本 Slice 做到作者自验+单测

## 完成判据
01a §7「02a 核心分片」判据逐条对照；移交记录写回本文件末尾标"待验收"。**上下文水位>50% 即 WIP 停手写明断点。**

## 移交记录

状态：**待验收**（2026-10-14，实施者：Slice C 子代理，工作树 `/Users/zhangping/DEV/clpm-p6-kcode-20261014`，分支 codex-p6-kcode-20261014，基线 6c92cf69；未 commit/push）

### 完成项

按任务书独占范围改了 6 个既有文件 + 新建 5 个源码文件 + 3 个测试文件，未越界（未动 use-loop-realtime.ts/realtime-ws.ts/layouts/basic.vue 与其他 8 个实时消费旧文件、未动整定/诊断业务实现、未动后端、未 commit）：

- **拟建 `workbench-context.ts`**：`WorkbenchContext`/`Wb360Presentation`/`Wb360NavigateRequest`（update-context | return）/`Wb360ResultChangedPayload` 类型 + 纯函数 `resolveWb360Route`（优先级：有效显式 mode → embed=1 → 无 mode 非空 loopId → 默认 all；非法显式 mode/section 与 single 缺 loopId 进 errors 直接提示；window/record/work 辅助参数非法进 notices 显式列出；`section=diagnosis|diag`→对外名 diagnosis；hash loopId 优先 query）、`resolveWb360Embed`（旧 embed props 协议）、`wb360QueryFromContext`（全模式恒写 `mode=all&loopId=…`，section 一次性键不回写）、`wb360ContextKey`（loopId+窗口+证据/工作/scope/profile/配置版本完整身份，证据归一化排序）、`WB360_MANAGED_QUERY_KEYS`。`carried` 独立于 loopId 暴露：all 模式未指定对象时 ?section=/window 在用户后续选中仍被消费。
- **拟建 `composables/use-wb360-session.ts`**：实例会话。owner=symbol（实时兴趣注册身份）；`contextKey` 变化递增 generation 并显式清空双预填；`beginRequest()/isCurrent(token)` 身份+代次双重核对；诊断/整定 prefill 从模块级 reactive 迁入会话实例；会话栈（栈顶=最近注册=可见实例）供 prefill 兼容模块写路径解析；setup 同步路径 provide(WB360_SESSION_KEY) + onScopeDispose 释放。
- **改 `composables/use-diag-prefill.ts` / `use-tuning-prefill.ts`**：导出签名不变（DiagSection/TuningSection/DiagTriggerModal 零改动），状态改会话承载——读经 inject 解析本实例会话、写经会话栈顶；无会话惰性兜底（真实消费方均在会话内）。
- **拟建 `composables/use-wb360-directory.ts`**：全目录（仅全宿主用）。monitor 清单渐进加载（首页即渲染+余页并发并入）+ plant-node 树；树选择身份改**稳定 nodeId**（A21；清单行仅 unitName 可关联，过滤由 nodeId 解析名称，口径限制已注释）；`loopsComplete` 全量终态标志（对象存在性判定前提）；**无任何选中/回退逻辑**。
- **重写 `composables/use-wb360-loop.ts`**：单回路对象上下文（仅单核心用）。单对象四接口（getLoopDetailApi/getLoopMonitorDetailApi/getLoopModeMappingApi + getLoopTagsApi→getTagDetailApi 量程）；detail+monitor 双失败→loadError 明确原因**不回退**；monitor 单失败（SPONSOR 403 等）→monitorError 如实显示+基本对象仍呈现+实时值诚实 null 占位；所有迟到响应（对象/量程）落地前 `session.isCurrent` 核对后丢弃；实时兴趣 `registerInterest(session.owner, …)`（未调 subscribe/bindLoopInterest）。
- **拟建 `components/SingleLoopWorkbench.vue`**：单回路核心（旧 index.vue 主列全量能力迁入：页头/旅程条/趋势画布/四剖面/缩略卡/状态栏/关注抽屉/导出/SP 容差带/自定义窗）。props 仅 context+presentation+readonly；不读 route/query/hash/window.location、不写宿主 URL（grep 验证）；事件仅 resultChanged/navigateRequest（template kebab 绑定同名对外）；左脊柱经 `#spine` 插槽由宿主注入；携带项回显条（window/record/work/scope/profile/configRevision 逐项 × 清除→navigateRequest(update-context)）；context.window 真实消费为自定义趋势窗；禁用剖面 inline 原因提示不静默换；对象不可用/加载中整区显式状态；readonly 徽标（只限制不授予）。
- **拟建 `components/AllLoopWorkbenchHost.vue`**：全回路树宿主。LoopSpine+vsplit（180–420 拖拽）+ 右侧一次挂载一个核心（key=wb360ContextKey）；无选中显示选择提示（关注/最近入口属 05/06，未编造）；URL 预选对象待 loopsComplete 后校验，不存在/越权明确提示**不回退首行**；选中经 selectLoop 上报 Adapter。
- **改 `components/LoopSpine.vue`**：单元选择身份 unitName→nodeId（展示名经树解析）；类型导入改自 use-wb360-directory。
- **改 `index.vue`**：收敛为路由 Adapter（唯一读写 route/query/hash/window.location 层）。受管键整体重建+未管理键保留；全模式选中写 mode=all&loopId；核心 navigateRequest(update-context)→URL、return→from/back；硬错误整页提示+「清除参数并进入」；notices 横幅；compact(embed=1) 返回条；**保留旧 embedLoopId/embedSection props 协议**（alert/events.vue 与 diagnosis/records.vue 两个 02b 前旧宿主零改动继续可用，presentation=drawer、不写宿主路由）。
- **改 `views/diagnosis/overview.vue`（第二真实宿主，仅此文件串行改）**：抽屉改挂 SingleLoopWorkbench（`:context={loopId, section:'diagnosis'}`、presentation=drawer、`:key=loopId`、destroy-on-close 卸载即释放会话/兴趣）；resultChanged→loadLatestOverview() 定向刷新概览；navigateRequest(update-context)→本地 loopId、return→关抽屉。
- **测试新建 3 文件 37 用例**：`__tests__/wb360-route-adapter.test.ts`（22：优先级矩阵 10 + section/辅助参数 7 + 回写协议 3 + embed 协议 3 + contextKey 2——含"无效对象不回退"所需的路由层断言与全上下文受管键往返）；`__tests__/wb360-session.test.ts`（7：代次递增/同回路换证据换代次/切换清预填/双实例 prefill 隔离×4 含 inject 读路径与栈顶写路径与卸载栈回落）；`__tests__/wb360-single-loop.test.ts`（5：四单对象接口计数+**getLoopMonitorListApi 零调用**、404 双失败 loadError 不回退、403 monitorError 诚实占位、A→B 迟到对象响应丢弃、A→B 迟到量程响应丢弃）。

### 对外 Interface（本 Slice 冻结候选）

```ts
// #/views/loop/workbench360/workbench-context.ts（类型+纯函数，vue 无关）
interface WorkbenchContext { loopId: string; section?: 'assess'|'diagnosis'|'tuning'|'handling';
  scope?: {nodeId?; filters?}; window?: {tsStart; tsEnd; timezone};
  evidenceRefs?: Array<{kind; id; version?}>; workRef?: {kind:'investigation'|'action_item'|'handling_order'; id};
  profileRef?; configRevision? }
resolveWb360Route(query, hashLoopId?) → Wb360RouteResolution{mode, compact, context, carried, initialLoopId, errors, notices, returnRef}
resolveWb360Embed(embedLoopId, embedSection?) → Wb360RouteResolution
wb360QueryFromContext(mode, context) → Record<string,string>;  wb360ContextKey(ctx) → string
// SingleLoopWorkbench.vue
props: { context: WorkbenchContext; presentation?: 'page'|'drawer'|'panel'; readonly?: boolean }
events: resultChanged({loopId, changeKind, workRef?}) / navigateRequest({target:'update-context', context} | {target:'return', returnRef?})
// composables/use-wb360-session.ts
useWb360Session(Ref<WorkbenchContext>) → { owner: symbol; generation; contextKey; beginRequest(); isCurrent(token);
  diagPrefill; tuningPrefill; set/clear×2; dispose() } ；WB360_SESSION_KEY；currentWb360Session()
// composables/use-wb360-loop.ts：useWb360Loop(Ref<loopId|null>, session) → { current, loading, loadError, monitorError, ranges, connectionStatus, lastMessageAt, onRealtimePoint, startRealtime }
// composables/use-wb360-directory.ts：useWb360Directory() → { loops, loopsLoading, loopsComplete, loopsError, tree, treeError, selectedUnitNodeId, selectedUnitName, keyword, gradeFilter, filteredLoops, hasLoop, loadLoops, loadTree }
```

URL 协议（Adapter 受管键）：`mode=single|all`、`loopId`、`section`（一次性）、`embed=1`、`from`（未管理保留）、`tsStart/tsEnd/tz`、`recordKind/recordId/recordVersion`、`workKind/workId`、`nodeId`、`profile`、`configRevision`。

### 验证命令与真实退出码（全部在工作树执行）

- `cd frontend && pnpm exec vitest run apps/web-antd/src/__tests__/wb360-route-adapter.test.ts apps/web-antd/src/__tests__/wb360-session.test.ts apps/web-antd/src/__tests__/wb360-single-loop.test.ts` → exit 0，37/37 passed
- 全量 `pnpm exec vitest run apps/web-antd` → exit 0，58 文件 473/473 passed（尾部 vitest-pool forks worker 终止告警与 loop-edit-drawer/token 测试自身 stderr 噪声，均为基线既有）
- `pnpm run check:type` → exit 0（2 successful）
- `pnpm exec eslint <本 Slice 14 个文件>` → exit 0，0 errors；4 个 `vue/one-component-per-file` warning 均在 wb360-session.test.ts（Slice B 同款测试宿主模式，其移交已接受）
- 红线自检：`grep useRoute|useRouter|window.location|location.hash` 于 SingleLoopWorkbench/AllLoopWorkbenchHost/use-wb360-session/use-wb360-loop/use-wb360-directory → 零命中

### 完成判据对照（01a §7「02a 核心分片」）

- 单模式无全目录请求 → 测试断言 getLoopMonitorListApi 零调用 ✓
- 末页/无效/无权不选首行 → 全链路无 auto-select（目录不含选中逻辑；宿主/核心显式错误态）✓
- 宿主 URL 隔离 → 核心 props/事件边界 + grep 零命中 ✓
- 对象/证据切换的迟到请求和预填隔离 → 会话代次+身份双核对，测试覆盖对象/量程迟到与双实例 prefill ✓
- 权限/禁用剖面显式 → monitorError 横幅（403 测试）、sectionNotice inline、loadError 整区 ✓

### 未完成与风险

- **readonly 仅核心层徽标**：剖面内部动作按钮（发起诊断/保存方案/建单）所在剖面文件不在本 Slice 范围未改；动作门禁最终由 API 权限校验（09 §3.1 口径），readonly 未授予任何能力。剖面级 readonly 呈现留 R-UI/02b。
- **未保存输入处理**：预填随会话身份显式失效（已测）；剖面内部草稿（如整定表单）随 keyed remount 销毁，未加脏检查拦截（剖面文件不可改）。
- **embed 旧宿主行为变化**：alert/events.vue、diagnosis/records.vue 抽屉由"全目录+首行回退"变为"单对象直达"（无左脊柱、不再拉全厂清单）——符合新契约且两文件零改动，但抽屉内视觉结构变化需 Phase 4 浏览器证据确认。
- **AllLoopWorkbenchHost 中 LoopSpine 接线重复两处**（核心 #spine slot + 无核心骨架）：拟建清单受限未新建共享子组件，功能等价，后续可抽。
- **预选对象校验依赖清单全量到达**（loopsComplete）：清单加载失败时显示"无法验证指定对象"而非对象本身错误；single 模式无此依赖（直接单对象读取）。
- 浏览器证据未取（按任务书由 Phase 4 统一）；本树另有 Slice A 后端未提交改动（backend/ 下），非本 Slice 范围未触碰未格式化。

### resume 接线段（Slice G 移交小接线，2026-10-14，标"待验收"）

状态：**待验收**（同一工作树，未 commit/push；仅改 O02-CORE 范围内 2 个源码文件 + 1 新测试文件 + 2 测试文件补 mock，未动 RT 冻结文件/后端/其他页面）

**完成项**

- `composables/use-wb360-session.ts`：会话新增 `recordResume()`（`Wb360Session` 接口同步导出）。协议对齐 my-work `recordLoopResumeQuietly`：首次记录先 GET `/me/work-resume?kind=loop&limit=100` 拉代际/版本镜像（会话内 `resumeGeneration`/`resumeRevisions`）→ PUT 携带 `expectedRevision`（镜像无该资源=0）+ `generation` + `requestId`；409 经 `extractRevisionConflict` 识别后刷新镜像重试一次，再失败静默放弃（不阻塞作业、不显示已保存）；写响应 `revision/generation` 回收进镜像减少自伤 409。存储 context 仅 `section/window/evidenceRefs`（禁业务结果副本/任意 URL；workRef 业务身份不入档）。客户端幂等键 `loopId+JSON(storedContext)`：同会话同对象同上下文不重复 PUT（仅成功后才登记幂等键，失败不冒充）。
- `composables/use-wb360-loop.ts`：`loadLoop` 在 `isCurrent(token)` 核对后、`current` 成功装配点调用 `void session.recordResume()`——loadError（404/越权/双来源失败）路径在其前 return，**失败打开不产生最近项**；monitorError（SPONSOR 403 但对象已开）属成功打开，如实记录。全模式树选切换与单模式直达同走 SingleLoopWorkbench→loadLoop 单点覆盖。
- 测试 `__tests__/wb360-resume.test.ts`（新建，5 用例）：成功加载后 PUT（kind/id/context/expectedRevision=0/generation/requestId 断言，workRef 不入档）；404 对象 GET/PUT 均零调用；409 刷新重试一次（重试体 expectedRevision=3/generation=8 取自 409 载荷）；409 重试仍失败静默放弃（作业面不受影响、恰好 2 次 PUT）；同对象同上下文不重复 PUT + 上下文变化后按回收版本（revision=1）再写。`wb360-session.test.ts`/`wb360-single-loop.test.ts` 补 `#/api/personal-work` mock（真实 request 链不进测试）。

**验证（工作树真实退出码）**

- `pnpm exec vitest run <wb360 四测试文件>` → exit 0，42/42 passed（resume 5 + single-loop 5 + session 7 + adapter 25）
- 全量 `pnpm exec vitest run apps/web-antd` → exit 0，62 文件 539/539 passed
- `pnpm run check:type` → exit 0（2 successful）
- `pnpm exec eslint <本段 5 文件>` → exit 0，0 errors（4 个 vue/one-component-per-file warning 为既有测试宿主模式）

**未完成与风险**

- resume 代际/版本镜像为会话级（每次开对象 1 次 GET + 1 次 PUT）：与 my-work 同款的正确性优先口径；跨会话共享镜像未做（stale 由 409 协议兜底）。
- `recordResume` 失败完全静默（契约要求不显示已保存）：无任何遥测上报点。
- 抽屉宿主（diagnosis/overview）经同一 loadLoop 覆盖；`alert/events.vue`、`diagnosis/records.vue` 旧 embed 路径同样经 index.vue→SingleLoopWorkbench→loadLoop，亦自动获得最近继续记录。
