# Slice G 任务书：P6-06 我的工作首页与个人接续（O06-NAV）

Owner：子代理G ｜ 工作树 `/Users/zhangping/DEV/clpm-p6-kcode-20261014`（只在此树）｜ `F/`=`frontend/apps/web-antd/src/`

## 必读（按节）
1. `执行状态/P6-01a-契约与文件Owner-20261014.md` **§2 表 IAR-D1/D2/D4 + §2.1 全文 + §6 O06-NAV 行 + §8 A01–A08/A14–A16/A19/A20 行**
2. 已冻结后端契约（树内源码即事实）：`backend/app/api/v1/endpoints/personal_workspace.py`（/me 六端点）、`personal_work.py`（/me/work）、`loop_investigation.py`（claim/handoff 动作）；409 携 currentRevision/currentGeneration 的刷新重试协议
3. Slice C/F 冻结：`workbench-context.ts`（wb360QueryFromContext/WorkbenchContext）、`wb-comps/use-drill.ts` 构造点
4. `03-个人收藏与工作接续数据设计.md`（缓存/迁移节按需）

## 范围（独占）
- 拟建：`F/views/my-work/index.vue`（可拆子组件于同目录）、`F/api/personal-work.ts`
- 修改：`F/composables/use-clpm-preferences.ts`（匿名 localStorage→服务端持久+浏览器缓存双制，退出/换账号/撤权清理）、`F/store/auth.ts`（默认首页/账号切换钩子，最小改）、`F/router/routes/modules/{loop-workbench.ts,cockpit.ts}`（我的工作入口注册）
- **禁止**：改 workbench360/wb-comps/RT 冻结文件/后端；改其他导航模块文件；commit/push
- 后端默认首页由主协调整合（不在你范围，报告写明所需值）

## 契约要点
- 四视图：分配给我/系统建议/我的关注/最近继续；三分区互斥（等待→今日计划→现在可行动）；空待办≠健康（不输出健康断言）；收藏不产生责任
- 详情复用原业务操作：investigation claim/handoff 按钮直连对应 API（409 刷新重试）；关注/偏好/最近继续走 /me 端点（expectedRevision/requestId/generation 协议完整实现；断网显示未保存不造离线成功）
- 入口携带 loopId+证据（recordId/精确窗，经 Slice F 构造点）跳 wb360 single；返回保留清单位置（from=fullPath 恢复筛选/分页）
- 匿名 clpm-preferences 显式一次性导入（用户确认后逐 key PUT，服务端校验生效；不自动导入；不建离线写队列）
- 撤权对象显示"有不可访问对象"计数不显示名称；来源失败分区如实 ERROR 不填 0（消费 readStatus/sourceStatuses）
- 缓存 key 至少含部署地址+稳定用户 ID+scope/权限版本；退出/换账号/撤权清理

## 验证
- vitest：四视图聚合渲染、分区互斥、409 重试流、断网未保存态、撤权计数不泄密、来源 ERROR 显式、缓存隔离（双 userId）、入口深链 query 完整性
- check:type、eslint scoped、全量 vitest 回归 exit 0

## 完成判据
01a §8 A01–A08 相关判据逐条对照（可单测层部分）；移交记录写本文件末尾标"待验收"。水位>50% 即 WIP 停手写断点。


## 移交记录

状态：**待验收**（2026-10-14，子代理G，工作树 `/Users/zhangping/DEV/clpm-p6-kcode-20261014`）

### 交付文件（均在 `frontend/apps/web-antd/src/`）

| 文件 | 变更 | 内容 |
|---|---|---|
| `api/personal-work.ts` | 新建 | GET /me/work、/me 六端点（loop-follows/workspace-preferences/work-resume 读写删清）、investigation get/claim/handoff；`extractRevisionConflict`（409 携 currentRevision/currentGeneration/stage/ownerId 提取）、`newRequestId`；预期冲突请求带 skipErrorMessage 由调用方处理 |
| `views/my-work/index.vue` | 新建 | 我的工作页：四视图 Tabs + 分配给我三分区（现在可行动/今日计划/等待条件）+ FUTURE/CLOSED 显式筛选 + 截至时间/计数（total=null→"计数未知"不填 0）+ 来源 ERROR/撤权计数 Alert + 旧匿名偏好显式导入条 + 设为默认首页（未保存/冲突徽标）+ 移交弹窗（稳定 sys_user.id） |
| `views/my-work/use-my-work.ts` | 新建 | 数据层：统一查询/分区纯函数/claim·handoff 409 刷新重试（仍可操作才以新版本重试一次，否则如实中止）/unfollow 设定语义/resume 删除·清空 generation 协议/recordLoopResumeQuietly（打开回路时记录最近继续，失败不阻塞不冒充）/buildWorkbenchTarget（经 wb360DrillQuery 构造点，from=fullPath） |
| `views/my-work/work-item-list.vue` | 新建 | 工作项清单展示组件（目标/回路装置/来源/理由/负责人/阶段期限/等待条件/证据时间/下一步；SPONSOR 不出现可执行入口，无 wb360 权限降级纯文本） |
| `composables/use-clpm-preferences.ts` | 重写 | 服务端持久（default_home/filter_presets/sort_presets 白名单 key，CAS+requestId，409 刷新不静默覆盖→conflict 态）+ 浏览器缓存双制（key=clpm-prefs:v2:{origin}:{userId}:{权限散列}）；`noteClpmAccountLogin`/`handleClpmAccountLogout`/`clearAccountScopedCaches`（退出/换账号/撤权清理）；匿名 clpm-preferences 显式一次性导入（detect/import/dismiss，逐 key PUT 服务端校验生效后移除旧键，不自动）；usePagePreference 签名兼容既有消费方 |
| `store/auth.ts` | 最小改 | authLogin 成功登记稳定用户 ID（换账号清缓存，动态 import 防初始化环）；logout 清理账户缓存+模块状态。**ROLE_DEFAULT_HOME 未改**（见"待主协调整合"） |
| `router/routes/modules/cockpit.ts` | 修改 | 注册 `/my-work`（name=MyWork，order=-1 位于驾驶舱前，title=我的工作，authority=五角色，permCode=`page:my-work.main`，标准 BasicLayout）；loop-workbench.ts 未动 |
| `__tests__/my-work.test.ts` | 新建 | 16 例：四视图聚合渲染/分区互斥/claim·handoff 409 重试流/撤权计数不泄密/来源 ERROR 显式不填 0/整请求失败显式/入口深链 query 完整性（含 resolveWb360Route 往返）/SPONSOR 无可执行入口/断网未保存徽标 |
| `__tests__/clpm-preferences.test.ts` | 新建 | 8 例：断网 unsaved→重试 saved/409 不静默覆盖/双 userId 缓存隔离/撤权 key 变化/换账号+退出清理/匿名旧值不自动导入/显式导入逐 key PUT/导入失败旧键保留 |

### 验证（真实退出码）

| 命令（工作树内） | 退出码 | 结果 |
|---|---|---|
| `cd frontend && pnpm run check:type` | **0** | turbo 2 successful（@vben/web-antd vue-tsc 通过） |
| `cd frontend && pnpm exec eslint apps/web-antd/src/api/personal-work.ts apps/web-antd/src/composables/use-clpm-preferences.ts apps/web-antd/src/views/my-work/ apps/web-antd/src/store/auth.ts apps/web-antd/src/router/routes/modules/cockpit.ts apps/web-antd/src/__tests__/my-work.test.ts apps/web-antd/src/__tests__/clpm-preferences.test.ts` | **0** | 0 errors（初报 30 已全部修复） |
| `cd frontend/apps/web-antd && pnpm exec vitest run src/__tests__/my-work.test.ts src/__tests__/clpm-preferences.test.ts` | **0** | 2 files / 24 tests 全过 |
| `cd frontend/apps/web-antd && pnpm exec vitest run`（全量回归） | **1** | 61 files：58 passed / 3 failed；521 tests：494 passed / **27 failed** |
| 基线对照：本 slice 三个改动文件全部 `git checkout HEAD --` 还原后跑同 3 个失败文件 | 1 | **同样 27 failed**（loop-confidence-content-error-states 12、loop-performance-detail-error-states 13、loop-performance-detail-semantic 2；jsdom `window.matchMedia is not a function` 源自 antd responsiveObserve）——**预存失败，非本 slice 引入**；还原后已恢复本 slice 文件并复跑确认 24/24 过 |
| 直接消费方回归：use-table-density/use-saved-view/loop-fleet-view/role-default-home/routes-perm-code/routes-authority/store 七文件 | 0 | 67 tests 全过 |

### 01a §8 A01–A08 对照（可单测层部分）

- A01/A02（分区/期限/计数）：单测层覆盖——分组互斥单项唯一、三分区计数与明细对账、FUTURE/CLOSED 仅经 partition 显式查询；真实责任/日期时区为后端口径（05 已验范围），本层不重复宣称。
- A03（稳定身份/换账号）：缓存双 userId 隔离、换账号/退出清理、权限散列撤权隔离（单测过）；DB 对账属 05。
- A04/A05（并发认领/移交）：claim/handoff 409 携 currentRevision 刷新重试流（重试成功/他人已认领中止/负责人变更中止，单测过）；唯一索引去重属 05 已验。
- A06（关注不产生责任）：取消关注设定语义+文案明示"不影响已分派工作与回路本身"；墓碑/迟到复活防护属 05 已验。
- A07/A08（来源失败/撤权/完整性）：sourceStatuses ERROR 显式、total=null 计数未知不填 0、健康来源数据仍可见、inaccessibleCount 只显示计数、nextCursor 加载更多（单测过）；跨页对账与禁浏览器全量兜底属 02b/G6。

### 待主协调（O-INTEGRATE）整合项

1. **后端默认首页（IAR-D2）**：所需值——`IC_ENGINEER/PE_ENGINEER/EXPERT → /my-work`；`ADMIN/SPONSOR → /cockpit` 不变。落点：backend auth ROLE_DEFAULT_HOME（用户个人 default_home 偏好优先于角色默认——"优先采用个人已保存且仍有权限的首页"）；前端 `store/auth.ts` ROLE_DEFAULT_HOME 现保持全角色 /cockpit（2026-10-05 裁决测试 role-default-home.test.ts 仍绿），待后端下发后由主协调同步前端映射与该测试。
2. **权限目录种子**：`page:my-work` 组（order -1，无 module 约束，同 page:cockpit 口径）+ `page:my-work.main` 页（五角色种子均含；SPONSOR 页内无可执行入口由前端角色显隐+后端每次查询/操作重校验双重保证）。未下发前 P4-02 页面码过滤会移除入口（符合既有口径）。
3. **wb360 内记录最近继续**：当前最近继续仅由"从我的页打开回路"写入（workbench360 冻结文件本 slice 不可改）；wb360 宿主内打开/切换回路时的 PUT /me/work-resume 接线建议交 O02-CORE 后续（协议已就绪：generation+expectedRevision+requestId，409 刷新重试一次后放弃）。
4. **filter_presets 载荷深度**：05 冻结的通用安全校验 JSON 深度 ≤4，嵌套 filters 对象/数组无法直存；本 slice 采用逐页平铺 `{pageKey:[{id,name,page,createdAt,filters}]}`、filters 以 JSON 字符串携带（读取反序列化，单测锁定形态）。如需服务端可校验 filters 内容，建议后续将 filter_presets 深度放宽至 6（本 slice 客户端形态兼容，无需改）。

### 红线自查

未改 workbench360/wb-comps/RT 冻结文件/后端/其他导航文件（git status 核对：本 slice 仅上表 9 个文件）；空待办未输出健康断言；收藏不产生责任（文案+语义）；撤权只显示计数；来源失败如实 ERROR 不填 0；未 commit/push；未跑 repo 全局 format（eslint --fix 仅本 slice 文件）；预存 27 例失败已基线对照非本 slice 引入，未代为修复（属 metric 组件其他 Owner 范围）。


### 返修段（2026-10-14 二批非作者验收 F-G1/F-G2）

状态：**待复验**（同工作树 `/Users/zhangping/DEV/clpm-p6-kcode-20261014`）

#### 改动

**F-G1（P2，必修——偏好同步失败闸断 mount）**

- `composables/use-clpm-preferences.ts`：`syncPreferencesFromServer()` 改为**永不 reject**——同步失败（网络/404/5xx）catch 后登记新增 `serverSyncFailed` ref（`useWorkspacePreferenceState()` 暴露，下次成功自动复位；`resetClpmPreferenceState` 同步复位）。根源消除：usePagePreference 内 `void syncPreferencesFromServer()` 自动同步也不再产生未捕获拒绝。
- `views/my-work/index.vue`：onMounted 注释固化该契约；页头新增显式提示 `<span data-testid="pref-sync-failed">个人偏好暂不可用（不影响工作清单）</span>`（`serverSyncFailed` 驱动；失败不伪造已同步、不静默）。

**F-G2（P3，裁决=补齐消费链）**

- `composables/use-clpm-preferences.ts`：新增 `fetchDefaultHomePreference()`（直接 GET /me/workspace-preferences，不经模块同步态——登录时序上模块尚未同步；**永不 reject**，失败/无偏好/非法值 → null）+ `isValidHomePath()`（服务端校验同口径站内路径，另排除 '/' 防根路径回环）；`getDefaultHomePreference()` 复用同一形态校验。
- `store/auth.ts`：新增 `resolveUserHomePath(role, backendDefaultHome)`——个人 default_home 偏好（合法路径，isValidHomePath 双端校验）优先于角色映射；任何失败回退 `resolveHomePath`，**不阻断登录**。authLogin 与 fetchUserInfo 均改用它（`homePath: await resolveUserHomePath(...)`）。**时序方案**：登录令牌与用户信息就绪后、`router.push` 前完成偏好读取——无"先跳角色首页再替换"的闪烁；越权路径由路由守卫按既有口径兜底（与书签/旧链接一致，未改后端）。原"注释超发"（偏好优先无实现）现与行为一致；顺带修正"兜底 /workbench"陈旧注释为 /cockpit。
- 测试：`__tests__/my-work.test.ts` +2（偏好 reject 时 /me/work 仍发起+显式失败态+真实清单渲染；恢复后提示消失）、`__tests__/clpm-preferences.test.ts` +5（同步失败不 reject/恢复、fetchDefaultHomePreference 合法/无偏好/非法值×5/失败、isValidHomePath 边界）、`__tests__/role-default-home.test.ts` +4（偏好优先/无偏好回退/非法值回退/失败回退）、`__tests__/store.test.ts` +2（登录偏好优先落地 /cockpit；偏好失败回退 /my-work 登录不阻断）。

#### 验证（真实退出码，工作树内）

| 命令 | 退出码 | 结果 |
|---|---|---|
| `cd frontend/apps/web-antd && pnpm exec vitest run src/__tests__/my-work.test.ts src/__tests__/clpm-preferences.test.ts src/__tests__/role-default-home.test.ts src/__tests__/store.test.ts` | **0** | 4 files / **50 tests 全过**（my-work 18、clpm-preferences 13、role-default-home 9、store 10） |
| `cd frontend && pnpm run check:type` | **0** | turbo 2 successful（@vben/web-antd vue-tsc） |
| `cd frontend && pnpm exec eslint apps/web-antd/src/api/personal-work.ts apps/web-antd/src/composables/use-clpm-preferences.ts apps/web-antd/src/views/my-work/ apps/web-antd/src/store/auth.ts apps/web-antd/src/router/routes/modules/cockpit.ts apps/web-antd/src/__tests__/{my-work,clpm-preferences,role-default-home,store}.test.ts` | **0** | 0 errors |
| `cd frontend/apps/web-antd && pnpm exec vitest run`（全量回归） | **0** | **61 files / 534 tests 全过**（首批移交时 27 例预存失败已由对应 Owner 在共享树修复，本轮全绿） |

#### 红线自查

未改 workbench360/wb-comps/RT 冻结文件/后端/其他页面（git status 核对：本轮改动仅 use-clpm-preferences.ts、store/auth.ts、views/my-work/index.vue + 三个测试文件）；未改后端（F-G2 只消费已实现 GET /me/workspace-preferences）；未 commit/push；未跑 repo 全局 format。
