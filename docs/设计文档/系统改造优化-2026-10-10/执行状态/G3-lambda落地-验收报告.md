# G3 λ 停点终裁落地批 验收报告——非作者独立验证

- 日期：2026-10-12。验证者：非作者（未参与实施，业务代码只读；独立复算全部经 `uv run python` 内联脚本执行，未在 worktree 落任何文件，diff 验证前后未变化）
- 裁决：**PASS**（定向核验清单五类全过；无阻断缺陷；3 项非阻断观察，见 §5）
- 验收基准：DEC 清单 λ 停点终裁行（2026-10-12 方案 a，回摊终裁 a''）——R14 λ 范围 [0.1,5.0]→[0.1,7.0]+IPDT 默认 λ=7.0（仅 IPDT）；R15 四项冻结指标一字不动；SOPDT/FOPDT 不变；落地含 λ=7.0 四项实测回归（64 集合宽 CI）；验证器"如实标记"行为保持

| 项 | 值 |
|---|---|
| worktree | `/Users/zhangping/DEV/CLPM-MVP-p307`，分支 `feat/p3-07-robust-tuning` @ 基点 `0ec8b45e`（未 commit） |
| 范围 | 4 修改 0 新增，**+144/−37**（`git diff --stat` 复核与自报一致；无 untracked） |
| 文件 | `services/tuning_algorithms.py`(+14/−11)/`services/tuning.py`(+6/−2)/`tests/test_p3_07_robust_tuning.py`(+124/−24)/golden manifest（2 处） |
| 环境只读性 | 验证期间未启动后端/Worker/Beat；无 DB 依赖；worktree diff 未变化 |

## 1. 裁决落地核（清单 1）

- **三处单源全部确认**：①注册表 `TUNING_METHODS_INFO` IPDT_PI 条目 `min:0.1/max:5.0→7.0/default:1.0→7.0`；②`tune_ipdt_pi` 签名默认 `lambda_ratio=7.0`；③`services/tuning.py` tune_pid IPDT 分支 `params.get("lambdaRatio", 7.0)`。运行时 dump 验证：IPDT_PI=(0.1, 7.0, **7.0**)，三处数值一致。
- **SOPDT/FOPDT 零改动**：diff hunk 仅两处（tune_ipdt_pi 签名+docstring、IPDT_PI 注册条目），其余全部未触碰；运行时 dump：IMC/LAMBDA（FOPDT）与 SOPDT_IMC 均维持 (0.1, 5.0, 1.0)，7 法注册表共 4 个 lambdaRatio 条目中仅 IPDT_PI 变更。
- **R15 冻结常量零触碰**：`ROBUSTNESS_GAIN_MARGIN_MIN=2.0`/`PHASE_MARGIN_MIN_DEG=30.0`/`OVERSHOOT_MAX_PCT=20.0`/`ROBUST_SET_MAX_MEMBERS=64` 不变；`git diff -U0` 中无任何 `ROBUSTNESS_`、`verify_robustness`、`build_trustworthy`、`closed_loop` 相关 +/- 行——**验证器逻辑零变更**，仅 docstring/description/notes 文案随裁决口径更新（授权内）。

## 2. λ=7.0 实测独立复算（清单 2，仓库验证器 `build_trustworthy_set`+`verify_robustness_on_set`，op_limits=±100，宽 CI K±5%/θ±10%）

| 工况 | λ | 集合 | GM | PM(°) | 超调(%) | 饱和 | 结论 | 对照 |
|---|---|---|---|---|---|---|---|---|
| K=0.1, θ=10（典型） | 7.0 | 宽 64 | 5.618 | 59.28 | **19.12** | 0 | 过 | **与 DEC 终裁行/交接 19.12 完全一致** |
| θ=5 / θ=20 / K=0.05/0.2/0.5 | 7.0 | 宽 64 | 5.618 | 59.28 | 19.12 | 0 | 过 | 与交接一致（尺度不变性） |
| θ=60 | 7.0 | 宽 64 | 5.618 | 59.28 | 15.63 | 0 | 过 | 数值一致；成因勘误见 §5-F2 |
| K=-0.1（正确镜像 CI） | 7.0 | 宽 64 | 5.618 | 59.28 | 19.12 | 0 | 过 | 与正增益**精确相等**（数学预期镜像恒等）；交接表该行数值有误见 §5-F1 |
| 典型（窄 CI K±2%/θ±5%） | 7.0 | 窄 64 | 6.069 | 60.03 | 18.66 | 0 | 过 | 与交接一致 |
| 典型 | 6.5 | 宽 64 | 5.277 | 58.16 | **19.62** | 0 | 过 | **与 DEC/前任补测 19.62 一致** |
| 典型 | 6.0 | 宽 64 | 4.936 | 56.89 | **20.23** | 0 | **超调不过** | **与 DEC/前任补测 20.23（差 0.23pct）一致** |
| 典型 | 5.0 | 宽 64 | 4.253 | 53.75 | 21.97 | 0 | 不过 | （交接为名义单点 20.8，测试口径亦单点，方向一致） |
| 典型 | 1.0 | 宽 64 | 1.438 | 15.16 | 100.55 | 0 | 不过 | （交接名义单点 GM1.71/PM21.2，方向一致） |

- **异种子稳定性（清单 2 要求，种子 42 之外）**：角点+名义+均匀填充分别用 seed 123/2026/777 重构 64 集合——λ=7.0 三种子全部 (5.618, 59.28, 19.12) 完全相同（最坏由角点承载，种子仅影响内部填充）；λ=6.0/6.5 在 seed 123/2026 下同为 20.23/19.62。结论对种子不敏感。
- **加密最坏搜索（验证者加做，64/批分片绕开冻结集合上限）**：宽 CI 盒内 4000 随机点+角点、40×25 网格+角点——λ=7.0 最坏超调 19.19/19.13（全盒真实余量 ≈0.81pct，略小于 64 集合口径的 0.88pct，见 §5-F3）；λ=6.5 最坏 19.70 仍过；λ=6.0 最坏 20.33 仍不过。DEC 三档结论在全盒意义下稳健。

## 3. 测试抽验（清单 3，独立运行，exit 0）

整文件 `tests/test_p3_07_robust_tuning.py` 独立复跑 **76 passed**（自报一致）；其中 10 个目标用例（含参数化共 14 例）显式点名复跑全过：

| 抽验点 | 测试 | 结果 |
|---|---|---|
| λ=7.0 宽 CI 四项达标 | `test_lambda_7_default_wide_ci_all_four_pass`（GM>5.0/PM>55/18.5<OS<20/零饱和/overallPass） | 过 |
| θ/K 变体+负增益 | `test_lambda_7_wide_ci_variants_all_pass`（5 参数化，**正确镜像 CI 构造在测试内**） | 过 |
| 注册范围 | `test_lambda_ratio_structure_reused`（IPDT (0.1,7.0,7.0)；SOPDT_IMC/IMC 维持 (0.1,5.0,1.0) 不变断言） | 过 |
| 校验接受/拒绝 | `test_validate_tuning_params_accepts_and_rejects`（7.0/6.5 收、7.5/0.05 拒、SOPDT 6.0 仍拒） | 过 |
| R15 冻结值守卫 | `test_r15_frozen_thresholds_verbatim` | 过 |
| 默认值三处单源 | `test_ipdt_default_lambda_is_7`（algorithmParams=None→λ=7.0 建议） | 过 |
| 诚实标注回归 λ=1.0 | `test_lambda_1_default_margin_fail`（仍标不达标） | 过 |
| 诚实标注回归 λ=5.0 | `test_lambda_5_overshoot_still_above_frozen_threshold`（20.0<OS<22.0 如实不过） | 过 |

## 4. 范围守卫与门禁复跑（清单 4/5）

- **前端零改动**：diff 全部 4 文件均在 `backend/`，`git diff -- frontend/` 为空；无 untracked 文件。
- **golden diff 仅 2 处**：`baseline_head 5d664dc3→0ec8b45e` + `tuning_algorithms.py` 文件哈希，其余 40+ 条目未动——符合既有重固化程序（该文件受保护清单内、本线唯一 Owner）。
- **验证器逻辑零变更**：见 §1。
- **diff 哈希口径注记**：交接自报 tracked diff hash `529a98fe…`，验证者 `git diff | shasum` 实测 `8603998417dfc769…`（换 git 配置再算又不同）——哈希对计算配置敏感，不具跨机可比性；本验收以基点/文件集/+144/−37/逐 hunk 内容审读+全数值复现为准确认范围，未依赖该哈希。
- **门禁（裸命令真实退出码，cwd=`backend/`）**：`uv run ruff check .` → 0（All checks passed）；`uv run ruff format --check .` → 0（864 files already formatted）；`uv run pytest -q` → **exit 0，6249 passed / 0 failed / 337 skipped / 108 deselected / 12 xfailed / 4 xpassed，296.04s**——分母与自报 6249 完全一致（P3-07 6212 + G3 批 B R03 增量 28 + 本批 9）。

## 5. 非阻断观察（登记，不构成本批 REWORK 依据）

- **F1（交接文档数值勘误）**：交接实测表 K=-0.1 行（GM 5.899/PM 59.34/OS 18.66）经探针复现，系该次运行 CI 反序 `[-0.095,-0.105]`（lo>hi）导致 K 退化为固定值所致（固定 K 后恰为 5.899/59.34/18.66）。正确镜像 CI `[-0.105,-0.095]` 下与正增益**逐位相等**（5.618/59.28/19.12，线性系统镜像恒等的数学预期）。测试用例采用正确构造、断言仅 overallPass——**代码无缺陷，仅交接表该行数值来源口径错误**，建议主协调在 G3 汇总时勘误。
- **F2（冻结 600s 仿真窗口对慢回路低估超调，P3-07 既有属性，非本批引入）**：θ=60 行超调 15.63 的成因不是交接所称"步长细化略优"，而是真实峰值（θ=66 角点 t≈778s）落在冻结 B-11 600s 窗口外——全时域真实超调 ≈18.24%（本档仍过）。边界探针：λ=6.0/θ=60 宽 CI 报 19.14% → overallPass=**True**（真实 ≈20.2% 应不过）；θ≤40（含 30/40 实测 20.34/20.32）均如实不过；λ=1.0 任何 θ 都因 GM/PM 不过。即注册 description"调小 λ（如 ≤6.0）验证器如实标记不通过"对 θ≳50 的慢回路在超调边缘档不严格成立（GM/PM 档不受影响）。该行为属 P3-07 冻结预算既有属性，本批"验证器零触碰"为落地条件、不可能修；DEC 裁决锚点（θ=10 典型）不受影响。建议登记后续批裁决（如报告中对峰值触窗边界的慢回路显式标注）。
- **F3（余量口径细化）**：DEC"余量 0.88pct"为 64 集合最坏（19.12%）；验证者全盒加密搜索最坏 19.19%，真实余量 ≈0.81pct。量级不变、结论不变，登记备查。
- 另注：交接清单 1"IMC/LAMBDA/SIMC/SOPDT_IMC 四条目 max=5.0"措辞不确——SIMC 注册参数为 tauCRatio 非 lambdaRatio；lambdaRatio 条目为 IMC/LAMBDA/SOPDT_IMC 三个，均未动（运行时 dump 证实）。纯措辞，无实质。

## 6. 结论

**PASS**。裁决五要素（上限 [0.1,7.0]+默认 7.0 三处单源、仅 IPDT、R15 一字不动、验证器零触碰、诚实标注保持）全部按 DEC λ 终裁行落地；λ=7.0/6.5/6.0 三档实测数值独立复算逐项吻合；76 例测试、ruff/format、全量 6249 分母门禁独立复跑全绿。§5 三项观察均为文档口径/既有冻结属性，不阻断，移交主协调登记（F1 勘误、F2 后续裁决项）。
