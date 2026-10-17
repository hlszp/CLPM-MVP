# G5-γ1 交接文档（kpi_calc INCONCLUSIVE 分支 source/版本丢参缺陷修复）

- **智能体**：G5-γ1 缺陷修复智能体（γ 测量批 1 暴露代码缺陷）
- **日期**：2026-10-13
- **worktree**：`/Users/zhangping/DEV/CLPM-MVP-g5g1`，分支 `fix/g5-gamma1-inconclusive-source`（基线 `1b8c69a4`）
- **状态**：修复 + 回归测试 + 门禁全部完成，**未 commit / 未 push**（按纪律，不合入不催部署，合入时机由主协调在 γ 批次间歇控制）

---

## 1. 缺陷核实结论（定位确认，测量智能体定位正确）

**文件**：`backend/app/tasks/kpi_calc.py`（基线行号 L2644 附近，`_calculate_loop_kpi` 内"综合评分为 None → INCONCLUSIVE"分支）

**丢字段机制（两层，均已核实）**：

1. **吞字段**（source/run_binding 两条违约的根因）：分支内 `fitness_kwargs` 初始 dict 含 `source`/`source_task_id`，但 fitness 计算成功后执行
   `fitness_kwargs = _build_fitness_kwargs_from_result(fresult)`（L2666）**整体替换**——该函数（L1992）返回集只有
   fitness_level/fitness_tags/fitness_detail/assess_level/diagnose_level/tune_level 七键，source/source_task_id 被吞。
   触发条件 = `composite_result.value is None`（E 级）**且 compute_fitness 成功**（返回 L0/DATA_INSUFFICIENT）——
   scenario b 的 100 个 PV 70% BAD 回路全部命中；fitness 计算抛异常的路径反而保留初始 dict（不丢）。
2. **从未传**（algorithm_version/latest 两条违约的根因）：主路径靠 `**lineage_info`（L2842）传入
   algorithm_version（`_extract_lineage_info` L3378 有 `or ALGORITHM_VERSION` 兜底）等 7 个血缘字段，
   而 INCONCLUSIVE 分支的 `_timed_persist` 调用压根没有 spread lineage_info——即使不发生吞字段，该分支的
   algorithm_version 也是 NULL。下游：`_save_snapshot`/`_save_custom_snapshot` 投影列直取 kwargs；
   `_save_confidence_latest` 的 algorithm_version 直取 kwargs **无兜底**（latest 违约）；
   `_persist_snapshot` L4208-4213 的 record logical_run_id 派生取 `kwargs.get("source")`（run_binding 违约）。

**主路径为何健康**：主路径在 `_timed_persist` 调用点**平铺显式传参** `source=source, source_task_id=source_task_id`
（L2811-2812，即 G5 前置立卡项1 的修复），不受 `main_fitness_kwargs` 整体替换影响 → 900 SUCCESS 行全健康。

**其他分支核查**：早退分支（DataPlanner 取数失败 L2429 / 空 Bundle L2451）source/source_task_id 平铺显式传参，健康；
但它们同样不传 algorithm_version/lineage（见 §6 残留登记）。主路径 `main_fitness_kwargs` 替换处（L2726）因
source 在调用点平铺，无吞字段风险。

## 2. 现场对照（dev 库只读取证，修复前实证）

dev 库（clpm-mvp-postgres，clpm 库）批 1 窗口 `kpi_snapshot_hourly ts_start='2026-10-13 20:00:00'`：

| 指标 | 值 |
|---|---|
| 总行数 | 1030（测量 1000 + 真实回路） |
| INCONCLUSIVE | 126，**全部** source/source_task_id/algorithm_version 三 NULL（测量 100 行为其子集） |
| SUCCESS source NULL | 0（source=SCHEDULED 共 904） |
| INCONCLUSIVE fitness 形态 | 全部 L0 + `{"tags": ["DATA_INSUFFICIENT"]}`，fitness_detail **无** reason 键 → 证明走的是 compute_fitness 成功路径（早退分支会写 reason 且透传 source） |

`loop_confidence_latest`：1032 行中 126 行 algorithm_version NULL，且 result_record_id 全非空（与批1.json "latest"
违约形态逐字吻合）。`calculation_result_record`：对应 record payload 无 source/source_task_id 键（kwargs 缺参直接实证），
record 自身 algorithm_version=KPI_CALC_v2.0（L4233 有兜底，账本行不受影响）。

## 3. 修复内容（最小修复，2 文件）

`backend/app/tasks/kpi_calc.py`（+25/-11）：

1. **kwargs 合并策略（对齐主路径）**：`source`/`source_task_id` 从可被整体替换的 `fitness_kwargs` 初始 dict 中移除，
   改为 `_timed_persist` 调用点平铺显式传参——与主路径 L2811-2812 同一口径，结构性免疫替换吞字段。
2. **lineage_info 前移**：`loop_valid_rate`/`lineage_info = _extract_lineage_info(...)` 计算从主路径位置（L2694）前移到
   INCONCLUSIVE 分支之前，两路共用；INCONCLUSIVE 分支 `_timed_persist` 增加 `**lineage_info`——algorithm_version
   等血缘字段与主路径同源同值（含 `or ALGORITHM_VERSION` 兜底），闭合快照/latest 版本列违约。
3. 分支原显式 `confidence_level=composite_result.confidence_level` 移除（经 lineage_info 携带，`composite or "E"`
   兜底口径不劣于原值；`_persist_snapshot` 对 INCONCLUSIVE 仍有 "E" 缺省兜底，语义等价）。

被吞字段排查：`_build_fitness_kwargs_from_result` 七键与外层显式字段的交集只有 source/source_task_id 两键，
无其他被覆盖丢失的字段；分支与主路径传参集对齐后差集仅为 score/KPI 值列（该分支设计上不产出，维持现状）。

`backend/tests/test_kpi_calc.py`（新增 `TestInconclusiveBranchSourceThreading`，3 用例）：

- `test_inconclusive_persist_receives_source_and_version`：monkeypatch 捕获 `_persist_snapshot` 实收 kwargs，
  构造 fitness 成功返回 L0/DATA_INSUFFICIENT（批 1 精确触发形态），断言 status=INCONCLUSIVE、fitness_level=L0
  （证明走过整体替换路径）+ source/source_task_id/algorithm_version(ALGORITHM_VERSION)/confidence_level("E")/
  sampling_freq 全部非 NULL。
- `test_inconclusive_source_parity_with_main_path`：同参跑主路径（composite=76.0 → SUCCESS）对照，
  source/source_task_id/algorithm_version 三键与主路径逐键同值。
- `test_build_fitness_kwargs_key_set_excludes_source`：键集守护——该函数返回集恒为七键、不含来源/血缘键
  （防止后人把 source 塞进该函数这个"看似更近"的修法，None 入参分支会再度恒丢）。

**修复前必红验证**：`git stash` 修复仅留测试 → 两个功能用例如期 FAILED（source KeyError=缺陷形态），
键集守护 passed；`stash pop` 恢复后全绿。测试确有回归检出能力。

## 4. 门禁结果

- `uv run ruff check .`：**All checks passed**
- `uv run pytest -q` 全量：**6554 passed, 335 skipped, 129 deselected, 12 xfailed, 4 xpassed，0 failed**（306s）——分母 6551 基线 + 本次新增 3 用例
- `uv run ruff format --check .`：我改的 2 文件 clean；报的 2 个漂移文件
  （`app/services/node_performance.py`、`tests/test_g5_pre_card_fixes.py`）**基线 1b8c69a4 即存在**，非本次引入，未顺手改

## 5. 现场数据处置建议（移交主协调）

批 1 已落库的 126 行 source/版本 NULL 行（含测量的 100 行）是历史数据，代码修复不回填。若 γ 批次重跑 scenario b，
同窗 UPSERT 会以新代码覆盖（hourly 表按 loop_id+ts_start 冲键）；是否需要针对性重算回填由主协调裁决。

## 6. 残留登记（不属本缺陷范围，请主协调裁决是否另立卡）

1. **早退分支版本缺参**：DataPlanner 取数失败（L2429）/空 Bundle（L2451）两分支透传 source/source_task_id 但不传
   algorithm_version/lineage → 若被触发，快照/latest 的 algorithm_version 仍会 NULL（批 1 未命中：
   fitness_detail 无 reason 键证明 126 行全走 compute_fitness 成功路径）。同类违约、不同分支，修复口径可复用本次的
   `**lineage_info` 前移（lineage 需从 bundles 提取，早退时 bundles 可能不存在，需回退 `algorithm_version=ALGORITHM_VERSION` 平铺）。
2. 基线 ruff format 漂移 2 文件（§4）。

## 7. 交付物清单

- 修复：`/Users/zhangping/DEV/CLPM-MVP-g5g1/backend/app/tasks/kpi_calc.py`（未提交，工作区）
- 回归测试：`/Users/zhangping/DEV/CLPM-MVP-g5g1/backend/tests/test_kpi_calc.py`（未提交，工作区）
- 取证证据：`docs/设计文档/系统改造优化-2026-10-10/执行状态/容量实测-20261013/批1.json`（测量方）+ 本文档 §2
- **不合入、不催部署**；worktree `CLPM-MVP-g5g1` 保留待主协调验收合并
