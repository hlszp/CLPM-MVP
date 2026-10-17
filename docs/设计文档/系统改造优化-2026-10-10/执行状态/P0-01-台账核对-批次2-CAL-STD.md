# P0-01 台账核对报告·批次2：CAL-01~11 + STD-01~03

- 核对子智能体：独立只读代理（未参与执行包编写）
- 开工/收工 HEAD：`2c817248fc1d55e85dfd413ecfd9b4f01b9efbad`（一致，无中途变更）
- 日期：2026-10-10（P0 执行期）
- 判定统计：✅确认存在 14 ｜ ⚠️漂移 0 ｜ ❌已修复/不可复现 0 ｜ ❓无法核验 0

## 逐条核对结果

**CAL-01 ｜ ✅确认存在（活链）** ｜ `backend/app/services/confidence_evaluator.py:340-342`——核心指标必需性检查循环中 `if weight <= 0: continue`，零权重指标（如 LOGIC 型 a=0 的 accuracy_rate）跳过存在性/E 级检查；`confidence_evaluator.py:439`——末尾 `confidence = metric_results["accuracy_rate"].confidence_level` 用 `[]` 无条件下标读取，a=0 且 metric_results 无 accuracy_rate 键时 KeyError（438 行注释"accuracy_rate 必存在"的前提只覆盖 weight>0 路径）。台账行号 340/439 未漂移。

**CAL-02 ｜ ✅确认存在（活链）** ｜ `backend/app/services/metric_calculator/stability.py:156-165`——`if std_error == 0.0 and mean_error != 0.0: return self._make_inconclusive(bundle, "dead_signal_constant_offset", ...)`；PV=变化SP+常数偏置时 error 序列恒定、std=0 且 mean≠0，会被判死信号（代码判据未区分该反例，注释自认"无法与真完美区分"仅针对 mean=0 分支）。行号 156 未漂移。

**CAL-03 ｜ ✅确认存在（活链）** ｜ 三个落点逐一核实：
- `backend/app/services/metric_calculator/effective_auto.py:118-120`——注释"偏差检查（pv/sp 信号缺失时视为偏差合理）"，`is_deviation_ok = True` 初始化，`if e_max > 0 and has_pv_sp and ...` 条件不满足即保持 True，偏差合理性自动成立；
- `db/postgresql/02_seed_data.sql:728`——`('effective_auto_rate', '有效自控率', 'MODE_HF', '["mode","op"]', ...)`，MODE_HF 契约默认只含 mode/op 无 pv/sp；
- `backend/app/services/data_planner.py:737`——`_derive_from_base` 中 `for role in tag_roles:` 按契约 tags 角色从 BASE 派生子 tagGroup 信号（MODE_HF 只带出 mode/op）。

**CAL-04 ｜ ✅确认存在（活链）** ｜ `backend/app/services/node_performance.py:348`——`weight_total = sum(... for r in representatives)` 分母计全部代表回路权重（含字段为 NULL 的行）；`:353-365`——`avg_value()` 分子仅累计 `val is not None` 的行（NULL 不进分子）但除以全量 weight_total，两回路等权其一 score=100 其一 NULL 时结果为 50。台账行号 348/353/365 未漂移。

**CAL-05 ｜ ✅确认存在（活链）** ｜ `backend/app/services/node_performance.py:310`——`KpiSnapshotHourly.ts_start <= ts_end` 聚合窗上边界为 `<=`（含等值边界）；`backend/app/tasks/kpi_calc.py:1057`——`_do_calculate`（:944 起，签名接受任意历史 `ts_start`，含 backfill 窗口）尾部 `calculate_node_kpi_hourly.delay()` 无参级联；`kpi_calc.py:3222-3225`——`_do_calculate_node_kpi` 无参时窗口固定为 `now.replace(minute=0,...)` 与其前 1 小时，即历史窗口计算完成后级联的是"当前系统时间上一小时"的节点聚合，两者窗口不一致。台账行号 1057/3222 未漂移。

**CAL-06 ｜ ✅确认存在（活链）** ｜ `backend/app/services/workbench_assessment.py:466-477`——参评分母 total_loops 取 GLOBAL 行 `loop_count`（窗口内已评回路·小时累计，见 `workbench_overview.py:96-106` `_loops_per_hour` = loop_count/hours）推算，即应评数从已评量反推而非回路台账口径；`workbench_assessment.py:75-84`——`_sparkline_delta` 返回 sparkline 末值−首值，环比为本窗首末差。台账行号 466/75 未漂移。

**CAL-07 ｜ ✅确认存在（活链）** ｜ `backend/app/services/workbench_precalc.py:318`——`score = agg["score"] if agg["score"] is not None else 0.0`，无评分聚合预计算落 0.0 写入 WorkbenchWindowSummary（:327/:340 upsert 均用该 score）；`backend/app/services/workbench_assessment.py:125`——`shape_summary` 中 `score = _to_float(getattr(win_row, "score", None))` 直接读预计算 score，无 loop_count=0 置 None 的兜底（该兜底只在 `workbench_overview.py:112-119` 的另一条读路径上）。台账行号 318/125 未漂移。

**CAL-08 ｜ ✅确认存在（活链）** ｜ `backend/app/services/preprocessing/quality_summary.py:79-81`——`unknown_slot_count is not None` 分支中 `missing_count = max(0, min(unknown_slot_count, total))` 且 `expected_count = total`，missing_count 可 >0；`:88`——`missing_rate = missing_count / expected_count if expected_count > total else 0.0`，该分支 expected_count==total 恒使 missing_rate 落 0.0，摘要出现 missing_count>0 且 missing_rate=0.0 的口径矛盾。台账行号 79/88 未漂移。

**CAL-09 ｜ ✅确认存在（活链）** ｜ 四个落点逐一核实：
- `backend/app/services/metric_calculator/settling_time.py:95`——`pairs = self._get_masked_pair(bundle, "pv", "sp")` 取掩码压缩后的有效对；
- `:272-274`——`_check_uniform_sampling` 基于压缩后时间戳排序求间隔；
- `:314-321`——非均匀判据为"偏离中位 ±20% 的间隔占比 > 10%"才拒绝（`jitter_ratio > _INTERVAL_JITTER_RATIO_LIMIT`），少量大 gap（占比 ≤10%）可整体通过，之后压缩序列按连续等间隔进 AR 辨识，非真实分段；
- `backend/app/services/preprocessing/input_guards.py:25`——`GAP_SENSITIVE_METRICS = frozenset({"time_constant"})`，settling_time 明确不入册（docstring :10-11 称其"已内建分段支持"），实际内建的仅是上述比例检查。

**CAL-10 ｜ ✅确认存在（活链）** ｜ `backend/app/tasks/kpi_calc.py:2840-2885`——`_save_confidence_latest` 中 `eval_time = now`（写入时刻），`on_conflict_do_update(index_elements=["loop_id"])` 无条件覆盖全部字段，不比较 data_ts_end/数据窗口大小，历史窗口任务迟到完成即覆盖 latest；`kpi_calc.py:3071-3137`——`_persist_snapshot` 分发：custom_task_id 非 None 走 `_save_custom_snapshot`（写 kpi_snapshot_custom，不更新 latest），标准分支才调 `_save_confidence_latest`，即 custom 不更新 latest 属现有边界。台账行号 2840/3071 未漂移。

**CAL-11 ｜ ✅确认存在（活链）** ｜ 三个落点逐一核实：
- `backend/app/models/metric.py:159-160`——`UniqueConstraint("loop_id", "ts_start", name="uq_kpi_snapshot_hourly_loop_ts")` 同 loop+窗唯一约束（实际语句在 160，159 为注释行，偏移 1 行属定位提示级）；
- `backend/app/tasks/kpi_calc.py:2812-2822`——`update_cols` 排除 id/loop_id/ts_start 后 `on_conflict_do_update(index_elements=["loop_id","ts_start"], set_=update_cols)`，冲突时旧数值/algorithm_version/fitness 等全部列被覆盖；
- `backend/app/services/node_performance.py:571-587`——`save_node_snapshot` 按 plant_node_id+ts_start 查到 existing 即逐字段 `setattr` 覆盖更新，节点快照同样覆盖。

**STD-01 ｜ ✅确认存在（活链）** ｜ `backend/app/services/metric_calculator/accuracy.py:91-102`——`|E|_max` 优先 CONFIG 覆盖，否则 `e_max = tolerance_ratio × U`（工程容限，默认 0.02 量程，:41）；`:73-74/112/120`——分子为 `mean_abs_error`（MAE 平均绝对偏差），r=MAE/e_max 代入指数公式 A=[1−r(1−e⁻ʳ)]×100。代码侧"MAE+工程容差"事实成立；与 GB/T 资料性 B.3 的比较不核（另由专门子智能体负责）。

**STD-02 ｜ ✅确认存在（活链）** ｜ `backend/app/services/metric_calculator/fast_rate.py:103`——`fast_threshold = ideal_t * ideal_settling_ratio * (1.0 + settling_tolerance)`；`:177-189`——`actual_t <= fast_threshold` 即返回 100 满分区；`:196-198`——超出后 `ratio = (actual_t - ideal_t)/ideal_t`、`fast_rate = exp(-ratio)*100`。tolerance>0 时阈值处右侧值 = exp(−ratio×tol)×100 < 100，100 分到衰减值的不连续跳变在合法参数下存在；`backend/tests/compliance/test_b4_fast_rate.py:58`——`test_t_equals_ideal_full_score_boundary`，测试经 `_calc` 注入 settling/ideal 依赖直接调 `FastRateCalculator.calculate`，验证的是产品公式边界而非资料性 B.4 全口径。台账行号 103/177/196/58 均未漂移。

**STD-03 ｜ ✅确认存在（活链）** ｜ `backend/app/services/workbench_precalc.py:103-115`——`score_to_status` 阈值 90/75/60/40（EXCELLENT≥90 / GOOD≥75 / FAIR≥60 / POOR≥40 / CRITICAL<40），产品五档与国标参考值不同（国标侧不核）；`backend/app/tasks/arma.py:32`——`DEFAULT_MA_ORDER = 1  # q=1，设计文档要求（当前 AR 近似未实现 MA 部分，保留常量记录设计意图）`，`:71` `fit_ar_model` 仅做 AR(p) Yule-Walker 辨识再接 Green 函数递推，"AR+Green 不能称完整 ARMA"由代码注释自证。台账行号 103/71 未漂移。

## 补充观察（供 P1 任务卡参考，不扩大 P0 范围）

- 台账编制基线内已含 2026-10-10 当日的 stability dead_signal 判据与 accuracy v2.2 工程容限修正，本轮台账描述与修正后代码一致，无"描述落后于代码"的情况。
- CAL-07 的伪 0 兜底只存在于 workbench_overview 读路径，workbench_assessment.shape_summary 路径确无兜底，两条读路径口径分叉本身可作 P1-02 附加素材。
- CAL-05 的级联目标 `calculate_node_kpi_hourly` 与手动单节点任务 `calculate_node_kpi`（kpi_calc.py:3195，支持显式时间窗）并存，修复时可复用后者的时间窗参数化模式。
- CAL-08 的矛盾只在 `unknown_slot_count is not None`（point 网格）分支触发，legacy 行数差分支因 `_compute_expected_count` 的 max 兜底恒有 expected≥total，missing_rate 不会为 0 而 count>0。
