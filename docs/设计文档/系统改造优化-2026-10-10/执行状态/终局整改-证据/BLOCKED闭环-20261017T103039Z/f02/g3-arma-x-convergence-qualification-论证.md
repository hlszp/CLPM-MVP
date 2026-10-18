# G3 ARMAX 未收敛候选资格论证（2026-10-17，v2 修正版）

> v2 修正：v1 论证称"封顶 INCONCLUSIVE + 排序键不变"，与实现错位。本版如实描述实际实现：排序键降权（同级决胜），不封顶可用性。

## 变更内容（实际实现）

1. `armax.py`：`ARMAXResult` 新增 `converged: bool` 字段，`identify_armax` 在 `max_diff < tol` 时置 True（默认 False）
2. `pipeline.py`：`_candidate_sort_key` 插入收敛位，同级可信度下收敛模型优先
3. `pipeline.py`：`_is_model_converged` 从 `reason_codes` 读取 `ARMAX_NOT_CONVERGED` 标记
4. `pipeline.py`：`ARMAX_NOT_CONVERGED` 入 `physical_flag` + `reason_codes`（双写）

**排序键**：`(confidence, converged, fitting_score, residual)` 其中 `converged` 为 1/0

## 为什么这是真实修复

**问题**：ARMAX 迭代 PEM 跑满 200 次仍 `max_diff ≥ 1e-6`（16/16 未收敛），但不检查收敛与否，未收敛模型凭 fitting_score 参与 best 排序。Linux 数值轨迹使未收敛 ARMAX 压过收敛 ARX → joint 失胜 → 3 断言失败。

**修复逻辑**：未收敛 = 参数未稳定 = 模型不可靠。排序键降权确保同级可信度下收敛模型胜出，未收敛模型不以 fitting_score 虚高压过收敛对手。

**与选择规则的关系**：排序键变更**是**选择规则的一部分（v1 否认此点是错误的）。但这是**质量门**而非方法变更：
- R9 方法范围（ARX+ARMAX 真实求解）不变
- 200/1e-6 收敛判据不变
- 22 样本/30s 等价测试不变
- 四业务断言、600s 门限不变
- Occam 规则不变

## 跨级缺口（已知边界，显式登记）

排序键第一维仍是可信度。反例：未收敛 ARMAX@D `(2,0,score)` > 收敛 ARX@E `(1,1,score)`——D>E 时收敛位无法翻转。

**对已证 Linux 红例够用**：两候选同为 E 级，收敛位决胜。

**跨级路径仍开**：若未来出现未收敛 ARMAX 跨级压过收敛 ARX 的场景，需升级为 INCONCLUSIVE 封顶（v1 论证的方案）。当前不实施，理由：
1. 封顶会改变未收敛模型的兜底可用性（SOPDT loop-16、大缺口辨识两测试依赖此路径）
2. 已证失败场景不跨级
3. 跨级场景无实证案例

## 影响边界

- 收敛 ARMAX 行为完全不变
- ARX/CLIVC 行为完全不变（`_is_model_converged` 默认 True）
- 未收敛 ARMAX 仍在 candidates 列表，reason_code 显式标注
- 原 22 样本/30s/200/1e-6/四业务断言/600s 门限均不变

## 非作者复验结论（v1）

REWORK：v1 论证与实现错位。代码机制、测试、R9 契约 PASS。本 v2 修正论证对齐实现，补单测见 `test_g3_convergence_sort_key.py`。
