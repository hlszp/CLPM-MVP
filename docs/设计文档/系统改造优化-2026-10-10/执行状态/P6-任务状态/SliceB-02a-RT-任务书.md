# Slice B 任务书：P6-02a 共享实时层接口冻结（O02-RT）

Owner：子代理B ｜ 基线 6c92cf69 ｜ 工作树 `/Users/zhangping/DEV/clpm-p6-kcode-20261014`（**只在此树工作**）｜ 前端根 `frontend/`，源码 `frontend/apps/web-antd/src/`（下文 `F/`）

## 必读（按节，禁全量通读）
1. `docs/设计文档/系统改造优化-2026-10-10/执行状态/P6-01a-契约与文件Owner-20261014.md` **§4.3 + §6 O02-RT 行及消费者清单**（契约事实源）
2. 工作树源码：`F/composables/use-loop-realtime.ts`、`F/utils/realtime-ws.ts`、`F/layouts/basic.vue`、`F/__tests__/use-loop-realtime.test.ts`、`F/__tests__/realtime-ws.test.ts`（测试文件路径以实际为准）
3. 09号文 §3.2（对象切换/生命周期）按需

## 现状缺陷（01a §5 已核）
`use-loop-realtime.ts:192/205` 每个 bind 直接 subscribe；`realtime-ws.ts:169` subscribe 替换全局兴趣——多实例互相覆盖。

## 范围（独占文件，越界即停）
- 修改：`F/composables/use-loop-realtime.ts`、`F/utils/realtime-ws.ts`、`F/layouts/basic.vue`、对应两个测试文件
- **禁止**：改 9 个既有消费文件（tag/list.vue、loop-fleet-view、loop/monitor.vue、tuning/overview.vue、cockpit/loops.vue、loop-detail-panel.vue、loop-trend-modal.vue、use-wb360-loop.ts 等——属 Slice C/D）；commit/push；改后端

## 契约要点（01a §4.3 为准）
- 订阅 registry 按 **owner** 保存 loop/tag 兴趣；对 WS 发送可见 owner 的**并集**；释放 owner B 不清 A；同 owner 重复注册幂等
- KeepAlive 停用→释放该页面 owner 兴趣；恢复→重新注册并**重验身份/权限语义**（重算兴趣源，不缓存陈旧）
- WS 断线重连后自动重发当前并集
- 布局退出/清连接前先停止页面 owner（layouts/basic.vue 接线）
- **冻结导出 Interface**（供 CORE/消费者调用，JSDoc/类型注释写清语义）：形如 `registerInterest(owner: symbol|string, source: () => {loops:string[], tags:string[]})` / `unregisterInterest(owner)`，具体签名你定但必须在报告与文件头注释冻结
- 关闭组件只释放本实例兴趣与 handler，不假称取消后端计算

## 验证
- 相关 vitest：`cd frontend && pnpm exec vitest run <两测试文件路径>` 全绿；新增用例覆盖：双 owner 并集、释放隔离、重连重发、反复注册/释放不积累监听
- `pnpm run check:type` 通过；eslint scoped：`pnpm exec eslint apps/web-antd/src/composables/use-loop-realtime.ts apps/web-antd/src/utils/realtime-ws.ts apps/web-antd/src/layouts/basic.vue`（全量 CI 口径由 Phase 3 统一跑）

## 完成判据
测试全绿+类型干净+Interface 冻结说明；移交状态写回本文件末尾「## 移交记录」，标"待验收"。**上下文水位>50% 即 WIP 停手写明断点。**

## 移交记录

状态：**待验收**（2026-10-14，实施者：Slice B 子代理，工作树 `/Users/zhangping/DEV/clpm-p6-kcode-20261014`，分支 codex/p6-kcode-20261014，基线 6c92cf69；未 commit/push）

### 完成项

按任务书范围改了 3 个源码文件 + 2 个测试文件，未越界（未动 9 个既有消费文件、未动后端、未 commit）：

- `F/utils/realtime-ws.ts`：WS 层 owner registry。`interestOwners: Map<InterestOwner,{loops,tags}>` 按 owner 存归一化快照（去空/去重/排序），`_applyUnion()` 重算并集缓存，并集内容变化或"从未声明→首次声明"才发 subscribe 帧；open/重连后自动重发当前并集（沿用既有 `_sendSubscribe`）。`interestTags===null` 保持"从未声明=全量转发"旧语义。旧 `subscribe()/clearInterest()` 委托到导出常量 `LEGACY_INTEREST_OWNER` 保留切片（等价旧全局替换语义，与其他 owner 求并集）。
- `F/composables/use-loop-realtime.ts`：冻结 Interface（见下）+ 兼容包装。composable 侧 `interestEntries` 跟踪每 owner 的 watch 与停用开关；条目对象身份作"同 owner 重复注册替换"令牌，防止旧条目 cleanup/生命周期回调误伤新注册。`bindLoopInterest/bindTagInterest` 签名不变：有实例按 `bind-<kind>:<uid>` 隔离（享 KeepAlive 语义），无实例（monitor.vue 用户 toggle 路径）退化 `LEGACY_INTEREST_OWNER`（同 owner 后注册停先注册 watch，不积累；路由离开由布局 clearInterest 清空）。
- `F/layouts/basic.vue`：`onUnmounted` 中 `releaseAllInterests()` 先于 `realtimeWs.disconnect()`；路由 path watcher 保留 `clearInterest()` 作兼容层安全网（注释已更新）。
- 测试：`realtime-ws.test.ts` +8 用例（双 owner 并集、释放隔离/空并集、同 owner 幂等替换、释放不存在 owner 空操作、断线重连重发并集、反复注册/释放无残留、unregisterAllInterests 声明/未声明两态、兼容层与 owner 并集共存）；`use-loop-realtime.test.ts` +9 用例（即时注册/变化重注册/乱序同内容不重发、卸载释放且 watch 停止、双 owner 隔离、同 owner 替换+旧 cleanup 不误伤+cleanup 幂等、KeepAlive 停用释放/停用期不注册/恢复重算 source 重注册、releaseAllInterests 停 watch 清 registry、bind 两包装快照/双实例不互覆）。

### 冻结导出 Interface（`#/composables/use-loop-realtime`，文件头注释即契约）

```ts
type InterestOwner = string | symbol;            // 调用方持有的唯一身份（re-export 自 realtime-ws）
interface RealtimeInterest { loops?: string[]; tags?: string[] }  // 快照，整体替换（re-export）
type InterestSource = () => RealtimeInterest;    // 无副作用 getter，必须可重复调用

registerInterest(owner: InterestOwner, source: InterestSource): () => void
unregisterInterest(owner: InterestOwner): void
releaseAllInterests(): void
// 兼容包装（签名不变，Slice D 逐文件迁移）：
bindLoopInterest(source: () => string[]): void
bindTagInterest(source: () => string[]): void
```

语义要点（冻结）：立即注册当前快照并 watch source 派生键（JSON.stringify 归一化数组），内容变化自动重注册、不变零发送；对 WS 发送全部存活 owner 并集；释放 B 不影响 A；同 owner 重复注册后者整体替换前者（幂等）；组件 setup 同步路径（含 onMounted 等生命周期回调）调用时：KeepAlive 停用→释放本 owner、恢复→重算 source 重注册（不回放停用前快照=重验身份/权限语义）、卸载→停 watch 并释放；cleanup 幂等；无实例上下文调用无生命周期接线，须自行 cleanup。WS 层配套：`realtimeWs.registerInterest/unregisterInterest/unregisterAllInterests` + `LEGACY_INTEREST_OWNER`。关闭组件只释放本实例兴趣与 handler，不假称取消后端计算（未改）。

### 验证命令与真实退出码（全部在工作树执行）

- `cd frontend && pnpm exec vitest run apps/web-antd/src/__tests__/realtime-ws.test.ts` → exit 0，22/22 passed
- `cd frontend && pnpm exec vitest run apps/web-antd/src/__tests__/use-loop-realtime.test.ts` → exit 0，33/33 passed
- 同命令四文件（上两 + loop-monitor-realtime + loop-fleet-view 消费者回归）→ exit 0，71/71 passed
- 全量 `pnpm exec vitest run apps/web-antd` → exit 0，55 文件 436/436 passed（尾部有 vitest-pool forks worker 终止告警，为 teardown 环境噪声，与本次改动文件无关）
- `pnpm run check:type` → exit 0（2 successful）
- `pnpm exec eslint apps/web-antd/src/composables/use-loop-realtime.ts apps/web-antd/src/utils/realtime-ws.ts apps/web-antd/src/layouts/basic.vue`（+两个测试文件）→ exit 0，0 errors；8 个 `vue/one-component-per-file` warning 均在测试文件（line 69 为基线既有，新增为同模式 defineComponent 宿主）

### 未完成与风险

- 消费页仍走兼容包装（本 Slice 禁改）：setup 顶层调用方已自动获得 owner 隔离+KeepAlive 语义；`monitor.vue:1004` 的 `startAutoRefresh()` onMounted 路径有实例（Vue injectHook 设 currentInstance）享隔离，用户 toggle 路径无实例落 LEGACY 切片（旧全局替换语义，行为与基线一致且不再积累 watch）；Slice D 逐文件迁移 registerInterest 时可消除该兜底。
- 路由 path watcher 的 `clearInterest()` 现只清 LEGACY 切片：正常页面 owner 经卸载/停用释放后并集自动置空，行为不变；若未来出现"无实例且长驻跨路由"的 bind 调用方需在 Slice D 接线时复查。
- 本树在我工作期间出现 O05-BE 并行的后端未提交改动（`backend/app/{main.py,models/__init__.py}` 修改 + `personal_workspace` 五新文件 + 迁移文件），非本 Slice 范围、未触碰；验收/提交时请按 Owner 分界区分。
- 布局退出接线只验证到单测层（releaseAllInterests 语义），未做浏览器真实登出链路验证（属 Phase 3 非作者验收范围）。

### Slice D（O02-RT 消费者串行接线）移交段落

状态：**待验收**（2026-10-14，同一工作树，未 commit/push；Slice C 已接 use-wb360-loop.ts，本段覆盖剩余 7 个消费文件）

#### 逐文件行为说明（owner 均为组件实例级唯一 Symbol，注册/重订由 registerInterest 内部 watch 驱动，卸载经 scope dispose 自动释放，KeepAlive 停用释放/恢复重算 source 重注册；每文件代码注释已写明）

1. `views/tag/list.vue`：`bindTagInterest`→`registerInterest(Symbol('tag-list-realtime'), ()=>({tags: 当前页位号}))`；分页/筛选变化自动重订；卸载/KeepAlive 语义同冻结契约。
2. `components/monitor/loop-fleet-view.vue`：`bindLoopInterest`→`registerInterest(Symbol('loop-fleet-view-realtime'), …loops)`；保留 2026-09-07 修复注释并补 O02-RT 行为说明；`__tests__/loop-fleet-view.test.ts` 模块 mock 增补 `registerInterest/unregisterInterest: vi.fn()`（组件改导入后必须，余无改动）。
3. `views/loop/monitor.vue`（toggle 路径重构）：导入改普通 script 块（registerInterest+unregisterInterest 换 bindLoopInterest）；setup 顶层 `realtimeInterestOwner=Symbol('loop-monitor-realtime')`；`startAutoRefresh()` 内 `registerInterest(owner,…)`——onMounted 路径有实例（生命周期接线），用户 toggle 路径无实例但同 owner 重注册整体替换不积累 watch；`stopAutoRefresh()` 新增 `unregisterInterest(owner)`（幂等）。**语义变化**：toggle OFF 现在同时释放兴趣（服务端停推），旧版 bind 无法退订白推流量；onUnmounted 经 stopAutoRefresh+scope dispose 双保险。选择实例 owner 而非 LEGACY 切片：消除兼容层兜底，退订精确到实例。
4. `views/tuning/overview.vue`：`bindLoopInterest`→`registerInterest(Symbol('tuning-overview-realtime'), …loops=overviewRows tagName)`。
5. `views/cockpit/loops.vue`：`bindLoopInterest`→`registerInterest(Symbol('cockpit-loops-realtime'), …loops=可见卡片 tagName)`；卡片墙多实例互不覆盖（并集）。
6. `views/cockpit/components/loop-detail-panel.vue`：同上（owner `loop-detail-panel-realtime`）；TDZ 注释同步（registerInterest 同样 immediate 求值 source，realtimeItem 须先声明）。
7. `components/loop/loop-trend-modal.vue`：**新增**声明式兴趣注册（此前只 start/onMessage 不声明兴趣，依赖宿主页面订阅存活，宿主停用/卸载后实时流冻结）：`registerInterest(Symbol('loop-trend-modal-realtime'), ()=>({loops: viewMode==='realtime'&&props.tagName?[props.tagName]:[]}))`；并集只增不减不影响宿主；viewMode/tagName 变化自动重订；stopRealtime 仍只管消息 handler 与定时器（语义不变）。

#### 兼容包装剩余调用方

生产源码 **零调用**（grep `bindLoopInterest|bindTagInterest` 仅剩冻结层定义、测试 mock/用例与注释）。包装保留导出供契约兼容；workbench360 由 Slice C 直接消费 registerInterest。

#### 验证命令与真实退出码（工作树内）

- 逐文件串行验证：每文件改完跑 `pnpm exec vitest run <use-loop-realtime+realtime-ws(+消费者相关)>`，7 次全 exit 0（55/55、37/37、55/55、55/55、55/55、55/55、67/67）
- `pnpm exec eslint <7 消费文件 + loop-fleet-view.test.ts>` → exit 0，0 problems
- `pnpm run check:type` → exit 0（2 successful）
- 全量 `pnpm exec vitest run apps/web-antd` → exit 0，58 文件 473/473 passed（较 Slice B 时新增他 Owner 测试文件，均绿）

#### 未完成与风险

- monitor.vue toggle OFF 退订为有意语义变化（见上），如验收要求完全行为等价可改回 toggle 期间保持订阅（保留 registerInterest 不 unregister），请主协调裁决。
- loop-trend-modal.vue 从无订阅变为实时模式自订阅（流量方向只增），属契约意图内修复，需浏览器端到端确认（Phase 3 非作者验收）。
- 树内 backend/ 与他人前端改动（O05-BE、O02-CORE）非本 Slice 范围，未触碰；本 Slice diff 恰为 7 消费文件 + loop-fleet-view.test.ts。
