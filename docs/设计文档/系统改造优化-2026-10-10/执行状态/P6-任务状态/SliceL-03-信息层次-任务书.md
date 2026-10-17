# Slice L 任务书：P6-03 工业信息层次（03/04-b，PL-03/04/05 展示层）

Owner：子代理L ｜ 工作树 `/Users/zhangping/DEV/clpm-p6-kcode-20261014`（只在此树）｜ `F/`=`frontend/apps/web-antd/src/`、`B/`=`backend/app/`

## 必读（按节）
1. `03-阶段任务计划.md` §9 P6-03 段（工作与验收逐行）
2. `执行状态/P6-01a-契约与文件Owner-20261014.md` **§8 PL-03/PL-04/PL-05 行 + A20 行**
3. 现状文件（03§9 指定）：`F/views/cockpit/wb-comps/EvalTrendChart.vue`、`F/views/metric/pid-dashboard.vue`、cockpit 趋势/雷达组件、wb360 header/AssessSection、task progress 组件
4. 数据源语义：P1/P3 成果（N/E、充分统计量、F 参考口径）——`B/services/node_performance.py`、`B/services/workbench_assessment.py` 输出字段（先核实现字段再动手）

## 范围（独占）
- 上述现状展示文件 + `F/__tests__/p6-03-info-hierarchy.test.ts`（新建）
- **禁止**：改评分算子/后端；改 wb360 其他文件（仅 header/AssessSection）；改 use-drill/RT 冻结；commit/push

## 契约要点（03§9 P6-03 验收行原文为准）
- 首屏综合评分/自控率/平稳率三核心指标 + **常驻 N/E**（可评分比例）+ 原因 + 截至时间；更多指标保留
- F 参考均分**紧邻**参考数量/动态证据不足/动态估计覆盖/来源同显，参考统计不冒充正式 F/P（PL-04）
- 真实时间轴与缺口：**缺口不连线伪连续**；0–100 或明确动态范围；低分 10/40 与 90 真实绘制；质量/绩效/行动条件区分；正确"无效数据"术语；数据截止/覆盖/排队/运行/失败/取消状态
- 深浅双主题、键盘/焦点可用、1920×1080 工程桌面无关键操作遮挡（A20）
- 数据不足≠低绩效；不确定值显示范围与原因；同状态颜色/名称跨页一致

## 验证
- vitest：N/E 常驻与来源字段绑定、F 参考同显块、缺口不插值（图表 series 断言）、低分/高分真实值渲染、主题变量切换、键盘焦点路径
- check:type、eslint scoped exit 0；相关存量回归
- 浏览器截图证据（双主题/分辨率/键盘）由主协调验收阶段统一补，本 Slice 作者自验+单测

## 完成判据
03§9 P6-03 验收行+§8 PL-03/04/05、A20 逐条对照（实现位置/证据/未验原因）；移交记录写本文件末尾标"待验收"。水位>50% 即 WIP 停手写断点。

## 移交记录

状态：**待验收**（2026-10-14，子代理L，工作树 `/Users/zhangping/DEV/clpm-p6-kcode-20261014`，分支 codex/p6-kcode-20261014，未 commit/push）

### 改动文件（独占范围内 5 改 1 新建；后端/算子/use-drill/RT/wb360 其他文件均未触碰）

| 文件 | 改动 |
|---|---|
| `F/views/cockpit/wb-comps/EvalTrendChart.vue` | 图表层重写：Y 轴固定 0–100（废除 78–92 局部窗钳平低分）；x=真实时间线性映射（废索引均布）；缺口不连线——null 点断开 + 相邻间距 >1.5×最小桶距判定数据缺口断开 + 孤立单点画圆点；上一周期按自身时间分位映射叠加同样分段；色码全量 `--ck-*`（主线 accent-2/目标 grade-excellent/恶化 grade-poor/刻度 text-3，SVG 经 style 绑定）；头部新增截至（snapshot_at）；图表区 role=button tabindex=0 + Enter/Space 下钻 + focus-visible 环；开关补 type="button" |
| `F/views/metric/pid-dashboard.vue` | 首屏 7 仪表盘下新增常驻 coverage strip：可评分 N/E（绑定 roster.scorableLoopCount/eligibleLoopCount，非 evaluatedLoops）+ 可评分比例（E=0→「无参评回路」区别于无完整 P）+ 截至（最新可知名册桶）+ 不可评分原因（六类状态账求和：数据不足/计算失败/无快照/已取消/排队中）；F 参考同显块（均分由 fastRateReference 分子/权重和充分统计量跨节点重算 + 参考数量/动态证据充分/动态证据不足/无数据/动态估计覆盖/来源 + 「参考」徽标 + 「参考口径不计入正式 F/P」+ 缺统计节点数显式标注）；全无名册→「暂无名册数据（历史快照不可追溯，断点显示不猜算）」；趋势图三折线显式 connectNulls:false。新增 CSS 全语义变量（hsl(var(--*))） |
| `F/views/cockpit/components/trend-panel.vue` | 自动投用率折线显式 connectNulls:false（契约显式化，ECharts 默认本为 false，行为不变） |
| `F/views/loop/workbench360/components/sections/AssessSection.vue` | 评估任务终态区分：CANCELLED→「上次评估已取消」(t-gray)，FAILED→「上次评估失败」(t-danger)，取消不再渲染成失败；陈腐脚注「仅整点来源，手动来源待 G2」（与实际能力矛盾）更正为「来源行内标注：自动 / 回算 / 手动」。LoopHeader 核查无违约项，未改 |
| `F/views/task/detail.vue` | CANCELLED 进度条 status=normal（原为 active，取消≠运行中） |
| `F/__tests__/p6-03-info-hierarchy.test.ts` | 新建 17 例：N/E 常驻与来源字段绑定、F 参考同显块、缺口不插值（分段 + series 断言）、低分 10/40 与 90 真实渲染、0–100 刻度、prev 不伪造、--ck-* 主题变量、键盘焦点路径、截至时间、取消≠失败、CANCELLED 进度态、无名册诚实态 |

### 验证命令与真实退出码

| 命令 | 结果 |
|---|---|
| `cd frontend && pnpm --filter @vben/web-antd exec vitest run src/__tests__/p6-03-info-hierarchy.test.ts` | **exit 0**，17/17 passed |
| 同上 7 个相关存量文件（cockpit-drill-honesty / wb360-assess-latest / task-list / score-sparkline-null-gap / trend-sampling / cockpit-assessment-rank-tree / loop-detail-drawer-kpi-scale） | **exit 0**，62/62 passed |
| `cd frontend/apps/web-antd && pnpm exec vitest run`（全量 CI 门禁） | 635/636 passed；**唯一失败 `p6-01ba-nav-groups.test.ts`**——路由模块 `config.ts` 新增 `/config/eng-templates` 属 P6-01b Owner 在途未提交改动（git status 证实 routes/* 均非本 Slice 触碰），与本 Slice 文件无关 |
| `cd frontend && pnpm run check:type` | **exit 0**，2 successful |
| `pnpm exec eslint <上述 6 个文件>`（先 `--fix` 修 5 处可自动修复项后复跑） | **exit 0**；fix 后已复跑 vitest+check:type 确认仍全 0 |

### 完成判据逐条对照（03§9 P6-03 验收行 + §8 PL-03/04/05、A20）

| 判据 | 实现位置/证据 | 状态 |
|---|---|---|
| 低分 10/40 与 90 真实绘制 | EvalTrendChart 固定 0–100；单测 y(90)<y(40)<y(10) 且三点互异不钳平（旧实现 10/40 同被钳到 78 基线） | 已实现+单测 |
| 缺口不中插线伪连续 | EvalTrendChart 时间映射 + 1.5×桶距断开 + null 断开 + 孤立点圆点；pid-dashboard/trend-panel connectNulls:false；单测分段/series 断言 | 已实现+单测 |
| 同状态颜色/名称跨页一致 | 全部走驾驶舱 `--ck-*` 既有五档色阶与 vben 语义变量，未新增状态色/状态名；术语沿用既有「无法判定/未计算/Unknown」 | 已实现 |
| 数据不足≠低绩效 | 不可评分原因单列（数据不足等五类）；无名册显式「断点显示不猜算」；E=0「无参评回路」与无完整 P 区分 | 已实现+单测 |
| 数据截止/覆盖/排队/运行/失败/取消 | 截至=EvalTrendChart snapshot_at + pid-dashboard 名册桶截至；覆盖=可评分比例+动态估计覆盖；取消≠失败（AssessSection）+ CANCELLED 进度 normal（task/detail） | 已实现+单测 |
| 首屏三核心+常驻 N/E+原因+截至 | pid-dashboard coverage strip（见改动表） | 已实现+单测 |
| F 参考紧邻数量/不足/覆盖/来源，不冒充正式 F/P（PL-04） | fastref-block：均分/参考数量/动态证据充分/动态证据不足/无数据/动态估计覆盖/来源同显 + 「参考」徽标 + 「不计入正式 F/P」 | 已实现+单测 |
| 充分统计量聚合、全缺 null（PL-03 展示层消费） | F 均分由分子/权重和重算（不取节点均分再平均）；全缺→mean null→块不渲染；算子语义属后端 P1/P3 未动 | 已实现+单测 |
| 三核心及 N/E 常驻、更多指标/主题/单位（PL-05） | 好值率/仪表故障率/参评回路保留；分为分、率/比例为 %；pid-dashboard vben 变量体系、EvalTrendChart `--ck-*` | 已实现 |
| 正确"无效数据"术语 | 现状文件全仓检索无"无效数据"误用（"无效值"仅用于实时数值语义，正确）；矛盾陈腐文案已更正 | 已实现+单测（脚注断言） |
| 深浅双主题、键盘/焦点、1920×1080 无遮挡（A20） | 结构层：EvalTrendChart 色值全 `--ck-*`、键盘下钻+焦点环、viewBox 尺寸未变；coverage strip flex-wrap 弹性换行。**未验原因**：实际截图/键盘走查证据按任务书验证节约定由主协调验收阶段统一补 | 结构已实现，截图待主协调 |

### 已知边界与登记

- EvalTrendChart 缺口判定限制：全序列仅 2 点时无法估计桶距，按实连线（代码注释已登记）。
- cockpit-radar 核查现状已合规（0–100 轴、null 维度显式「无数据」、主题色解析），未改；kpi-band 可点卡片键盘化属 cockpit 总览 Owner 范围，未越界。
- 单测基建发现（供其他 Owner 参考）：vitest 对同一工厂 mock 模块的并发动态 import 存在竞态（pid-dashboard 的 `await import('#/api/dashboard')` 两个并发调用一个解析到 mock、另一个挂起或返回真实模块，已实测复现）；本测试文件对动态 import 的 API 模块一律静态引入真实模块后 vi.spyOn 替换出口函数，文件头有注释登记。
- 工作树含其他 Owner 在途改动（02b 的 pid-dashboard 深链、01b 路由等），本 Slice 仅在其上叠加 P6-03 块；唯一全量单测失败项属 01b 范围（见验证表）。

### G-V1 返修段（2026-10-15，收口报告 G-V1 节）

状态：**待复验**

- 缺陷：`EvalTrendChart.vue` 原 `parseTs(s)=new Date(s+'Z')` 对 tz-aware `snapshot_at`（G5 库 timestamptz，API 实返 `+00:00` 后缀）生成 Invalid Date → 头部「截至」静默不渲染；单测此前仅覆盖 naive 形态未抓获。
- 修复（验收方建议口径落地）：`parseTs` 复用项目标准 `normalizeUtcTimestamp`（`F/utils/format.ts:51-54`，tz-aware 原样/naive 补 Z 双兼容），一行归一+注释登记；未引入新依赖、未自造解析。非法输入仍走既有 `Number.isFinite` 守卫显式不渲染。改动仅 `EvalTrendChart.vue`（import + parseTs 两行级）+ 对应测试文件，红线内。
- 补单测 2 例（p6-03-info-hierarchy.test.ts，EvalTrendChart 套件）：
  1. tz-aware 三形态（`+00:00`、`+08:00`、`Z`，另含 G5 实返的微秒+偏移形态）→ 截至渲染且文本与 naive UTC 参照逐字一致（同一时刻）；
  2. naive 回归不破（原「截至时间」用例保持）+ 非法值 `not-a-date` → 截至不渲染且图本体正常不崩溃。
- 复验命令与真实退出码：
  - `vitest run src/__tests__/p6-03-info-hierarchy.test.ts` → **exit 0，19/19**（含 2 新例）；
  - 相关存量 7 文件（cockpit-drill-honesty / wb360-assess-latest / task-list / score-sparkline-null-gap / trend-sampling / cockpit-assessment-rank-tree / loop-detail-drawer-kpi-scale）→ **exit 0，62/62**；
  - `pnpm run check:type` → **exit 0**（2 successful）；
  - `pnpm exec eslint <EvalTrendChart.vue + 测试文件>` → **exit 0**。
- 浏览器实证（b2 截图「截至」真实渲染）属主协调复验范围，本段仅作者自验+单测；未 commit/push。
