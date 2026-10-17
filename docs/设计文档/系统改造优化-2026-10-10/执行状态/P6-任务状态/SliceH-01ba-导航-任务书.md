# Slice H 任务书：P6-01b-a 五组导航/术语/驾驶舱工作区出口

Owner：子代理H2 ｜ 工作树 `/Users/zhangping/DEV/clpm-p6-kcode-20261014`（只在此树）｜ `F/`=`frontend/apps/web-antd/src/`

## 必读（按节）
1. `执行状态/P6-01a-契约与文件Owner-20261014.md` **§2 表 IAR-D1/D2/D3/D6/D8 + §8 A13–A16/A20 行**
2. `03-阶段任务计划.md` §9 P6-01 段（`docs/设计文档/系统改造优化-2026-10-10/03-阶段任务计划.md`）
3. 现状：`F/router/routes/modules/`（全部模块）、`F/views/cockpit/index.vue` 与六页签结构、Slice G 已注册的 `/my-work`（cockpit.ts 模块内）、`F/store/auth.ts` resolveHomePath/resolveUserHomePath
4. 根 AGENTS.md 导航顺序口径（驾驶舱0-工作台1-…-系统管理9）

## 范围（独占）
- 修改：`F/router/routes/modules/`（五组导航归组/命名/顺序）、`F/locales/` 相关菜单语言包（术语）、`F/views/cockpit/`（仅"进入工作区"出口按钮与其携带上下文，六页签结构不动）、`F/views/my-work/`（术语对齐，最小改）
- **禁止**：改 workbench360/wb-comps/use-drill.ts（F 冻结构造点，出口跳转复用它）/RT 冻结/后端/其他模块页面；改权限目录（后端整合已完成）；commit/push

## 契约要点
- 五组导航落地（IAR-D1）：全局观察（驾驶舱）/回路作业（工作台+监控+评估+诊断+整定+处置）/个人责任（我的工作）/统计分析（报告）/系统（配置+系统管理）——组名/顺序按已采纳口径，我的工作入口保留（order=-1 现状）
- 术语（IAR-D3）：回路工作台（非"工作台360"等内部名）/系统建议/我的关注/计算任务——菜单与页头一致；内部代号不出现在用户可见文案
- 角色首页已落地（G+整合：IC/PE/EXPERT→/my-work，ADMIN/SPONSOR→/cockpit）——本 Slice 验证导航与首页一致无死链，不重做
- 驾驶舱"进入工作区"出口（IAR-D6/D8）：六页签头区统一出口按钮，经 use-drill 构造点携当前页签上下文（节点/回路/精确窗/profile 视页签而定）跳 wb360 对应 mode/section；SPONSOR 等无 wb360 权限时按 canOpenWorkbench360 降级纯文本
- 观察角色（SPONSOR）导航全部可达且只读可完成（不显示不可执行入口）；经理只读同口径
- 原专业入口在权限范围内仍可达（A15/A16）；旧书签/报告链接不因归组改路径（只改归组与命名，不改既有 path）

## 验证
- vitest：五组归组/命名快照、术语断言（菜单文案无内部代号）、出口按钮 query 构造（经冻结构造点）、SPONSOR 降级、既有 path 未变回归
- check:type、eslint scoped、全量 scoped 相关测试 exit 0

## 完成判据
IAR-D1/D3/D6/D8 单测层判据逐条对照；移交记录写本文件末尾标"待验收"。水位>50% 即 WIP 停手写断点。


## 移交记录

状态：**待验收**（2026-10-14，子代理H2，工作树 `/Users/zhangping/DEV/clpm-p6-kcode-20261014`）

### 交付文件（均在 `frontend/apps/web-antd/src/`，F/ 前缀略）

| 文件 | 变更 | 内容 |
|---|---|---|
| `router/routes/nav-groups.ts` | 新建 | 五组导航登记（IAR-D1）：NAV_GROUPS（个人责任/全局观察/回路作业/统计分析/系统，菜单出现顺序）+ NavGroupKey + NAV_GROUP_LABELS；注明组标题可视化依赖布局层（超出独占范围）、order/path 不动 |
| `router/routes/modules/cockpit.ts` | 修改 | MyWork meta.navGroup='personal'、CockpitOverview='observe' + 组注释（含 Slice G 已注册的 /my-work 不动） |
| `router/routes/modules/loop-workbench.ts` | 修改 | title「工作台」→「回路工作台」（IAR-D3，URL 不变），navGroup='loop-ops'，头注释同步（内部代号 wb360/工作台360 不上界面） |
| `router/routes/modules/monitor.ts` | 修改 | Monitor navGroup='loop-ops'；MonitorAttention title「关注队列」→「系统建议」（IAR-D3）；头注释术语同步 |
| `router/routes/modules/assess.ts` | 修改 | Assess navGroup='loop-ops'；AssessTasks title「评估任务」→「评估计算任务」（计算任务=系统计算） |
| `router/routes/modules/diagnosis.ts` | 修改 | Diagnosis navGroup='loop-ops'；DiagnosisTasks title「诊断任务」→「诊断计算任务」 |
| `router/routes/modules/tuning.ts` | 修改 | Tuning navGroup='loop-ops' |
| `router/routes/modules/handling.ts` | 修改 | Handling navGroup='loop-ops'（处置显示名见"待主协调"第 4 条） |
| `router/routes/modules/reports.ts` | 修改 | Reports navGroup='analytics' |
| `router/routes/modules/config.ts` | 修改 | Config navGroup='system' |
| `router/routes/modules/system.ts` | 修改 | System navGroup='system' |
| `views/cockpit/utils/workzone-exit.ts` | 新建 | 「进入工作区」出口目标纯函数（IAR-D6/D8）：经 use-drill 冻结构造点 wb360DrillQuery；页签→剖面映射（诊断/整定/处置，其余默认 assess 不显式携带）；preciseCockpitWindow 精确窗；节点/回路仅 /cockpit/loops 页签携带（selected→single，node→all+scope）；from 返回来源 |
| `views/cockpit/components/cockpit-header.vue` | 修改 | 六页签头区 ck-actions 新增「进入工作区」统一出口（管理后台按钮之前，描边样式区分渐变后台钮）；canOpenWorkbench360 门禁，SPONSOR/空角色降级纯文本（data-testid=workzone-exit-plain，不造死链）；六页签结构不动 |
| `views/my-work/work-item-list.vue` | 修改 | 术语最小对齐：「无工作台入口权限」→「无回路工作台入口权限」（IAR-D3） |
| `__tests__/my-work.test.ts` | 修改 | 同步上一条断言字符串（Slice G 既有测试其余不动） |
| `__tests__/p6-01ba-nav-groups.test.ts` | 新建 | 32 例：五组登记组名顺序 / 11 项可见一级菜单归组·命名·order 快照 / navGroup 有效性与组间连续 / 新术语生效 / 旧称+内部代号（360 系）不上可见菜单 / 出口构造 5 例（single 直达、all+scope、剖面映射、精确窗等长换算、非回路页签不携带）含 resolveWb360Route 往返 / 头区按钮 4 例（SPONSOR·空角色降级、ADMIN 诊断页签跳转、IC 回路页签 single）/ 14 模块既有 path 逐模块回归 / 角色首页无死链 |

### 验证（真实退出码，工作树内）

| 命令 | 退出码 | 结果 |
|---|---|---|
| `cd frontend/apps/web-antd && pnpm exec vitest run src/__tests__/p6-01ba-nav-groups.test.ts` | **0** | 32/32 过 |
| `cd frontend/apps/web-antd && pnpm exec vitest run src/__tests__/{my-work,routes-authority,routes-perm-code,role-default-home,store,p6-02b-drill-context}.test.ts` | **0** | 6 files / 73 tests 全过（直接消费方回归） |
| `cd frontend/apps/web-antd && pnpm exec vitest run`（全量回归） | **0** | **63 files / 571 tests 全过** |
| `cd frontend && pnpm run check:type` | **0** | turbo 2 successful（@vben/web-antd vue-tsc） |
| `cd frontend && pnpm exec eslint apps/web-antd/src/router/routes/nav-groups.ts apps/web-antd/src/router/routes/modules/ apps/web-antd/src/views/cockpit/utils/workzone-exit.ts apps/web-antd/src/views/cockpit/components/cockpit-header.vue apps/web-antd/src/views/my-work/work-item-list.vue apps/web-antd/src/__tests__/p6-01ba-nav-groups.test.ts apps/web-antd/src/__tests__/my-work.test.ts` | **0** | 0 errors（eslint --fix 仅用于本 slice 新建测试文件） |

### IAR 对照（单测层判据）

- **IAR-D1**：五组落地为一级菜单 meta.navGroup + 顺序（我的工作 -1 保留现状，组间连续快照锁定）；未改任何 path/order 数值/authority/permCode（旧书签回归 14 模块逐条锁定）；SPONSOR 导航可达性未收窄（config/system authority 未动，出口降级纯文本）；原专业入口权限内仍可达（无权限目录改动）。
- **IAR-D3**：回路工作台/系统建议/我的关注/计算任务——菜单与标签页头（同源 meta.title）一致；可见菜单无 360 系内部代号；我的关注/系统建议在 Slice G 我的工作中已对齐（本 slice 断言回归）。
- **IAR-D6/D8**：驾驶舱六页签头区统一「进入工作区」出口，经冻结构造点携当前页签上下文（剖面/精确窗/回路页签节点·回路/from）跳 wb360；SPONSOR 等按 canOpenWorkbench360 降级纯文本；管理后台入口保留（任务书口径为"加"出口，未移除既有入口）。
- **角色首页**：G+整合现状不重做；本 slice 断言 ROLE_DEFAULT_HOME 五落点均为已知路由（无死链）。

### 待主协调/后续项

1. **F/locales 无菜单语言包可改**：现行菜单标题为路由 meta.title 中文字面量（generateMenus 不经 i18n；locales/langs 仅 demos/page.json 喂 vben 演示页），术语已落在 meta.title；如需多语言菜单另立事项。
2. **侧边栏分组标题渲染**：@vben layouts/menu-ui 无菜单分组能力、`F/layouts/basic.vue` 为 O02-RT 冻结——五组以 navGroup 元数据+顺序落地（00-IA「首先是导航分组，不新增空白中转页」）；组标题可视化需布局 Owner 后续消费 nav-groups.ts。
3. **页内标题残留（红线文件，不可本 slice 改）**：`views/monitor/attention.vue`（关注队列）、`views/metric/tasks.vue`（评估任务）、`views/diagnosis/tasks.vue`（诊断任务）、`views/handling/workbench.vue`（诊断建议/处置任务/处置工单）、`views/cockpit/wb-comps/DgSummaryBand.vue`（已入关注队列）为其他模块页面/冻结文件，菜单已先行改名，页内页头对齐需对应 Owner 后续（标签页头与菜单一致不受影响——同源 meta.title）。
4. **处置显示名（建议审核/待执行/执行与验证）未改**：00-IA §3.3 命名规则含此三项，但任务书 IAR-D3 枚举范围为四术语，且处置页内标题在红线文件（改名即菜单↔页头新不一致）——请主协调裁决是否另立分片。
5. **总览页签排名选中节点不携带**：overview 的 RankSelection 为页内瞬态（切页重挂载即复位），出口仅携带回路页签持久化选中态（store+URL 恢复口径）；如需装置上下文出口，需先持久化总览选中态（建议交驾驶舱 Owner）。
6. `router/routes/nav-groups.ts` 为新文件（任务书"修改 modules/"字面范围外、无其他 Owner 冲突），供 modules 与测试共引，特此声明。

### 红线自查

未改 workbench360/wb-comps（use-drill 仅只读导入构造点）/RT 冻结/后端/其他模块页面（git status 本 slice 增量=上表 16 文件）；未改既有路由 path/authority/permCode/order 数值；未 commit/push；未跑 repo 全局 format（eslint --fix 仅本 slice 新测试文件）；SPONSOR 降级为纯文本非死链按钮。
