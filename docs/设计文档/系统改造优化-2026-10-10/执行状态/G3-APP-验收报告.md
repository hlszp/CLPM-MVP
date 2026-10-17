# G3-APP 算法适用性核查 + joint 低 SNR 漏标注修补——非作者验收报告

- 日期：2026-10-12（验证会话独立于实施会话；业务代码只读）
- 对象：worktree `/Users/zhangping/DEV/CLPM-MVP-g3app`，分支 `fix/g3-app-applicability`，基点 `5d664dc3`，未提交 diff
- 范围固定（git status/diff --stat）：3 改 + 1 新增 = `pipeline.py`(+81/−7)/`calc_snapshot.py`(+3)/golden manifest(2 处)/`tests/test_g3_app_applicability.py`(新 433 行)——**与自报 4 文件完全一致，无范围外文件**
- 依据：G3纠偏-任务书-20261012.md G3-APP 节、14 号文 §5（正本）、G3-APP-交接.md（矩阵 34 行+守卫 10 条+未验 3 处）、P3-S1落地-交接 已知局限①、P3-06c-验收报告 D1、05 号文 §5 协议
- 验证环境：基线对照用独立 worktree（`git worktree add /tmp/g3app-baseline 5d664dc3`，uv sync 独立 venv，验后已移除，候选工作区零污染——git status 复核 4 文件原样）

## 总裁决：**PASS**（附 1 项非阻断登记 NI-1 + 1 项观察 O-1，无 REWORK 项）

修补在基线上独立复现缺口、修补后同一数据全链带标注；范围守卫全部成立（R9/S1-b/R15 口径零改动、P3-07 域零触碰、golden 仅程序性 2 处）；门禁全量独立复跑与自报完全一致，xfail/xpass 集合与基线逐字节相同（零翻转）。区间复核结论（uncalibrated）被独立复跑**加强**确认。

## 1 核验 1：joint 漏标注修补（S1 已知局限①）

### 1.1 基线缺口独立复现（验证者自有构造，非作者种子/参数）

- 构造：两段 700 点 FOPDT（**K=1.2/τ=18/θ=5/偏置 30+62/噪声 3.5/gap 150**——与作者 K=1.5/τ=20/θ=4/偏置 40+55/噪声 1.4/gap 200 全不同源），生成器独立编写（一阶差分状态递推）。种子搜索（自有 seed 101-117@noise1.6 + 201-208@noise2.5/3.5/5.0）实证 joint 胜出集中在深噪声区（noise≤1.6 共 17 种子**零** joint 胜出——独立印证交接"高 SNR joint 胜出不可达"工程事实），取 noise=3.5/seed=201 为验证构造。
- 基线（5d664dc3）同数据运行：`joint_win=True`（逐窗最优 fit=0.0 vs joint fit=0.1，joint 胜出）且 **`evidence.low_snr=None`、`preprocessing` 无 `lowSnr` 键、reason codes 无 `LOW_SNR_PARAM_UNCOMMITTED`**——缺口独立复现 ✓（机理与 S1 交接登记一致：多窗早退 return 在单窗 SNR 估计块之前）。

### 1.2 修补后同数据全链带标注 ✓

修补版同数据运行：`evidence.low_snr={mode: PARAM_ACCURACY_UNCOMMITTED, snrDb: −8.55, thresholdDb: 20.0, basis: WELCH_PSD_NOISE_FLOOR_JOINT_AGGREGATE, note 含段数与 ΣS/ΣN 口径}`；reason codes 增 `LOW_SNR_PARAM_UNCOMMITTED`；候选 reason 尾段 `LOW_SNR_PARAM_UNCOMMITTED(snr≈−8.6dB)`；外层 `preprocessing.lowSnr` 在场；`to_dict()` evidence/preprocessing 两键均序列化在场。

### 1.3 snrDb 独立重算 ±0.2dB ✓

验证者独立编写 Welch 噪底拆分（按 S1 交接登记口径从文档公式重写，非复制实现）：独立重算 −8.5520 dB vs 记录 −8.55，**差 0.002 dB**（容差 ±0.2 内两个量级）。

### 1.4 逐段聚合禁跨 gap 拼接（代码级 + 反例）✓

- 代码级：`_estimate_snr_db_segments` 逐段调 `_estimate_snr_db_powers` 后 ΣS/ΣN，全程无 concatenate；消费的段切片 `y_raw[s.start:s.end]` 与 P-26 重选段切片同索引口径。
- 验证者三场景反例（拼接 vs 分段聚合，dB）：
  - 噪声less 快过程异偏置两段：分段 14.70 vs 拼接 19.24（**虚高 +4.5**）；
  - 低 SNR 探针数据（1.2 节同数据）：分段 **−8.55** vs 拼接 **+3.12（虚高 +11.68）**——若实现拼接，边界场景足以把 <20dB 翻成 ≥20dB 漏标注；
  - 噪声less 慢极点异偏置两段：分段 62.08 vs 拼接 21.05（**虚低 −41**，缝阶跃抬高噪底中位）。
  - 结论：跨 gap 拼接失真实测**双向**存在（±11~41dB），禁拼接的设计决策必要性被反例直接证实（O-1：交接陈述"抬高噪底"为单向机理表述，实测失真方向依数据而异——不影响设计正确性，建议后续文档措辞改"双向失真"）。

### 1.5 只标注不封顶（S1-b 口径未动）✓

同一数据基线 vs 修补运行：胜者 `confidence=E`、`fitting_score=0.12` **逐位一致**（标注块代码不动 confidence/fitting/sort key；reason codes 仅追加）。`_LOW_SNR_ANNOTATION_DB=20.0`、`_CROSS_WINDOW_MAX_REL_DEV=0.25` 与基线 grep 相同。单窗成功路径标注块（pipeline.py:2685-2692）与 joint 新块结构逐字段同款（mode/snrDb/thresholdDb/basis/note + reason code + reason 段 + preprocessing 键）。

## 2 核验 2：14 项反例独立抽验（独立构造非复跑）

| 象限 | 验证者构造（独立参数/种子） | 结果 |
|---|---|---|
| 单窗成功低 SNR | SOPDT 快极点 (1,9,4,3)@8dB 4000 点 seed 401/402 | success + evidence.lowSnr + to_dict 两键在场 ✓ |
| 单窗失败深低 SNR | SOPDT (1,45,12,5)@4dB seed 411/412 | success=False + reason 含"低信噪比" + to_dict preprocessing.lowSnr ✓ |
| 多窗 joint 胜出成功 | §1.2（noise 3.5/seed 201） | 全链标注 ✓ |
| 多窗逐窗胜出回归 | noise 1.6/seed 101（joint 未胜出） | 内层标注 basis=WELCH_PSD_NOISE_FLOOR 不回退；joint 候选并入池（1 个）且全部不带胜者标注 ✓ |
| 多窗全失败 fall-through | 两段 2200 点慢极点 SOPDT@5dB（SOPDT-only） | success=False + "低信噪比" + preprocessing.lowSnr + 序列化 ✓ |
| 保存/重读 round trip | §1.2 数据经 `save_identification_snapshot`（`_SaveDB` 假会话）→ manifest → `_read_files` | 层 3 `searchAudit.preprocessing.lowSnr`（聚合 basis，snrDb 与结果相等）+ 层 5 `resultEvidence.lowSnr` 写入且**读回逐键相等**；jointEstimate.ran=true 随包 ✓ |

## 3 核验 3：区间可信度独立复核（预登记族不挑种子）

验证者用 **06c 原版 `TestOC7ReselectUncertainty._weak_delay_case`**（K=1.5/τ=25/θ=12、n=1200、20dB、FOPDT-only，重选链 RNG 固定 42 故逐位确定）复跑**全量 20 种子**（21000-21019，预登记族前缀顺序全量，无挑选）：

| 量 | 作者附录 A | 验证者复跑 | 判读 |
|---|---|---|---|
| K 覆盖 | 17/20（85%） | **13/20（65%）** | 均远低于 95% 标称 |
| τ 覆盖 | 18/20（90%） | **12/20（60%）** | 同上 |
| θ 覆盖 | 17/20（85%） | **17/20（85%）** | 完全一致（同样 3 miss） |
| 错误盆地种子 | 21001/21013/21014 | **同三种子同形态**（K̂≈0、θ̂∈[0,2]） | 机制完全复现 ✓ |

**结论确认且加强**：ci95 区间 uncalibrated（两套独立数据 K/τ/θ 均不能稳定达到 95% 标称；≥90% 断言在验证者族不成立）；欠分散（近边界窄区间 miss，如 K [1.448,1.496] vs 真值 1.5）与错误盆地（同三种子）双机制均在。守卫 4 口径（不作 95% 覆盖保证、只作相对分散度证据）成立且必要。

**NI-1（非阻断登记）**：附录 A 逐例数值**不能**从其所称"与 O-C7 作者口径逐字相同"的构造逐位复现——验证者用 06c 原版 helper 复跑，21000/21001/21002 判定一致但区间数值不同（如 21000 K ci95 作者 [1.498,1.505] vs 复跑 [1.472,1.515]），21003/21004/21006/21009/21015/21017/21018 共 7 种子个别参数判定翻转（均为近边界窄区间，非错误盆地）。推测作者独立脚本的噪声流与 06c helper 存在未登载差异。**不影响任何裁决面**：uncalibrated 结论方向在两套数据下一致且我方复跑更悲观（对"不得作 95% 保证"的守卫是加强而非削弱）。建议作者在附录 A 补登一句"逐例数值以独立脚本生成器为准（与 06c helper 非逐位同数据），合计数对构造敏感，结论取保守侧"。

## 4 核验 4：矩阵审查（34 行抽 7 行）

抽行覆盖 B1-B5 各区：#1 FOPDT 单窗（06a `TestOA1FopdtRecovery` 存在）、#6 IPDT 单窗（06a O-A4 节+独立递推生成器存在）、#19 τ<ts（06b `TestP19BelowResolution` 存在）、#20 θ 物理边界（06a O-A5:855 + 06b O-B4:491 存在）、#25 joint 聚合口径（app `TestJointWinnerLowSnrAnnotated`，本报告 §1/§2 独立复证）、#32 终验链（03f collect-only **25 项**=23 函数+1 参数化 ×3，与"03f 全套（25 测试）"一致）、#33 区间可信度（06c `TestOC7ReselectUncertainty` + 本报告 §3 复核）。**未验 3 处明示核**：#18 近极点单独 oracle、#26 宽带低估量化边界、#34 真 Worker 取消/超时——矩阵内均显式标"未验"且未包装为已支持 ✓。低 SNR/慢极点/窄区间/串级未知四类在矩阵中均无"可靠整定"表述（分别由守卫 3/xfail strict/守卫 4/守卫 5 约束）✓。

## 5 核验 5-7：守卫清单 / 串级登记 / 范围守卫

- **守卫清单 10 条**（交接 §三）与 P3-07 交接/任务书逐条对齐：tunable=("FOPDT",) 零触碰、S1-b lowSnr 知悉传递（P3-07 链已含）、ci95 按记录值消费不加宽（P3-07 交接处置 8）、P-34 OP 实际限幅已按"provenance≠ACTUAL 拒绝"落地（守卫 7 的前瞻性约束方向一致）、D/E 门禁测试在案（`test_d_e_confidence_gate_not_lowered`）。**合理，无与 P3-07 冲突条目** ✓。
- **串级未接线**：`services/tuning.py:874` 调用参数仅 op/pv/sp/ts/theta_estimate/candidate_models/mode/op_limits，**未传 `sp_cascade_hint`**；全文件 grep 无 cascade 字样——与交接"生产未接线=未知来源限制，不称已覆盖"登记一致 ✓。
- **范围守卫**：`git diff -- app/models/`、`services/tuning.py`、`tuning_algorithms.py`、`endpoints/` 均 0 行；R9 方法集零新增估计器（§7 仅登记）；golden diff 恰 2 处（pipeline.py 哈希 + baseline_head 8807b45e→5d664dc3=当前 HEAD，程序性固化，且全量套件含 golden 守卫测试通过=哈希与工作区文件一致）；schemaVersion 保持 1.3、resultEvidence 只加 `lowSnr` 一键（None 不伪补）✓。

## 6 门禁独立复跑（真实退出码）

| 命令 | 对象 | 退出码 | 结果 |
|---|---|---|---|
| `uv run ruff check .` | 修补版 | 0 | All checks passed |
| `uv run ruff format --check .` | 修补版 | 0 | 861 files already formatted |
| `uv run pytest -q --tb=no -rxX`（全量） | 修补版 | 0 | **6159 passed**, 337 skipped, 102 deselected, **12 xfailed, 4 xpassed**, 0 failed（304.68s）——与自报完全一致 |
| 同上（全量） | **基线 5d664dc3** | 0 | **6145 passed**（+14 新增 = 6159 精确账目）, 337 skipped, 102 deselected, 12 xfailed, 4 xpassed（297.50s） |
| xfail/xpass 集合对照 | 基线 vs 修补 | — | 16 条（12 XFAIL+4 XPASS：compliance 矩阵 10+Phase6 SOPDT 1+S1 慢极点 strict 1+XPASS 4）排序 diff **为空=零翻转** ✓ |

## 7 结论

1. **PASS**。14 号文 §5 五项与任务书【2】逐条核验通过：修补（§5.2）、矩阵（§5.1）、区间复核 uncalibrated 如实登记（§5.3）、守卫清单（§5.4）、串级未接线如实（§5.5）。
2. NI-1（附录 A 逐例复现性）与 O-1（拼接失真双向措辞）为非阻断登记，建议作者在下一次交接/文档触及时顺带补登，不构成本批返工条件。
3. 未验边界（沿交接登记，验收确认不升级）：真 Worker 取消/超时、近极点单独 oracle、宽带低估量化边界、串级 hint 生产接线（挂后续批）、高 SNR joint 胜出防误报（构造不可达，由估计器 oracle+S1 锚定承担——验证者 17 种子独立搜索零命中再证）。
