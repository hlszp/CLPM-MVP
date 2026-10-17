# 交接：P3-D6b 第二段——R02/R03/R07 受保护路径读端适配（11 项全批）

- 状态：**已完成自测，待非作者验收**（2026-10-12）
- worktree/分支/基点：`/Users/zhangping/DEV/CLPM-MVP-p3d6b`，分支 `feat/p3-d6b-protected-readers`，基点 `feat/p1-integration@6eee4a73`（D6 第一段+P4-02+迁移合拢已含）；**未提交**（工作区 diff：9 修改 + 1 新增测试，+755/−83 行），无 commit/push 授权
- 授权依据：`P3-D6-08d-交接.md` 申请清单 11 项（**11/11 全批**，DEC 清单 2026-10-12 追加裁决：逐项批 8 + 复审后补批 3 → 11/11；四条件=仅清单所列读端语义/逐文件事后审计/超语义单独呈批/串行排队）；第八波任务书【一】节
- **批文口径备注**：任务书【2】节文字枚举 10 项（漏列 R02-4），但 DEC 记录"11/11 全批"与申请清单表格（11 行，含 R02-4）一致，且协调指令明示"申请清单 11 项=实施清单"——按申请清单实施 R02-4，口径歧义此处登记
- **纯前端批**：`git status` 仅 frontend/ 9 文件 + 1 新增测试；后端零改动（08d 已落 API=唯一消费基础，未新增任何后端键）
- 消费基础：第一段已落 `assessment-labels.ts`（本批 metric 系未涉及诊断标签）、`api/diagnosis.ts` 类型、后端 `assessmentStats`（nodes snapshot/ranking/overview/monitor 出口）与 record payload `metricsSemantic` 形状（`fast_rate.{value, details.referenceScore}`、`__composite__.assessmentState`——按 08d `_load_roster_inputs` 消费口径对齐）

## 一、11 项逐文件审计清单（事后审计件）

锚点=申请清单（bd22c345 实测；6eee4a73 与 bd22c345 间 views/metric 零 diff，已核实锚点未漂移）。

### R02 评估详情

| # | 文件（均在 `frontend/apps/web-antd/src/`） | 实施变化 | 反例自验 |
|---|---|---|---|
| R02-1 | `views/metric/components/loop-performance-detail-content.vue`（评估等级 Tag） | 新增 `gradeScore` 判定：仅 `status==='SUCCESS' && score!=null &&（无语义 或 compositeState==='COMPLETE'）`才定级——对齐 08d `classify_roster_state` 的 scorable 判定（legacy 行 SUCCESS+score 非 null 即正式；新语义行要求 COMPLETE）；不满足显示 '—' + `gradeUnavailableReason` Tooltip（未计算/状态非 SUCCESS/PARTIAL 证据不足，均注明"非 0 分"）；携带参考语义时等级下方注记行"快速性参考分 X%·动态证据不足（静稳规则），不参与定级" | ①PARTIAL+score 78 → '—'+原因；②SUCCESS+score null → '—'+"未计算（null，非 0 分）"；③参考分 100 不进等级（注记行单独展示）；④metricsSemantic 路径为条件式（当前 API 不返回该键，inert 就绪，见"类型增量"节） |
| R02-2 | 同文件（8 大 KPI 区快速率行） | `fastReferenceScore = metricsSemantic?.fast_rate?.details?.referenceScore`（number 守卫）；非 null 时拆两行："正式 F：{formalValue}" + "参考分 {ref}% · 动态证据不足（静稳规则）"（Tag 徽标"参考"）；无语义按原单值渲染（现状不变） | 仅参考（value=null, ref=100）→ 正式行 '—' + 参考行 100% 徽标；正式优先（value=0 残留 ref）→ 08d 互斥口径读端同款（0 是有效正式分）；无语义 → 与基线逐字节同渲染 |

### R02 可信度详情（loop-confidence-content.vue 整文件重写为分节结构）

| # | 文件 | 实施变化 | 反例自验 |
|---|---|---|---|
| R02-3 | `views/metric/components/loop-confidence-content.vue` | 新增"评估历史"分页表：消费 `getLoopSnapshotsApi({loopId, latestOnly:false, page, pageSize:10})`（评估记录账本分页），列=时间窗/综合评分/可信度/状态/**算法版本**（每行标注窗口+版本）；概要标题改"评估概要（最新一条 latest）"（latest 与历史并列、窗口/算法版本均在概要区随行标注）；空历史显式空态文案 | 历史多版本不再被 latest 替代（翻页可见全量，`共 N 条`）；latest 概要失败不影响历史加载（两个独立请求，各自 catch）；历史加载失败空表+空态文案（不静默） |
| R02-4 | 同文件（原"评估概要"混合区） | 概要 Descriptions 保留时间/区间/状态/评分/算法版本；**新增"数据质量与动态证据（分列）"节**：左=数据质量（好值率[metrics.good_value_rate 同源]+有效数据率），右=动态证据充分性（可信度 Badge+固定说明"可信度反映动态证据充分性（D/E=证据不足），不等于数据质量问题"——对齐 R11 语义） | confidence D/E 不再与好值率/有效数据率同块混展示；D 级回路在数据质量良好时两列各说各话（不再暗示 D=数据质量问题） |

### R03 列表与分析

| # | 文件 | 实施变化 | 反例自验 |
|---|---|---|---|
| R03-1 | `views/metric/loop-evaluation/tabs/rank-tab.vue`（评分列 :397 sorter + 兜底位点） | ①列头"综合评分"包 Tooltip："服务端排序：未计算（null）评分在升/降序中均排末位（NULLS LAST）；'—' 表示未计算，非 0 分"（服务端 `performance.py:771/773` asc/desc 均 `.nulls_last()` 已核——显式化而非改变）；②非 L0/L1 的 null 评分单元格包 Tooltip"该窗口评分未计算（null，非 0 分）；排序时恒排末位"。**导出条款**：rank-tab 无导出功能（grep exportData/导出/headers 零命中，如实登记无触及面）；history-tab 导出已用 `formatScore/formatNumber`（null→'—'，验证登记零改动）。申请清单所引 :462/470-472/502 `?? 0` 为等级统计卡 Record 索引兜底（gradeStats 计数非评分），非 null-as-0 路径，未触及 | null 行升降序均末位（服务端 NULLS LAST+UI 显式说明）；null 评分显示 '—'+Tooltip 而非 0 |
| R03-2 | `components/metric/score-sparkline.vue` + `views/metric/loop-evaluation/tabs/history-tab.vue:1051` | sparkline Props `data: number[]` → `(null\|number)[]`：null 断点分段渲染（连续非 null 段各一段，单点不成段）；比例尺仅按非 null 值计算；末点圆点/取色取最后一个非 null；全 null 不画。history-tab 传参删 `?? 0` 直传 `s.score`；新增 `drawerTrendNullCount`，>0 时趋势图下标注"含 N 个未计算窗口，趋势断开显示（未画作 0 分）"（明细清单各行已带窗口时间+formatScore '—'） | **新增单测** `__tests__/score-sparkline-null-gap.test.ts` 5 例全过：[80,90,null,70,75]→2 段；[70,80,90,null] 比例尺 min=70（null 不拉低基线，80 居中部非顶格）；全 null→0 路径 0 圆点；尾 null 圆点取最后非 null 位置；纯 number[] 单段（旧调用零行为变化——该组件全仓唯一消费方即 history-tab，已 grep 核实） |
| R03-3 | `views/metric/loop-evaluation/tabs/matrix-tab.vue`（cellTooltip :718-722） | Tooltip 追加（parts 结构）：①行携带 `metricsSemantic.fast_rate.details.referenceScore`（number 守卫）→"构成：含快速率参考分（静稳，动态证据不足）"（条件式就绪）；②`row.algorithmVersion`（latest 行）→"算法版本 X"；③window-agg 行（无版本有 `sampleCount`）→"窗口均值（N 个小时快照样本，AVG 跳过 NULL）"。**导出条款**：matrix-tab 无导出功能（grep 零命中，如实登记无触及面） | latest 行带算法版本；8h/24h/168h 窗口模式行显示样本数与均值口径（不再冒充单点实测）；构成行条件式（当前 inert） |
| R03-4 | `views/metric/indicator-analysis.vue`（:327-343 中位数/均值、:375-384 环比、:490 sortedNodes） | ①`medianOf` 参数已是滤 null 数组（`curValues`/`prevValues` 前置 `.filter(v!=null)`），`?? 0` 为死防御——改为非空索引 `sorted[mid]!`（清 0 陷阱）；②`avgDelta/medianDelta` 的 `?? 0` 改显式 null 守卫（`prevAvg != null` 三条件，null 语义保留不作 0）；③sortedNodes 排序 `?? 0` 改后置非空断言（前置已 filter）；④**参评回路数卡显式对账**：新增 `participationBaseItems`（includeInEvaluation+fitness 过滤、不含指标值过滤）与 `uncomputedCount`，卡片副行显示"有效 N 条（共 M 条，k 条未计算）"。**分布桶同口径**：`distBuckets` 消费 `curValues`（已滤 null）——核实已同口径，零改动登记 | 均值/中位数/分布桶只统计有效值行（结构既有+死陷阱清除）；M>N 时未计算条数显式（不再让"参评回路数"与统计基数静默不一致）；环比无上窗数据→'—'（null 不作 0） |

### R07 装置（pid-dashboard.vue）

| # | 锚点 | 实施变化 | 反例自验 |
|---|---|---|---|
| R07-1 | topN 排序（原 :492 `(a.score ?? 0)`） | 拆 scored/unscored 两组：组内按方向排序，**null 恒沉底拼接**（升/降序一致）；`isUnscored`（非 L0/L1 且 score null）行评分列显示"未计算"+Tooltip"该窗口评分未计算（null，非 0 分）；不参与最差排序（恒排末位）"。**方向依据（任务书【3】要求记录）**：治理语义=「待治理榜」是"可采取行动的最差回路"排名，未评≠最差——与 L0/L1 不适用同属不可排名对象（服务端 fitnessFilter 已剔 L0/L1，score null 行保留但沉底），故 nulls last（未评沉底） | asc 序 [null,5,10] → [5,10,null]（未评不再冒充 0 分最差占据榜首）；desc 序 [5,10,null] → [10,5,null]；未评行可见且标注"未计算"（不静默丢弃，诚实化原则） |
| R07-2 | 趋势柱序列（原 :747 `trend.evaluatedLoops` "产品回路"） | 新增 `loadRosterTrend()`（loadAll 内 board+tree 就绪后链式）：按作用域节点（选中节点 / 全厂=boardTree 各根并列，名册成员跨根不相交→N/E 按桶求和）拉 `nodes/{nodeId}/monitor?dimension=hour`，取每桶 `assessmentStats.roster`（N=scorableLoopCount 柱值；E 进 Tooltip）；**hour 桶**=UTC 整点对齐，**day 桶**=该北京日最末一条小时快照（日末名册状态）；柱序列更名"可评分 N/E"；自定义 Tooltip：N/E 及占比（`N=x / E=y（z%）`）、**E=0→"无参评回路（E=0）"**、N=0&E>0→0%（占比 0%）、无名册→"该窗口无名册数据（历史快照不可追溯）"（null 断点不画）；任一参与节点该桶无快照/无名册→该桶合计不可知（null，不猜算部分和）；角标注记：全空→"历史快照无名册统计（不可追溯，断点显示）"、有无快照未计入节点→"N 个节点窗口内无快照（未计入 N/E）"。evaluatedLoops 序列退役（其为误导元素本体，批准文本=改名消费 roster） | 已评数≠参评分母不再混同（柱=N，Tooltip=N/E/ratio）；dev 现库全历史快照 assessmentStats=NULL（已查 6 节点 514×N 行全 null）→柱全断点+角标注明不可追溯（诚实空缺，writer 激活后自然填充）；多根全厂（EO 工厂+致联工厂）各自 monitor 并列求和，致联无快照→角标披露未计入 |
| R07-3 | 装置卡均分（原 :981 formatNumber null→'--' 已诚实；上游聚合口径未对齐） | 性能评分仪表卡：`gaugeScore` = 窗口内最新带 assessmentStats 小时快照的 `metricStats.score.weightedMean`（**null 不作 0**：metricStats 在而 weightedMean null→仪表 null '—'）；meta="评分对象 numericCount/objectCount（metricStats）"；无 metricStats（历史快照）回退既有 board/aggregate 已评加权均分，meta="统计窗口：X"（来源可见切换，非静默）；仪表卡包 Tooltip 说明口径与回退。**窗口级 metricStats 说明**：08d metricStats 为逐小时快照统计，窗口级逐指标权重合并归 P5/P6（08d 交接遗留①）——本卡按"窗口内最新小时快照"最低兼容消费（R07 行头"最低兼容；P6 再三指标首屏和 PL"口径） | metricStats.weightedMean=null（评分对象 0）→仪表 '—' 不显示回退值（不掩盖）；历史快照（dev 现状）→回退分支显示与基线相同数值+口径标注；Tooltip 带 numericCount/objectCount |

## 二、超清单必要触及（逐文件登记，均为清单语义的必要实现件）

1. **`api/metric.ts`（+61 行，纯增量可选键）**：①`KpiSnapshotItem` 增可选 `metricsSemantic?: MetricsSemantic | null`（新增 `MetricSemanticEntry`/`MetricsSemantic` 形状，对齐 record payload 实际键 `fast_rate.{value, details.referenceScore}`/`__composite__.assessmentState`）；②`MetricApi.NodeMonitorSnapshot` 增可选 `assessmentStats?: NodeAssessmentStats | null`（新增 `NodeAssessmentRoster`/`NodeMetricStat`/`NodeAssessmentStats`，对齐 08d `_snapshot_to_dict`/monitor 出口）。理由：R02-1/R02-2/R03-3 的"有 record 语义时"条件分支与 R07-2/R07-3 的 roster/metricStats 消费无类型即无法通过 typecheck；全部可选增量键，旧数据/旧调用零影响。11§5 R01 行类型 Owner=D6（第一段同款先例：api/diagnosis.ts 增量类型）。
2. **`components/metric/score-sparkline.vue`（共享组件，全仓唯一消费方=history-tab，已核实）**：R03-2"null 断点（不画）"的渲染本体——Props 放宽为 `(null|number)[]`（向后兼容 number[]），分段渲染/比例尺/末点逻辑如清单语义；不改布局不改其它消费方。
3. **`__tests__/score-sparkline-null-gap.test.ts`（新增测试，5 例）**：编码 R03-2 反例（断点分段/null 不拉低基线/全空不画/尾 null 末点/旧调用兼容），供非作者验收直接复跑。

## 三、自测与门禁（cwd=worktree/frontend，裸命令真实退出码）

| 命令 | 退出码 | 结果 |
|---|---|---|
| `pnpm run check:type` | 0 | 2 successful（web-antd vue-tsc） |
| `pnpm exec eslint apps/web-antd --cache` | 0 | 0 errors；4 警告均为既有非本卡（directives.test.ts×3 + EvalSummary.vue v-html，与第一段报告同集） |
| `pnpm run test:unit` | 0 | **85 文件 / 675 tests 全过**（基线 84/670 + 新增 sparkline 5 例）；teardown 警告（metrics-panel worker terminate）为既有遗留（08d 交接遗留⑥同款，非本卡引入） |

过程记录：首轮 typecheck 1 错（loadRosterTrend 闭包内 trend 收窄失效→提 bucketTimestamps 局部量）；eslint --fix 修复 6 处风格（dot-notation×3/nested-ternary/sort-union-types/prefer-array-find，语义零变化，diff 复核）；新测试首轮 1 例期望构造错误（[80,90,null,70] 尾单点不成段→改 [80,90,null,70,75]）后全过。

## 四、行为差异与影响

1. **R02-1**：PARTIAL 快照行（score 非 null）原显示等级 → 现 '—'+原因（授权行为变化，对齐 scorable 判定）；SUCCESS+score null 不变仍 '—'（多 Tooltip）
2. **R02-2/R03-3 构成分支**：条件式就绪（loop 级 API 当前不返回 metricsSemantic，展示与基线一致）；后端未来暴露该增量键即自动启用，无需再改读端
3. **R02-3/R02-4**：可信度抽屉（loop-performance 页 + workbench360 评估详情抽屉两处共用）增评估历史分页表与分列节——内容增加，无布局/导航改动
4. **R03-2**：含未计算窗口的趋势 sparkline 由"连到 0"变"断点+标注"（授权核心语义）
5. **R07-1**：待治理榜未评行恒沉底+标注"未计算"（原可能以 0 分冒充最差占榜首）
6. **R07-2**：趋势柱由 evaluatedLoops（已评数）改 roster N——**dev 现库全历史快照无名册→柱呈断点+角标"不可追溯"**（诚实空缺；writer 代际激活后自然填充；这是批准文本的直接后果，如需恢复"已评数"展示请另立申请）
7. **R07-3**：性能评分卡在有名册快照时切换 metricStats 口径（当前 dev 全走回退分支，显示值与基线相同+口径标注）
8. **回退**：还原本 worktree diff（10 条目）即可整批回退；无 schema/后端/共享服务改动

## 五、范围外同类位点（未触及，登记供后续裁决）

1. `rank-tab.vue` 评估等级列（:1355 getGrade(record.score)）与详情抽屉同源语义但**不在 11 项清单锚点内**——按"仅清单所列变化"未改；如需同样 gating 请追加申请
2. R02-1 的 metricsSemantic 条件分支依赖 loop 级 API 出口（当前无任何端点返回该键）——如后续 08 Owner 决定暴露，读端已就绪零改动
3. `pid-dashboard.vue` "参评回路"仪表卡（productLoopCount=有快照−数据不足）仍为旧口径——R07-2 批准文本仅覆盖趋势柱序列（:747 锚点），参评卡属 P6 三指标首屏范围

## 六、接续第一步

1. 非作者验收：固定 worktree diff（9 修改+1 新增）；重点独立复核——①sparkline 断点单测实跑；②topN nulls 沉底方向依据（本文件 R07-1 行）；③R02-1 gating 与 08d classify_roster_state 语义一致性；④R02-3 历史分页与 latest 并列；⑤超清单三触及的必要性与最小性（§二）；⑥后端零改动（`git status` 仅 frontend/）
2. 验收通过后归主协调合并（ zp 线）并更新任务看板 D6 第二段行
