# 交接：G3 收口最后一步——λ 停点终裁落地（R14 扩上限+IPDT 默认 7.0）

- 状态：**实施完成，待非作者验收**（2026-10-12 λ 终裁裁决落地批：前任回摊停手后从头执行，树已核实零改动起步）
- 根目录/分支/基点：worktree `/Users/zhangping/DEV/CLPM-MVP-p307`，分支 `feat/p3-07-robust-tuning`，基点 `0ec8b45e`（G3 批 B R03 合并提交，零改动起步；**未 commit**）；工作区 4 改 0 新增：`services/tuning_algorithms.py`(+14/−11)/`services/tuning.py`(+6/−2)/`tests/test_p3_07_robust_tuning.py`(+124/−24)/golden manifest（哈希+baseline_head）；+144/−37；tracked diff hash `529a98fe08b3f431fc9008236b877a416acce2e9`
- 运行环境/代码 Owner：λ 落地实施智能体（本作者）；worktree 既有 `.venv`（uv sync）；无 DB 依赖（全部 mock/纯计算）；未启动任何后端/Worker/Beat；**未 commit/push**
- 用户授权范围：DEC 清单 λ 停点终裁行一字不差——**R14 λ 范围 [0.1,5.0]→[0.1,7.0]（仅 IPDT；SOPDT/FOPDT 不变）+ IPDT 默认 λ=7.0；R15 四项冻结指标一字不动；落地含 λ=7.0 四项实测回归（64 集合宽 CI）；验证器"如实标记"行为保持**。tuning.py + tuning_algorithms.py 本批 Owner（承接 P3-07 Owner 链）

## 裁决落地清单

| # | 裁决项 | 文件/符号 | 行为差异 |
|---|---|---|---|
| 1 | R14 范围 [0.1,7.0] | `tuning_algorithms.py`：TUNING_METHODS_INFO `IPDT_PI` 条目 | `max: 5.0→7.0`（`min: 0.1` 不变）；校验边界随注册表单源自动生效（schema 422 + 服务端 ERR_TUNING_PARAM_INVALID 同源）；**IMC/LAMBDA/SIMC/SOPDT_IMC 四条目 max=5.0 一字未动**（SOPDT/FOPDT 不变） |
| 2 | IPDT 默认 7.0 | 同上 `"default": 1.0→7.0` + `tune_ipdt_pi` 签名默认 `lambda_ratio=7.0` + `services/tuning.py` tune_pid IPDT 分支 fallback `params.get("lambdaRatio", 7.0)` | 三处单源一致（注册表/内核签名/服务 fallback）；缺省 algorithmParams 时建议=λ=7.0 档；notes 文案同步（默认 7.0 宽 CI 达标+调小如实标记）；registry description 更新为终裁口径 |
| 3 | R15 四项一字不动 | 冻结常量 ROBUSTNESS_* | **零触碰**（守卫测试 `test_r15_frozen_thresholds_verbatim` 持续锁定 GM≥2.0/PM≥30°/超调≤20%/集合≤64） |
| 4 | 诚实标注保持 | 验证器 `verify_robustness_on_set` | **零触碰**（调小 λ 仍见不通过标记，测试锚定见下） |

## λ=7.0 四项实测数值表（本批实测，2026-10-12，内核直测）

主口径=宽 CI（K±5%/θ±10%，`build_trustworthy_set` 64 成员），复核=窄 CI（K±2%/θ±5%）；op_limits=±100；GM≥2.0/PM≥30°/超调≤20%/OP 零饱和为 R15 冻结判据：

| 工况（IPDT） | λ | 集合 | GM | PM(°) | 超调(%) | OP 饱和步 | 结论 |
|---|---|---|---|---|---|---|---|
| K=0.1, θ=10（典型） | 7.0 | 宽 64 | 5.618 | 59.28 | **19.12** | 0 | **四项全过** |
| θ=5 / θ=20 / θ=60 | 7.0 | 宽 64 | 5.618/5.618/5.618 | 59.28/59.28/59.28 | 19.12/19.12/**15.63** | 0 | 全过（θ=60 因步长细化略优） |
| K=0.05 / 0.2 / 0.5 | 7.0 | 宽 64 | 5.618 | 59.28 | 19.12 | 0 | 全过（IPDT 规则 K/θ 尺度不变性） |
| **K=-0.1（负增益）** | 7.0 | 宽 64 | 5.899 | 59.34 | 18.66 | 0 | 全过（镜像一致） |
| 典型（复核） | 7.0 | 窄 64 | 6.069 | 60.03 | 18.66 | 0 | 全过（8/8 工况窄 CI 均过，余量更大） |
| 典型 | 6.5 | 宽 64 | 5.277 | 58.16 | 19.62 | 0 | 过（边界抽验档） |
| 典型 | 6.0 | 宽 64 | 4.936 | 56.89 | 20.23 | 0 | **超调不过**（如实标记） |
| 典型 | 5.0 | 名义单点 | ≈4.9 | ≈56 | ≈20.8 | 0 | 超调不过（诚实标注回归锚点） |
| 典型 | 1.0 | 名义单点 | 1.71 | 21.2 | ≈86 | 0 | GM/PM/超调三项不过 |

**与裁决引用数据逐项吻合**：λ=7.0 宽 CI 超调 19.12%（余量 0.88pct）、λ=6.5→19.62%、λ=6.0→20.23%（差 0.23pct 不过）——三值与 DEC 清单 λ 终裁行及前任补测数据完全一致，独立复现成立，未触发"不达标即停"分支。

## 测试断言（`tests/test_p3_07_robust_tuning.py`，67→76 例，+9）

| 断言 | 测试 |
|---|---|
| λ=7.0 宽 CI 四项达标 | `test_lambda_7_default_wide_ci_all_four_pass`（GM>5.0/PM>55/18.5<OS<20/零饱和/overallPass） |
| θ/K 变体+负增益宽 CI 全过 | `test_lambda_7_wide_ci_variants_all_pass`（5 参数化：θ∈{5,20}/K∈{0.05,0.2}/K=-0.1） |
| 窄 CI 复核 | `test_lambda_7_narrow_ci_recheck_pass` |
| λ=6.5 边界抽验 | `test_lambda_65_boundary_spot_check_pass`（19.0<OS<20 达标） |
| 注册范围+默认值 | `test_lambda_ratio_structure_reused` 重写（IPDT (0.1,7.0,7.0)；SOPDT/FOPDT 维持 (0.1,5.0,1.0) 不变断言）+ `test_validate_tuning_params_accepts_and_rejects`（7.0/6.5 收、7.5/0.05 拒、SOPDT 6.0 仍拒） |
| 服务级默认 | `test_ipdt_default_lambda_is_7`（algorithmParams=None → λ=7.0 建议，三处单源一致） |
| **诚实标注回归（调小 λ 仍见标注）** | `test_lambda_5_overshoot_still_above_frozen_threshold`（20.0<OS<22.0 如实不过）+ `test_lambda_1_default_margin_fail`（λ=1.0 激进档 GM/PM 不过）——docstring 更新为终裁后语义，断言保留 |
| 端点 422 边界 | `test_endpoint_rejects_invalid_lambda`（6.0→7.5 越界值同步） |

## 自测（裸命令真实退出码）

| 命令 | 退出码 | 结果 |
|---|---|---|
| `uv run pytest tests/test_p3_07_robust_tuning.py -q` | 0 | **76 passed** |
| 相邻回归（tuning/p2_01/eligibility/security_dcs/p4_02t/openapi 契约） | 0 | 236 passed |
| `uv run ruff check .` / `ruff format --check .` | 0 | All checks passed / 864 files formatted |
| golden 守卫（重固化前） | 1 failed | 预期漂移（tuning_algorithms.py 受保护清单） |
| `REFACTOR_UPDATE_GOLDEN=1 uv run pytest tests/test_refactor_protected_manifest.py` | 0 | 按既有授权机制重固化（同 P3-07 程序：该文件在受保护清单且本线唯一 Owner；diff 仅 2 处=文件哈希+baseline_head 5d664dc3→0ec8b45e） |
| golden 守卫（重固化后） | 0 | 1 passed |
| `uv run pytest -q`（全量） | **0** | **6249 passed, 0 failed**, 337 skipped, 108 deselected, 12 xfailed, 4 xpassed（292.14s；`PYTEST_EXIT_CODE=0` 裸命令实捕获；分母较 P3-07 交接的 6212 多 37 = G3 批 B R03 增量 + 本批 9 例） |
| `uv run alembic check` | 环境限制 | 共享 dev 库凭据不可用（06 系同款）；补偿=`git diff -- app/models/` 为空（零 ORM 变更） |

## 影响与回退

- **行为变更**（有意，全部裁决授权）：①IPDT_PI lambdaRatio 注册范围上限 5.0→7.0（校验边界同步，SOPDT/FOPDT 五法一字未动）；②IPDT 缺省 λ 1.0→7.0（注册表/内核签名/服务 fallback 三处单源）；③registry description 与 tune_pid notes 文案更新为终裁口径（原"建议 ≥2.0"引导废止）。
- **零变更**：R15 冻结常量与验证器判定逻辑、SOPDT_IMC/IMC/LAMBDA/ZN/COHEN_COON/SIMC 全部条目、tuning_identification 内核、前端（方法参数消费 GET /tuning/methods 注册表动态单源，P2-01；无 lambdaRatio 硬编码）、DB 模型/迁移。
- **API 兼容**：OpenAPI 契约漂移测试通过（registry 值不进 schema）；schema 层 422 边界随注册表变化属裁决授权语义。
- **回退**：worktree 未 commit；`git checkout -- .` 即完全回退（4 文件，golden 一并还原）。
- **验收后接续**（主协调）：G3 汇总报告草稿转正式（λ 停点已闭合）→ G3 终审；P3-07 交接"接续第一步"其余项（tunable 放开裁决/前端 P6 接线）不受本批影响。

## 接续第一步（非作者验收建议复验点）

①`test_lambda_7_default_wide_ci_all_four_pass` 直接复跑（19.12% 实测锚点与 DEC 终裁行数值对得上）；②`test_lambda_ratio_structure_reused`（SOPDT/FOPDT 不变断言=裁决"仅 IPDT"边界）；③`test_ipdt_default_lambda_is_7`（服务级默认单源）；④诚实标注两例（λ=5.0/λ=1.0 仍不过）；⑤golden manifest diff 仅 2 处复核。
