# Slice N：PL 对账与四岗位走查报告（P6-03/04-c 验证收口）

- 执行者：子代理N ｜ 日期：2026-10-14 ｜ 工作树：`/Users/zhangping/DEV/clpm-p6-kcode-20261014`（分支 `codex/p6-kcode-20261014`，未 commit/push）
- 判据来源：`03-阶段任务计划.md` §9 P6-02 装置增补/P6-04 验收 G6 段；`执行状态/P6-01a-契约与文件Owner-20261014.md` §8 PL-01/PL-02/PL-06 行 + A15/A16/A19/A20 行；`工程师任务导向IA改造-2026-10-11/04-装置性能核查与增强建议.md` PL-01–06
- **走查性质声明**：无合格岗位参与者，本报告全部为**代理走查**（种子账号 admin/ic_engineer/expert/sponsor，密码统一 admin123，见主目录 README §5），不称用户验收
- **环境分层**（与五批验收同层）：前端 dev 15666=**工作树**（含全部 P6 前端改动）；后端 17101=**G5 follow-up 树**（`/private/tmp/clpm-g5-followup-20261014`，**不含** P6 后端）——`/me/work`、`/me/work-resume`、`/me/workspace-preferences` 均 404、`page:my-work`/`page:config.eng-templates` 页面码缺失、整定详情四身份字段不下发，涉及点位一律记**环境缺省**，不伪造
- 证据目录：`/tmp/p6-sliceN-evidence/`（probe.mjs/probe2/3/8 + probe4–7 探针、probe*-report.json、api-diagnosis.txt、vitest-run.txt、eslint.txt、28 张截图，全部可复跑）

---

## 1. 单测交付（输出要求第 1 条）

`frontend/apps/web-antd/src/__tests__/p6-pl-reconciliation.test.ts`（新建，本 Slice 唯一新增代码资产）：

| 复跑 | 结果 |
|---|---|
| `vitest run p6-pl-reconciliation.test.ts` | **exit 0，15/15 passed**（vitest-run.txt） |
| `eslint` 该文件 | **exit 0，0 problems**（eslint.txt） |
| `pnpm run check:type` | **exit 0**（2 successful） |
| 关联套件回归（p6-02b + p6-03 + cockpit-drill-honesty + wb360-route-adapter + my-work + cockpit-assessment-rank-tree） | **exit 0，97/97 passed** |

覆盖结构：Part A PL-01 scope 链 4 例；Part B PL-02 精确窗往返/单项清除/等长比较 7 例；Part C PL-06 三端对账 4 例（含 G-N1 缺口登记 1 例）。与既有单测不重复：02b 单点行为（F）、Adapter 优先级矩阵（C）、信息层次（L）已由对应测试覆盖，本文件只做**跨端对账**。

---

## 2. PL-01：装置→单元→回路稳定 scope 链

| 环节 | 结论 | 证据 |
|---|---|---|
| 装置/单元行 → 节点回路清单（稳定 nodeId 下推 + 范围回显） | **单测 PASS；浏览器链断于 G-N2（如实登记）** | 单测 A1/A2：unit 行点击 → `getRankingApi(plantNodeId='101')`、description 回显「范围：节点「常压单元」（nodeId=101）」、装置/单元两级 id 不串。浏览器（admin）：行点击 → 弹窗打开且范围回显正确（`范围：节点「PERF-T-AREA5」（nodeId=1279311030）`，a2 截图），但清单加载 **500**——G-N2（见 §5），弹窗呈诚实失败态「加载失败…请重试」不伪装空数据（IA-01 行为正确） |
| 无稳定 nodeId 行不回退全厂 | **PASS** | 单测 A4：`nodeLoopsDrillTarget(id=null)=null` + 行渲染禁用 title「缺少稳定节点 ID…不回退全厂口径」 |
| 清单行 → 回路详情 → wb360（loopId 稳定传递） | **单测 PASS；浏览器经旁路 PASS** | 单测 A1：`onListRowClick` → `loopDetail.loopId='loop-001'`。浏览器经驾驶舱回路页卡片墙（同一 `loop-detail-panel` 发送路径）：卡片 → 态二详情「工作台」按钮（a3-1）→ wb360（a3-2） |
| wb360 消费还原（loopId+精确窗+scope+from） | **PASS** | 浏览器 URL 实证：`mode=single&loopId=feed9230-…&tsStart=2026-10-13T13:28:58.116Z&tsEnd=2026-10-14T13:28:58.116Z&tz=Asia/Shanghai&nodeId=35af8328-…&from=/cockpit/loops?node=…%26loop=…`（probe3-report）；chip 回显「窗口 10-13 21:28~10-14 21:28 · Asia/Shanghai」「范围节点 35af8328-…」（a3-2 截图）；状态栏「趋势 自定义 10-13 21:28~10-14 21:28 · 1762 点 · monitor 自定义起止」=精确窗驱动趋势查询（消费实证） |

注：旁路链 nodeId 为 **UUID**（cockpit/loops 左树 id 空间），与 EvalRankTable 行 int id 不同空间——这正是 G-N2 的成因（§5）。

## 3. PL-02：精确窗/时区/指标/profile/证据 往返 + 单项清除 + 等长比较

| 环节 | 结论 | 证据 |
|---|---|---|
| 跨日非整日精确窗双程不漂移（含毫秒） | **PASS** | 单测 B1：`2026-10-10T16:30:00.500Z→2026-10-12T04:45:00.250Z` 经 query→context→query→context 双程 `wb360ContextKey` 恒等、tz 原值 |
| 时区/指标/节点/fitness/from 跨页同窗同口径 | **PASS** | 单测 B2：同一精确窗喂 indicator-analysis 解析→回写全键恒等（customWindow 激活不写 window 枚举=禁近似滚动窗替换） |
| 发送→消费→回显（浏览器） | **PASS** | a3-2 截图+probe3-report chipEcho 两行 |
| 单项清除（清窗保留 scope/from；互不影响） | **PASS（单测+浏览器双通道）** | 单测 B3（真实 Adapter mount）：清窗后 ts*/tz/section 消失、nodeId/profile/record 三元组/from 保留；再清 scope 其余不动。浏览器：窗口 chip × 后 chip 消失、「范围节点」chip 保留、趋势回退 24H 预设（a3-3 vs a3-2），URL ts*/tz 消失 from/loopId 保留（probe3-report.afterClear 全 true） |
| 返回恢复 | **PASS（单测+浏览器）** | 单测 B3：`return` → push(from)；B7：my-work 深链 returnRef=`/my-work?view=ASSIGNED`。浏览器：goBack → `/cockpit/loops?node=…&loop=…` 原选择现场恢复（a3-4）；IC embed=1 精简模式返回条 → 回 `/my-work`（c2→c3） |
| 等长比较口径 | **PASS** | 单测 B5：跨日非整日窗 `equalLengthPrevRange` 时长恒等、prev.end==current.start（无重叠无缺口）；indicator-analysis 加载代码 `prevRange = equalLengthPrevRange(...)`（:425-427）真实消费 |
| profile/证据版本（同回路异版本=异会话） | **PASS** | 单测 B6：snapshotDrillQuery 三元组+profile 还原；异 recordVersion → 异 contextKey。报告端 profile=聚合算法版本随深链（C3：`nav.query.profile='algo-v2'`） |

## 4. PL-06：页面/驾驶舱/报告导出 同范围同窗同版本数字对账（单测）

同一 mock 数据集（node 聚合 82.34/91.23/71.56；TIC-101=66.00（v2 快照 [60,72] 均值 + 1 条 v1 旧版本 99.99 必须排除）、TIC-102=55.44）三端渲染断言：

| 端 | 结论 | 证据 |
|---|---|---|
| 页面（pid-dashboard，1dp 显示精度） | **PASS** | C1 mount：bullet 卡 '82.3'/'91.2'/'71.6'、明细树 '91.2%'/'71.6%'、TOP 榜 '66.0'/'81.0%'；显示值==源值 1dp 舍入（无漂移） |
| 驾驶舱（EvalRankTable+EvalSummary+drill 清单，2dp） | **PASS** | C2：装置行 '82.34'、gauge '82.34'、参评 '3/8'；drill 清单 scoreText '66.00'/'55.44'；footer 计数「全厂合计 3/8 参评 · 不可评 5 条」 |
| 报告+导出（reports/performance，2dp） | **PASS** | C3 mount：综合表 '82.34'/'91.23%'/'71.56%'/'良好'；回路表 '66.00'/'81.00%' 且 **不含 99.99**（R08 版本锁定聚合真实生效）；CSV 综合行含 '82.34'/'91.23'+'algo-v2'；CSV 回路行 '66.00'+`algo-v2（不含旧版本 1 条）`（跨版本窗口不静默丢构成）；回路行深链携报表日窗+`profile=algo-v2`+from，Adapter 可原样还原 |
| 三端互对 | **PASS（除 G-N1 已登记）** | cockpit scoreText '66.00' === 报告页 '66.00' === CSV '66.00'；pid 1dp 回解析==源值舍入。**唯一不一致=G-N1**（§5）：同数据集驾驶舱指标清单 metricText 显示 '8100.00%'（缺口登记用例钉住现状） |

浏览器抽值（G5 真实数据，窗口口径各自显示）：cockpit 性能页 gauge 与装置行评分同屏一致（a1）；pid-dashboard 三指标卡与报告页同窗值未显矛盾；G-N1 对应的真实数据旁证：ranking API `steadyRate=65.42`（0–100 口径，api-diagnosis.txt 与 probe-report.env.sampleLoop）。

## 5. 真实缺口清单（先报主协调裁决，本 Slice 均未修）

> 任务书红线「发现真实缺口先报主协调裁决再动」——以下 3 项均附证据、修复建议与 Owner 建议，**等裁决**。其中 G-N2 为 P6-02b 新链引入，建议优先裁决。

### G-N2（建议级→严重，P6-02b 新链）装置排行→节点回路清单 500：int/UUID 节点身份空间不一致

- **现象**：驾驶舱性能页装置/单元行点击 → 节点回路清单弹窗「加载失败」（a2 截图）；API 实证 `GET /performance/ranking?plantNodeId=1279311030`（行 id）→ **HTTP 500**（asyncpg `invalid UUID '1279311030'`）；`plantNodeId=1001` 同 500；`plantNodeId=<plant_node UUID>` → 200 且子树展开正确（EO 装置 UUID → 精馏塔单元回路 3 条）（api-diagnosis.txt）。
- **根因**：`plant_node.id`=UUID PK、`source_node_id`=BIGINT 双身份（01_schema.sql:73+）；`/workbench/assessment` 排名行 `id`=int source_node_id（前端类型 `AssessmentRankRow.id: number`），而 `/performance/ranking` 的 `plantNodeId` 过滤 `loops.plant_node_id`（UUID 列）。02b 发送端 `nodeLoopsDrillTarget` 把行 int id 直接下推 → 崩溃。驾驶舱回路页旁路链用左树 UUID（`nodeId=35af8328-…`）故正常——同一产品内两条链两个 id 空间。
- **修复建议**：前端——EvalRankTable 已持有 `nodeTree` prop（节点同构含 `id` int + `nodeId` UUID），行点击时经树把 int id 解析为 UUID 再构造 drill 目标（未匹配行维持现「缺少稳定节点 ID」禁用口径）；后端加固——ranking 端点对非 UUID 输入返回 400（参数校验）而非 500 traceback。修复后单测 A1 断言（已注记 G-N2）同步改为 UUID 值。
- **Owner 建议**：前端 02b（主）；后端 performance 端点健壮性（辅）。

### G-N3（建议级→一般，预存后端口径）参评 N>M：plant 行 loop_count 与 total_loops 口径不一致

- **现象**：浏览器实证装置行参评「1763/1030」、页脚「全厂合计 10312/1030 参评 · 不可评 0 条」（a1/e1 截图、probe-report）；API 实证 `summary.participation={evaluated:430,total:1030}` 而六装置行 `loop_count` 合 10312、`join='1763/1030'`（api-diagnosis.txt）。
- **根因**：`workbench_assessment.py` CAL-06（2026-10-10）已把分母改台账参评三条件计数（`_count_evaluable_loops`=1030），但 plant 行分子 `loop_count` 仍取预计算窗口行——代码自注该口径为「窗口内已评回路·小时累计」（:564 注释）。分子（回路·小时）/分母（回路）必然 N>M。
- **修复建议**：plant 行参评分子改同口径（子树台账计数或窗口内 distinct 已评回路数），或 join/页脚显式标注分子为回路·小时口径。
- **Owner 建议**：后端 workbench_assessment / P1-02 Owner。预存（P1-02 期引入），G5 千回路数据下矛盾被放大。

### G-N1（建议级→一般，预存 66419bbd）驾驶舱指标清单率值 ×100 显示

- **现象**：同数据集下驾驶舱指标清单（热力格点击→指标清单弹窗）`metricText='8100.00%'`，与 pid-dashboard '81.0%'、reports '81.00%'、驾驶舱热力格原值矛盾；实测 ranking API 率字段为 0–100（`steadyRate=65.42`/`goodValueRate=100.0`）。
- **根因**：`use-drill.ts pct()` 按 0–1 假设计 `(v*100).toFixed(2)%`，66419bbd（2026-10-05 驾驶舱整合）引入，非 P6 改动；同函数 `fittingScore*100`（0–1 口径）为正确用法不受影响。
- **修复建议**：`pct` 不再 ×100（率字段 0–100 直接 `toFixed(2)%`）；修复后单测 G-N1 登记用例断言改为 '81.00%'/'63.25%'。
- **Owner 建议**：前端驾驶舱/use-drill（02b Owner）。

## 6. 观察项（非阻断，登记）

- **O-N1（环境分层/建议）**：wb360 内 `GET /me/work-resume`、`GET /me/workspace-preferences` 404（G5 树环境缺省，probe8-report）。`recordResume`/`refreshResumeState` 设计为静默失败，但全局拦截器对 404 代打「Not Found」toast（a3-3 截图顶部）——P6 后端就绪后应无此 toast；建议届时复验，或裁决拦截器对 `/me/*` 404 免打扰。
- **O-N2（建议，预存）**：indicator-analysis A 区均值/中位数与 E 区分布基于 `limit=100 asc` 最差 100 条样本（:411-419，后端 le=100 契约内），UI 无截断口径提示，仅代码注释登记方案 §10 风险表；千回路规模下 A 区「全厂均值」名实有偏差。归 A19 观察，不阻断。
- **O-N3（建议，预存页面）**：SPONSOR 在诊断建议页「接受/驳回/忽略」与诊断概览「复核/诊断」按钮**未灰置**（probe5 DOM disabled=false），点击经 popconfirm 确认后 **POST 403** 服务端硬拒绝（probe7-report，页面数据未变仍 40 待审核，b5/b6 截图）——后端零写红线（authz_catalog SPONSOR 写集=frozenset()，P1-04 裁决）有效；前端灰置属预存欠账（两页均为 P6 前既有模块页，非本 Slice 范围）。Owner 建议：前端模块页 Owner。

## 7. A15/A16/A19/A20 逐项

| 项 | 结论 | 证据/未验原因 |
|---|---|---|
| A15（五角色/撤权/服务端拒绝真实消费） | **PASS（代理走查）** | 四角色登录走查全通；SPONSOR 写操作 POST 403 服务端拒绝实证（probe7）；默认落地页 P6 前端逻辑生效（ADMIN/SPONSOR→/cockpit，IC/EXPERT→/my-work，probe1/3 landed） |
| A16（模块开关/旧书签/刷新与返回/兼容参数） | **PASS（部分环境缺省）** | 旧 embed 深链（embed=1 精简模式+返回条）IC 实证（c2/c3）；刷新返回恢复（goBack 还原 node/loop 现场，a3-4）；菜单页面码过滤降级=环境缺省登记（我的工作菜单全角色不可见=后端无 page:my-work；eng-templates 同五批口径）；非法参数 notices 由单测/Adapter 矩阵覆盖（未重复） |
| A19（1000 回路与跨来源多页队列前台口径） | **PASS（口径核对，不实测容量；G-N3 另列）** | my-work：`pageSize:50`==后端 `Query(le=50)`、游标 `nextCursor`「加载更多」、`total=null→「计数未知（部分来源失败）」`、PARTIAL「该来源计数未知，未按 0 处理」、撤权计数文案——前端文案与后端契约逐项一致（personal_work.py:760/851）；装置排行：footer 计数文案在屏（e1，数值矛盾归 G-N3）；节点清单 ranking `limit=100+offset` 循环拉全（端点 le=100）+上限 1000 显式截断提示「仅显示前 1000 条」（use-drill.ts:297-319，G5 数据单节点 ≤200 未触发截断，modalTruncationNote=[] 如实）；reports 快照 `pageSize=100` 翻页拉全+客户端分页「共 N 条」；indicator-analysis 观察项 O-N2 |
| A20（双主题/键盘/焦点返回/窄工程桌面） | **部分覆盖（不重复已验点位）** | 双主题/键盘焦点路径已由五批 L 验收（不重复）；本 Slice 全部走查在 1920×1080 工程桌面分辨率进行，关键操作无遮挡（各截图）；焦点返回=from 返回恢复链（§3 已验） |

## 8. 四岗位代理走查记录（路径+截图）

| 岗位 | 路径 | 结果 |
|---|---|---|
| ADMIN | 登录→/cockpit（落地）→性能页签→装置行→清单弹窗（G-N2 失败态）→回路页卡片→态二详情→工作台→wb360 chips→单项清除→返回恢复；侧栏全菜单（含参数配置/系统管理） | 全导航可达 ✓；PL 链后半全通 ✓（a1–a6/a2-1/a3-1~4） |
| IC_ENGINEER | 登录→落地 `/my-work`（P6 前端默认首页生效）→页面 404（**环境缺省**：后端无 page:my-work 页面码，c1）→embed=1 深链 wb360（c2）→返回条回 `/my-work`（c3） | 深链+返回恢复 ✓；我的工作页本体待含 P6 后端环境复验 |
| EXPERT | 登录→落地 `/my-work`（同环境缺省）→/tuning/overview ✓（d1）→/tuning/records ✓→首行「详情」抽屉：记录 ID 实值 d982e8c9-…，来源记录/模型版本/模型来源/DCS 模板均「—」（**环境缺省**：G5 后端 response_model 无四字段，与五批 O-1 同口径，d3-1）；无保存/发布/批量应用入口 | 整定只读链 ✓ |
| SPONSOR | 登录→/cockpit（落地）→侧栏菜单枚举（驾驶舱/回路监控/性能评估/回路诊断/参数整定/运维处置/统计分析，**无**我的工作/参数配置/系统管理，b2-1）→10 页 strict-404 全无（probe3）→降级纯文本：报表位号链接数=0、驾驶舱工作台按钮=0、调参优化不渲染（probe1/5）→接受建议 popconfirm→POST 403（probe7，数据未变） | 只读全可达无死链 ✓；降级纯文本 ✓；无可执行入口=按钮未灰置但服务端硬拒绝（O-N3 登记） |

环境写保护：浏览器均为临时 context；探针唯一写尝试（SPONSOR 接受建议）被后端 403 拒绝未产生变更；未改实现文件、未 commit/push、未跑迁移、未写共享环境。

## 9. 总结论

- **PL-01/02**：单测 11 例全绿；浏览器链除 G-N2 断点外全环节实证通过（发送/消费/回显/单项清除/返回恢复/等长比较）——**对账通过，附 G-N2 断点登记**。
- **PL-06**：同一 mock 三端对账单测全绿（含 R08 版本锁定与 CSV 版本列）——**通过，附 G-N1 不一致登记**。
- **四岗位代理走查**：四角色路径全通，SPONSOR 只读边界与降级纯文本实证，服务端 403 红线实证——**代理走查通过**（环境缺省项如实登记）。
- **A19**：前端分页/筛选/计数文案与后端契约一致（容量不实测）——**通过（附 O-N2 观察）**。
- **真实缺口 3 项（G-N2 严重建议优先、G-N3/G-N1 一般）+ 观察 3 项，全部先报主协调裁决，本 Slice 零修复、零代码改动（除新测试文件）。**
