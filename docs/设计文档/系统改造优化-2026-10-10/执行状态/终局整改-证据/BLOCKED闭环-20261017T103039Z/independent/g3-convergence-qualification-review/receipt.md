# G3 ARMAX 收敛资格修复 — 非作者独立复验回执

- **复验人**：独立复验子代理（非作者）
- **复验时间**：2026-10-18
- **被验物**：`/Users/zhangping/DEV/clpm-remediation-20261017`
  - 分支 `fix/final-audit-remediation`
  - HEAD=`12e821a80861d452a4f5a2f3fcf4763afa5f8f9e`（与任务声明一致，工作区 clean）
  - 变更 commit：`12e821a8 fix(tuning): ARMAX convergence qualification in candidate sort`
  - 变更文件恰 3 个：`armax.py` / `pipeline.py` / `refactor_protected_manifest.json`
- **论证文档**：`.../f02/g3-arma-x-convergence-qualification-论证.md`
- **总判定**：**REWORK**

---

## 总判定理由（先看这里）

**REWORK 的唯一决定性原因：论证文档与实现不一致（复验要求第 1 项的否决条件）。**

| 论证文档声称 | 实际实现 |
|---|---|
| "ARMAX 候选 `converged=False` 时**可信度封顶 INCONCLUSIVE**"（§变更内容 2、§为什么封 INCONCLUSIVE 而非 D/C、质量门表"ARMAX_NOT_CONVERGED→封顶 INCONCLUSIVE"） | **不做任何可信度封顶**。`pipeline.py:2484-2490` 注释明文："不封顶可用性（未收敛仍可作兜底），但在排序键中降权"。全仓无任何对 `ARMAX_NOT_CONVERGED` 的 `_cap_confidence` 调用 |
| "**非选择规则变更**：排序键 `(confidence, fitting_score, residual)` **不变**" | 排序键**已改**为 `(confidence, converged, fitting_score, residual)`（`pipeline.py:5179`）；`_candidate_sort_key` 即选模规则本体 |

commit message 与代码注释描述的是"排序键降权"方案；论证文档描述的是"封顶 INCONCLUSIVE"方案。两者是**两套不同修复策略**，不能同时为真。按复验要求"若论证与实现不一致，判 REWORK"，判 **REWORK**。

代码本身对已观测到的 Linux 失败场景（同为 E 级）是有效的，R9 冻结面未破坏，测试全绿——但论证不能作为该实现的证据，且论证自述的 INCONCLUSIVE 封顶恰恰是为关闭实现留下的跨可信度级缺口而写（见 §R2）。

---

## 分项判定

### R1 代码变更与论证一致（排序键）— **REWORK**

**要求**：排序键从 `(confidence, fitting_score, residual)` 变为 `(confidence, converged, fitting_score, residual)`，且与论证一致。

**排序键变更本身 — 属实**：
- 变更前：`pipeline.py` `_candidate_sort_key` 返回 `(level_order.get(c.confidence, 0), c.fitting_score, c.residual_test_passed)`
- 变更后：`pipeline.py:5178-5179`
  ```python
  converged = 1 if _is_model_converged(c) else 0
  return (level_order.get(c.confidence, 0), converged, c.fitting_score, c.residual_test_passed)
  ```
- 新增 `_is_model_converged`（`pipeline.py:5182-5185`）：`c.evidence.reason_codes` 含 `ARMAX_NOT_CONVERGED` → False，否则 True。

**与论证一致性 — 不成立（决定性证据）**：

| 出处 | 论证原文 | 实现事实 |
|---|---|---|
| 论证 §变更内容 2 | "ARMAX 候选 `converged=False` 时可信度**封顶 INCONCLUSIVE**，reason_code 加 `ARMAX_NOT_CONVERGED`" | reason_code 已加；**可信度封顶不存在** |
| 论证 §非选择规则变更 | "排序键 `(confidence, fitting_score, residual)` **不变**；…仅在候选已算出后加可靠性质量门" | 排序键**已变**；改的正是选模排序，不是叠加质量门 |
| 论证 质量门表 | "`ARMAX_NOT_CONVERGED` \| **封顶 INCONCLUSIVE** \| 参数未稳定" | 无 `_cap_confidence(..., INCONCLUSIVE)` 路径 |
| 论证 §为什么封 INCONCLUSIVE 而非 D/C | "封 D（2）仍压 E（1）。封 INCONCLUSIVE（0）确保未收敛模型**不胜出任何有级候选**" | 实现只在**同级**降权，不阻止跨级胜出（见 R2 反例） |
| 实现 `pipeline.py:2484-2488` | （注释）"不封顶可用性…但在排序键中降权，**同级可信度时**收敛模型胜出" | 与论证封顶主张直接冲突 |

**判定**：**REWORK**。须二选一收敛后再审：(a) 把论证改写为实际实现的"排序键降权"方案，并补上跨级缺口的显式裁决；或 (b) 把实现改为论证所述的 INCONCLUSIVE 封顶。当前不能以该论证为据签署"已验证"。

---

### R2 附带发现：跨可信度级残留缺口（实现语义弱点）— **REWORK（并入 R1 修订范围）**

**要求背景**：论证自己写明"封 D（2）仍压 E（1）。封 INCONCLUSIVE（0）确保未收敛模型不胜出任何有级候选"——作者已识别同级降权不够。

**独立单测反例（只读运行时构造，未改任何代码）**：
```
unconv_D sort key = (2, 0, 10.25, True)   # 未收敛 ARMAX @ D
conv_E   sort key = (1, 1,  6.91, True)   # 收敛 ARX    @ E
unconv_D > conv_E  → True   # 未收敛仍胜出
```
排序键第一维是可信度；`converged` 仅在同级内决胜。**若未收敛 ARMAX 可信度高于收敛 ARX，本修复不能阻止其胜出。**

**对已观测 Linux 失败是否够用 — 够用**（这也是代码注释"同级可信度时"的边界）：
- Linux 红 run 选模事实（`independent/f02-g3-linux-two-run-originals/事实对账与因果边界.md` §要求→事实链 5）：
  - 绿：最佳逐窗 `ARX/E/6.27`，joint `ARX/E/6.91` 胜出
  - 红：最佳逐窗 `ARMAX/E/10.25`（E 级、未收敛、fitting 10.25），joint `ARX/E/6.91`（E 级）
  - 原键 `(E,10.25,..) > (E,6.91,..)` → 逐窗 ARMAX 胜、joint 失胜；新键 `(E,0,10.25) < (E,1,6.91)` → joint 恢复胜出
- 即：**已证失败路径上两候选同为 E 级，本修复可闭合**；未证路径（跨级）仍开。

**判定**：实现对已证症状有效，但残留缺口恰是论证主张封顶的原因。修订时必须显式裁决：接受缺口（写明边界）或补齐封顶。不得以"同级降权"冒充论证中的"不胜出任何有级候选"。

---

### R3 `ARMAXResult.converged` 正确设置 — **PASS**

**证据**（`backend/app/services/tuning_identification/armax.py`）：
- `ARMAXResult` 新增字段 `converged: bool = False`（L33），默认 False 不破坏既有构造调用
- 迭代前 `converged = False`（L71）
- 收敛判据处（L143-146）：
  ```python
  if max_diff < tol:
      converged = True
      break
  ```
- 返回时 `converged=converged`（L172）

**核对要点**：
- 判据是 `max_diff < tol`（A/B 更新量），**不是** `iterations == max_iter`——符合契约移交文件要求（"不能仅凭 `iterations==max_iter` 忽略最后一次恰好满足容差"）
- `max_diff` 仅比较 A/B（不含 C），与原收敛式一致，未扩大解释为全局 PEM 收敛
- 默认 `max_iter=200` / `tol=1e-6` 未改（L43-44）

**判定**：**PASS**

---

### R4 `ARMAX_NOT_CONVERGED` reason_code 正确添加 — **PASS**

**证据**（`backend/app/services/tuning_identification/pipeline.py`）：
- L2489-2490：`_algo_key == "armax" and not getattr(res, "converged", True)` → `physical_flag += ", ARMAX_NOT_CONVERGED"`（reason 字符串，可审计）
- L2502-2503：同条件 → `reason_codes.append("ARMAX_NOT_CONVERGED")`（进 `ModelEvidence.reason_codes`）
- 该 reason_codes 列表传入 `ModelEvidence(..., reason_codes=reason_codes, ...)`（L2539）→ 随 `CandidateModel.evidence` 携带
- `_is_model_converged` 读取的正是 `c.evidence.reason_codes`（L5184），链路闭合
- 运行时单测确认：含 `ARMAX_NOT_CONVERGED` → False；含 `LOW_COHERENCE` 等其他码 → True（不误伤）

**判定**：**PASS**（reason_code 标注本身正确；其"应否伴随封顶"归 R1/R2）

---

### R5 G3 测试 — **PASS**

**命令**：
```
PYTHONDONTWRITEBYTECODE=1 CLPM_TEST_MODE=1 CLPM_SKIP_EXIT_HOOKS=1 \
  /Users/zhangping/DEV/CLPM-MVP/backend/.venv/bin/python -m pytest backend/tests/test_g3_app_applicability.py -v
```
**结果**：`14 passed, 6 warnings in 9.58s`（全过）

逐条 PASSED（14/14）：
- `TestJointSnrAggregateEstimator` 5 例（low_snr / high_snr / identical / too_short / mixed_lengths）
- `TestJointWinnerLowSnrAnnotated` 3 例（joint_winner_annotated_not_silent / annotation_snr_matches_independent_recompute / annotation_survives_to_dict）← Linux 红的 3 例在此
- `TestMultiWindowPerWindowWinnerRegression` 1 例
- `TestMultiWindowDeepLowSnrFailure` 2 例
- `TestSingleWindowQuadrantSerialization` 2 例
- `TestSnapshotRoundTrip` 1 例（test_joint_low_snr_saved_and_reloaded）← Linux 红第 3 例在此

**判定**：**PASS**

---

### R6 受影响测试无回归 — **PASS**

**命令**：
```
... pytest backend/tests/test_tuning_identification.py \
      backend/tests/test_annotated_loops_evaluation.py \
      backend/tests/test_tuning_nan_cleaning.py -q
```
**结果**：`128 passed, 4 warnings in 22.79s`（0 failed）

**判定**：**PASS**

---

### R7 反向验证（恒 True 路径）— **PASS**

**方法**（满足"不改任何代码"禁令）：未临时改源文件；改用运行时 monkeypatch pytest 插件（`/tmp` 下，已删除），在 `pytest_configure` 中令 `pipeline._is_model_converged = lambda c: True`（等价于移除 `ARMAX_NOT_CONVERGED` 检查），不落盘、不改仓。

**结果**：`14 passed, 6 warnings in 8.69s` — G3 仍全过。

**解读（与任务预期一致）**：macOS 上 ARX 本就胜出（同 E 级、fitting 不靠未收敛 ARMAX 翻转），故禁用收敛检查后断言不翻转。**此步只证明代码路径可被旁路且测试在 macOS 不依赖该路径，不证明 Linux 修复有效**。恢复后（未打补丁）R5 亦全过。另见 R2 单测：同级 E 时新键确能让收敛 ARX 压过未收敛 ARMAX。

**判定**：**PASS**

---

### R8 R9 契约保留 — **PASS**

| R9 约束 | 核对结果 | 证据 |
|---|---|---|
| 原方法范围（ARX+ARMAX 真实求解） | 未变。仍 `identify_arx` / `identify_armax` 真实最小二乘求解；未引入 BJ/SRIVC/OE；`identify_armax` 内部仍从 ARX 初值走迭代 PEM | `armax.py:60` `arx = identify_arx(...)`；`pipeline.py:2252-2254` 调用点未改 |
| 200 / 1e-6 | 未变。`max_iter: int = 200`, `tol: float = 1e-6` 默认值原样；调用点 `identify_armax(u_train, y_train, d_try, na=na, nb=nb, nc=1)` 不覆盖默认 | `armax.py:43-44`；`pipeline.py:2254` |
| 原 22 样本 / 30s | 未变。`test_armax_vectorized_equivalence.py` / `test_arx_vectorized_equivalence.py` 均在且未改；两文件头部仍登记"unchanged 30-second test"；本 commit diff 不含任何测试文件 | git show `12e821a8 --stat` 恰 3 文件；两测试文件存在 |
| 原四业务断言 | 未变。G3 测试文件零改动（本 commit 不含 tests/）；R5 全绿 | 同上 |
| 600s 门限 | 未变（不在本 diff） | git stat |
| golden 保护清单 | 程序性更新且哈希与磁盘一致 | 见下 |

**golden hash 独立重算**：
```
armax.py   disk=483afd65a820e0f6fe2e3b7ee3ebfac9568ff306f3741b3bcf673f49d060eddd  golden=同  match=True
pipeline.py disk=828fdc12a5b1694aba7edee8f33164c7e3f27a610959e0a39c45185b0ee18fad  golden=同  match=True
baseline_head = 8fc850802612b6077f3b07c08324e3694945aa0d = HEAD^（正确锚定父提交）
```

**判定**：**PASS**

---

## 附加观察（不单独改判，供修订参考）

1. **无针对新逻辑的测试**：`backend/tests/` 内 `rg '_is_model_converged|ARMAX_NOT_CONVERGED|converged'` 零命中。G3 三例在 macOS 上无论有无该路径均绿（R7），Linux 红例亦无本地可复现门禁。建议补最小单测：同级 E 未收敛 ARMAX vs 收敛 ARX 的 `_candidate_sort_key` 序关系 + `_is_model_converged` 码表语义。
2. **契约口径**：`f02/g3-contract-ci-implementation/算法契约与Linux同Job诊断移交.md` 写明"joint 胜出后的低 SNR 处理只标注、不改选模或评级"、"是否在 pipeline 中判不合格是行为变化，不能称作纯性能等价修复"、"若证明确有未满足停止量的候选参与选模：先明确政策，再改资格"。本实现改的正是选模排序（资格/序关系），commit message 亦自承 "candidate sort"。论证却写"非选择规则变更/排序键不变"，与契约移交的边界表述和自身代码双重不符。修订时应把"这是经授权的资格政策变更"或"这不在授权内"说清楚，而不是否认变更性质。
3. **加性记录面**：`converged` 字段默认 `False`、`ARMAX_NOT_CONVERGED` 进 reason_codes、未收敛候选仍留在 candidates 列表——符合"保留失败候选/原因"的契约要求，此点做得对。
4. **未验证 Linux**：本机 macOS；Linux CI 红→绿不在本次可证范围。R5/R7 结论均不外推 Linux。

---

## 签署

| 项 | 判定 |
|---|---|
| R1 代码变更与论证一致 | **REWORK** |
| R2 跨级残留缺口 | **REWORK**（并入 R1 修订） |
| R3 `converged` 设置 | PASS |
| R4 `ARMAX_NOT_CONVERGED` | PASS |
| R5 G3 测试 | PASS（14/14） |
| R6 受影响测试 | PASS（128/128） |
| R7 反向验证 | PASS |
| R8 R9 契约 | PASS |
| **总判定** | **REWORK** |

**REWORK 修复指引**（择一，不得两头占）：
- **方案 A（保留实现）**：重写论证——删除"封顶 INCONCLUSIVE"与"排序键不变"表述，如实写"排序键插入 converged 位、同级降权"；显式裁决跨级缺口（接受则写明边界与理由，不接受则补封顶）；补 `_is_model_converged` / `_candidate_sort_key` 单测。
- **方案 B（保留论证）**：实现改为 `converged=False` 时 `_cap_confidence(confidence, INCONCLUSIVE)`，排序键恢复三元组；补单测断言未收敛 ARMAX 不胜出任何有级候选。

复验前不得称"已验证"（论证文档自身末节要求亦如此）。

**环境与纪律**：仅只读复验 + 运行测试 + /tmp 运行时 monkeypatch（已删）；未改被验仓任何文件（`git status` clean）、未 commit/push、未碰生产、未启 Worker/Beat。测试使用 `/Users/zhangping/DEV/CLPM-MVP/backend/.venv/bin/python`，环境变量 `PYTHONDONTWRITEBYTECODE=1 CLPM_TEST_MODE=1 CLPM_SKIP_EXIT_HOOKS=1`。
