# ZL 工业 UI/UX 套件前端页面审查与优化方案

日期：2026-07-06  
分支：`zpdev`  
依据：ZL 致联工业软件 UI/UX 设计套件、CLPM UI/UX v6.0、DESIGN.md v3.0、当前 `frontend/apps/web-antd` 实现。

## 1. 审查结论

当前 CLPM 前端已经具备工业桌面端基础：`ClpmPageToolbar`、`ClpmDataCanvas`、`ClpmKpiStrip`、`PlantNodeTree`、表格主导页面、部分等宽数字和微图趋势已落地。但与新增 ZL 工业 UI/UX 套件相比，仍存在 6 类系统性差距：

| 类别 | 结论 | 影响 |
|---|---|---|
| 视觉语义 | 状态色仍混用 Ant Design 预设色与局部 hex，未完全收敛到 Emerald/Amber/Rose/Blue/Neutral 语义 | 操作员扫视时语义不稳定 |
| 页面架构 | 看板、台账、配置、分析页模式基本存在，但历史示例页和部分详情页仍混用 Card 堆叠 | 信息密度与 F 型阅读流不稳定 |
| 数字排版 | CLPM 核心组件已支持等宽数字，但部分表格列、配置页、统计页仍未强制 tabular nums | 实时刷新时数字跳动，降低可读性 |
| 表格交互 | 表格为主已达成，但行内微图、hover reveal、批量操作、安全操作分级不一致 | 台账/监控/任务页扫视效率不足 |
| 防呆交互 | 普通确认弹窗、Popconfirm 较多；高风险操作缺少“输入确认码/变更原因/影响范围”统一屏障 | 不满足 Poka-Yoke 高危操作要求 |
| 旧页面遗留 | `_core`、`dashboard/analytics`、`dashboard/workspace` 等 vue-vben 示例页仍在代码树中 | 可能污染菜单、样式、后续复用判断 |

## 2. 规范融合基线

| 来源 | 可直接采用 | 需与 CLPM 现行口径融合 |
|---|---|---|
| ZL 设计套件 | Calm UI、Glanceability、Poka-Yoke、语义色、等宽数字、边框优先、触控 44px | ZL 的 Teal/Emerald 状态色需映射到 CLPM `--status-ok`；主操作色以 CLPM 工业蓝为准 |
| CLPM UI/UX v6.0 | 6 模块 + 1 门户、PV 质量码、可信度、状态机、安全边界 | 增补 ZL 的 hover reveal、表格内微图、高风险 typed confirmation |
| DESIGN.md v3.0 | 反 AI Slop、表格优先、token 化、配置确认留痕 | 将 ZL 套件作为“工业 UI 细化准则”，不替代 v6.0 事实来源 |

关键证据：

| 规则 | 证据 |
|---|---|
| Calm UI、Glanceability、Poka-Yoke 是 ZL 核心 | `ZL-MES-UI-Design-Kit/IndustrialDesignSystem.md:12`、`ZL-MES-UI-Design-Kit/IndustrialDesignSystem.md:15` |
| 状态色语义严格限定 | `ZL-MES-UI-Design-Kit/IndustrialDesignSystem.md:17` |
| 动态数字必须等宽 | `ZL-MES-UI-Design-Kit/IndustrialDesignSystem.md:23` |
| 边框优先于阴影，圆角 4-8px | `ZL-MES-UI-Design-Kit/IndustrialDesignSystem.md:27` |
| 高危操作禁止 alert，需自定义确认 | `ZL-MES-UI-Design-Kit/Industrial_UI_Agent_Skill.md:34` |
| CLPM 禁止下写 DCS 参数 | `docs/设计文档/06-UIUX/ui-ux-design-guidelines.md:46` |
| CLPM 颜色必须 token 化 | `docs/设计文档/06-UIUX/ui-ux-design-guidelines.md:111` |
| CLPM 数字、位号、时间戳必须等宽 | `docs/设计文档/06-UIUX/ui-ux-design-guidelines.md:186` |
| CLPM 列表页必须以表格为主 | `DESIGN.md:201` |
| CLPM 可点击元素需满足 44px | `DESIGN.md:197` |

## 3. 页面清单与审查分类

| 模块 | 路由/页面 | 页面类型 | 审查重点 |
|---|---|---|---|
| 工作台 | `/dashboard/workbench` → `frontend/apps/web-antd/src/views/dashboard/workbench.vue` | 总览/看板 | KPI、低效列表、趋势图、实时状态、全局摘要 |
| 回路管理 | `/loop/manage` → `frontend/apps/web-antd/src/views/loop/manage.vue` | 台账/组态 | 工厂树、回路表、Tag 关联、批量配置、危险编辑 |
| 回路管理 | `/tag/list` → `frontend/apps/web-antd/src/views/tag/list.vue` | 台账 | Tag 类型/质量色、删除/解绑确认、批量操作 |
| 回路管理 | `/loop/monitor` → `frontend/apps/web-antd/src/views/loop/monitor.vue` | 监控 | 实时 PV/SP/OP/MODE、行内趋势、列设置、异常状态 |
| 回路管理 | `/loop/aas-sync` → `frontend/apps/web-antd/src/views/loop/aas.vue` | 运维状态 | 同步状态、质量分布、失败重试、状态动效 |
| 回路管理 | `/loop/detail/:id` → `frontend/apps/web-antd/src/views/loop/detail.vue` | 详情分析 | 摘要、趋势、诊断证据、快照明细 |
| 性能评估 | `/metric/dashboard` → `frontend/apps/web-antd/src/views/metric/dashboard.vue` | 看板 | 装置级 KPI、实时自控率、Partial 横幅、低效 Top10 |
| 性能评估 | `/metric/ranking`、`/metric/snapshots` | 台账/排行 | 评分色、INCONCLUSIVE、行内微图、批量过滤 |
| 性能评估 | `/metric/statistics` | 分析 | 图表语义色、数据墨水比、筛选上下文 |
| 性能评估配置 | `/metric/config`、`/metric/weight-config`、`/metric/engine-config`、`/metric/task-strategy` | 配置 | 变更确认、版本留痕、危险操作屏障 |
| 诊断中心 | `/diagnosis/list` | 台账 | 诊断标签、置信度、状态机、主/次操作 |
| 诊断中心 | `/diagnosis/detail/:loopId`、`/diagnosis/waveform` | 详情/波形 | 证据链、质量码线型、图表色、数据血缘 |
| 诊断中心 | `/diagnosis/tracker` | 跟踪台账 | 状态流、A/B 对比、导出、状态更新确认 |
| 诊断中心 | `/diagnosis/statistics` | 分析 | 图表阴影、色彩语义、统计摘要 |
| 回路整定 | `/tuning/workbench`、`/tuning/model`、`/tuning/algorithm`、`/tuning/simulation`、`/tuning/stats` | 实验/配置/分析 | 安全边界、风险区间、参数建议非下写、防呆确认 |
| 评估任务 | `/tasks/list`、`/tasks/:taskId` | 执行台账/详情 | 任务状态、取消/重跑确认、进度可视化 |
| 系统管理 | `/system/users`、`/system/audit`、`/system/permissions`、`/system/reports` | 管理/审计 | 审计化表格、权限矩阵密度、删除/禁用确认 |
| 旧示例页 | `_core/*`、`dashboard/analytics/*`、`dashboard/workspace/*` | 示例/兼容 | 是否仍进入路由或被复用；如无业务价值应隔离 |

## 4. 主要不符合项

### 4.1 状态色与视觉 token 未完全统一

| 编号 | 不符合项 | 证据 | 建议 |
|---|---|---|---|
| C-01 | 多处使用 Ant Design 字符串色和局部 hex，未统一映射到 CLPM/ZL 语义色 | `frontend/apps/web-antd/src/views/loop/manage.vue:173`、`frontend/apps/web-antd/src/views/tag/list.vue:126`、`frontend/apps/web-antd/src/views/metric/grading-threshold.vue:42` | 建立 `industrial-status.ts`：运行/成功=ok，警告/部分=warning，错误/低效=danger，处理中=info，忽略/无数据=neutral |
| C-02 | 示例/遗留页使用高饱和多色图表，不符合 Calm UI | `frontend/apps/web-antd/src/views/dashboard/workspace/index.vue:42`、`frontend/apps/web-antd/src/views/dashboard/analytics/analytics-visits-sales.vue:21` | 下架业务菜单入口；保留时改为开发示例并隔离 |
| C-03 | 部分图表 still 使用阴影或高装饰样式 | `frontend/apps/web-antd/src/views/diagnosis/statistics.vue:161`、`frontend/apps/web-antd/src/views/tuning/stats.vue:332` | 图表去重阴影，仅在异常/选中态使用轻边线或低透明度背景 |

### 4.2 等宽数字覆盖不完整

| 编号 | 不符合项 | 证据 | 建议 |
|---|---|---|---|
| T-01 | `ClpmKpiStrip` 已为主值使用 mono，但页面表格数值列未统一复用 | `frontend/apps/web-antd/src/components/clpm/kpi-strip.vue:258`、`frontend/apps/web-antd/src/views/diagnosis/list.vue:447` | 新增 `ClpmNumeric` 或 `numeric-cell` class，覆盖 score、valid_rate、PV/SP/OP、PID、时间戳 |
| T-02 | 部分详情页已局部使用 tabular nums，但不是全局规则 | `frontend/apps/web-antd/src/views/loop/detail.vue:938`、`frontend/apps/web-antd/src/views/loop/monitor.vue:1390` | 将等宽数字下沉为全局 CSS utility，禁止页面零散实现 |
| T-03 | 时间戳列多处普通文本显示 | `frontend/apps/web-antd/src/views/task/list.vue:757`、`frontend/apps/web-antd/src/views/diagnosis/tracker.vue:160` | 时间戳统一 mono + secondary 色，支持相对时间 + tooltip 原始时间 |

### 4.3 高风险操作防呆不足

| 编号 | 不符合项 | 证据 | 风险 | 建议 |
|---|---|---|---|---|
| P-01 | 删除、取消、重算等高影响操作仍使用 `Popconfirm` | `frontend/apps/web-antd/src/views/tag/list.vue:528`、`frontend/apps/web-antd/src/views/task/list.vue:769`、`frontend/apps/web-antd/src/views/metric/recompute.vue:505` | 容易误触，缺少影响范围与审计原因 | 引入 `ClpmDangerConfirmModal`：显示对象、影响范围、回退提示、输入确认码、变更原因 |
| P-02 | 配置页使用 `Modal.confirm`，但确认语义和审计字段不统一 | `frontend/apps/web-antd/src/views/diagnosis/config.vue:164`、`frontend/apps/web-antd/src/views/tuning/algorithm.vue:257` | 变更留痕不一致 | 配置保存统一走“变更摘要 + 原值/新值 + 备注 + 二次确认” |
| P-03 | 整定模块需要强化“建议不下写 DCS”安全边界 | `docs/设计文档/06-UIUX/ui-ux-design-guidelines.md:46`、`frontend/apps/web-antd/src/router/routes/modules/tuning.ts:3` | 用户可能误解为可自动下发 | 所有参数建议卡片加入“只读建议/人工实施/需留痕”固定标识 |

### 4.4 表格扫视性与微可视化不足

| 编号 | 不符合项 | 证据 | 建议 |
|---|---|---|---|
| G-01 | ZL 要求表格内进度条/微图与 hover reveal；当前仅 KPI strip 和局部图表实现 | `ZL-MES-UI-Design-Kit/IndustrialDesignReference.html:180`、`frontend/apps/web-antd/src/components/clpm/kpi-strip.vue:161` | 为低效排行、任务进度、回路监控、AAS 同步列表加入行内趋势/进度条 |
| G-02 | 操作列常显按钮较多，降低数据墨水比 | `frontend/apps/web-antd/src/views/diagnosis/list.vue:486`、`frontend/apps/web-antd/src/views/task/list.vue:760` | 主操作常显，次操作 hover reveal 或“更多”菜单 |
| G-03 | 表格列设置已有组件，但没有成为所有台账页强制能力 | `frontend/apps/web-antd/src/components/clpm/column-settings.vue` | 台账页统一接入列密度、列显示、固定列、默认排序偏好 |

### 4.5 页面架构与旧页面遗留

| 编号 | 不符合项 | 证据 | 建议 |
|---|---|---|---|
| A-01 | `dashboard/index.vue` 和 vue-vben analytics/workspace 示例页仍存在，可能与 CLPM 工作台事实入口混淆 | `frontend/apps/web-antd/src/views/dashboard/index.vue:7`、`frontend/apps/web-antd/src/router/routes/modules/dashboard.ts:24` | 确认无路由入口后移入 examples 或删除；禁止复用示例色板 |
| A-02 | 统计/详情/任务详情页仍有较多 Ant Design `Card` 堆叠，不完全符合“表格/侧栏/时间线/对比”模式 | `frontend/apps/web-antd/src/views/task/detail.vue:218`、`frontend/apps/web-antd/src/views/diagnosis/statistics.vue:397` | 统一改为 `ClpmDataCanvas` + 摘要条 + 时间线/详情分栏 |
| A-03 | AAS 页面混用 `Card` 与 `ClpmDataCanvas`，状态卡视觉不一致 | `frontend/apps/web-antd/src/views/loop/aas.vue:559`、`frontend/apps/web-antd/src/views/loop/aas.vue:660` | 抽象 `ClpmStatusPanel` 或全部迁移到 DataCanvas/KpiStrip |

### 4.6 触控与交互目标未显性验收

| 编号 | 不符合项 | 证据 | 建议 |
|---|---|---|---|
| I-01 | ZL 与 CLPM 均要求触控目标至少 44px，但现有工具栏/图标按钮缺少统一 min-height/min-width | `ZL-MES-UI-Design-Kit/Industrial_UI_Agent_Skill.md:32`、`DESIGN.md:197` | `ClpmToolbarButton`、表格行操作、图标按钮统一 32px 桌面密度 + 44px 触控模式，可由页面密度切换 |
| I-02 | 自动刷新、实时状态、数据延迟分散在页面逻辑中 | `frontend/apps/web-antd/src/views/dashboard/workbench.vue:98`、`frontend/apps/web-antd/src/views/metric/dashboard.vue:121` | 抽象 `ClpmRealtimeStatus`：在线/延迟/失败/刷新中统一表达 |

## 5. 优化方案

### 5.1 设计目标

| 目标 | 验收标准 |
|---|---|
| 冷静统一 | 页面背景、面板、状态色全部来自 token；无业务页面散落 hex |
| 一眼可读 | KPI、评分、PV/SP/OP、PID、时间戳等全部等宽；关键表格引入微图/进度条 |
| 防误操作 | 高影响操作统一 typed confirmation；配置变更展示影响范围和审计备注 |
| 高密度工业桌面 | 台账页表格优先，次要操作隐藏，详情用抽屉/侧栏/时间线 |
| 安全边界明确 | 整定模块全链路标识“建议输出，不下写 DCS” |

### 5.2 横切组件任务

| ID | 任务 | 范围 | 优先级 |
|---|---|---|---|
| UI-01 | 建立工业语义色映射工具 | `constants` / `composables`，替换页面散落色值 | P0 |
| UI-02 | 新增 `ClpmNumeric` / 全局 `.clpm-num` utility | 数值、时间戳、位号、PID、版本号 | P0 |
| UI-03 | 新增 `ClpmDangerConfirmModal` | 删除、取消、重算、配置保存、整定建议确认 | P0 |
| UI-04 | 新增 `ClpmRealtimeStatus` | 数据延迟、自动刷新、接口失败、在线/离线 | P1 |
| UI-05 | 增强 `ClpmTable` 或表格规范封装 | hover reveal、行内进度、列设置、密度切换 | P1 |
| UI-06 | 建立图表主题 preset | ECharts grid、axis、tooltip、shadow、状态色 | P1 |

### 5.3 页面级改造任务

| ID | 页面组 | 任务 | 优先级 |
|---|---|---|---|
| P0-1 | 回路管理台账/Tag 清单 | 替换删除/解绑 Popconfirm；统一状态色；数值/位号等宽；操作列主次分层 | P0 |
| P0-2 | 任务列表/历史重算 | 取消、重跑、重算统一危险确认；进度列内联 progress；任务 ID/时间 mono | P0 |
| P0-3 | 整定模块 | 全页面加入安全边界条；参数建议区标记经济/警告/过载区间；禁止出现“下发/写入 DCS”语义 | P0 |
| P1-1 | 工作台 + 性能看板 | 统一实时状态组件；KPI 卡数值等宽；低效 Top10 加行内趋势/可信度；减少装饰性 Card | P1 |
| P1-2 | 回路监控 + AAS 同步 | PV/SP/OP/MODE 行内高密度展示；质量码线型/颜色统一；同步状态面板组件化 | P1 |
| P1-3 | 诊断列表 + 异常跟踪 | 状态流色彩重映射；A/B 对比入口主次分层；状态更新使用审计确认 | P1 |
| P2-1 | 统计类页面 | 去图表阴影，统一 ECharts preset；图例与语义色一致；增加空/partial/error 状态 | P2 |
| P2-2 | 系统管理 | 用户禁用/角色调整/报表删除加入 typed confirmation；审计字段等宽 | P2 |
| P2-3 | 旧示例页清理 | 隔离或删除 `_core` 外业务无关 dashboard 示例页，避免样式污染 | P2 |

### 5.4 实施顺序

| 阶段 | 内容 | 产出 |
|---|---|---|
| 第 1 阶段 | 横切 token、数字、危险确认、图表主题 | 组件/工具层 PR，最小页面改动验证 |
| 第 2 阶段 | P0 页面组改造 | 回路管理、任务、整定安全边界达标 |
| 第 3 阶段 | P1 页面组改造 | 看板/监控/诊断主要业务页面达标 |
| 第 4 阶段 | 统计/系统/旧页清理 | 视觉债务收口，建立长期验收脚本 |

## 6. 验收清单

| 检查项 | 验收方式 |
|---|---|
| 无业务页面新增散落 hex | `grep -R "#[0-9a-fA-F]\{6\}" frontend/apps/web-antd/src/views` 人工白名单 |
| 高风险操作不用简单 Popconfirm | 搜索 `Popconfirm`、`Modal.confirm`，逐项分类豁免 |
| 数值等宽覆盖 | 评分、KPI、PV/SP/OP、PID、时间戳视觉检查 + class 检查 |
| 图表无装饰阴影 | 搜索 `shadowBlur`，仅 tooltip/选中态允许 |
| 状态语义一致 | SUCCESS/PARTIAL/INCONCLUSIVE、PENDING/IN_PROGRESS/IMPLEMENTED/IGNORED、GOOD/BAD/UNCERTAIN 快照检查 |
| 整定安全边界 | 所有整定页面可见“不下写 DCS/人工实施/留痕”提示 |
| 质量验证 | `cd frontend && pnpm run check:type`、关键页面截图审查、必要 E2E 补充 |

## 7. 逐页审查强制判定口径

| 等级 | 判定 |
|---|---|
| 通过 | 强制项全部满足；页面类型结构正确；状态、权限、数据质量、可信度、空态完整；无明显 v6.0 冲突 |
| 有条件通过 | 不影响业务安全和状态正确性，但存在建议项缺失、局部密度、层级或响应式不足 |
| 不通过 | 出现空点击、权限错误、状态枚举错误、INCONCLUSIVE 显示 0、PV Bad 断线/置 0、无审计确认、危险操作用浏览器默认确认、暗示下写 DCS、用卡片替代表格等任一严重问题 |
| 需融合设计 | 视觉来自 ZL 工业规范但未映射到 CLPM token、状态机、路由、权限或技术栈，需要先做规范融合后再验收 |

## 8. 强制检查清单补充

| 类别 | 强制项 | 落地要求 |
|---|---|---|
| 产品定位 | 页面必须体现工业治理平台 | 稳重、审计化、任务驱动；首屏优先回答“现在该做什么” |
| 任务优先 | 不允许空点击 | 主按钮或主链接必须触发状态变化、上下文变化或路由变化 |
| 信息架构 | 以 v6.0 与实现契约为准 | 旧 25 页面清单仅作历史来源；正式路由为当前页面事实 |
| 全局壳层 | 保留左导航、顶部状态栏、标题区、主体区、全局摘要 | ZL 无侧栏大屏模式仅可作为独立 HMI/大屏参考 |
| 角色权限 | 菜单和按钮按角色过滤 | 无权限菜单隐藏；无权限按钮隐藏或置灰并提示 |
| 色彩 token | 所有颜色来自 token/CSS 变量 | 不在业务页面散落 hex；ZL 色板只映射到 CLPM token |
| 风险语义 | 状态色严格语义化 | 正常青绿、信息蓝、警告琥珀、危险红、中性灰；不得只靠颜色区分 |
| 数字排版 | 动态数字、位号、Tag、版本号、时间戳、PID 参数等宽 | 使用 `--font-mono`、`tabular-nums` 或统一组件 |
| 表格优先 | 台账、排行、任务、审计以高密度表格为主 | 支持筛选、排序、固定列、批量、空态、Partial 提示 |
| 状态标签 | 标签必须有颜色、文本、图标或语义前缀 | 状态枚举使用 v6.0 大写标准 |
| KPI/评分 | INCONCLUSIVE 不得显示 0 分 | 可信度 E 级显示 `—`，不参与聚合 |
| 可信度 | 评分、KPI、任务结果显示 A/B/C/D/E | tooltip 显示 valid_rate，可展开或跳转数据血缘 |
| PV 质量码 | Bad/Uncertain 不断线、不置 0 | Bad 灰色虚线保留连线；Uncertain 琥珀虚线保留连线 |
| 数据血缘 | 指标、诊断、任务结果可追溯 | 展示采样频率、质量策略、tag_group、valid_rate、策略版本、算法版本 |
| 配置变更 | 保存必须二次确认并留痕 | 弹窗展示变更摘要、新旧值、影响范围、变更说明 |
| 高危操作 | 不用浏览器默认确认承载高危操作 | 使用自定义模态框、确认凭据、影响范围、审计备注 |
| 安全边界 | UI 不得出现“下发参数”“写入 DCS”“自动实施”入口 | 整定只输出建议、证据、风险与回退方案 |
| 空/异常/Partial | 是一等状态 | 说明原因、可用能力、下一步动作和恢复入口 |
| 响应式 | 不简单堆成细长卡片流 | Desktop 保留三栏/双栏，Tablet 折叠次级区，Mobile 步骤式或抽屉式 |
| 无障碍 | 触达、键盘、对比度合格 | 点击目标 ≥44px；拖拽必须有下拉替代；状态不能只靠颜色 |
