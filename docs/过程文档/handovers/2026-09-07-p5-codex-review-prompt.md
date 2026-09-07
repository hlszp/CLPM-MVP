# P5 主审 Codex 检查提示词：测点子表重构 + 接口补充（两轮合并复核）

> 你是本轮重构的**主审 Codex**，在仓库 CLPM-MVP 的独立 worktree 中执行 P5 阶段：复核实施者（zcode）交付的两轮改动（P0~P4 主体重构 + AD01~AD08 接口补充），对**最终工作区状态**重新验证，通过后按用户已有授权完成合并。实施者不自行合并；你合并不等于推送/部署。

## 1. 必读清单（按序）

1. worktree `AGENTS.md`（项目纪律红线，尤其"关键注意事项"与"Git 工作流"）
2. `docs/过程文档/stale-docs.md`（引用任何旧文档前对照）
3. `docs/设计文档/2026-09-06-algorithm-data-interface-amendment.md`（**补充方案全文**——本轮接缝改造的授权范围与验收标准，I01~I07/AD01~AD08/§6 用例表）
4. `docs/设计文档/2026-09-06-tag-timeseries-logical-wide-refactor.md`（原设计，重点 §2.2 冻结边界/§4/§5）
5. `docs/过程文档/2026-09-06-tag-timeseries-refactor-plan.md` §2/§5/§6/§7 + **§8 最新状态行**（P0~P4 与 AD 轮的完成状态与遗留登记）
6. 三份交付报告：
   - `docs/过程文档/2026-09-06-tag-timeseries-contract-baseline.md`（P0 契约/基线/入口清单）
   - `docs/过程文档/2026-09-06-tag-timeseries-acceptance.md`（P4 验收：V01~V16 矩阵、A-1~A-8/B-1 未验项）
   - `docs/过程文档/2026-09-07-amendment-ad01-ad08-record.md`（AD 轮：§1 任务对照/§2 用例结果/**§3 受保护差异申报**/§4 签名对照/§6 登记项）
7. 背景（按需）：`docs/过程文档/2026-09-06-tag-timeseries-refactor-handoff.md`（原始阶段交接）

## 2. 交付物状态与复现命令

**审查对象**：
- worktree：`/Users/zhangping/DEV/CLPM-MVP-worktrees/tag-timeseries-refactor`
- 分支 `codex/tag-timeseries-refactor`，HEAD 恒为 `9ee40210e133288830ac8a3ee25103ee6be8bb8d`（两轮全部改动**未提交**，`git status` 应约 63 项：25 个 tracked 修改 + 新增模块/测试/文档 untracked）
- 主工作区 `/Users/zhangping/DEV/CLPM-MVP` 应保持干净（未触碰）；WS/token 修复已含于基线

**隔离环境**（实施者搭建，容器名 `clpm-ref-*` 与主开发 `clpm-mvp-*` 隔离）：

```bash
cd deploy && docker compose -f docker-compose.refactor.yml up -d
# PG16@17202（clpm_refactor，alembic head=r1p0int00001）/ Redis@17203
# TDengine 3.3.6.0@17204 + 3.3.6.6@17214（arm64；注意 compose 项目名与 dev 共享
# "docker"——严禁 --remove-orphans）
# worktree backend/.env 已指向该栈
```

**复现命令**（worktree/backend 下；证据须来自你对最终状态的重跑，不采信实施者历史输出）：

```bash
uv run ruff check . && uv run ruff format --check .     # 期望：全过
uv run pytest -q                                          # 期望：4807 passed, 379 skipped, 32 xfailed
uv run alembic check                                      # 期望：无漂移
uv run pytest tests/integration/test_refactor_point_store.py \
   tests/integration/test_refactor_metadata.py \
   tests/integration/test_refactor_writer_and_import.py \
   tests/integration/test_refactor_builder_equivalence.py \
   tests/integration/test_refactor_provider_differential.py \
   tests/integration/test_refactor_baseline.py \
   tests/integration/test_refactor_acceptance.py \
   tests/integration/test_refactor_amendment_e2e.py -m integration -q   # 期望：61 passed
```

**Goldens**（`backend/tests/golden/`）：`refactor_algorithm_baseline.json`（算法等价基准，勿重录）、`refactor_protected_manifest.json`（受保护清单，39 文件）、`refactor_query_baseline.json`（时延记录，不比对）。

## 3. 阶段范围（P5-1～P5-5 + 补充方案复核）

按计划 §4-P5 执行，另加补充方案专项：

- **P5-1 状态接收**：核对 HEAD/未提交状态与报告声明一致；确认无"测试后继续改代码仍引用旧结果"（对照 §8 各行时间与测试名）。
- **P5-2 实质审查**（重点，对照计划 §7 合并阻断项逐条）：
  1. **算法零改动**：受保护 3 文件 diff 逐行核对（申报见实施记录 §3）——`data_types.py` 仅可选字段、`pipeline.py` 仅两处透传接缝、`diagnosis_orchestrator.py` 仅输入组装/门禁/KPI 上下文接缝；**分类/融合/算子数学/阈值不得有任何变化**。metric_calculator/tuning_identification/tuning_algorithms/arma 等其余 36 文件哈希应与 golden 一致（跑 `tests/test_refactor_protected_manifest.py` 验证）。
  2. **无假 Good/未知填正常/断点拼接**：重点文件 `logical_wide_builder.py`（未知→None+(-1) 不删行）、`point_history_writer.py::decode_quality`（未知码恒 UNKNOWN）、`tuning.py::_point_axis_signals`（同轴直通+最长连续段+全窗/选段分开）、`diagnosis_orchestrator.py::_scoped_operator_input`（公共掩码同轴）、`preprocessing/input_guards.py`（time_constant 缺口守卫）。
  3. **写入语义**：`point_history_repository.write_events`（读比分流：同 payload 幂等/不同 payload 登记冲突不静默覆盖）；`realtime_subscriber` 的 PointHistoryWriter 挂接是否复用同一事件流（不得有第二订阅者）、启停是否跟随 Leader。
  4. **缓存语义**：`l1_datablock/l2_bundle` 键构造——**仅非 legacy 追加版本分量、legacy 键逐字节不变**（部署零失效声明）；control_type 序列化修复真实；manifest 变更→`invalidate_layout_caches` 全量失效。
  5. **旧表/旧读保留**：st_loop_data 与 legacy 读路径未删未禁；回退覆盖边界如实披露（验收报告 §4）。
  6. **无远端降级**：builder/router 只读本地 TD；`get_provider()` 恒 TDengineProvider。
  7. **迁移同批**：`alembic/versions/r1p0int00001_*.py` 与 bootstrap `db/postgresql/01_schema.sql` 六表一致；`alembic check` 无漂移；TD DDL 幂等（`ensure_schema`）。
  8. **事件披露核查**：实施者报告了一次 `git checkout` 误还原 provider 后凭上下文重建——请重点 diff `tdengine_provider.py` 全文（路由闭包/`_point_or_mixed_query`/`_legacy_wide_rows_query`/上下文 mixed 标记），并以 `test_refactor_provider_differential.py`（4 例，含 V14 边界）+ `test_refactor_amendment_e2e.py` 三链路重跑确认重建完整。
- **P5-3 对齐 main**：`git fetch github main` 后检查 main 是否有 9ee40210 之后的新提交；若有，在**重构分支**合入解决冲突（保留 WS/token 等成果），不得在主工作区 stash/reset。
- **P5-4 整合后复验**：对整合准确版本重跑 §2 命令；若 main 新提交触及共享数据链路（realtime_subscriber/data_import/tdengine*），重跑对应差分/故障测试。
- **P5-5 合并**：审查通过后 `--no-ff` 合并保留分支历史，输出合并提交号+验证结论+未执行环境项清单。
- **补充方案专项**：对照实施记录 §2 十二条用例——抽验至少：用例 2（无效有限值不进辨识）、用例 5（SP/OP 单独缺失同轴）、用例 6（[0,1,60,61] 守卫）、用例 7（0.7 不二次乘）、用例 9（L1 冷热 control_type）。签名对照表（实施记录 §4）逐项核对。

## 4. 执行纪律

- 未验项不得写成通过；A-1~A-8/B-1/R-1~R-4 的登记口径不得在合并中被改写。
- 测试失败先区分：实施缺陷→打回实施者修复（不放宽断言）；预存在非回归→维持登记。**已知预存在项**（非本分支引入）：`test_grade_distribution_pg.py::test_distribution_matches_row_by_row` 在全新 bootstrap+seed 库失败（9ee40210 原始库复现已证）；`test_aas_api.py` 2 例需活跃 mock AAS 数据。
- 实施者改动**未提交**：你复核通过后先在重构分支按逻辑单元提交（Conventional Commits，迁移与 ORM 同批），再合并；**推送 github 与部署不在本轮授权内**，合并后停手并报告。
- 不动 `origin`（gitea，pushurl 锁死）；不删旧表/旧代码；不注册已退役诊断引擎。
- 隔离栈仅供测试，勿对主开发实例（clpm-mvp-*）或 zpdev 执行任何变更。

## 5. 完成度核验问题（合并前逐项作答）

1. 你重跑的门禁数字与 §2 期望一致吗？不一致项归因（实施缺陷/环境/预存在）？
2. 受保护 3 文件 diff 是否全部落在申报接缝内？有无任何公式/阈值/评分/门禁语义变化？
3. 三链路（评估/诊断/整定）point 等价证据是否来自最终代码（尤其 provider 重建文件）？
4. 缓存键兼容声明（legacy 字节不变）经你抽查构造验证了吗？
5. 合并阻断项（计划 §7 八条）逐条排除？
6. §6 十二条 + V01~V16 抽验结果；未验项清单是否在合并说明中原样保留？
7. main 对齐后的复验对象与合并对象是否同一版本？

## 6. 人工决策点

- 受保护文件出现**超出申报范围**的差异、或需改公式/阈值/外部契约 → 停止合并，提交具体差异给用户裁决。
- B-1（读时延 2.5~2.7× vs 目标 1.2×，绝对值 ~80ms/回路·小时窗）是否阻塞合并：实施者建议**不阻塞**（已登记+绝对值可用+不以降采样换取达标）；如你判断阻塞，给出理由交用户。
- R-1（解释配置历史以改绑边界近似）接受与否：涉及现场是否改过量程/单位的业务判断，如接受请在合并说明标注为已知近似。
- 其余按已有授权完成（审查通过即合并，`--no-ff`），报告提交与实际验收状态。
