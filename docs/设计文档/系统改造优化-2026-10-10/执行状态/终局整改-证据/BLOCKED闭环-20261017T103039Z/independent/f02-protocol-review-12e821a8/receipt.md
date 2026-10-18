# F02 容量测试协议 — 非作者独立审核回执

- **审核人**：非作者独立审核人（未参与本协议、工具链、G3 修复的编写）
- **审核时间**：2026-10-18（CST）
- **被审物**：`f02/series-s01/protocol-12e821a8.json`
  - schemaVersion=2，seriesId=`clpm-final-remediation-s01-12e821a8`
  - createdAt=`2026-10-17T17:11:47.452731+00:00`（01:11:47 CST）
  - candidateSha=`12e821a80861d452a4f5a2f3fcf4763afa5f8f9e`，ciRunId=`39050996941`
- **证据根**：`/Users/zhangping/DEV/CLPM-MVP/docs/设计文档/系统改造优化-2026-10-10/执行状态/终局整改-证据/BLOCKED闭环-20261017T103039Z`
- **审核性质**：只读文档与原件核对。未执行容量测试、未改代码/协议、未操作环境。
- **总判定**：**REWORK**

---

## 总判定理由

协议的**容量矩阵、资源预算、不变量名称、输入状态、候选身份**五块核心内容与事实一致，可作修订基础；但协议**尚不具备"完整可执行"条件**，存在 3 类文档缺陷 + 1 项前置状态变化：

1. **工具链路径错误**：5 个工具中 3 个的 `f02/` 前缀路径与磁盘实际位置不符（实为证据根下）；2 个工具缺 SHA 钉扎。
2. **缺方法与来源绑定**：投前清单明文要求新协议须含"方法勘误01/补充05"及原工具 SHA、原 history6+3、原四 oracle 等 refs；协议一项都没有。按该清单"正文、ref 与实际原件不一致即退回"，构成退回条件。
3. **g3FixNote 表述不准确**："not selection rule change" 与已落地实现及刚完成的非作者 G3 复核结论冲突（该复核总判定 REWORK，并明确"本实现改的正是选模排序"）。
4. **前置状态已变化**：协议 `readinessPending` 列的 G3 非作者复核，在协议写就 19 分钟后已完成且判 **REWORK**；若 G3 按方案 B 改实现，candidateSha 将变，本协议绑定随之失效，须重签。

缺陷均可由作者在不改动任何验收口径（分母、expectedStatus、600s、九条件、四业务断言）的前提下修复，故判 REWORK 而非 BLOCKED。**在 G3 复核 REWORK 关闭前，即使本协议修订完毕，正式 20 批仍不得投递。**

---

## 逐项核对

### 1. 容量矩阵 — PASS

| 核对点 | 协议原文 | 事实来源 | 结果 |
|---|---|---|---|
| 总批次 | `totalBatches: 20` | `planned-batches.json` 20 项；`冻结验收协议.md` §3.1 "完整 20 批" | 一致 |
| 结构 | `A/B x cold/hot x 5 batches each` | planned-batches 批 1–5 A冷 / 6–10 A热 / 11–15 B冷 / 16–20 B热 | 一致（2×2×5=20） |
| 每批对象 | `objectsPerBatch: 1000` | planned-batches `expected_loops: 1000`；投前清单"各 1000" | 一致 |
| SLO | `sloSeconds: 600` | `cold-hot-definitions.json` `formalSloSeconds: 600`；冻结协议 §3.4 | 一致 |
| A 期望 | `expectedA: "1000 EVALUATED"` | 投前清单"A原 expected 为 1000 EVALUATED" | 一致 |
| B 期望 | `expectedB: "850 EVALUATED / 150 INCONCLUSIVE"` | 投前清单："800 正常 EVALUATED + 50 手动仍 EVALUATED + 100 DATA_INSUFFICIENT + 50 覆盖缺口，汇总 850/150" | 一致 |
| 额外工况 | `extraCases: 4`，`extraCasesNotInDenominator: true` | `f02/extra-inputs/plan.json` 四窗（fault-recovery / fault-persistent / independent-cold / independent-hot）均 `formal20Denominator: false` | 一致 |

**修订建议（非否决）**：B 场景 150 INCONCLUSIVE 内含两类不同原因（100 DATA_INSUFFICIENT + 50 覆盖缺口）。建议在协议 `expectedB` 或旁注中保留该拆分，避免执行方把 150 当单一原因码核对；冻结协议 expected_status 条件本身要求"按冻结状态/原因核对"。

### 2. 资源预算 — PASS

| 核对点 | 协议原文 | 事实来源 | 结果 |
|---|---|---|---|
| 总预算 | `totalGiB: 6` | `resource-profile-plan.json` `memoryBudgetBytes: 6442450944` = 6×1024³ | 一致 |
| 保守上界 | `conservativeUpperGiB: 4.8` | `maximumAccountingBytes: 5153960755` ≈ 4.8×1024³ | 一致 |
| VM 可用 | `vmAvailableMinGiB: 1` | `minimumVmMemAvailableBytes: 1073741824` = 1×1024³ | 一致 |
| TD pool | `pool0 (test profile, NOT production pool1)` | resource-profile-plan `limits`："TD supported pool0 is explicit test configuration, does not demonstrate default pool1 or production capacity"；接续检查点同口径 | 一致，且生产边界表述正确 |

三容器（postgres/tdengine/redis-17123）+ 宿主七 PID 的 accounting 口径、numeric 单线程、采样 2s/≤5s/前 60s/后 30s 在 `resource-profile-plan.json` 已冻结，协议未复述但未与之冲突。

### 3. 不变量 — 基本 PASS，一处表述需修正

| 核对点 | 协议原文 | 事实来源 | 结果 |
|---|---|---|---|
| R9 方法范围 | `ARX+ARMAX real solve, 200 iterations, tol 1e-6` | G3 复核 R8「R9 契约 PASS」；f03-arx/armax 复核均确认 200/1e-6 未改 | 一致 |
| 22 样本 / 30s | `22 samples, 30s per case` | G3 复核：两测试文件未改、"unchanged 30-second test"；f03-armax-protected-review 确认 22 例/30s 逐字节同 edfa | 一致 |
| 四业务断言 | `fourBusinessAssertions: "unchanged"` | 冻结协议 §4（AUTH/PERM/CALC/EVIDENCE）；多份复核确认未改 | 一致 |
| 九条件 | `ledger/source/run_binding/algorithm_version/expected_status/latest/nodes/task_terminal/parent_terminal` | `冻结验收协议.md` §3.2 表：同名九条，顺序一致，计数=9 | 一致 |
| 不改 expectedStatus | `noExpectedStatusChange: true` | 冻结协议 §3.2 expected_status 条件；执行纪律红线 | 一致 |
| 不降分母 | `noDenominatorReduction: true` | 执行纪律；cold-hot `required`："No denominator shrink" | 一致 |
| 不为刷绿跳过 | `noSkipForGreen: true` | 执行纪律 | 一致 |
| 失败保留 | `failurePreservation: "any batch failure preserves entire series"` | cold-hot `required`："A failure closes this series unsuccessfully" | 一致 |

**g3FixNote — 与实现/复核冲突（否决点 C）**：

协议原文：
> `ARMAX convergence qualification added to sort key (not selection rule change). Non-converged ARMAX cannot beat converged ARX at same confidence level. R9 scope unchanged.`

与实际代码对照（runtime 树 `backend/app/services/tuning_identification/pipeline.py`）：

- "added to sort key" **属实**：`_candidate_sort_key` 现返回 `(level_order, converged, fitting_score, residual_test_passed)`（`pipeline.py:5178-5179`）。
- "not selection rule change" **不成立**：排序键即选模规则本体；commit 信息亦为 "ARMAX convergence qualification in candidate sort"。刚完成的非作者 G3 复核（`independent/g3-convergence-qualification-review/receipt.md`，01:30:46，总判定 **REWORK**）附加观察第 2 条明确："本实现改的正是选模排序（资格/序关系）……论证却写'非选择规则变更/排序键不变'，与契约移交的边界表述和自身代码双重不符。"
- "Non-converged ARMAX cannot beat converged ARX **at same confidence level**" **属实但不完整**：`converged` 只在同级决胜。G3 复核 R2 反例：未收敛 ARMAX @D 级 sort key `(2,0,10.25,True)` 仍压过收敛 ARX @E 级 `(1,1,6.91,True)`——**跨可信度级缺口未关闭**，协议未披露。
- "R9 scope unchanged" **属实**（G3 复核 R8 PASS）。

另：协议对 G3 修复的描述（排序键降权）与同目录 `f02/g3-arma-x-convergence-qualification-论证.md`（可信度封顶 INCONCLUSIVE、排序键不变）互相矛盾；后者与代码不符，已由 G3 复核判 REWORK。协议采用的描述更接近代码，但 "(not selection rule change)" 的定性仍须更正。

### 4. 工具链 — REWORK（否决点 A）

协议 `tools` 与磁盘实际对照（证据根下 `shasum -a 256` 实测）：

| 协议条目 | 协议声明路径 | 实际路径 | SHA 核对 | 结果 |
|---|---|---|---|---|
| freeze | `f02/freeze_capacity_configuration_v2.py (SHA 268873...)` | `f02/freeze_capacity_configuration_v2.py` | `2688732523d3f930…` 前缀吻合 | 路径✓ SHA✓ |
| runner | `f02/run_capacity_series.py` | **证据根** `run_capacity_series.py` | 协议**未给 SHA**；实测 `2eb03f4306c30da6…`（与旧草稿 toolSources 一致） | **路径✗ SHA缺** |
| extraRunner | `f02/run_capacity_extra_case.py` | **证据根** `run_capacity_extra_case.py` | 协议**未给 SHA**；实测 `c34175b0ac0534dd…` | **路径✗ SHA缺** |
| nativeObserver | `f02/observe_capacity_native_identity.py (f9ef...)` | **证据根** `observe_capacity_native_identity.py` | `f9ef43b64c5b3dee…` 前缀吻合（与补充-05 一致） | **路径✗** SHA✓ |
| adapter | `f02/adapt_capacity_artifacts.py (fb6e...)` | `f02/adapt_capacity_artifacts.py` | `fb6e7f2add4bad51…` 前缀吻合 | 路径✓ SHA✓ |

说明：
- 3/5 工具的 `f02/` 前缀路径错误。按协议字面执行会找不到文件。`find` 确认 f02/ 下无这三份的副本。
- SHA 钉扎不完整：runner / extraRunner 无 SHA。旧草稿 `protocol-c452-draft.json` 的 `toolSources` 对 `run_capacity_series.py` 钉了 `2eb03f43…`/13018B，本次协议丢掉了该钉扎。
- adapter 即投前清单所称 "adapter v3 fb6e"，身份正确。

### 5. 输入状态 — PASS

| 核对点 | 协议原文 | 事实来源 | 结果 |
|---|---|---|---|
| 正常窗 | `normalWindows: 20` | `input-generation-complete.json` `prepared: 20`；`input-series-audit-v2/progress.json` ALL_INPUTS_PASS 20/20 | 一致 |
| 额外窗 | `extraWindows: 4` | `f02/extra-inputs/complete.json` preparedAndAudited=4 | 一致 |
| 合成绑定 | `7000 rows valid_from=2026-10-15T12:00Z basis=SYNTHETIC_FIXTURE (applied)` | 接续检查点：e213 已执行 UPDATE 7000、committed=true、persistedReadbackVerified=true；非作者 `f02-s01-actual-input-review` PASS | 一致 |
| 输入状态 | `20+4 windows generated and reviewed` | 投前清单：24 窗生成结束、闭端点 672 请求已核 | 一致 |

### 6. 候选身份 — PASS（写就时点）

| 核对点 | 协议原文 | 事实来源 | 结果 |
|---|---|---|---|
| 候选 SHA | `12e821a80861d452a4f5a2f3fcf4763afa5f8f9e` | 接续检查点 MiMo 节：fix/final-audit-remediation@12e821a8 | 一致 |
| PR head | `prHeadSha` 同上 | PR #1 draft | 一致 |
| CI | `ciRunId: 39050996941`，`success (Backend/Critical/Frontend all pass)` | 接续检查点："run 39050996941，checkout 12e821a8，三 job 全部 success" | 一致 |
| main | `mainShaAtPreparation: b676ea59…`，`githubMainSha: 86301e5e…` | 接续检查点固定身份表 | 一致 |
| 运行环境 | runtimeDir / testDb `clpm_capacity_20261017_s01` / redisPort 17123 | 接续检查点 | 一致 |
| 版本 | KPI_CALC_v2.0 / pre_v2 / tqpv3 | 协议自载；与旧草稿 versions 同源 | 未发现冲突 |

---

## 遗漏的前置条件与风险（审核要求第 7 项）

### 否决点 A — 工具路径/SHA 缺陷（见 §4）

**建议**：`tools` 五条改为真实路径 + 完整 SHA-256（可直接采用实测值）：

```
freeze:        f02/freeze_capacity_configuration_v2.py  2688732523d3f9300c9d11e2dbf9194248a7be195c18cbfa313f5bbf020e66e5
runner:        run_capacity_series.py                   2eb03f4306c30da671f18f367b7bdfe4d683eb21001c0c60265c67eeb75a8dae
extraRunner:   run_capacity_extra_case.py               c34175b0ac0534dd688b5530a4ade54d2d3cd3269e70acb5600d473b0ea9bfb2
nativeObserver:observe_capacity_native_identity.py      f9ef43b64c5b3deeed24d8429f6a5d2eaaf96ccd40c3e41a99ddceff26a245f4
adapter:       f02/adapt_capacity_artifacts.py          fb6e7f2add4bad514830f675944e7b29f508cf273e4ce1a4a25355751ded4674
```

（或把三份根级工具移入 f02/ 后统一前缀；两种做法取一，但正文与磁盘必须一致。）

### 否决点 B — 缺方法勘误/补充与来源绑定

投前清单 `f02/正式20批投前待填清单与最小封协议步骤-20261017.md` §2 末段明文：

> 新 M 协议须补**方法勘误 01/补充 05**、native 源 f9ef、adapter v3 fb6e 及其实际 refs……正文、ref 与实际原件不一致即退回。

协议现状：`invariants` 只有名称字符串，无任何 method/ref 绑定。至少应钉扎（路径 + sha256，可引用 `independent/tools-supplement-source-sha256-v4.json`）：

| 应绑原件 | 作用 | 现状 |
|---|---|---|
| `independent/冻结验收协议.md` | 九条件屏障、四业务、600s、F02 判定标准的正文 | 协议未引用 |
| `independent/协议方法勘误-01.md` | AUTO_MODE_RATE 含 APC；窗含尾 3601s 边界；容差 0.005pp/1e-8pp | 协议未引用 |
| `independent/协议方法补充-02-资源与版本身份.md` | 资源 accounting / 版本身份 | 协议未引用 |
| `independent/协议方法补充-03-节点作用域与进程时间来源.md` | nodes 条件字段来源；九条件同轮事实 | 协议未引用 |
| `independent/协议方法补充-04-事件PID与闭合进程集合.md` | task_terminal/parent_terminal 的 PID 语义 | 协议未引用 |
| `independent/协议方法补充-05-原生进程连续取证.md` | **直接约束 nativeObserver**：七身份、lsof、≤5s 间隔、600s 边界 | 协议未引用 |
| `independent/协议方法补充-06-平稳率输入块既有别名.md` | steady_rate↔stability_rate 别名 | 协议未引用 |
| `independent/history-input-refs.json` | 原 history 6+3 九报告 | 协议未引用 |
| 原四 oracle / `independent_kpi_oracle*.py` | 四业务数值验收 | 协议未引用 |
| `f02/series-s01/input-closed-root-check.json` 等 | 输入闭合 | 协议未引用 |
| eventProcessProvenance / nodeScopeProvenance | 旧草稿有，新协议无 | 缺 |

`nineConditions` 若不绑补充 03/04/05，执行方无法确定 nodes/task_terminal/parent_terminal 的独立核查口径。`invariants` 亦应补 `scopeLimits` 语义（四项用户豁免保留、G6/G7=CONDITIONAL_PASS、不合 main、不碰生产）——旧草稿 `scopeLimits` 四条被丢掉了。

### 否决点 C — g3FixNote 定性错误 + 跨级缺口未披露（见 §3）

**建议（二选一，与 G3 复核 R1 修复指引对齐）**：
- **方案 A（保留现实现）**：g3FixNote 改为如实描述——"排序键插入 converged 位做同级降权（选模排序变更，经授权的资格政策调整）；未收敛 ARMAX 在同级可信度下不胜收敛模型；跨可信度级仍有残余胜出可能（R2），此边界显式接受/或另补封顶"。同时重写 `g3-arma-x-convergence-qualification-论证.md`，删去"封顶 INCONCLUSIVE/排序键不变"。
- **方案 B（保留论证）**：实现改为 `_cap_confidence(INCONCLUSIVE)` + 三元组排序键，协议相应改写；**candidateSha 必变，CI 须重跑，本协议全部身份绑定作废重签。**

### 否决点 D — readinessPending 已过时 + G3 REWORK 连锁

协议写就（01:11:47）时 `readinessPending` 列 "Non-author review of G3 ARMAX convergence qualification fix" 是准确的。但 01:30:46 该复核已完成并判 **REWORK**（`independent/g3-convergence-qualification-review/receipt.md`）。

风险传导：
1. G3 复核 REWORK 未关闭前，不得投正式 20 批（协议自身 `formalReady: false` 已声明，此处予以确认并升级为硬门）。
2. 若走方案 B 改实现 → 新 head → 新 CI → 新 config freeze → **协议须整体重签**（candidateSha/ciRunId/mainSha/工具 SHA 全部重核）。
3. 若走方案 A 只改文档 → candidateSha 12e821a8 可保留，但协议 g3FixNote 与 `readinessPending` 须更新，并补 R2 跨级缺口的显式裁决。
4. 另：G3 复核附加观察指出新逻辑**无任何单测**（`_is_model_converged`/`ARMAX_NOT_CONVERGED` 零命中）。建议在关闭 G3 前补最小单测，否则 20 批通过也不能为该资格守卫背书。

### 其他风险（不单独否决，修订时应知）

1. **config-freeze-12e821a8 观察**：审核过程中该目录已由空变为完整（01:21 左右，含 config-freeze.json / sha256-manifest.json 等）。协议 `readinessPending` 第 3 条 "New config freeze for 12e821a8" 在写就时正确；修订版协议必须把**实际 freeze 的 configDigest / manifest SHA** 填入（旧草稿有 `configDigest`/`configFreezeManifest` 字段，新协议完全缺失该字段位）。不得复用 bc48701e 的 374f9964… 摘要。
2. **B 组 150 的两类原因**（100 DATA_INSUFFICIENT + 50 覆盖缺口）应在核对口径中分列，见 §1 建议。
3. **cold/hot 方法**在 `cold-hot-definitions.json` 已冻结（冷=TD 重启 + pdb 键 UNLINK；热=真实 prewarm_cache），协议只给矩阵不给冷热定义。执行方须以该 JSON 为准；建议在协议 tools 或 refs 段加一行指向，防止"伪冷态"（冻结协议 REWORK 条件明文禁止伪造冷态）。
4. **独立重跑门**：冻结协议 §3.4 要求"至少独立重跑一组关键工况（需 Keeper 另行授权投递）"。协议未列此门。20 批跑完 ≠ F02 PASS；须预留独立复跑授权与窗口。
5. **连续两个真实已过去日期**：cold-hot `required` 与冻结协议 §3.4 均要求跨两个 UTC 日期；planned-batches 实际跨 2026-10-15/16，满足。协议未复述，无冲突。
6. **资源错误/恢复/最终 FAILED 路径**（冻结协议 §3.3）与额外 4 工况（fault-recovery / fault-persistent）相衔接；协议 extraCases=4 已覆盖计数，但未写明四工况的预期行为（注入 1850、耗尽三次后 FAILED 持久化等）。建议在 extraCases 旁补 purpose 列表（可直接引 `f02/extra-inputs/plan.json`）。

---

## 判定汇总

| 审核要求 | 判定 | 说明 |
|---|---|---|
| 1 容量矩阵 | **PASS** | 20 批/1000/600s/A=1000/B=850+150/额外 4 不进分母 全部一致 |
| 2 资源预算 | **PASS** | 6GiB/4.8GiB/≥1GiB/pool0 测试 profile 全部一致，生产边界正确 |
| 3 不变量 | **基本 PASS** | R9、22/30s、四业务、九条件、不改 expectedStatus/分母/断言 全部一致；g3FixNote 定性错误并入否决点 C |
| 4 工具链 | **REWORK** | 3/5 路径错误，2/5 缺 SHA（否决点 A） |
| 5 输入状态 | **PASS** | 20+4 窗、7000 合成绑定已修正 属实 |
| 6 候选身份 | **PASS**（写就时点） | 12e821a8 / CI 39050996941 全绿 属实；G3 方案 B 将使其失效 |
| 7 前置与风险 | **REWORK** | 缺方法 01/05 等绑定（否决点 B）；g3FixNote（C）；G3 REWORK 连锁（D） |

**总判定：REWORK**

协议核心验收口径（矩阵/预算/分母/期望/不变量名称）**无需改动**，修订只涉及：工具身份钉扎、方法与来源 refs、g3FixNote 如实化、readinessPending 与 config freeze 字段更新。修订后本审核可快速复核；**正式投递仍以 G3 REWORK 关闭 + 新 config freeze 绑定 + 本协议非作者复核通过为前置**，且方案 B 路径下须重跑 CI 并重签协议。

---

## 审核纪律声明

- 只读核对协议、证据原件与 runtime 树代码片段；未执行任何容量测试、未写数据库、未启停服务、未提交/推送。
- 未修改被审协议或任何产品代码。
- 本回执不构成 F02 容量验收 PASS，也不替代 G3 修复复核、config freeze 非作者准入或正式 20 批实测。
