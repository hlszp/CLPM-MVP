# G3 论证修正 + F02 协议修订 — 非作者独立统一复审回执

- **复审人**：独立复审子代理（非作者，未参与实现/论证/协议编写）
- **复审时间**：2026-10-18（CST）
- **复审性质**：只读核对 + 运行指定验证命令。未改代码/协议/文档，未操作环境。
- **被审对象**：
  1. G3 ARMAX 收敛资格修复（方案 A：保留实现，修正论证）——论证 v2 + 单测 7 例
  2. F02 容量协议修订——`protocol-12e821a8.json`（revisedAt 2026-10-17T17:44:05Z）
- **候选 HEAD**：`ba3b61009b5c108db59c4b7da8f4697fd3acf073`（= 12e821a8 实现 + ba3b6100 单测，git diff 确认仅新增测试文件）
- **总判定**：**G3 = PASS；协议 = REWORK（窄幅）→ 尚不可投正式容量**

---

## 总判定理由（先看这里）

| 项 | 判定 | 一句话理由 |
|---|---|---|
| G3 ARMAX 收敛资格（方案 A） | **PASS** | 论证 v2 已如实对齐实现（排序键降权、非封顶），跨级缺口显式登记并裁决，7 例单测覆盖三条关键路径，R9 契约保持 |
| F02 容量协议（修订版） | **REWORK** | tools/SHA/g3FixNote/configFreeze/readiness 五处已修正且经独立核验属实；但 methodBindings 内容仍有 3 处与原件事实不符 + 缺路径/SHA 钉扎 + scopeLimits 未补 |
| **可否投正式容量** | **否** | 协议 methodBindings 缺陷关闭前不可投；G3 侧已无障碍 |

G3 的 REWORK 已按方案 A 闭环（论证对齐实现 + 补单测 + 缺口显式登记）。协议的核心验收口径（矩阵/预算/分母/期望/不变量名称）本就无需改动，修订集中在文档绑定层——其中工具身份钉扎已做对，方法绑定层仍留有与上轮否决点 B 同类的"ref 与实际原件不一致"缺陷，故判窄幅 REWORK。

---

# 第一部分：G3 ARMAX 收敛资格修复 — PASS

## G1 论证 v2 是否如实描述实现（排序键降权，非封顶 INCONCLUSIVE）— **PASS**

| 核对点 | 论证 v2 原文 | 实现事实 | 结果 |
|---|---|---|---|
| 修复机制 | "排序键降权（同级决胜），不封顶可用性"（文首 v2 修正说明） | `pipeline.py:5178-5179` 插入 converged 位；全仓无 `ARMAX_NOT_CONVERGED` 的 `_cap_confidence` 调用 | 一致 |
| 排序键 | "`(confidence, converged, fitting_score, residual)` 其中 converged 为 1/0" | `return (level_order.get(c.confidence, 0), converged, c.fitting_score, c.residual_test_passed)` | 逐字段一致 |
| 选择规则定性 | "排序键变更**是**选择规则的一部分（v1 否认此点是错误的）。但这是**质量门**而非方法变更" | commit message "ARMAX convergence qualification in candidate sort"；`_candidate_sort_key` 即选模序关系 | 一致（已纠正 v1 的"非选择规则变更"错误） |
| 封顶主张 | **已删除** v1 全部"封顶 INCONCLUSIVE"表述 | 无封顶路径 | 一致 |
| 影响边界 | "收敛 ARMAX 行为完全不变 / ARX/CLIVC 行为完全不变（`_is_model_converged` 默认 True）/ 未收敛 ARMAX 仍在 candidates 列表" | `_is_model_converged` 无码返回 True；candidates 列表不删候选 | 一致 |

v1 的两处决定性错位（"封顶 INCONCLUSIVE" 与 "排序键不变"）在 v2 中均已消除，且 v2 显式承认 v1 错误。论证现可作为该实现的证据。

**判定：PASS**

## G2 跨级缺口是否显式登记 — **PASS**

论证 v2 专节"跨级缺口（已知边界，显式登记）"：

- 反例与上轮复审 R2 完全同构："未收敛 ARMAX@D `(2,0,score)` > 收敛 ARX@E `(1,1,score)`——D>E 时收敛位无法翻转"
- 显式裁决："当前不实施 [INCONCLUSIVE 封顶]"，并给出 3 条理由
- 对已证 Linux 红例的边界写明："两候选同为 E 级，收敛位决胜"；"跨级路径仍开"

符合方案 A 修复指引"显式裁决跨级缺口（接受则写明边界与理由）"。

**附加观察（不改判）**：不封顶的第 1 条理由"SOPDT loop-16、大缺口辨识两测试依赖此路径"证据偏弱：
- `loop-16` 确实存在（`backend/tests/golden/annotated_loops_dataset.json:498`，SOPDT，T1=40/T2=10，大开环时间常数）；`pipeline.py:2274/4472` 有 "loop-16 教训" 注释
- SOPDT 门禁测试（`test_annotated_loops_evaluation.py` `test_sopdt_loops_known_limitation`）断言的是"结构成功（产出候选）+ K 精度"，并不断言未收敛 ARMAX 胜出；INCONCLUSIVE 封顶不会把候选移出列表，结构成功断言仍可满足
- "大缺口辨识"未找到同名测试
- 该条仅为设计裁决理由（缺口已显式登记并接受），不影响"登记属实"这一复审要点

**判定：PASS**（缺口登记与裁决到位；理由引用可再收紧，非否决）

## G3 单测是否覆盖关键路径 — **PASS**

文件：`backend/tests/test_g3_convergence_sort_key.py`（7 例，`def test_` 计数=7）

| 要求路径 | 测试 | 断言 | 结果 |
|---|---|---|---|
| 同级收敛优先（Linux 修复路径） | `test_converged_beats_nonconverged_same_confidence` | 同 E 级，收敛 ARX(6.91) > 未收敛 ARMAX(10.25) | 覆盖 |
| 跨级缺口 | `test_nonconverged_higher_confidence_still_wins` | 未收敛@D > 收敛@E（断言缺口存在，与文档登记一致） | 覆盖 |
| fitting_score 决胜（双收敛） | `test_both_converged_fitting_score_decides` | 同级同收敛位，高 fitting 胜 | 覆盖 |
| fitting_score 决胜（双未收敛） | `test_both_nonconverged_fitting_score_decides` | 同级同收敛位，高 fitting 胜 | 覆盖 |
| `_is_model_converged` 码表语义 | 3 例：无码→True / `ARMAX_NOT_CONVERGED`→False / 其他码（SIGN_MISMATCH、LOW_SNR）→True | 不误伤其他 reason_code | 覆盖 |

**验证命令**（任务指定，原样执行）：
```
cd /Users/zhangping/DEV/clpm-remediation-20261017 && PYTHONDONTWRITEBYTECODE=1 CLPM_TEST_MODE=1 CLPM_SKIP_EXIT_HOOKS=1 \
  /Users/zhangping/DEV/CLPM-MVP/backend/.venv/bin/python -B -m pytest \
  backend/tests/test_g3_convergence_sort_key.py backend/tests/test_g3_app_applicability.py -q
```
**结果**：`21 passed, 6 warnings in 9.55s`（7 新增 + 14 既有 G3，0 failed）

跨级缺口测试以断言缺口存在的方式锁定已知边界（防止静默改变语义），与论证登记互为印证——这是正确做法。

**判定：PASS**

## G4 R9 契约是否保持 — **PASS**

| R9 约束 | 核对结果 | 证据 |
|---|---|---|
| 原方法范围（ARX+ARMAX 真实求解） | 未变 | `armax.py` 仍 `identify_arx` 初值 + 迭代 PEM；未引入 BJ/SRIVC/OE |
| 200 / 1e-6 | 未变 | `armax.py:43-44` `max_iter: int = 200, tol: float = 1e-6`；收敛判据 `max_diff < tol`（L145-146） |
| 22 样本 / 30s | 未变 | 本复审两 commit diff 不含等价测试文件；上轮 R8 已核 |
| 四业务断言 | 未变 | 同上 |
| 600s 门限 | 未变 | 不在 diff 内 |
| golden 保护清单 | 磁盘哈希与 golden 一致 | 独立重算：armax.py=`483afd65a820e0f6fe2e3b7ee3ebfac9568ff306f3741b3bcf673f49d060eddd`（=golden）；pipeline.py=`828fdc12a5b1694aba7edee8f33164c7e3f27a610959e0a39c45185b0ee18fad`（=golden） |

`ba3b6100` 相对 `12e821a8` 的 diff 恰 1 文件（`test_g3_convergence_sort_key.py` +77 行），生产代码零改动——协议绑定 candidateSha=`12e821a8` 在生产代码身份上仍有效。

**判定：PASS**

### G3 分项汇总

| 项 | 判定 |
|---|---|
| G1 论证 v2 如实描述实现 | **PASS** |
| G2 跨级缺口显式登记 | **PASS** |
| G3 单测覆盖关键路径 | **PASS**（21/21） |
| G4 R9 契约保持 | **PASS** |
| **G3 总判定** | **PASS** |

---

# 第二部分：F02 容量协议修订 — REWORK（窄幅）

被审物：`f02/series-s01/protocol-12e821a8.json`（schemaVersion=2，revisedAt=2026-10-17T17:44:05Z，revisedReason 已声明对应上轮 REWORK）

## P1 tools 路径是否修正 — **PASS**

| 工具 | 协议 path | 实际位置 | 结果 |
|---|---|---|---|
| freeze | `f02/freeze_capacity_configuration_v2.py` | f02/ 下 | ✓ |
| runner | `run_capacity_series.py` | 证据根 | ✓（已去错误 f02/ 前缀） |
| extraRunner | `run_capacity_extra_case.py` | 证据根 | ✓ |
| nativeObserver | `observe_capacity_native_identity.py` | 证据根 | ✓ |
| adapter | `f02/adapt_capacity_artifacts.py` | f02/ 下 | ✓ |

五条 path 与磁盘实际位置一一对应，按协议字面可定位文件。

**判定：PASS**

## P2 工具 SHA 是否完整（非前缀）— **PASS**

独立以 Python hashlib.sha256 重算五个工具全文摘要，与协议声明逐字比对：

| 工具 | 协议 sha256 | 独立重算 | 长度 | 结果 |
|---|---|---|---|---|
| freeze | `2688732523d3f9300c9d11e2dbf9194248a7be195c18cbfa313f5bbf020e66e5` | 同 | 64 | MATCH |
| runner | `2eb03f4306c30da671f18f367b7bdfe4d683eb21001c0c60265c67eeb75a8dae` | 同 | 64 | MATCH |
| extraRunner | `c34175b0ac0534dd688b5530a4ade54d2d3cd3269e70acb5600d473b0ea9bfb2` | 同 | 64 | MATCH |
| nativeObserver | `f9ef43b64c5b3deeed24d8429f6a5d2eaaf96ccd40c3e41a99ddceff26a245f4` | 同 | 64 | MATCH |
| adapter | `fb6e7f2add4bad514830f675944e7b29f508cf273e4ce1a4a25355751ded4674` | 同 | 64 | MATCH |

全部为完整 64 位十六进制（非前缀），且与磁盘内容一致。上轮 runner/extraRunner 缺 SHA 的问题已关闭。

**判定：PASS**

## P3 methodBindings 是否补充 — **REWORK（否决点残留）**

**已做对的部分**：键位已补齐——`methodErratum01`、`supplement02`~`supplement06`、`historyInputRefs`、`fourOracles` 共 8 个键均存在，覆盖上轮要求的勘误01/补充02-06。键的"是否补充"字面要求已满足。

**残留缺陷（构成 REWORK）**：

### P3a 三处描述与原件事实不符（与 g3FixNote 上轮缺陷同类）

以各文档首行标题独立核对：

| 键 | 协议声称 | 原件实际主题 | 判定 |
|---|---|---|---|
| methodErratum01 | "required - binds R9 method scope" | 《冻结协议的方法勘误 01（投前，未读真实结果）》——AUTO_MODE_RATE 含 APC、窗含尾 3601s 边界、容差 0.005pp/1e-8pp | 表述过窄，R9 方法范围主要在冻结验收协议/代码，勉强可接受 |
| **supplement02** | "required - **algorithm contract**" | 《投前方法补充02：**冻结资源上界与版本映射**》 | **不符**——资源/版本身份，非算法契约 |
| supplement03 | "required - node scope provenance" | 《节点作用域与进程时间来源》 | 一致 |
| supplement04 | "required - event provenance" | 《事件 PID 与闭合进程集合》 | 基本一致 |
| **supplement05** | "required - **nine-condition audit basis**" | 《**原生进程连续取证**》——约束 nativeObserver（七身份/lsof/≤5s/600s 边界） | **不符**——九条件基准主要在冻结验收协议+补充03；补充05 是原生取证方法 |
| **supplement06** | "required - **history input refs**** | 《**平稳率输入块既有别名**》——steady_rate↔stability_rate | **不符**——history 输入 refs 是另一键（historyInputRefs）的职责 |

按上轮否决点 B 的退回标准"**正文、ref 与实际原件不一致即退回**"，P3a 即退回条件。执行方按协议描述去补充05 找九条件审计基准、去补充06 找 history refs，会找错文件。

### P3b 缺路径 + SHA-256 钉扎

上轮修复指引明文："至少应钉扎（**路径 + sha256**，可引用 `independent/tools-supplement-source-sha256-v4.json`）"。本轮 methodBindings 值仅为 `"required - <描述>"` 字符串，无路径、无摘要。8 个绑定全部未钉扎。

可直接采用的实测摘要（本次复审独立重算，供修订用）：

| 原件 | sha256（前 16 位示意，修订时用全文） |
|---|---|
| `independent/协议方法勘误-01.md` | `df43afc449c93a17…` |
| `independent/协议方法补充-02-资源与版本身份.md` | `d34b6277eeda5439…` |
| `independent/协议方法补充-03-节点作用域与进程时间来源.md` | `dce0d31ffe3b4726…` |
| `independent/协议方法补充-04-事件PID与闭合进程集合.md` | `105a2c6ff83d664d…` |
| `independent/协议方法补充-05-原生进程连续取证.md` | `78633c800b201715…` |
| `independent/协议方法补充-06-平稳率输入块既有别名.md` | `dd5867597e64b34e…` |

### P3c scopeLimits 仍未补

上轮指出"旧草稿 `scopeLimits` 四条被丢掉了……（四项用户豁免保留、G6/G7=CONDITIONAL_PASS、不合 main、不碰生产）"。修订版 top-level keys 中**无 `scopeLimits`**，invariants 内亦无等价语义。此为安全边界声明，投前协议缺失会使执行方无从核对豁免与生产边界。

**判定：REWORK**（键位已补、内容未达"与原件一致 + 可核验钉扎"标准）

## P4 g3FixNote 是否如实 — **PASS**

修订版原文：
> ARMAX convergence qualification: sort key now includes converged bit (same-confidence demotion). **This IS a selection rule change (non-author review confirmed)**. Cross-level gap (nonconverged@D can beat converged@E) is a known limitation, documented and unit-tested. R9 method scope (ARX+ARMAX real solve, 200/1e-6) unchanged. Argumentation v2 aligned with implementation.

| 要点 | 核对 | 结果 |
|---|---|---|
| "IS a selection rule change" | 与实现、commit message、本轮 G3 论证 v2 一致；纠正了上轮 "not selection rule change" | ✓ |
| 跨级缺口披露 | 明确写出 nonconverged@D can beat converged@E，与 G3 论证登记、单测 `test_nonconverged_higher_confidence_still_wins` 一致 | ✓ |
| R9 不变 | 与本轮 G4 PASS 一致 | ✓ |
| 论证 v2 对齐 | 与本轮 G1 PASS 一致 | ✓ |

上轮否决点 C 已关闭。

**判定：PASS**

## P5 configFreeze 是否绑定 12e821a8 — **PASS**

| 字段 | 协议值 | 独立核验（`config-freeze-12e821a8-v2/config-freeze.json`） | 结果 |
|---|---|---|---|
| status | FROZEN | `status: FROZEN` | ✓ |
| revision | 3 | `configRevision: 3` | ✓ |
| **digest** | `24e23e9e5d83c40ef9c2bc6628bb2f5eeb355614b5f7b64706793180e45c3988` | `configDigest: 24e23e9e5d83c40ef9c2bc6628bb2f5eeb355614b5f7b64706793180e45c3988` | ✓ |
| loopCount | 1000 | 1000 | ✓ |
| mappingCount | 7000 | 7000 | ✓ |
| predictedNodeCount | 20 | 20 | ✓ |
| outDir | `f02/series-s01/config-freeze-12e821a8-v2` | 目录存在且含 config-freeze.json / sha256-manifest.json 等 14 项 | ✓ |

**与 bc48701e 的区分正确**：`config-freeze-bc48701e/config-freeze.json` 的 `configDigest=374f9964e62d6ae7…`（candidateSha=bc48701e…）——协议**未**误用该摘要。另注：`config-freeze-12e821a8/`（v1）status=BLOCKED（API read failed），协议正确指向 v2 目录。

**判定：PASS**

## P6 readinessPending 是否合理 — **PASS**

修订版：
```json
"formalReady": false,
"readinessPending": [
  "G3 REWORK closure (argumentation v2 + unit tests done, pending re-review)",
  "Protocol revision re-review after this update",
  "Config freeze 12e821a8 binding verified"
]
```

| 核对点 | 结果 |
|---|---|
| formalReady=false | 正确——本轮复审未结束前不可标 true |
| G3 项表述 | 与事实一致（v2+单测已完成、待复审）；本轮复审后该项可关闭为 PASS |
| 协议自身待复审 | 与本回执对应，合理 |
| configFreeze 绑定 | "verified" 与 P5 核验一致（该项可视为已完成） |
| 上轮"已过时的 G3 非作者复核"条目 | 已替换为当前状态，不再陈旧 |

**观察（不改判）**：`candidateSha`/`ciRunId` 仍绑 `12e821a8` / CI `39050996941`。`ba3b6100` 仅新增测试、生产代码与 12e821a8 逐字节相同，容量测试跑的是生产算法，故该绑定对容量有效。但 ba3b6100 本身未见独立 CI run 记录——若验收口径要求"候选 HEAD 全量过 CI"，需补跑或在协议注明"测试-only commit，生产身份=12e821a8"。

**判定：PASS**

### 协议分项汇总

| 项 | 判定 |
|---|---|
| P1 tools 路径 | **PASS** |
| P2 工具 SHA 完整 | **PASS**（5/5 全文匹配） |
| P3 methodBindings | **REWORK**（键位已补；3 处描述与原件不符 + 无路径/SHA + scopeLimits 缺） |
| P4 g3FixNote | **PASS** |
| P5 configFreeze 绑定 | **PASS**（24e23e9e…，非 374f…） |
| P6 readinessPending | **PASS** |
| **协议总判定** | **REWORK**（窄幅，仅 P3） |

---

## REWORK 精确差异（协议 P3，供作者一次关闭）

不改动任何验收口径（分母/expectedStatus/600s/九条件/四业务断言）即可修复：

1. **改对三处描述**（或改为引用实际标题）：
   - `supplement02`："required - algorithm contract" → 指向《投前方法补充02：冻结资源上界与版本映射》（资源 accounting/版本身份）
   - `supplement05`："required - nine-condition audit basis" → 指向《协议方法补充 05：原生进程连续取证》（nativeObserver 方法约束：七身份/lsof/≤5s/600s）
   - `supplement06`："required - history input refs" → 指向《协议方法补充06：平稳率输入块既有别名》（steady_rate↔stability_rate）
   - 顺带收紧 `methodErratum01` 描述（勘误01 实为 AUTO_MODE_RATE 含 APC、窗尾 3601s 边界、容差口径，非泛称 R9）
2. **补路径 + 全文 sha256 钉扎**：8 个绑定项每项给 `{path, sha256}`；可引 `independent/tools-supplement-source-sha256-v4.json`，或用本次实测值。至少钉扎：勘误01、补充02~06、`history-input-refs.json`、四 oracle（`independent/independent_kpi_oracle.py`、`independent_kpi_oracle_v2.py`）、`independent/冻结验收协议.md`。
3. **补 `scopeLimits`**（四条）：四项用户豁免保留、G6/G7=CONDITIONAL_PASS、不合 main、不碰生产。

修订后本复审可快速复核 methodBindings 一节。

---

## 其他观察（不改判，供投前参考）

1. **B 组 150 拆分**：`expectedB` 仍为 "850 EVALUATED / 150 INCONCLUSIVE"，未分列 100 DATA_INSUFFICIENT + 50 覆盖缺口。上轮已建议分列，非否决。
2. **cold/hot 定义指向**：协议未引 `cold-hot-definitions.json`（冷=TD 重启+pdb UNLINK、热=prewarm_cache）。执行方须以该 JSON 为准防伪冷态。
3. **独立重跑门**：冻结协议 §3.4 要求 Keeper 另行授权独立重跑一组关键工况；协议未列此门。20 批跑完 ≠ F02 PASS。
4. **G3 论证理由引用**："大缺口辨识"测试未检索到同名项；若作者能给出具体测试 ID，建议在论证中写明，增强不封顶裁决的可核验性。
5. **ba3b6100 CI**：见 P6 观察。

---

## 签署

| 项 | 判定 |
|---|---|
| G1 论证 v2 如实描述实现 | **PASS** |
| G2 跨级缺口显式登记 | **PASS** |
| G3 单测覆盖关键路径 | **PASS** |
| G4 R9 契约保持 | **PASS** |
| **G3 总判定** | **PASS** |
| P1 tools 路径 | **PASS** |
| P2 工具 SHA 完整 | **PASS** |
| P3 methodBindings | **REWORK** |
| P4 g3FixNote | **PASS** |
| P5 configFreeze 绑定 | **PASS** |
| P6 readinessPending | **PASS** |
| **协议总判定** | **REWORK** |
| **总判定** | **G3 PASS + 协议 REWORK → 不可投正式容量** |

**正式容量投递前置**（更新后的门）：
1. ~~G3 REWORK 关闭~~ —— **本轮已 PASS**
2. 协议 P3 methodBindings 修订关闭（本回执 §REWORK 精确差异）
3. 协议修订后非作者复核通过
4. （建议）ba3b6100 CI 记录或声明测试-only 身份等价
5. 投递仍须 Keeper 授权 + 独立重跑门窗口

## 环境与纪律声明

- 只读复审：读取论证 v2、单测、协议 JSON、config-freeze 元数据、golden manifest、方法补充原件标题；独立重算工具 SHA 与 golden 哈希；按任务指定命令运行 pytest。
- **未改**任何代码/协议/文档；**未** commit/push；**未**操作生产或开发容器；**未**启停 Worker/Beat；**未**写数据库。
- 本回执不构成 F02 容量验收 PASS，也不替代正式 20 批实测与 Keeper 授权。
