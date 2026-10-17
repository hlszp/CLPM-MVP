# P0-R2 漂移复核报告·批次3：DIA-01/04 + TUN-01/02/04/05/06

- 重验子智能体：独立只读代理；区间 `2c817248..baa3f50d`
- 开工/收工 HEAD：`baa3f50d8d883356064d5774c07122ebb5f360eb`（一致，全程只读）
- 判定统计：✅仍存在 6 ｜ ⚠️漂移 1（TUN-04）｜ ❌已修复 0

## 逐条判定

**DIA-01 ｜ ✅仍存在（两文件本轮零变更）** ｜ `diagnosis_orchestrator.py:479-486` 仍读顶层 `result.get("effectiveThreshold")`；`diagnosis_threshold.py:377-384` 顶层仍只返回 6 键、effectiveThreshold 仅在 recommendation 项内（:372）→ 恒 None 恒回退 `{}`，断链原样。

**DIA-04 ｜ ✅仍存在（算子内核零变更；26ee4ca5 是展示层+复核标签）** ｜ `diagnosis_operators/` 目录本轮零提交。`classification.py:361` NO_SYMPTOM 兜底、`stiction.py:116-118`/`disturbance.py:122-124` 吞异常返阴性、orchestrator:667-699 三重 skip 均在。**26ee4ca5 语义**：后端仅 `endpoints/diagnosis_v2.py:77-81` `_CATEGORY_LABELS` 增 `NORMAL:"正常"/OTHER:"其他"`（人工复核结论域，primary_category 不取这两值）；"未见异常明确化"是前端显示（主分类空值渲染"正常/不适用/无"，FAILED 留"—"），不改后端结论语义。附带观察：all-skip 的 NO_SYMPTOM 前端现在更显式显示"正常"，DIA-04 的后果展示面反而更"确信"，混淆本身未解。

**TUN-01 ｜ ✅仍存在** ｜ `loop.py` 本轮唯一变更是 `update_loop` 末尾 +10 行聚合缓存失效（:1668-1677，fef31fcb），PID 分支仍 :1332-1346（DCS 原值直通）；批量路径 :1228-1232 同。前端 use-tuning-workbench.ts 零 diff（:252 原样）。pid_conversion.py 未动。

**TUN-02 ｜ ✅仍存在（落点 +4）** ｜ `tuning.py` 变更仅在 :838-856（identify_model_from_history 新入参接线）。STEP 拉 get_waveform 现 :1040；MV 阶跃零点+负基线原样（:1087-1091）。`tuning_algorithms.py` 零 diff：a1 梯形积分仍从 ts[0]（含负基线）起算、theta 仍从窗口起点度量（:230/:241）；IPDT 同式（:462）。K 口径不统一原样。STEP 的 IPDT 分支与 identify_ipdt 均为基线已存在，非本轮新增。

**TUN-04 ｜ ⚠️漂移（核心"最后段仅计数"仍在，管线大幅改形 + 出现一个相关新弱点）** ｜
- 分割仍在且形态未变：`pipeline.py:753-767` 仍时序 60/20/20（短数据退化 70/30、n_test=0）；全文件证实 n_test 仅出现于计算清零与元数据传递，**无 u_test/y_test 切片、无 test 集残差评估**——原判定成立，落点从 :349 漂移到 :753-767。
- **multi-window（v1.4，:585-630）没有引入独立验证段**：多窗独立辨识+按（可信度档，fitting_score，residual_test_passed）择优，每窗内部仍是同一套 train/val，每窗最后 20% 依旧仅计数。跨窗择优是稳健性机制，不是 held-out 终验。
- **val 稳态防护（v1.2，:919-935）是新引入的相关弱点**：`val_stationary = ptp(y_val) < 1.5`（归一化域）时 `r2_eval = r2_train` 回退、可信度封顶 C、reason_codes 标 VAL_STATIONARY（evidence.r2_val 保留真值）——承认 val 段死水后不修分割而是改用训练集 R² 参与择优/拟合分（fitting_score=r2_eval×100，:1046），val 独立性在该场景被显式放弃，以封顶+标注作补偿。IPDT 分支同构（:1555-1585）。
- STEP 路径仍无独立终验（tuning.py:1100-1121）；`model_type != "FOPDT"` fail-closed 门禁原样（:1373-1398）。
- 任务卡补充：**IPDT 进默认候选后，Occam 择优选出的 best_model 是 IPDT 的概率上升，而 IPDT 仍被整定门禁拦死——"辨识成功但不可整定"命中面变大**，P3-03 需注意。

**TUN-05 ｜ ✅仍存在（落点 +4）** ｜ `params = algorithm_params or {}` 现 :1412；schemas/tuning.py 零 diff；tune_imc 静默兜底仍在（:507-511）；TUNING_METHODS_INFO 仍未成 API 契约。

**TUN-06 ｜ ✅仍存在（落点 +4）** ｜ 前端 :579-598 零 diff，载荷仍缺六字段；后端 create_tuning_task 现 :1683-1738，TuningRecord 构造（:1712-1738）仍不写 time_window/process_model_version_id。

## 附加语义摘要（5920fe14 + a63aa946 辨识变更，供 P2-03/P3-03 任务卡）

两提交只动 `tuning_identification/pipeline.py`、`types.py`、调用方 `tuning.py` 与测试/golden，**无前端、无 schema、无 DB 变更**：
- **新增入参**（identify_from_history :542-551）：`mode`（MODE 时序切分依据，G24 兼容=不作门控仅降级）、`op_limits`（饱和检测，生产链传 (0,100)）、`_internal_call`（递归防环）。调用方 tuning.py:838-856 已接线。
- **新增模式**：自动选段 `_auto_select_segment`（:240）；动态感知子窗 `_scan_dynamic_windows`+`_auto_select_window`（v1.3 :310/:373）；multi-window 多窗择优（v1.4 :585-630）；IPDT 进默认候选集 `["FOPDT","SOPDT","IPDT"]`（tuning.py:842）；去趋势 `_detrend_signals`（:404，**已实现但总开关 `_DETREND_ENABLED = False`（:83，2026-10-10 用户裁决默认关闭**）；低通 `_lowpass_signals`（:469，Butterworth-2+filtfilt，PV 谱主峰×20 截止，差异 <10% 不替换）。
- **新增输出/标注**：`algorithm_version` TUNE_IDENT_v1.0→**v1.4**（types.py:248-249）；reason 前缀 auto-segment/auto-window/detrend/lowpass/multi-window；reason_codes 新增 `VAL_STATIONARY`；关键常数 `_VAL_STATIONARY_MIN_PTP=1.5`、`_AUTOWIN_MIN_POINTS=600`、`_AUTOWIN_MIN_FULL_PTP=3.0`。测试 +8 项、golden/protected manifest 重固化。
- **对 P2-03**：辨识 reason 更丰富但仍不进保存链（TUN-06 原样）。**对 P3-03**：v1.2 的 r2_eval 回退使 fitting_score 在 val 死水窗可为训练集口径（评估"拟合度"语义须读 reason_codes）；IPDT 默认候选扩大"不可整定"命中面。
