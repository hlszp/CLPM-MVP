# P0-R2 漂移复核报告·批次2：CAL-03/04/05/06/07/10/11

- 重验子智能体：独立只读代理；区间 `2c817248..baa3f50d`
- 开工/收工 HEAD：`baa3f50d8d883356064d5774c07122ebb5f360eb`（一致，全程只读）
- 判定统计：✅仍存在 7 ｜ ❌已修复 0 ｜ ⚠️漂移 0

## 逐条重验

**CAL-03 ｜ ✅仍存在（语义未漂移）** ｜ 本轮变更仅 `effective_auto.py` 的 `_read_bounds`（现 :154-172）：`DEFAULT_EPSILON` 2.0→0.0 且 ε 改三级优先（CONFIG 信号 `saturation_epsilon` > 算法参数链 > 默认 0，提交 c811b2fd）——ε 只作用于 OP 饱和判定（:117），不触及偏差检查。偏差检查段原样：现 :119-121 `is_deviation_ok = True` 初始化、`if e_max > 0 and has_pv_sp and ...` 不满足即保持 True；`_read_e_max`（:174-185）未变。`data_planner.py:737` 精确仍在 737 行；`02_seed_data.sql:728` MODE_HF 契约 `["mode","op"]` 未变。

**CAL-04 ｜ ✅仍存在** ｜ `node_performance.py` 本轮仅 3 行序列化键名改动（fastResponseRate→fastRate，:692/:850/:973，b222bc79），全在读取/序列化路径。聚合逻辑原样：现 :347 `weight_total` 分母计全部代表回路（含 NULL 行）；现 :352-364 `avg_value` 分子仅累计非 None 但除以全量 weight_total——两回路等权其一 100 其一 NULL 仍得 50。

**CAL-05 ｜ ✅仍存在（两半均未变）** ｜ ① `node_performance.py:309` 上边界 `<=` 原样；② `kpi_calc.py` 尾部无参级联原样：`_do_calculate`（现 :1003 起）尾部现 :1112 `calculate_node_kpi_hourly.delay()` 无参；`_do_calculate_node_kpi`（现 :3288）无参窗口仍固定当前系统时间上一小时。行号 :1057→:1112、:3222→:3288（kpi_calc 前部 +55 行纯位移）。

**CAL-06 ｜ ✅仍存在（fef31fcb 未修复此条，仅缩小推算偏差）** ｜ `workbench_assessment.py` 本轮零变更：:466-477 参评分母仍从 GLOBAL 行 loop_count（窗口内已评回路·小时累计）反推；:75-84 `_sparkline_delta` 仍末值−首值。fef31fcb 改的是**快照产出人口**（全量扫描只给参评回路写快照，`kpi_calc.py:232-248 _eval_loop_selection_stmt`）与**前端读方过滤**，未触及 workbench_assessment/overview/precalc 三文件——应评数推算口径结构性问题（窗口内未产出快照的参评回路不计入）仍在，只是"不参评回路混入分子"这一偏差源被消除。

**CAL-07 ｜ ✅仍存在** ｜ 两文件本轮零变更。`workbench_precalc.py:318` 无评分聚合落 0.0（:326/:343 upsert 均用）；`workbench_assessment.py:125` 直接读预计算 score，loop_count=0→None 兜底仍只在 `workbench_overview.py:118-120` 那条读路径。

**CAL-10 ｜ ✅仍存在（纯行号漂移）** ｜ `_save_confidence_latest` 现 :2917-2963——`eval_time: now`（写入时刻）、`on_conflict_do_update(index_elements=["loop_id"])` 无条件全字段覆盖仍不比较 data_ts_end；`_persist_snapshot` 现 :3148-3215——custom 不更新 latest、标准分支才 latest，分发原样。

**CAL-11 ｜ ✅仍存在（P1-05 前提成立）** ｜ `kpi_calc.py` 现 :2889/:2894-2896 `on_conflict_do_update(index_elements=["loop_id","ts_start"], set_=update_cols)` 冲突全列覆盖；`node_performance.py:573-593` `save_node_snapshot` 查到 existing 即逐字段 setattr 覆盖；`models/metric.py` UniqueConstraint 未变。

## 附加登记

四文件 diff 合计 +177/−44：
- `effective_auto.py` +20：ε 默认 0 + 三级可配置（c811b2fd）
- `saturation.py` 23 行：同 c811b2fd——DEFAULT_EPSILON 2.0→0.0、ε 读取 CONFIG>参数链>默认 0、饱和=严格贴限位端点
- `node_performance.py` 6 行：fastResponseRate→fastRate 键名修复（b222bc79，节点快照快速率恒 null 已修）
- `kpi_calc.py` +172：fef31fcb 新增 `_eval_loop_selection_stmt` 并三处替换选路；00d96192 回填韧性（退避重试+failed_detail 明细+skip_existing 补差贯通）；c811b2fd `_resolve_op_pv_ranges` 改从 bundle CONFIG 信号读 op_low/op_high（缺失回退 (0,100)）

**fef31fcb 参评口径语义**（供 P0-03/P1-02 引用）：评估写入侧——全量扫描（loop_ids=None 的小时 Beat 与全量回填）只选 `is_active=True AND status='READY' AND include_in_evaluation=True` 的参评回路（显式指定 loop_ids 的手动/精准重算不滤）；读方同批过滤参评并前端显示"—"。落点：`kpi_calc.py:232-248` + :1047/:3447/:4104 调用；读侧 `services/{performance,monitor,dashboard,workbench_diagnosis,workbench_loop_latest,loop,loop_batch,agg_cache}.py`。
