# CLPM UI/UX 工业桌面端改造工作计划

**文档状态**：基线工作计划
**当前版本**：v1.0
**发布日期**：2026-06-28
**适用范围**：基于 `CLPM_UIUX_工业桌面端改造方案_v1.0.md` 的第二轮代码改造实施计划
**技术基线**：Vue 3 + TypeScript + vue-vben-admin 5.7 + Ant Design Vue + ECharts
**前置文档**：`CLPM_UIUX_工业桌面端改造方案_v1.0.md`（设计基线）、`implementation-contract.md` v1.0、`ui-ux-design-guidelines.md` v5.1

---

## 0. 文档定位

本文档是 `CLPM_UIUX_工业桌面端改造方案_v1.0.md` 的配套实施工作计划。基于第一轮改造已完成的基线，识别剩余差距，制定分阶段、可验收的代码改造任务清单。

### 0.1 与设计文档关系

| 文档 | 关系 |
|---|---|
| `CLPM_UIUX_工业桌面端改造方案_v1.0.md` | 设计基线，定义"做什么" |
| 本文档 | 实施计划，定义"做多少、什么顺序、怎么验收" |
| `implementation-contract.md` v1.0 | 路由/API/权限事实来源，不得冲突 |
| `ui-ux-design-guidelines.md` v5.1 | 现行 UI/UX 规范 |

### 0.2 本计划不包含

- 不重做系统（保留 v4.0 模块、路由、API）
- 不脱离框架（基于 vue-vben-admin 5.7）
- 不大改后端（接口最小补充原则）
- 不实施 Phase D 增强体验（快捷键/多图联动/中控深色主题等远期项）

---

## 1. 第一轮改造基线总结

第一轮改造已建立组件骨架与部分页面采纳。

### 1.1 已完成项

| 类别 | 已完成内容 | 文件位置 |
|---|---|---|
| 主题配置 | CLPM_INDUSTRIAL_TOKENS、THEME_COLORS、KPI_COLOR_MAP；工业蓝主色 hsl(211 98% 52%)；半深色侧栏；克制圆角 0.25；themeToggle | `apps/web-antd/src/preferences.ts` |
| CLPM 组件 | 5 个组件：PageToolbar、KpiStrip、ObjectSummaryBar、DataCanvas、TagAssociationBadge | `apps/web-antd/src/components/clpm/` |
| 已采纳页面 | workbench、loop/detail、loop/monitor、metric/dashboard、metric/ranking、metric/statistics、metric/config、tuning/workbench、tuning/simulation、diagnosis/detail、diagnosis/tracker（独立页模式） | `apps/web-antd/src/views/` |
| 路由/IA | "系统配置"改为"指标配置"；任务管理已并入性能评估"执行记录"Tab；ConfigTabs 5 Tab 已对齐 | `router/routes/modules/metric.ts` |
| 趋势图统一 | WaveformChart 组件在 loop/monitor Modal + loop/detail 复用 | `components/loop/waveform-chart.vue` |

### 1.2 已采纳 CLPM 组件的页面清单

| 页面 | PageToolbar | KpiStrip | ObjectSummaryBar | DataCanvas | TagAssociationBadge |
|---|:---:|:---:|:---:|:---:|:---:|
| dashboard/workbench | ✓ | ✓ | ✓ | ✓ | — |
| loop/detail | ✓ | ✓ | ✓ | ✓ | ✓ |
| loop/monitor | ✓ | ✓ | ✓ | ✓ | — |
| metric/dashboard | ✓ | ✓ | — | ✓ | — |
| metric/ranking | ✓ | ✓ | ✓ | ✓ | — |
| metric/statistics | ✓ | — | — | ✓ | — |
| metric/config | — | — | — | — | — |
| tuning/workbench | ✓ | ✓ | — | ✓ | — |
| tuning/simulation | ✓ | — | — | ✓ | — |
| diagnosis/detail | ✓ | — | ✓ | ✓ | — |
| diagnosis/tracker（独立页） | ✓ | — | — | ✓ | — |
| **loop/manage** | ✗ | ✗ | ✗ | ✗ | ✗ |
| **diagnosis/list** | ✗ | ✗ | ✗ | ✗ | — |
| **task/list** | ✗ | ✗ | ✗ | ✗ | — |

> 标 ✗ 的页面为第二轮需重点重构的对象。

---

## 2. 核心差距识别

### 2.1 全局组件差距

| 差距 | 影响 | 对应设计章节 |
|---|---|---|
| PageToolbar 缺第 4 区（状态反馈） | 全局组件无法展示刷新中/数据延迟/最近刷新时间 | §5.3、§7.1 |
| KpiStrip 缺 sparkline/点击下钻/delta 方向 | 指标展示不够生动，无法下钻 | §7.2 |
| ObjectSummaryBar 缺主指标突出/dropdown/loading | 主次不分，动作区扩展性不足 | §5.4、§7.2 |
| DataCanvas loading 无 skeleton，partial 无详情入口 | 状态表达不够明确 | §7.4、§11 |
| TagAssociationBadge 已实现但未被 loop/manage 采纳 | 组件沦为孤儿 | §7.3 |
| 工业浅色视觉规范仅 token 常量，未落全局 CSS | 冷灰底/强表头/等宽数字未实际生效 | §9 |

### 2.2 工具栏按钮规范差距（新增）

> 用户补充要求：工具栏不同的按钮最好使用图标表示，并根据不同的功能和状态采用不同的颜色。

| 差距 | 影响 | 对应设计章节 |
|---|---|---|
| 工具栏按钮普遍无图标 | 仅文字按钮，识别慢，专业感不足 | §6.3、§7.1 |
| 按钮无功能色区分 | 刷新/导出/新建/危险操作视觉一致，易误操作 | §4.5、§6.3 |
| 按钮无状态色反馈 | 加载中/禁用/激活态表达弱 | §11 |

### 2.3 页面级差距

| 页面 | 主要差距 | 对应设计章节 |
|---|---|---|
| dashboard/workbench | 缺仪表盘/Bullet/环形图/排行条/异常区/任务状态区/StatusFooter | §8.1 |
| loop/detail | 工具条缺导出/诊断/整定；摘要条缺设备机泵；缺设备信息区/数据质量摘要/StatusFooter；主图占比不达标 | §8.4 |
| loop/monitor | 右侧缺趋势预览/风险标签/下一步动作；缺 StatusFooter | §8.3 |
| metric/dashboard | 缺综合健康仪表盘/Bullet/数据质量环形图/ObjectSummaryBar/StatusFooter | §8.5 |
| metric/config | level-weight 路由缺失；缺任务策略 Tab；未用 PageToolbar；缺配置变更确认弹窗 | §8.5 |
| loop/manage | 完全未采纳 CLPM 组件；缺主页面 Tab；缺配置变更确认；Tag 关联用 7 个 Tag 平铺 | §8.2 |
| diagnosis/list | 完全未采纳 CLPM 组件；缺 Partial 横幅；缺可信度筛选；缺顶部 KpiStrip | §8.7 |
| diagnosis/detail | 未按 65/35 布局；工具条缺加入跟踪/导出；摘要条缺风险等级/处理状态；趋势图未复用 WaveformChart | §8.7 |
| diagnosis/tracker | 抽屉模式未用 CLPM 组件；状态机仅 Tag 无可视化；缺 KpiStrip；状态更新缺审计字段 | §8.7 |
| tuning/workbench | 缺风险提示横幅；KpiStrip 缺风险指标；流程导航用首字母占位非统一图标 | §8.8 |
| tuning/simulation | 仿真图未优先于参数表单；缺风险提示/ObjectSummaryBar/改善退化标识 | §8.8 |
| metric/statistics | 缺 KpiStrip；筛选未入 PageToolbar；缺 Partial 横幅；ECharts 无 empty/error 状态 | §8.5 |
| task/list | 完全未采纳 CLPM 组件；状态机仅 Tag；详情 Drawer 未用 ObjectSummaryBar；新建任务缺配置变更确认 | §8.6 |

### 2.4 交互状态差距

| 差距 | 影响范围 |
|---|---|
| 配置变更确认弹窗（§7.8）普遍缺失 | loop/manage、metric/config、task/list、tracker |
| Partial 警告横幅（§7.9 + §6.3.1）普遍缺失 | diagnosis/list、metric/statistics、task/list |
| 状态机可视化（§8.2）仅 Tag，无流转图/时间线 | tracker、task/list |
| loading/empty/error/partial 四状态覆盖不全 | 多个页面 |

---

## 3. 改造工作计划

### 3.1 总体路线图

```
Phase A2（P0，组件与主题增强，前置依赖）
  ├─ A2.0 工具栏按钮图标与色彩规范
  ├─ A2.1 工业浅色视觉规范落地
  ├─ A2.2 PageToolbar 增强状态反馈区
  ├─ A2.3 KpiStrip 增强
  ├─ A2.4 ObjectSummaryBar 增强
  ├─ A2.5 DataCanvas 增强
  └─ A2.6 TagAssociationBadge 采纳到 loop/manage
       ↓
Phase B2（P1，关键页面深化）
  ├─ B2.1 工作台深化
  ├─ B2.2 回路详情深化
  ├─ B2.3 回路监控深化
  ├─ B2.4 KPI 看板深化
  └─ B2.5 指标配置修复与增强
       ↓
Phase C（P2，全模块一致性）
  ├─ C1 回路管理重构（高优先，完全未采纳）
  ├─ C2 诊断列表重构（高优先，完全未采纳）
  ├─ C3 诊断详情布局调整
  ├─ C4 异常跟踪统一
  ├─ C5 整定工作台风险提示
  ├─ C6 闭环仿真图优先
  ├─ C7 统计分析增强
  └─ C8 任务列表重构（高优先，完全未采纳）
       ↓
Phase D（P3，远期增强，本轮不实施）
```

**关键依赖**：Phase A2 必须先完成，因为 Phase B2/C 依赖增强后的组件能力（sparkline、状态反馈区、skeleton loading、按钮图标规范等）。

**并行机会**：Phase B2 各页面间相互独立，可并行；Phase C 各页面间相互独立，可并行。C1/C2/C8 因完全未采纳 CLPM 组件，建议优先于 C3-C7。

---

### 3.2 Phase A2：主题与组件增强（P0）

#### A2.0 工具栏按钮图标与色彩规范（新增）

- **范围**：定义全局工具栏按钮图标+色彩规范，并在 PageToolbar 及各页面工具条落地
- **目标**：
  - 定义工具栏按钮图标映射表（刷新/导出/导入/新建/编辑/删除/查询/筛选/自动刷新/全屏/更多/返回/下载 PDF/加入跟踪/进入诊断/整定建议等）
  - 定义按钮功能色规范：
    - 主操作（primary）：工业蓝，如新建、查询、执行仿真
    - 刷新/同步（default + icon）：中性灰，如刷新、自动刷新
    - 导出/下载（default + icon + 绿色 hover）：如导出 CSV、下载 PDF
    - 危险操作（danger 红色）：如删除、取消任务
    - 状态切换（dashed/默认）：如视图切换、时间窗
  - 定义按钮状态色规范：
    - loading：spinner + 禁用
    - disabled：灰色 + tooltip 原因
    - active：主色填充（如自动刷新开启时）
  - 在 PageToolbar 组件中提供标准按钮组件或按钮配置项
- **验收标准**：
  - 所有核心页面工具栏按钮均带图标
  - 主操作/刷新/导出/危险操作/状态切换 5 类按钮颜色可区分
  - loading/disabled/active 三态有明确视觉反馈
  - icon-only 按钮有 tooltip 可读标签（对齐 §12.2 可访问性）

#### A2.1 工业浅色主题视觉规范落地（§9）

- **范围**：在 `apps/web-antd/src/` 新增全局样式（或扩展 `@vben/styles` 包），落地 §9 视觉规范
- **目标**：
  - 页面底色：冷灰白 `#f4f7fb`（非纯白）
  - 面板底色：白色或极浅灰
  - 表格：浅冷灰表头背景 + 字重 600 + 行高 40-44px + hover 浅蓝灰 + 选中行左侧 2px 主色线
  - 数字列：右对齐 + 等宽数字（tabular numbers）
  - 面板：1px 明确边框 + 4-6px 圆角 + 默认无阴影
  - 分区标题：32-40px 标题栏，像工业软件区域标题
- **验收标准**：
  - 页面底色为冷灰白而非纯白
  - 表格表头浅冷灰背景 + 字重 600
  - 数值列右对齐 + 等宽字体
  - 选中行左侧 2px 主色线 + 浅蓝底
  - 面板 1px 边框，默认无阴影

#### A2.2 PageToolbar 增强状态反馈区

- **范围**：`apps/web-antd/src/components/clpm/page-toolbar.vue`
- **目标**：
  - 新增第 4 区"状态反馈"（status slot/prop），支持展示刷新中/导出中/数据延迟/最近刷新时间/任务执行中
  - 新增 loading prop
  - 集成 A2.0 按钮图标与色彩规范
- **验收标准**：
  - 工具条 4 区分明：左侧上下文 / 中部控制 / 右侧动作 / 状态反馈
  - loading=true 时状态反馈区显示"刷新中…"
  - 支持自定义 status slot 内容
  - actions 槽按钮支持图标+功能色配置

#### A2.3 KpiStrip 增强

- **范围**：`apps/web-antd/src/components/clpm/kpi-strip.vue`
- **目标**：
  - 支持 sparkline 趋势小图（30px 高）
  - 支持点击下钻（emit `item-click` 事件）
  - delta 方向箭头+颜色（上升绿↑/下降红↓/持平灰→）
  - loading 改为骨架屏
- **验收标准**：
  - item 支持 sparkline 数据，渲染 30px 高趋势小图
  - 点击 item emit `item-click` 事件
  - delta 上升绿色↑/下降红色↓/持平灰色→
  - loading=true 显示骨架屏

#### A2.4 ObjectSummaryBar 增强

- **范围**：`apps/web-antd/src/components/clpm/object-summary-bar.vue`
- **目标**：
  - 支持主指标突出展示（primaryItem prop，大号 24-28px 数值）
  - actions 支持 dropdown"更多操作"模式
  - loading 态骨架屏
- **验收标准**：
  - 支持 primaryItem prop，主指标大号显示
  - actions 支持 dropdown 模式
  - loading=true 显示骨架屏

#### A2.5 DataCanvas 增强

- **范围**：`apps/web-antd/src/components/clpm/data-canvas.vue`
- **目标**：
  - loading 改为骨架屏/spinner（非纯 opacity）
  - partial 增加"查看详情"链接，emit `partial-detail` 事件
  - empty/error 增加图标 + 文案可自定义
  - 支持 partial + error 共存
- **验收标准**：
  - loading=true 显示骨架屏
  - partial 显示"查看详情"链接
  - empty/error 带图标 + 文案可自定义
  - partial 与 error 可同时显示

#### A2.6 TagAssociationBadge 采纳到 loop/manage

- **范围**：`apps/web-antd/src/views/loop/manage.vue`
- **目标**：替换 7 个普通 Tag 平铺为 TagAssociationBadge 组件
- **验收标准**：
  - 回路列表 Tag 关联列显示 `7/7 已关联` 摘要形式
  - 点击打开详情弹窗展示 PV/SP/OP/MODE/PID_P/PID_I/PID_D 槽位

---

### 3.3 Phase B2：关键页面深化改造（P1）

#### B2.1 工作台深化

- **范围**：`apps/web-antd/src/views/dashboard/workbench.vue`
- **目标**：
  - 主区上：综合健康仪表盘（半圆 Gauge）+ 自控率仪表 + 稳定率 Bullet + 数据质量环形图
  - 主区中：KPI 趋势图 + 低效回路 Top10 横向条形图
  - 主区下：待处理异常列表 + 最近评估任务状态
  - 工具条补：自动刷新开关、导出日报按钮（带图标+功能色）
  - 新增 StatusFooter：数据延迟/最近刷新/任务状态/数据质量/可信度
- **验收标准**：
  - 首屏不滚动可见工厂/装置/回路/任务对象
  - KPI 展示使用仪表盘/Bullet/环形图/排行组合，非简单卡片
  - StatusFooter 显示数据延迟、最近刷新、任务状态、数据质量、可信度
  - 工具条含自动刷新开关 + 导出日报按钮（带图标）
- **接口影响**：可能需要"工作台聚合摘要"接口（P2）一次返回 KPI/异常/任务/数据质量

#### B2.2 回路详情深化

- **范围**：`apps/web-antd/src/views/loop/detail.vue`
- **目标**：
  - 工具条补：导出、进入诊断、整定建议、更多（均带图标+功能色）
  - 摘要条补：设备/机泵信息（机泵 P-101）
  - 新增设备/机泵信息区（设备名称/类型/机泵编号/介质/工况/责任单元）
  - 新增数据质量摘要区（Good/Bad/Uncertain 占比 + valid_rate）
  - 主趋势图占比达 45%-55%
  - 新增 StatusFooter
- **验收标准**：
  - 主趋势图占主体面积 45% 以上
  - Tag 关联默认折叠为摘要（已达标，保持）
  - 设备/机泵信息区可见
  - 数据质量摘要区可见
  - StatusFooter 显示数据延迟、可信度
- **接口影响**：需补设备/机泵字段（设备名称/类型/机泵编号/介质/工况/责任单元）

#### B2.3 回路监控深化

- **范围**：`apps/web-antd/src/views/loop/monitor.vue`
- **目标**：
  - 右侧选中回路区：趋势预览（小图）+ KPI 摘要 + 风险标签 + 下一步动作
  - 工具条补：导出（带图标）
  - 新增 StatusFooter
- **验收标准**：
  - 右侧选中回路后显示趋势预览（非 Modal）
  - 风险标签显示诊断结论
  - 下一步动作显示"进入诊断/查看详情/整定建议"
  - StatusFooter 显示数据延迟、最近刷新

#### B2.4 KPI 看板深化

- **范围**：`apps/web-antd/src/views/metric/dashboard.vue`
- **目标**：
  - 新增综合健康仪表盘（半圆 Gauge）
  - 新增核心指标 Bullet Chart（稳定率/好值率/快速率）
  - 新增数据质量环形图（Good/Bad/Uncertain 占比）
  - 新增 ObjectSummaryBar
  - 新增 StatusFooter
  - 工具条补：导出（带图标）
- **验收标准**：
  - KPI 总览使用仪表盘 + Bullet + 环形图组合
  - ObjectSummaryBar 显示当前对象摘要
  - StatusFooter 显示数据质量、可信度

#### B2.5 性能评估指标配置修复与增强

- **范围**：`apps/web-antd/src/router/routes/modules/metric.ts`、`apps/web-antd/src/components/metric/config-tabs.vue`、各配置页
- **目标**：
  - 修复 level-weight 路由缺失（ConfigTabs 有 tab 无路由）
  - 评估是否合并"类型权重+级别权重"为"权重配置"单 Tab（设计要求 5 Tab：指标定义/权重配置/引擎规则/任务策略/执行记录）
  - 新增"任务策略"Tab（标准评估任务/自动触发/重试/调度策略）
  - 配置页采纳 PageToolbar
  - 配置变更确认弹窗（§7.8）：变更摘要 + 影响范围 + 变更说明输入框
- **验收标准**：
  - level-weight 路由可访问
  - ConfigTabs 5 Tab 全部可点击且路由对齐
  - 任务策略 Tab 存在
  - 配置页使用 PageToolbar
  - 保存配置前展示变更摘要弹窗，含影响范围与变更说明
- **接口影响**：可能需要"配置变更预览"接口（P1）返回影响范围

---

### 3.4 Phase C：全模块一致性（P2）

#### C1 回路管理重构（高优先，完全未采纳 CLPM 组件）

- **范围**：`apps/web-antd/src/views/loop/manage.vue`
- **目标**：
  - 主页面 Tab：工厂结构 / 回路台账 / Tag 关联 / 批量配置
  - 采纳 PageToolbar、DataCanvas、TagAssociationBadge
  - 配置变更确认弹窗（保存前展示变更摘要 + 影响范围 + 变更说明）
  - 批量配置显示影响回路数
  - 删除操作独立确认（不与普通操作混排）
- **验收标准**：
  - 主页面 4 Tab 可切换
  - 全页面使用 CLPM 组件
  - 保存配置前弹出变更确认弹窗
  - 批量配置显示影响回路数
- **接口影响**：可能需要"批量操作预校验"接口（P1）

#### C2 诊断列表重构（高优先，完全未采纳 CLPM 组件）

- **范围**：`apps/web-antd/src/views/diagnosis/list.vue`
- **目标**：
  - 采纳 PageToolbar、DataCanvas、KpiStrip
  - 顶部 KpiStrip：待处理数/处理中数/近 7 天新增
  - Partial 警告横幅（INCONCLUSIVE 回路数与影响范围）
  - 新增可信度等级筛选（A/B/C/D/E）
- **验收标准**：
  - 全页面使用 CLPM 组件
  - KpiStrip 顶部摘要可见
  - Partial 横幅显示 INCONCLUSIVE 回路数
  - 可信度筛选可用

#### C3 诊断详情布局调整

- **范围**：`apps/web-antd/src/views/diagnosis/detail.vue`
- **目标**：
  - 主区左 65%：趋势图 + 异常标记 + PV-OP 散点
  - 主区右 35%：诊断结论 + 推荐动作 + 跟踪状态
  - 工具条补：加入跟踪、导出（带图标）
  - 摘要条补：风险等级、处理状态
  - 趋势图复用 WaveformChart（替换手写 ECharts）
- **验收标准**：
  - 主区左右 65/35 分栏
  - 工具条含加入跟踪按钮
  - 摘要条含风险等级、处理状态
  - 趋势图使用 WaveformChart 组件

#### C4 异常跟踪统一

- **范围**：`apps/web-antd/src/views/diagnosis/tracker.vue`
- **目标**：
  - 抽屉模式采纳 CLPM 组件（与独立页模式一致）
  - 状态机可视化（时间线或状态流转图）
  - KpiStrip 各状态计数（待处理/处理中/已实施/已忽略）
  - 状态更新 Modal 增加审计字段（变更说明）
- **验收标准**：
  - 抽屉模式与独立页模式样式一致
  - 状态流转可视化可见
  - KpiStrip 显示各状态计数
  - 状态更新需填写变更说明

#### C5 整定工作台风险提示

- **范围**：`apps/web-antd/src/views/tuning/workbench.vue`
- **目标**：
  - 常驻风险提示横幅（平台只输出建议、不直接修改 DCS）
  - KpiStrip 增加风险任务数/超阈值任务数
  - 流程导航卡用统一 SVG 图标（替代首字母占位）
- **验收标准**：
  - 风险提示横幅常驻可见
  - KpiStrip 含风险指标
  - 流程导航卡使用统一图标

#### C6 闭环仿真图优先

- **范围**：`apps/web-antd/src/views/tuning/simulation.vue`
- **目标**：
  - 仿真图优先于参数表单（参数表单折叠/侧边）
  - 新增风险提示区（风险等级 + 回退方案 + 适用边界）
  - 新增 ObjectSummaryBar（当前 PID vs 推荐 PID 对比）
  - 改善/退化语义标识（图标 + 文本）
- **验收标准**：
  - 仿真图占据主空间，参数表单为辅
  - 风险提示区可见
  - ObjectSummaryBar 显示 PID 对比
  - 改善/退化有图标+文本标识
- **接口影响**：需补整定风险字段（风险等级/回退方案/适用边界）

#### C7 统计分析增强

- **范围**：`apps/web-antd/src/views/metric/statistics.vue`
- **目标**：
  - 顶部 KpiStrip（平均评分/低效回路数/同比环比）
  - 筛选移入 PageToolbar controls 槽
  - Partial 警告横幅
  - ECharts 支持 empty/error 状态（走 DataCanvas 状态）
- **验收标准**：
  - KpiStrip 顶部摘要可见
  - 筛选在工具条中
  - Partial 横幅可见
  - 图表无数据时走 DataCanvas empty 状态

#### C8 任务列表重构（高优先，完全未采纳 CLPM 组件）

- **范围**：`apps/web-antd/src/views/task/list.vue`
- **目标**：
  - 采纳 PageToolbar、DataCanvas、KpiStrip、ObjectSummaryBar
  - KpiStrip 替代 3 个 Statistic Card
  - 状态机可视化（PENDING/RUNNING/SUCCESS/FAILED/CANCELLED 流转）
  - 详情 Drawer 用 ObjectSummaryBar
  - 新建任务 Drawer 增加配置变更确认
- **验收标准**：
  - 全页面使用 CLPM 组件
  - 状态流转可视化可见
  - 新建任务前展示变更确认弹窗

---

### 3.5 Phase D：增强体验（P3，远期，本轮不实施）

| 项 | 说明 |
|---|---|
| 快捷键 | 工具条、表格、抽屉、弹窗快捷操作 |
| 保存常用列和时间窗 | 用户偏好持久化 |
| 多图联动 | 趋势图/散点图/排行联动 |
| 中控深色主题 | CLPM Control Dark 预设 |

---

## 4. 接口影响评估汇总

### 4.1 需补字段（P1）

| 场景 | 字段需求 | 影响页面 |
|---|---|---|
| 回路详情设备/机泵 | 设备名称、设备类型、机泵编号、介质、工况、责任单元 | loop/detail |
| 整定风险 | 风险等级、回退方案、适用边界 | tuning/simulation |
| 配置确认 | 变更摘要、影响范围、操作备注 | loop/manage、metric/config、task/list |
| 任务执行摘要 | 最近任务状态、失败原因、当前阶段、预计完成时间 | workbench、metric/dashboard |

### 4.2 需补字段（P2）

| 场景 | 字段需求 | 影响页面 |
|---|---|---|
| 数据质量状态栏 | Good/Bad/Uncertain 占比、valid_rate、数据延迟 | StatusFooter 全局 |
| KPI Strip 完整 | 综合评分、自控率、稳定率、好值率、快速率、可信度、趋势 | 全局 KpiStrip |

### 4.3 可能新增聚合接口

| 接口 | 优先级 | 用途 |
|---|---|---|
| 工作台聚合摘要 | P2 | KPI/异常/任务/数据质量一次返回 |
| 回路详情聚合 | P1/P2 | 回路对象+设备信息+KPI+Tag 摘要+趋势摘要 |
| 配置变更预览 | P1 | 保存前返回影响范围和风险 |
| 批量操作预校验 | P1 | 批量配置前返回冲突和影响对象 |
| 任务执行摘要 | P2 | 性能评估页内展示最近任务状态 |

### 4.4 接口改动原则（对齐设计文档 §10.1）

| 原则 | 说明 |
|---|---|
| 兼容优先 | 不破坏已有字段和调用方式 |
| 聚合优先 | 一屏展示需要时可补摘要字段或聚合接口 |
| 补字段优先 | 能加字段就不新建接口 |
| 读接口优先 | 先补展示数据，少动写接口 |
| 写接口慎重 | 仅配置确认、备注、审计涉及写接口补充 |

---

## 5. 与设计文档验收清单对齐

| 设计文档验收项 | 本计划覆盖 |
|---|---|
| 默认界面具备冷灰画布、强表格、明确边框、工业蓝主色 | A2.1 |
| 保留 vben 主题切换、偏好配置、标签页和布局能力 | 已达标（preferences.ts 保留） |
| 核心页面使用 PageToolbar 和 ObjectSummaryBar | B2.1-B2.5、C1-C8 |
| 工作台和 KPI 看板使用仪表盘、Bullet、趋势、排行等丰富组件 | B2.1、B2.4 |
| 回路详情机泵/设备信息、KPI、趋势图为主，Tag 关联默认折叠 | B2.2（Tag 已达标） |
| 性能评估"系统配置"改为"指标配置"，Tab 聚合配置和执行记录 | 已达标 + B2.5 修复 level-weight + 任务策略 |
| 任务管理并入性能评估执行体系 | 已达标（路由已并） |
| loading/empty/error/partial/success/permission 都有用户可见表达 | A2.5 + 各页面 |
| 明确纯前端、补字段、新增接口三类影响 | §4 接口影响评估 |
| 整定模块不出现 DCS 参数下写暗示 | C5、C6 风险提示 |
| 工具栏按钮使用图标+功能色+状态色 | A2.0（新增） |

---

## 6. 实施风险与缓解

| 风险 | 缓解措施 |
|---|---|
| Phase A2 组件增强改动面大，可能影响已采纳页面 | 增强采用向后兼容方式，新增 prop 默认值不破坏现有用法；增强完成后回归测试已采纳页面 |
| level-weight 路由缺失可能是历史遗留 | 先确认 level-weight.vue 是否存在；若页面已删，则从 ConfigTabs 移除该 Tab |
| 接口补充依赖后端排期 | 前端先用 mock 数据落地 UI，接口就绪后切换；接口未就绪前用现有字段重组展示 |
| 工具栏按钮图标规范需统一图标库 | 优先使用 lucide 图标库（vben 已集成），不引入新依赖 |
| 配置变更确认弹窗需后端"配置变更预览"接口 | 前端可先做本地变更摘要（diff 前后字段），接口就绪后接入影响范围 |

---

## 7. 计划维护

- 本计划随实施进度更新，每完成一个 Phase 在文档末尾追加"实施记录"
- 若设计文档 `CLPM_UIUX_工业桌面端改造方案_v1.0.md` 升级，本计划同步校准
- 若发现新的差距或新增需求，追加到对应 Phase，并更新版本号

---

## 8. 实施记录

（待实施后追加）
