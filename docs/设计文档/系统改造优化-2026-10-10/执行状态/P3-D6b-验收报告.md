# 验收报告：P3-D6b 第二段——R02/R03/R07 受保护路径读端适配（11 项全批）

- 裁决：**PASS**（2026-10-12，非作者独立核验，05 号文 §5 协议）
- 验收对象：worktree `/Users/zhangping/DEV/CLPM-MVP-p3d6b`，分支 `feat/p3-d6b-protected-readers`，基点 6eee4a73，未提交工作区 diff
- 范围固定：`git diff --stat` = 9 修改 + `git status` 1 新增测试（`__tests__/score-sparkline-null-gap.test.ts`），+755/−83——与交接自报**逐条目一致**（api/metric.ts、score-sparkline.vue、loop-confidence-content.vue、loop-performance-detail-content.vue、indicator-analysis.vue、history-tab.vue、matrix-tab.vue、rank-tab.vue、pid-dashboard.vue + 新测试）
- 授权依据：DEC 清单追加裁决区 R02/R03/R07 行（2026-10-12，11/11 全批，四条件=仅清单所列读端语义/逐文件事后审计/超语义单独呈批/串行排队）；08d 交接申请清单 11 项原始定义

## 一、11 项逐项独立核验（不采信实施者断言，逐项重查代码与外部事实）

| # | 核验方法与证据 | 结论 |
|---|---|---|
| R02-1 | diff 走查：`gradeScore` 三条件 gating（status==='SUCCESS' ∧ score≠null ∧〔无语义 ∨ compositeState==='COMPLETE'〕）；反例推演：①PARTIAL+score78→'—'+状态原因；②SUCCESS+score null→'—'+"未计算（null，非 0 分）"；③参考分只进 `fastReferenceNote` 注记行不进等级。与 08d `classify_roster_state` scorable 判定（SUCCESS+COMPLETE 或 legacy 有限分）语义一致 | **通过** |
| R02-2 | `fastReferenceScore` typeof number 守卫；有参考分拆"正式 F/参考分"两行+`参考` Tag 徽标；无语义走原单值分支（`v-else` 与基线同渲染）；正式 value=0 为有效正式分（`fastFormalValue` 0 显示 0%，对齐 08d 互斥口径）；metricsSemantic 为可选键，当前 API 不返回即 inert | **通过** |
| R02-3 | 独立核 `KpiSnapshotQueryParams`：`latestOnly/page/pageSize` 参数真实存在（api/metric.ts:1689-1745）；历史分页表列=时间窗/评分/可信度/状态/算法版本，`formatTsRange`/`STATUS_*_MAP`/`CONFIDENCE_*_MAP` 均为既有导入符号；latest 概要与历史两请求独立 catch 互不阻塞；空历史显式空态文案；`共 N 条` 分页 total | **通过** |
| R02-4 | 数据质量（好值率+有效数据率）与动态证据充分性（可信度 Badge+固定说明"D/E=证据不足，不等于数据质量问题"）分列两 Descriptions；`goodValueRate` 取 `metrics.good_value_rate.value` 与 12 子指标表同源（`LoopConfidenceLatestItem.metrics` 形状核实） | **通过** |
| R03-1 | **服务端独立核**：快照列表排序 `services/performance.py:2234/2269` asc/desc 均 `nulls_last(col_order)`（rank-tab 评分列实际消费路径 `/performance/loops/snapshots`）；另 `:770-773` 同款（实施者引用"performance.py:771/773"实为 services 层文件，见观察项 O-4，实质成立）。列头+null 单元格 Tooltip 显式 NULLS LAST 与"非 0 分"；rank-tab/matrix-tab 导出功能 grep 独立核**零命中**（实施者如实登记"无触及面"）；申请清单所引 :462/470-472/502 `?? 0` 独立核为 `gradeStats[N] ?? 0` 等级计数 Record 索引兜底（非评分 null-as-0），未触及正确；history-tab 导出走 `formatScore`（null→'—'） | **通过** |
| R03-2 | 组件逻辑走查：分段渲染（连续非 null 段各一段、单点不成段 `current.length>=2`）、比例尺仅按 `validValues`、末点圆点/取色取最后非 null、全 null 零路径零圆点（`v-if="lastValid"`）；history-tab 删 `?? 0` 直传 `s.score`；`drawerTrendNullCount>0` 标注"含 N 个未计算窗口，趋势断开显示（未画作 0 分）"；**5 例新单测独立复跑**：`pnpm exec vitest run apps/web-antd/src/__tests__/score-sparkline-null-gap.test.ts` → **exit 0，5/5 通过**（断点分段/null 不拉低基线/全空不画/尾 null 末点/旧调用兼容）；组件全仓消费方 grep 独立核=仅 history-tab | **通过** |
| R03-3 | `cellTooltip` parts 结构追加：①`row.metricsSemantic?.fast_rate?.details?.referenceScore` number 守卫→构成说明（条件式 inert）；②`row.algorithmVersion`→"算法版本 X"；③无版本有 `sampleCount`→"窗口均值（N 个小时快照样本，AVG 跳过 NULL）" | **通过** |
| R03-4 | `medianOf` `?? 0`→`!`（入参 `curValues`/`prevValues` 前置 `.filter(v!=null)` 已核）；`avgDelta/medianDelta` 改三条件显式 null 守卫（无上窗→null 不作 0）；`sortedNodes` 前置已 filter 改 `!` 断言；**口径链独立核**：`participationBaseItems`（includeInEvaluation+fitness）⊇ `filteredItems`（再加指标值非 null）= `curValues` 基数 → `uncomputedCount = M−N` 数学恒非负，"有效 N（共 M，k 未计算）"对账正确；分布桶经 `curValues` 同口径（零改动登记属实） | **通过** |
| R07-1 | `topNList` 拆 scored/unscored，组内排序后 `[...scored, ...unscored]` 拼接——asc [null,5,10]→[5,10,null]、desc→[10,5,null]（推演确认 null 恒沉底）；未评行 `isUnscored`（非 L0/L1 且 null）评分列显示"未计算"+Tooltip"不参与最差排序（恒排末位）"；**方向依据已在代码注释+交接双记录**（治理语义：待治理榜=可行动最差排名，未评≠最差，与 L0/L1 同属不可排名对象），满足任务书【3】 | **通过** |
| R07-2 | `loadRosterTrend` 消费 `nodes/{nodeId}/monitor?dimension=hour` 的 `assessmentStats.roster`（N=scorableLoopCount 柱值、E 进 Tooltip）；**桶对齐独立核**：hour 桶 board trend timestamps=`'%Y-%m-%dT%H:00:00'`（dashboard.py:431/446）vs monitor tsStart=`isoformat()` 无时区后缀（node_performance.py:1481）——`slice(0,13)` 匹配成立；day 桶北京日（`+8h` 钟面运算在带/不带时区解析两种情况下结果一致，独立推演）；多根全厂按桶求和（名册成员跨根不相交口径）；任一参与节点桶缺失/无名册→null 断点不猜算部分和；E=0→"无参评回路（E=0）"、N=0&E>0→0%、无名册→Tooltip 断点说明；角标双注记；evaluatedLoops 序列退役（批准文本授权）；**dev 库独立抽查**：`kpi_node_snapshot_hourly` 3074 行 `assessment_stats` 非 NULL **0 行**——"dev 现库全历史快照无名册→柱断点+不可追溯角标"行为差异描述属实 | **通过** |
| R07-3 | `gaugeScore`：`latestMetricStat` 对象存在→返回其 `weightedMean`（**对象在而 weightedMean null→null '—' 不回退**，代码路径确认不掩盖）；无 metricStats→回退 `aggregateData.avgScore`+meta"统计窗口：X"（来源可见切换）；meta="评分对象 numericCount/objectCount（metricStats）"；仪表卡 Tooltip 双口径说明；"窗口内最新小时快照最低兼容"口径与 R07 行头一致 | **通过** |

## 二、四条件合规

1. **仅清单语义**：11 项改动点全部落在申请清单锚点语义内；超清单 3 件（见 §三）独立审查均为清单语义的**必要实现件**而非独立新语义——不触发条件③"单独呈批"，登记口径成立（api 类型增量有第一段 `api/diagnosis.ts` 先例且 11§5 R01 行类型 Owner=D6）。
2. **不改布局导航**：无路由/布局/导航改动。内容增量=可信度抽屉分节+历史表（内容增加）、Tooltip/角标/标注行、趋势柱序列改名"可评分 N/E"；帮助文案随序列改名同步更新属必要诚实化（否则帮助文本与图不符）。
3. **审计清单与 diff 一致**：交接 §一 11 行 + §二 3 件 ↔ diff 10 条目一一对应（R02-1/2 同文件、R02-3/4 同文件、R03-2 跨组件+消费方两文件、R07×3 同文件）；锚点漂移独立核：bd22c345→6eee4a73 在 views/metric+components/metric **零 diff**，锚点未漂移属实。
4. **串行排队**：唯一 worktree/分支 `feat/p3-d6b-protected-readers`，无并发触碰。

## 三、超清单 3 件必要性与最小性审查

| 件 | 独立审查 | 结论 |
|---|---|---|
| api/metric.ts（+61） | 全部为**可选增量键**类型声明（`assessmentStats?`/`metricsSemantic?` + 新形状接口），无逻辑无必填变更，旧调用零影响；R02-1/R02-2/R03-3 条件分支与 R07-2/R07-3 消费无类型即无法 typecheck——必要性成立；类型形状与 08d `_snapshot_to_dict`/monitor 出口/record payload 逐键核对一致 | **必要且最小** |
| score-sparkline.vue | R03-2"null 断点（不画）"的渲染本体——Props 放宽 `(null\|number)[]` 向后兼容；全仓唯一消费方=history-tab（grep 独立核），无其它消费方行为影响面 | **必要且最小** |
| 新测试（5 例） | 编码 R03-2 五个反例供验收复跑；独立复跑通过（见 §一） | **必要** |

## 四、INT-05 与后端零改动

- **INT-05（新旧并存不伪正常）**：全部条件分支按"缺数据显式呈现"——R02-2/R03-3 无语义按原渲染不冒充构成、R02-1 legacy 行正式分定级不受影响、R07-2 无名册断点+不可追溯角标、R07-3 回退分支口径标注。无任何"新语义未激活却渲染伪正常值"路径。**通过**。
- **后端零改动**：`git diff -- backend/` 输出为空（0 行）——**确认**；08d 已落 assessmentStats 出口为唯一消费基础，本批未新增任何后端键。

## 五、门禁独立复跑（cwd=worktree/frontend，裸命令真实退出码）

| 命令 | 退出码 | 结果 |
|---|---|---|
| `pnpm run check:type` | **0** | 2 successful（web-antd vue-tsc，FULL TURBO） |
| `pnpm exec eslint apps/web-antd --cache` | **0** | 0 errors、4 warnings（directives.test.ts×3 + EvalSummary.vue v-html，既有同集非本卡） |
| `pnpm run test:unit` | **0** | **85 文件 / 675 tests 全过**（基线 84/670 + sparkline 5 例）；teardown 悬挂警告为 08d 遗留⑥同款 |
| `pnpm exec vitest run …score-sparkline-null-gap.test.ts`（单跑） | **0** | 5/5 通过（13ms） |

三项与实施者自报门禁完全一致，无失实。

## 六、观察项（不阻断，供后续小批或对应 Owner 酌处）

- **O-1**（R02-3）：历史加载失败（catch）与真空态共用同一空文案"该回路暂无历史评估记录"——请求失败被显示为"暂无记录"，轻微（latest 概要独立可见且不受影响）；建议后续区分"加载失败/确无记录"。
- **O-2**（R02-1）：`gradeScore` 有值但 `getGrade` 映射不到等级（低于最低档阈值）时显示 '—' 且 Tooltip title 为空串——antd 空标题不弹泡，无害瑕疵。
- **O-3**（R03-4）：参评回路数卡删除了 `countDelta` null 时的 '—' 占位行（排版微调，轻微超清单，无语义损失——无上窗数据时环比行不显示而非显示 '—'）。
- **O-4**（R03-1 证据引用）：实施者引用"performance.py:771/773"未注明是 `services/performance.py`（与 `endpoints/performance.py` 同名易混）；行号实指 services 层 `:770-773`，且 rank-tab 实际消费路径的排序 `:2234/:2269` 同样 nulls_last——断言实质独立核成立，仅引用精度问题。
- **O-5**（R07）：`handleHelp` 替换块缩进异常（6 空格起缩进）；eslint 通过，纯格式无语义影响。

## 七、裁决与接续

**PASS**。11/11 项独立核验通过、四条件合规、INT-05 通过、后端零改动确认、门禁三项独立复跑全 0 与自报一致。观察项 O-1~O-5 均不构成 REWORK。

接续：归主协调合并（zp 线）并更新任务看板 D6 第二段行；范围外同类位点（rank-tab 等级列 gating、参评回路卡旧口径、metricsSemantic 出口暴露）按交接 §五登记留待后续裁决，本批不予处置。
