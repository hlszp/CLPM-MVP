# G5-γ1 验收报告（kpi_calc INCONCLUSIVE 分支 source/版本丢参缺陷修复）

- **验收智能体**：G5-γ1 非作者验收（单缺陷小修轻量验收）
- **日期**：2026-10-13
- **被验物**：worktree `/Users/zhangping/DEV/CLPM-MVP-g5g1`，分支 `fix/g5-gamma1-inconclusive-source`，基线 `1b8c69a4`，未提交（+25/-11 kpi_calc.py、+142 test_kpi_calc.py）
- **交接文档**：`G5-gamma1-交接.md`（同目录）
- **结论**：**验收 PASS**（A~F 全部通过；2 项轻微笔误登记，不阻塞）

---

## A. 两层缺陷定位复核 —— PASS（独立确认，非采信修复者转述）

对照基线 `git show 1b8c69a4:backend/app/tasks/kpi_calc.py` 逐条核验：

**① 吞字段机制（source/run_binding 两条违约根因）**：确认。基线 L2647-2653 分支内
`fitness_kwargs` 初始 dict 含 `"source": source, "source_task_id": source_task_id`；fitness
计算成功后 L2666 `fitness_kwargs = _build_fitness_kwargs_from_result(fresult)` **整体替换**。
该函数（基线 L1992-2012）两分支返回键集均为
`fitness_level/fitness_tags/fitness_detail/assess_level/diagnose_level/tune_level`——不含
source/source_task_id，两键被吞。触发条件（composite value None 且 compute_fitness 成功）
与批 1 scenario b 100 回路全命中、fitness 抛异常路径保留初始 dict 不丢——均与交接 §1 一致。

**② lineage_info 从未传（algorithm_version/latest 两条违约根因）**：确认。基线
INCONCLUSIVE 分支 `_timed_persist` 调用（L2669-2681）仅传
db/loop_id/ts_start/ts_end/status/custom_task_id/confidence_level/metrics_detail/`**fitness_kwargs`，
无 `**lineage_info`；且分支在此早退 return，lineage_info 赋值在基线 L2694（分支之后），
该分支结构上不可能携带。主路径对照：基线 L2798-2844 调用点平铺 `source=source,
source_task_id=source_task_id`（L2811-2812）+ `**lineage_info`（L2842）→ 主路径健康成立。
下游消费点核实：`_save_confidence_latest` algorithm_version 直取 kwargs 无兜底（现文件
L4314）；`_persist_snapshot` record logical_run_id 派生取 `kwargs.get("source")/
("source_task_id")`（L4208-4213）——四条违约的传导链与交接 §1 逐环吻合。

**批 1.json defect 条目核对**：rootCause/trigger/impact 与上述独立复核一致。

## B. 修复正确性 —— PASS

1. **平铺传参（结构性免疫声明核）**：确认。修复将 source/source_task_id 从可被整体替换的
   `fitness_kwargs` 初始 dict 移除，改为 INCONCLUSIVE 分支 `_timed_persist` 调用点平铺
   `source=source, source_task_id=source_task_id`——与主路径 L2811-2812（基线行号）同一口径。
   调用点显式 kwargs 不受 dict 重绑定影响，吞字段机制被结构性消除（不是补丁式回填）。
2. **lineage_info 前移**：确认。`loop_valid_rate`/`lineage_info = _extract_lineage_info(...)`
   前移至 INCONCLUSIVE 分支之前（现 L2628-2638），单一定义两路共用。依赖项
   `metric_results`/`composite_result`/`base_valid_rate`（L2485 定义）均在前移位置之前就绪，
   前移安全；主路径沿用同一变量，gate_failed/coverage 两处下游均 `dict(lineage_info)` 拷贝后
   改写（L2763/2792），共享 dict 无污染。algorithm_version 经 `_extract_lineage_info` 的
   `or ALGORITHM_VERSION` 兜底（L3378），与主路径同源同值。
3. **confidence_level 等价性**：确认等价。原显式 `confidence_level=composite_result.confidence_level`
   改经 lineage 携带：truthy 时同值直传；falsy（None）时 lineage 传 "E"，而原路径传 None 后
   由 `_persist_snapshot` 的 INCONCLUSIVE 缺省兜底为 "E"（L4203-4204，该分支恒 INCONCLUSIVE，
   兜底必达）——最终落库值全分支等价。
4. **键集无冲突**：lineage 6 键（algorithm_version/sampling_freq/quality_policy/valid_rate/
   confidence_level/data_lineage）与 fitness_kwargs 键集（初始 3 键/替换后 6 键）无交集，
   `**lineage_info, **fitness_kwargs` spread 无 shadowing/重复键风险。`_timed_persist` 为
   `**kwargs` 纯透传包装（setdefault 注入 logical_run_id 等 4 键），调用点即 `_persist_snapshot`
   实参。

## C. 测试 —— PASS

- **3 用例复跑**：`pytest tests/test_kpi_calc.py::TestInconclusiveBranchSourceThreading -v`
  → 3 passed（0.94s）。
- **反向验证（验收方独立执行）**：`git stash push -- backend/app/tasks/kpi_calc.py`（只撤修复、
  留测试）→ 两个功能用例 FAILED，失败形态恰为缺陷原形 `KeyError: 'source'`（kwargs 缺参直接
  实证）；键集守护用例 PASSED（预期——被测函数未改动）。`stash pop` 恢复后复绿。测试确有
  回归检出能力，与修复者自述的反向验证结论一致且被我独立复现。
- **断言覆盖度评估**：够用。用例 1 覆盖 status/fitness_level=L0（证明走过整体替换路径，触发
  条件精确）/source/source_task_id/algorithm_version/confidence_level/sampling_freq；用例 2
  同参对照主路径，source/source_task_id/algorithm_version 三键逐键同值断言。四条违约的传导
  键全部覆盖（run_binding 由 kwargs source/source_task_id 在 `_persist_snapshot` 内派生，
  latest 违约即 algorithm_version 列，均被捕获点断言传递性覆盖）。可更强（lineage 全 6 键
  逐键对照）但两路 spread 的是同一 dict 对象，现状充分。
- 用例构造与批 1 触发形态精确一致（compute_fitness 成功返回 L0/DATA_INSUFFICIENT）。

## D. 未扩散核 —— PASS

`git diff --stat`：`backend/app/tasks/kpi_calc.py | 36 (++/--)`（+25/-11）、
`backend/tests/test_kpi_calc.py | 142 (+)`，恰为修复者声明的 2 文件、无第三文件。stash
往返后 diff 完整性复核（5 处 G5-γ1 注释标记 + 测试类在位）无损。worktree 另有 3 条
2026-08-22 遗留 stash（feat/handling-v2、feat/tuning-module 的无关 WIP），系仓库既有残留，
非本次产生，未触碰。

## E. 门禁 —— PASS

- `uv run ruff check .`：All checks passed
- `uv run pytest -q` 全量：**6554 passed, 335 skipped, 129 deselected, 12 xfailed,
  4 xpassed，0 failed**（309.15s，验收方独立复跑）——与交接声明逐字一致，分母 = 基线 6551 +
  新增 3 用例
- `uv run ruff format --check .`：修复的 2 文件 clean（882 formatted 之内）；报漂移的
  `app/services/node_performance.py`、`tests/test_g5_pre_card_fixes.py` 经干净基线
  （stash 全部工作区改动后）复核**同样报漂移**——确系基线 1b8c69a4 遗留、非本次引入，
  交接 §4 声明属实

## F. 移交两事项知悉核实 —— 属实 ×2

1. **早退分支版本缺参另立卡**：声明属实。现文件 L2429（DataPlanner 取数失败）/L2451（空
   Bundle）两分支平铺透传 source/source_task_id（L2442-2443/L2464-2465）但不传
   algorithm_version/lineage——若触发，快照/latest 版本列仍会 NULL。同类违约、不同分支，
   归主协调裁决是否另立卡，不属本卡范围。
2. **126 行 NULL 历史由 scenario b 重投 UPSERT 覆盖**：覆盖语义成立。`_save_snapshot`
   UPSERT `on_conflict_do_update(index_elements=["loop_id", "ts_start"], set_=update_cols)`
   （现文件 L3885-3892），update_cols 仅排除 id/loop_id/ts_start，source/source_task_id/
   algorithm_version 均在覆盖集（insert_values L3845/L3877/L3878）；`_save_confidence_latest`
   按 loop_id 冲突全列覆盖，DEC-09 迟到规则对同窗重投（ts_end 相等）不拦截。γ 批次同窗重跑
   scenario b 即以新代码覆盖 NULL 行，不需专项回填——语义确认。

## 轻微笔误登记（不阻塞，建议合入时顺手或留档）

1. **"七键"计数笔误**：`_build_fitness_kwargs_from_result` 返回键集实为 **6 键**
   （fitness 域 3 键 + 三性 3 键）。交接 §1 与 kpi_calc.py 修复注释（L2660"fitness/三性七键"）
   均写"七键"；测试断言（`test_build_fitness_kwargs_key_set_excludes_source`）用的是正确的
   6 键集合，功能与守护均不受影响。
2. 首次全量 pytest 后台执行时汇总行被 tail 截断（LAPACK DLASCL 噪声掩盖），已完整复跑取证，
   结论以上文 E 为准。

## 结论

**G5-γ1 验收 PASS。** 缺陷定位两层均独立复核成立；修复口径与主路径对齐（调用点平铺 +
lineage 前移共享），结构性消除吞字段机制而非点位补丁；回归测试有独立复现的反向检出能力；
改动范围未扩散；门禁全绿且全量分母与声明一致；移交两事项声明均核实属实。建议主协调按 γ 批次
节奏安排合入（+25/-11 与 +142 未提交，worktree `CLPM-MVP-g5g1` 保留）。

红线遵守情况：未启任何服务；未连 dev 库（全部取证走代码与 git 基线对照）；未触碰其他 worktree；
未 commit/push；未动既有 stash。
