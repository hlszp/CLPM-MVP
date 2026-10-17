# 交接：G3-APP 算法适用性核查 + joint 低 SNR 漏标注修补（执行包「系统改造优化-2026-10-10」G3 前纠偏）

- 状态：**待非作者验收**
- 根目录/分支/基点：worktree `/Users/zhangping/DEV/CLPM-MVP-g3app`，分支 `fix/g3-app-applicability`，基点 `5d664dc3`（未 commit；工作区 4 文件 = 2 改 [pipeline.py / calc_snapshot.py] + 1 新增 [tests/test_g3_app_applicability.py] + 1 golden 重固化）
- 运行环境/代码 Owner：G3-APP 实施智能体（本作者）；**pipeline 域唯一 Owner**（P3-07 在途不碰 pipeline——无冲突；本批零触碰 tuning_algorithms.py / services/tuning.py / endpoints）；worktree 自建 `.venv`（uv sync）；无 DB 依赖（算法测试全合成数据）
- 用户授权范围：G3纠偏-任务书-20261012.md G3-APP 节【2】五项 + 14 号文 §5（正本）；**R9/S1/S1-b/R15 冻结口径一律未改**（§7 新估计器研究只登记不实施）
- 文档版本：任务书=执行状态/G3纠偏-任务书-20261012.md；14 号文=14-G3纠偏任务与执行提示词-2026-10-12.md §5/§7；oracle 冻结=执行状态/P3-06-07-oracle冻结草案-20261011.md；前序交接=P3-06a/b/c、P3-S1落地、P3-03终验、P3-06c 验收报告（D1）
- 配置 revision/数据 manifest：无配置变更；无 DB 模型变更（`git diff -- app/models/` 为空 → 无迁移）；快照 schemaVersion 保持 1.3（只加不改：resultEvidence 增 1 键）

---

## 一、已完成

### 1. joint 低 SNR 漏标注修补（S1 落地交接已知局限①，14 号文 §5.2）

**缺口实证（修补前探针）**：两段 700 点 FOPDT（噪声 1.4）构造下 joint 候选胜出（`multi-window joint ... 胜出`）且深低 SNR——胜者 `evidence.low_snr=None`、外层 `preprocessing.lowSnr` 缺失、reason codes 无 `LOW_SNR_PARAM_UNCOMMITTED`。机理：多窗编排外层早退 `return`（`identify_from_history` 多窗分支）在单窗路径 SNR 估计块之前，逐窗胜者经内层单窗调用有标注，joint 胜者构造时无标注。

| 文件/符号 | 改动 | 行为差异 |
|---|---|---|
| `pipeline.py`：`_estimate_snr_powers`（抽取）+ `_estimate_snr_db`（改为包装） | 重构非行为变更 | 单序列 SNR 估计逐位等价（Welch 噪底拆分返回 (signal, noise) 功率；−30dB 下界/None 语义原样） |
| `pipeline.py`：`_estimate_snr_db_segments`（新增） | 多段聚合 SNR | 逐段 (signal, noise) 功率 **ΣS/ΣN 聚合**（与 P-22 段块堆叠回归的信息聚合语义一致）；**禁止跨 gap 拼接后整段谱估计**（拼接不连续人为抬高噪底）；全段过短→None；聚合信号功率≤噪底→−30dB（与单序列口径一致） |
| `pipeline.py`：多窗分支 joint 胜出标注块（`preprocessing["windowSearch"]` 赋值后、终验装配前） | 早退路径补标注 | `best_r.best_model is joint_best` 时按 joint 段块聚合 SNR <20dB → `evidence.low_snr`（mode=PARAM_ACCURACY_UNCOMMITTED / snrDb 实测 / basis=WELCH_PSD_NOISE_FLOOR_JOINT_AGGREGATE / note 含段数与聚合口径）+ reason code `LOW_SNR_PARAM_UNCOMMITTED` + 候选 reason 段 `LOW_SNR_PARAM_UNCOMMITTED(snr≈XdB)` + 外层 `preprocessing.lowSnr`。**只标注不封顶不拒绝**（S1-b 裁决口径不变——不承诺档不进参数级门禁） |
| `calc_snapshot.py`：`resultEvidence.lowSnr` | 加性键 | `evidence.get("lowSnr")` 透出（None=当时未触发不伪补）——P3-07/P6 消费链在快照层可见 S1-b 标注；schemaVersion 保持 1.3（只加不改，旧包读取不变） |

**修补后实证**：同构造（seed 521/571）joint 胜者携带 `evidence.low_snr={snrDb:-3.15, basis:JOINT_AGGREGATE,...}`、reason codes 含 `LOW_SNR_PARAM_UNCOMMITTED`、外层 `preprocessing.lowSnr` 在场、`to_dict()` 序列化两路键均在场。

**工程事实登记（探针 ~150 组合，未入库脚本）**：joint 候选胜出集中在**低 SNR/低拟合区**——高 SNR（noise ≤0.05~0.1，理论 21~35dB）下 3 噪声档 × 4 段长(620~900) × 6~7 种子 × 2/3 段构造 **零 joint 胜出**（逐窗候选稳定占优，joint 堆叠自由仿真分数略低）；即漏标注缺口恰好落在标注最需要的区域。防误报（高 SNR joint 不标注）因构造不可达，改由聚合估计器独立重算 oracle（低/高 SNR 双档）+ S1 既有噪声less 锚定测试承担。

### 2. 真实序列化反例（四象限 + 保存/重读）

新增 `backend/tests/test_g3_app_applicability.py`（14 项全过，18s）：

| 象限/测试 | 构造（独立生成器：状态递推非回归） | 断言 |
|---|---|---|
| 单窗成功低 SNR | SOPDT 快极点 (1,8,3,2)@7dB（S1 边缘成功例同族） | 标注在场 + `to_dict` evidence/preprocessing.lowSnr 序列化 |
| 单窗失败深低 SNR | SOPDT 慢极点 (1,40,10,6)@5dB（S1 深噪声例同族） | success=False + reason 含"低信噪比" + preprocessing.lowSnr 序列化 |
| 多窗 joint 胜出成功低 SNR | 两段 700 点 FOPDT 噪声 1.4 seed 521（探针实证 deterministic joint 胜出，聚合实测 ≈−3dB） | evidence.lowSnr（聚合口径）+ reason code + 候选 reason 段 + 外层 preprocessing.lowSnr |
| joint 标注 SNR 独立重算 | 同上 | 记录 snrDb vs 测试侧独立实现（按 S1 交接登记口径从文档公式独立编写）±0.2dB |
| 多窗逐窗胜出（joint 未胜出）回归 | 两段 1800 点噪声 1.2 | 内层单窗标注不回退（basis=WELCH_PSD_NOISE_FLOOR）；joint 候选并入池但不带胜者标注 |
| 多窗全失败 fall-through | 两段 2500 点慢极点 SOPDT@5dB（各窗失败→落回整窗亦失败） | success=False + 显式低信噪比标注（非裸失败）+ 序列化 |
| 聚合估计器 oracle（5 项） | 低/高 SNR 段、等长段比值不变性、过短段 None、混合段长 | 高 SNR（噪声less 慢极点 SOPDT）≥20dB 防误报；低 SNR 与独立重算一致；ΣS/ΣN 数学性质 |
| 保存/重读 round trip | joint 胜出场景经 `save_identification_snapshot`（mock 会话复用 test_calc_snapshot `_SaveDB` 程式）→ manifest JSON 落盘 → `_read_files` 读回 | 层 3 `searchAudit.preprocessing.lowSnr`（聚合 basis）+ 层 5 `resultEvidence.lowSnr` 写入且读回相等；jointEstimate.ran 随包 |

**估计器口径限制登记（如实）**：Welch 噪底估计对**宽带白输入驱动的快过程**保守低估——噪声less FOPDT（τ=20s@ts=1s，900 点）实测估计 14.9dB（高频直通分量被计入噪底）。S1 交接已登记"有色噪声/宽带过程动态下为保守近似"；保守方向=多标注不漏标注（仅驱动不承诺标注，不进数值门禁），矩阵噪声轴按此口径陈述。

### 3. 区间可信度独立复核（14 号文 §5.3，不挑种子）

**预登记种子族（先声明后运行）**：seeds **21000~21019**（20 实验，与作者族 920~939、06c 验收 D1 验证者族 6000~6011 均不相交）；构造与 O-C7 作者口径逐字相同（n=1200、20dB、τ=25/θ=12、FOPDT-only）——同冻结分母（20）同指标（真值落入 ci95 覆盖率）。

| 族 | K 覆盖 | τ 覆盖 | θ 覆盖 | 出处 |
|---|---|---|---|---|
| 作者族 920-939 | 18/20（90%） | 14/20（70%） | 20/20（100%） | 06c 交接实施发现 1 |
| D1 验证者族 6000-6011 | 8/12（67%） | — | — | 06c 验收报告 D1 |
| **本批独立族 21000-21019** | **17/20（85%）** | **18/20（90%）** | **17/20（85%）** | 本批（逐实验明细见附录 A） |

**结论（如实登记，不改指标不加修正）**：三个独立族上 K/τ/θ 覆盖率均不能稳定达到 95% 区间标称（≥90% 断言仅在部分族成立）——`reselectUncertainty` ci95 区间**不具备频率学派校准**，性质=**uncalibrated**。失败模式两种并存：①欠分散（子窗高度重叠→族分散度低于跨实验分散度，区间过窄不含真值，D1 机制）；②**错误盆地**（本批新观察：重选链 BIC 平局抽样落入错误时滞盆地，如 seed 21001/21013/21014 整族 θ̂∈[0,2] 样本、K 区间 [-0.15,0.09]——区间宽但位置错）。

**适用限制（P3-07 消费约束，进 §三守卫清单）**：ci95 区间可作**相对分散度/灵敏度证据**（比较参数间、窗间稳定性），**不得作校准 95% 覆盖保证**；P-32 可信模型集合以其采样时须知悉 K 区间偏窄 + 错误盆地可能；"64 个集合成员全过鲁棒测试仅证明该 64 成员，不证明真实对象在集合内"（14 号文 §7 口径一致）。

### 4. 串级 hint 未接线如实登记（14 号文 §5.5）

工程事实（代码核对于 5d664dc3）：
- `identify_from_history(sp_cascade_hint=...)` 参数在（P-21 条件②），oracle 已验证 True 触发 CASCADE_TEMPLATE 拒绝路径（`TestOC2CascadeAndTrackingRejection`）；
- **生产调用点 `services/tuning.py:874` 未传该参数**（调用参数仅 op/pv/sp/ts/theta_estimate/candidate_models/mode/op_limits）——engineeringFacts 当前**无串级结构字段可传**（06c 裁量③原样）；
- 数据侧条件①（锁步 lead-lag 不对称）③（SP-残差领先相关）已覆盖**有激励**串级（实测双条件命中）；**平滑串级（PI 主控）不触发数据侧条件**——按"无激励"分支正确拒绝但归因非串级（06c 实施发现 2）。

**结论**：串级结构的指定检出路径（②结构模板）在生产链路**未接线=未知来源限制**——CLIVC 在平滑串级场景仍可能被启用（数据侧不触发），其结果带封顶 C 但不被归类为串级。不称"已覆盖生产串级路径"；数据侧"未检出内生性"不能等同证明 SP 外生（07 号文纪律）。接线需先扩 engineeringFacts schema（挂后续批，P3-06c 交接"接续第一步"已登记）。

### 5. 全路径适用性矩阵（14 号文 §5.1）

见 **附录 B**（34 行矩阵：实现支持 / 理论前提 / 独立验证覆盖（测试名）/ 参数-响应-整定承诺 / 拒绝或降级原因；未验=明确未验）。

### 6. P3-07 输入资格守卫清单（14 号文 §5.4，产出不改其文件）

见 **§三**（10 条消费约束，交 P3-07 Owner 整合）。

---

## 二、未完成与限制（如实）

- **§7 新估计器研究未实施**（14 号文明示建议非授权）：SRIVC/OE/BJ/受约束 PEM、T1/T2 和积参数化、SOPDT 降阶近似误差路线——全部只登记（矩阵"理论前提"列引用），不写代码。
- **B-03/B-08 墙钟预算未实施**（`budgetTermination` 显式 null 占位，P3-04/P5-02 承接口径）——真 Worker 取消/超时行为**未验**（14 号文 APP 验收"必要真 Worker 取消/超时仍未验时分别记录"——本批记录：未验，无 mock 升级）。B-05 窗池 cap 64 已生效有测试。
- **ARMAX/IPDT joint 共同估计未实现**（06c 裁量②原样）——矩阵标"不支持"。
- **跨输入/跨工况共同估计未实现**（regimeId 恒 None，同输入窗内互不重叠动态窗为工况代理——06c 裁量①）。
- 高 SNR joint 胜出构造不可达（探针 ~150 组合零命中）——joint 路径防误报由估计器 oracle 承担（见 §一.1 工程事实）。
- 环境冲突登记（06 系同款，非本批问题）：共享 dev 库 alembic 不可判；补偿验证 `git diff -- app/models/` 为空（零 ORM 变更→无迁移必要、无漂移可能）。

## 三、P3-07 输入资格守卫清单（交 P3-07 Owner；本批未触碰其文件）

P3-07 消费辨识输出（模型→终验→整定→反馈仿真→DCS 转换→来源保存）时的**输入资格约束**：

1. **结构门禁**：只消费 `modelRole.tunable` 非空的结果——判定=模型级终验 `finalVerification.status==PASSED` **且** 结构 ∈ TUNABLE_MODEL_TYPES（当前 `("FOPDT",)`）。IPDT/SOPDT 在 P3-07 验收通过并裁决放开前保持拒绝（03 号文 P3-07 边界；只删拒绝分支/填 τ=30/改名均不算完成）。
2. **终验链完整消费**：PASSED 判定背后是双通道（finalTest 尾段 20% + finalEventTest 独立事件不重叠）；依据链 `basis` 含跨窗稳定性（P-25）与重选不确定性（P-26）——建议来源必须绑定终验事件引用与模型版本（P-35）。
3. **S1-b 标注透传（必须）**：`evidence.lowSnr` / `preprocessing.lowSnr`（含本批 joint 聚合口径 `WELCH_PSD_NOISE_FLOOR_JOINT_AGGREGATE`）在场时，整定建议层**必须携带"参数精度不承诺"标注**（reason code `LOW_SNR_PARAM_UNCOMMITTED`），不因 success/高 R² 将该建议升级为"可靠整定"表述。快照层读 `resultEvidence.lowSnr`。S1-b 冻结：标注不进参数级门禁（低 SNR 不阻断整定链），但承诺面必须如实。
4. **区间消费口径**：`reselectUncertainty` ci95 为 **uncalibrated**（§一.3 三族复核）——P-32 可信模型集合采样只作相对分散度来源，不得宣称 95% 覆盖保证；集合鲁棒测试通过仅证明被测集合成员，不证明真实对象在集合内。
5. **串级/外生证据边界**：`spExogeneity.verdict != "EXOGENOUS"`（或 None=未检测）时，CLIVC 来源结果不可作为"SP 外生"证据进整定依据；"未检出内生性"≠外生证明；串级结构 hint 生产未接线——结构未知按未知处理（平滑串级场景 CLIVC 结果封顶 C 携带，不宣称已识别串级）。
6. **慢极点 SOPDT 参数精度**：xfail(strict) 在案（test_p3_06a `test_sopdt_20db_slow_poles`）——即便未来结构放开，慢极点真值 SOPDT 参数恢复在现行 R9 方法集下不可达，建议层不得引用其参数精度。
7. **工程约束消费**：执行器限幅消费 engineeringFacts 实际 OP 上下限（P-34：UNKNOWN=明确拒绝，不得默认 ±100/0-100——现 `tuning_algorithms.py` 仿真 OP 限幅 ±100 硬编码处须按此改）；控制方向/单位走 B1 pid_conversion 单源。
8. **D/E 推荐门禁不放宽**：可信度 A/B 放行、C 确认、D/E 拒绝（R15 冻结联动）；本批矩阵中一切"封顶 C"路径（终验失败/跨窗不稳定/θ 边界命中/τ<ts/串级未知/恒 SP 闭环）到达 D/E 时不得进推荐链。
9. **未实现即不支持**：ARMAX/IPDT joint、跨工况共同估计、B-03/B-08 墙钟预算、SRIVC/BJ/OE（R9 冻结不引入）——P3-07 文档/UI 不得宣称这些能力。
10. **序列化契约**：消费 evidence 键（lowSnr/spExogeneity/finalEventTest/reselectUncertainty/finalVerification）时按"缺键=当时未记录，显式 null 不伪补"处理（P2-03 只加不改纪律），不得对旧结果补造语义。

---

## 四、自测与非作者验证

作者自测（worktree `/Users/zhangping/DEV/CLPM-MVP-g3app/backend`，`uv run`）：

| 命令 | 退出码 | 结果 |
|---|---|---|
| `uv run pytest -q --tb=no`（全量） | 0 | **6159 passed**, 337 skipped, 102 deselected, 12 xfailed, 4 xpassed（305.62s；xfail 全部为既有登记项——compliance 矩阵/Phase6 基线/S1 慢极点 xfail(strict) 在案，本批零翻转；基点 5d664dc3 全量 + 本批新增 14） |
| `uv run pytest tests/test_g3_app_applicability.py -q`（新增） | 0 | **14 passed** |
| 相邻回归（p3_06a/p3_06b/p3_06c/p3_03_final_verification/calc_snapshot/tuning_identification/annotated_loops——含在全量内） | 0 | 全过（S1 标注语义/终验门禁/快照 schema 不回退） |
| `uv run ruff check .` / `ruff format --check .` | 0 | All checks passed / 861 files already formatted |
| `REFACTOR_UPDATE_GOLDEN=1 uv run pytest tests/test_refactor_protected_manifest.py` | 0 | golden 重固化（diff 仅 pipeline.py 哈希 + baseline_head→5d664dc3；06a/b/c/S1/03终验同款程序，pipeline.py 为 G3-APP 任务书点名的授权算法文件） |
| `git diff -- app/models/` | — | 空（零 ORM 变更→无迁移必要） |
| 区间独立复核脚本（预登记族 21000-21019） | 0 | K 17/20、τ 18/20、θ 17/20（附录 A） |

**非作者验证：未执行（待主协调派发）**。建议复验点：①joint 胜出标注测试直接复跑（`TestJointWinnerLowSnrAnnotated` 三件套——含 snrDb 独立重算 ±0.2dB）；②快照 round trip（`TestSnapshotRoundTrip`——manifest 两层 lowSnr 键读回相等）；③独立复核脚本换一族种子（如 31000-31019）重跑对照（脚本见附录 A 末尾说明）；④高 SNR 段防误报 oracle（噪声less 聚合 ≥20dB）；⑤alembic 待环境恢复后按共享库自然可判。

## 五、影响与回退

- **schema/迁移**：零模型变更、零迁移；evidence/manifest 只加不改（resultEvidence.lowSnr 加性键 + joint 胜者 evidence.low_snr/preprocessing.lowSnr 填充），1.1/1.2/1.3 旧包读取不变（calc_snapshot 全套回归证明）；schemaVersion 保持 1.3。
- **API 兼容**：加性键与追加段（joint 胜者 reason 尾部 `LOW_SNR_PARAM_UNCOMMITTED(snr≈XdB)`）；`_estimate_snr_db` 重构为 `_estimate_snr_powers` 包装（单序列行为逐位等价，S1 测试零改动通过证明）；算法版本保持 TUNE_IDENT_v1.8（S1 标注批同款不 bump 裁量——行为变更面由新测试锚定，消费方按 lowSnr 键存在性区分）。
- **行为变更**（有意，仅一处）：joint 胜出且聚合 SNR<20dB 的辨识结果携带不承诺标注（此前静默）——不影响可信度评级/选模/门禁（S1-b 冻结口径）。
- **回退步骤**：worktree 未 commit；`git checkout -- . && rm backend/tests/test_g3_app_applicability.py` 即完全回退（golden 一并还原）。

## 六、接续第一步

- **主协调**：非作者验收派发；G3-STATE 看板同步时登记本批（G3-APP 待闭合→作者自验完成）。
- **P3-07 Owner**：消费 §三守卫清单（10 条）；特别是 lowSnr 建议层透传（S1-b 知悉行）与 uncalibrated 区间口径。
- **可选后续小批**：串级 hint 生产接线（先扩 engineeringFacts schema）；区间校准改进（block bootstrap/去重叠子窗——D1 备选，需新裁决）；§7 研究卡（独立方案，不占本批）。
- **已完成检查不要重复**：S1 分档/标注机制勿重做；`_estimate_snr_db`/`_LOW_SNR_ANNOTATION_DB` 已登记勿重复实现；06a/b/c/03终验交付勿重写（本批只消费+一处修补）。

---

## 附录 A：区间可信度独立复核逐实验明细（预登记族 seeds 21000-21019）

| seed | K ci95 含真值 | τ ci95 含真值 | θ ci95 含真值 |
|---|---|---|---|
| 21000 | ✓ [1.498,1.505] | ✓ [24.97,25.11] | ✓ [12.0,12.0] |
| 21001 | ✗ [-0.153,0.093]（错误盆地） | ✓ [12.63,35.31] | ✗ [0.0,2.0]（错误盆地） |
| 21002~21012 | ✓（10/10，区间宽 ~0.5~2%） | ✓（10/10） | ✓（10/10，全部 [12.0,12.0]） |
| 21013 | ✗ [-0.034,0.044]（错误盆地） | ✗ [12.67,21.28]（错误盆地） | ✗ [0.0,2.0] |
| 21014 | ✗ [-0.039,0.060]（错误盆地） | ✗ [12.15,19.88]（错误盆地） | ✗ [0.0,2.0] |
| 21015~21019 | ✓（5/5） | ✓（5/5） | ✓（5/5） |
| **合计** | **17/20（85%）** | **18/20（90%）** | **17/20（85%）** |

真值 K=1.5/τ=25/θ=12；✗ 3 例全部为重选链时滞平局抽样落入 d≈0-2 错误盆地（整族参数随错时滞偏移——区间有宽但位置错，与 D1 欠分散机制不同源）。复核脚本要点（可独立重写）：`_weak_delay_case` 同参数构造（n=1200、20dB 白噪、一阶差分递推生成器）× seeds 21000-21019，逐实验取 `best_model.evidence.reselect_uncertainty["K"/"tau"/"theta"]["ci95"]` 判真值包含；全程在 worktree venv `uv run python` 执行，退出码 0。

## 附录 B：全路径适用性矩阵（14 号文 §5.1）

口径：**实现支持**=代码路径存在且可达；**理论前提**=方法一致性条件（含未满足时的语义）；**独立验证覆盖**=oracle 测试名（全部为合成已知真系统+独立参考，冻结草案 §0.2）；**承诺**分参数（恢复容差）/响应（自由仿真/频带）/整定（可整定链）；**未验=明确未验**。测试文件缩写：06a=`test_p3_06a_identification_correctness.py`、06b=`test_p3_06b_segments_timescales.py`、06c=`test_p3_06c_closed_loop.py`、03f=`test_p3_03_final_verification.py`、app=`test_g3_app_applicability.py`。

### B1 模型结构 × 窗模式

| # | 路径 | 实现支持 | 理论前提 | 独立验证 | 参数承诺 | 响应承诺 | 整定承诺 | 拒绝/降级原因 |
|---|---|---|---|---|---|---|---|---|
| 1 | FOPDT 单窗 | ✅ ARX/ARMAX/CLIVC | OP 激励覆盖过程带宽 | 06a O-A1（两档两种子） | K≤5%(20dB)/≤10%(10dB)，τ≤10%/20%，θ≤max(2ts,10%) | 自由仿真 R²+终验四态 | 终验 PASSED→tunable | 激励不足/终验失败→拒绝或封顶 C |
| 2 | FOPDT joint（多窗堆叠） | ✅ ARX+CLIVC（FOPDT/SOPDT 结构） | 同输入窗内互不重叠动态窗=同工况代理（裁量①） | 06c O-C4 | ≤15%（闭环容差族） | 段块堆叠验证段评分 | 同上（经终验） | 跨 gap 拼接结构性不可能 |
| 3 | SOPDT 单窗·快极点 | ✅ | 离散极点分离（EIV 偏差可控） | 06a S1 真测试（5/5 种子） | **≥20dB 且快极点**：T1≤10%/T2≤20%（S1 冻结） | 同 1 | 结构限制（P3-07 前 non-tunable） | 25dB 档内反例 T1 偏 57-64%（登记，非单调） |
| 4 | SOPDT 单窗·慢极点 | ✅（估计运行） | 慢极点共线+ARX EIV 渐近偏差 | 06a xfail(strict) 登记 | **不可达**（20/25/30dB 全失败，K 偏 22-73%） | 垃圾候选拟合不承诺 | 不可整定（结构+精度双卡） | R9 方法集不引入 SRIVC 的既定代价（§7 只登记） |
| 5 | SOPDT 10~20dB | ✅ | — | 06a S1-b 真测试 + app 单窗象限 | **不承诺**（LOW_SNR_PARAM_UNCOMMITTED 标注） | 同 1 | 建议层必须透传标注（守卫 3） | S1-b 裁决：10dB 档数值弃用为门禁 |
| 6 | IPDT 单窗 | ✅ 差分回归+显式漂移项 | 漂移被截距/斜坡吸收（R7） | 06a O-A4 | K≤10%，θ 同 O-A1；漂移率解析可核 | 自由仿真含漂移 | **不可整定**（P3-07 在途；τ=30 填值不算完成） | TUNABLE_MODEL_TYPES=("FOPDT",) |
| 7 | IPDT joint | ❌ 未实现 | — | 无 | 不支持 | 不支持 | 不支持 | 06c 裁量②登记（ARMAX 同） |
| 8 | ARMAX joint | ❌ 未实现（创新状态段间重置语义未定） | — | 无 | 不支持 | 不支持 | 不支持 | 同上 |

### B2 SP 来源（闭环）

| # | 路径 | 实现支持 | 理论前提 | 独立验证 | 承诺 | 拒绝/降级 |
|---|---|---|---|---|---|---|
| 9 | 无 SP | ✅ ARX/ARMAX | — | 06a 全 oracle（白输入开环） | 同 B1 各行 | — |
| 10 | 外生 SP（CLIVC） | ✅ | SP 外生（三条件核验） | 06c O-C1 | ≤15%（ARX 有偏 >30% 对照） | 外生性不过→CLIVC 不进候选 |
| 11 | 串级/跟踪 SP·有激励（数据侧检出） | ✅ 检出并拒绝 CLIVC | 锁步不对称/SP-残差领先相关 | 06c O-C2 | 拒绝例行为断言 | 全候选封顶 C + spExogeneity 记录（C19-ID3） |
| 12 | 串级模板 hint（②结构来源） | ✅ 算法侧 / ❌ **生产未接线** | engineeringFacts 串级字段 | 06c O-C2（oracle 以 True 验证） | — | **未知来源限制如实登记**（§一.4）；接线挂后续批 |
| 13 | 平滑串级（PI 主控） | ⚠️ 按"无激励"拒绝（归因非串级） | 数据侧条件不触发 | 06c 实施发现 2 | — | 正确拒绝但非串级归因；指定检出路径=②未接线 |
| 14 | 恒 SP+有色 OP 闭环扰动 | ✅ 检出并降级 | 闭环有偏方法不适用 | 06c O-C3 | 记录性（SP_NO_EXCITATION_CLOSED_LOOP） | 封顶 C；不由输入变化推因果 |
| 15 | joint 内逐段外生性 | ✅ 任一段不过→joint 只跑 ARX | P-21 同判定逐段核验 | 06c O-C4 相关断言 | 同 10 | CLIVC 不参与共同估计 |

### B3 动态/极点/时间尺度

| # | 路径 | 实现支持 | 独立验证 | 承诺 | 拒绝/降级 |
|---|---|---|---|---|---|
| 16 | 快极点（离散极点分离） | ✅ | 06a S1（5/5 种子过档） | 见 B1#3 | — |
| 17 | 慢极点 | ✅ 估计运行 | 06a xfail 登记 | 参数不可达（B1#4） | 结构拯救仅全灭兜底（06a 配套） |
| 18 | 近极点 T1≈T2 | ⚠️ 与慢极点同族（共线） | 未单独构造 oracle（**未验**） | T1/T2 分别恢复不承诺（§7 和积参数化为建议） | 配对歧义按档内容差归一取优（S1 测试处理） |
| 19 | τ<ts（分辨率不足） | ✅ 检出 | 06b TestP19BelowResolution | 该时间常数显式不可判断 | DYNAMICS_BELOW_RESOLUTION+封顶 C |
| 20 | θ 物理边界（长时滞） | ✅ 物理秒搜索 max(300s,5×粗估)≤时长/4 | 06a O-A5（θ=120s×4 采样率）+06b O-B4（两两 ≤15%） | θ≤max(2ts,10%)；跨采样率连续参数一致 | 边界命中→DELAY_BOUNDARY_UPPER_HIT+封顶 C（O-A6） |
| 21 | 信息不足（θ 超界/近稳态） | ✅ 显式 INCONCLUSIVE/原因码 | 06a R1 审计+06b O-A6 | 明确不可判断 | 禁止输出冒充 θ |

### B4 噪声（S1/S1-b 冻结口径，全路径）

| # | 路径 | 实现支持 | 独立验证 | 承诺 | 备注 |
|---|---|---|---|---|---|
| 22 | ≥20dB | ✅ 标注不触发 | 06a 噪声less 锚定+app 聚合 oracle | 按结构档（B1） | 快 SOPDT 参数承诺前提 |
| 23 | 10~20dB | ✅ 全模型全路径标注 | 06a S1-b+app 四象限（**含 joint 修补**） | **参数精度不承诺**（LOW_SNR 标注；成功/失败两态都带） | 标注不进门禁（S1-b 冻结） |
| 24 | <10dB 深低 | ✅ 通常失败+标注 | 06a/app 深低失败例 | 不承诺；失败显式原因 | 近稳态谱能量≤噪底→−30dB 下界记 |
| 25 | joint 聚合口径 | ✅（本批修补） | app TestJointWinnerLowSnrAnnotated+独立重算 | ΣS/ΣN 聚合（不跨 gap 拼接谱） | 高 SNR joint 胜出构造不可达（探针登记），防误报由估计器 oracle 承担 |
| 26 | 宽带白输入快过程 | ⚠️ 估计器保守低估（噪声less FOPDT τ=20 实测 14.9dB） | app 估计器 oracle 登记 | 保守方向=多标注不漏标注 | 口径限制登记（§一.2 末段），非缺陷 |

### B5 激励/工况/工程事实

| # | 路径 | 实现支持 | 独立验证 | 承诺 | 拒绝/降级 |
|---|---|---|---|---|---|
| 27 | 激励充分（幅度+信息量双通道） | ✅ | 06b O-B2/O-B3 | 正常估计 | — |
| 28 | 激励不足（保持/慢变化/死水） | ✅ 信息量通道 | 06b O-B2 | 明确拒绝或降级 | INSUFFICIENT_INFO 审计；Occam 防单频 SOPDT 假胜出 |
| 29 | 大缺口多段 | ✅ 段目录全保留不拼接 | 06b O-B1（C12b-ID） | 激励段不丢 | 无合格段→显式拒绝（R2 红线出口） |
| 30 | 手动段 | ✅ 降级使用（G24 扩展） | 06b O-B2 手动段例 | 不作外生证据（07§4 纪律） | note 双重标注降级事实 |
| 31 | 未知工况（regime） | ⚠️ regimeId 恒 None 显式 | 06b P-14 断言 | joint 仅同输入窗内（裁量①） | 跨输入/跨工况共同估计**不支持** |
| 32 | 终验链（模型级四态） | ✅ finalTest+finalEventTest 双通道 | 03f 全套（25 测试） | FAILED 封顶 C；NO_DYNAMICS/NOT_RUN 不伪称通过 | tunable=终验 PASSED+FOPDT |
| 33 | 区间可信度 | ✅ 记录（48 次重选） | 06c O-C7（作者族）+**本批独立族复核** | **uncalibrated**（K 85%/τ 90%/θ 85% @21000-21019；欠分散+错误盆地两机制） | 守卫 4：不作 95% 覆盖保证 |
| 34 | 墙钟预算/真 Worker 取消 | ❌ budgetTermination 显式 null | **未验**（记录：无 mock 升级） | — | B-03/B-08 归 P3-04/P5-02 承接 |

**矩阵结论**：每条"实现支持"路径均有对应独立 oracle 反例（测试名列于行内）；低 SNR（#23-25）/慢极点（#4/#17）/窄区间（#33）/串级未知（#12/#13）四类均**不无条件获得可靠整定**——分别由标注透传义务（守卫 3）、参数不可达登记（xfail strict）、uncalibrated 口径（守卫 4）、封顶 C+未知来源登记（守卫 5）约束；合法支持路径（#1/#10 等）经全链（终验→整定资格）仍可达 tunable，未被 blanket 拒绝。**未验项 3 处**：#18 近极点单独 oracle、#26 宽带低估的量化边界、#34 真 Worker 取消/超时——均明确登记未验。
