# P0-01 台账核对报告·批次3：DIA-01~10 + TUN-01~06 + ENG-01~05

- 核对子智能体：独立只读代理（未参与执行包编写）
- 开工/收工 HEAD：`2c817248fc1d55e85dfd413ecfd9b4f01b9efbad`（一致，全程只读）
- 日期：2026-10-10（P0 执行期）
- 判定统计：✅确认存在 21 ｜ ⚠️漂移 0 ｜ ❌已修复/不可复现 0 ｜ ❓无法核验 0
- hotfix 影响面核实：`fb615f32` 仅改 `loop.py list_loops`（controlMode 筛选，约 541-587 行，+35 行），`708ecc3e` 仅改前端 `LoopSpine.vue`。除 TUN-01 落点行号整体 +35 外，其余 20 条落点文件均未被触碰。

## §4 诊断（DIA-01～DIA-10）

- **DIA-01 ｜ ✅确认存在（活链）** ｜ `backend/app/services/diagnosis_orchestrator.py:479-486`：`_effective_thresholds` 读 `result.get("effectiveThreshold")`（顶层）；`backend/app/services/diagnosis_threshold.py:377-384`：`recommend_for_loop` 返回顶层仅 `loopId/tagName/loopType/plantId/plantName/recommendations`，effectiveThreshold 只在每个 recommendation 项内（:372）→ 顶层取值恒 None → 恒回退 `{}` 用算子默认，阈值覆盖链断裂；try/except 兜底使诊断正常跑完（"启停正常"）。

- **DIA-02 ｜ ✅确认存在（活链）** ｜ `backend/app/services/data_source/logical_wide_builder.py:336-377`：gap/held_too_long 槽位保留 Good 质量码与值，仅标 `(False, UNKNOWN_REASON_GAP/HELD_TOO_LONG)` 进 slot_status；`backend/app/services/preprocessing/data_quality_assessor.py:160-166`：validity 只由质量码+异常值推导（全文件无 slot_status/held/series_context 引用）；`backend/app/services/diagnosis_orchestrator.py:857-879`：门禁 usable = row_valid（仅质量码 BAD/pv None 判 False）或 len(aligned)，unknown 角色槽计入可用点。observed_mask（:932-953，R3）只透传算子，不进门禁与 assessor。

- **DIA-03 ｜ ✅确认存在（活链）** ｜ `backend/app/services/diagnosis_orchestrator.py:592-648`（609 在 `_scoped_operator_input` 内）：point 同轴按公共掩码切片后，meta `sample_interval` 原样透传（:643）不随压缩重算，`observed_mask` 在 meta 复制中保留但**未按 idx 切片**（:642）；`diagnosis_operators/oscillation.py:69-70`：`fs = 1.0 / sample_interval` 按原间隔算频谱；`diagnosis_operators/sensor.py:387-396`：`len(_m) == len(pv)` 不等时静默跳过掩码（except/pass）→ scoped 压缩后逐点掩码失配退化。

- **DIA-04 ｜ ✅确认存在（活链）** ｜ `backend/app/services/diagnosis_orchestrator.py:667-674`：fast 组过滤、白名单细选、停用码三重 skip，可全部算子 skip；:697-699 `except Exception` 记 error 不中断；`backend/app/services/diagnosis_operators/classification.py:361-366`：`if not candidates:` → 门禁通过时兜底 NO_SYMPTOM（门禁不过走 :224-240 DATA_INSUFFICIENT）；`diagnosis_operators/stiction.py:116-118`、`diagnosis_operators/disturbance.py:122-124`：内核 except 后返回 `_empty_*_result()`（detected=False），异常吞成阴性。

- **DIA-05 ｜ ✅确认存在（活链）** ｜ `backend/app/services/diagnosis_operators/stiction.py:80-95`：`_ellipse_kernel` 只收 pv/op/sample_interval，无量程参数，调 `assess_stiction_features` 也不传 pv_range/op_range；`backend/app/services/metric_calculator/stiction.py:432-439`（常量 :51-52）：缺省 `DEFAULT_PV_RANGE = 100.0` / `DEFAULT_OP_RANGE = 100.0` → 工程值输入按两轴 100 归一化，轴比随量纲失真。

- **DIA-06 ｜ ✅确认存在（活链）** ｜ `backend/app/services/diagnosis_operators/disturbance.py:59`：`bias = pv_arr - sp_arr`，CUSUM 直接对 PV-SP 偏差，未剔除/检测 SP 阶跃；:148 算子 meta `confidence_basis` 自称"与 SP 变更无关"；`backend/app/services/diagnosis_operators/classification.py:209`：`auto_rate = float(kpi_context.get("auto_rate_avg") or 0.0)`——None 折叠为 0 无来源区分；:312 basis 亦宣称"且与 SP 变更无关"；:347：`utilization_hit = 0.0 < auto_rate < UTILIZATION_DETECT_RATE`——0 不进低自控条件。

- **DIA-07 ｜ ✅确认存在（活链）** ｜ `backend/app/services/diagnosis_operators/fusion.py:16-39`：D-S 融合为 odds 乘积 `Πc/(Πc+Π(1-c))`，独立性假设；`diagnosis_operators/stiction.py:247-254`：Choudhury 内部调 `_ellipse_kernel`，`fitting_score` 以 0.2 权重入 confidence，与独立注册的 `stiction_ellipse`（:351-360）同族（stiction/VALVE_STICTION，:417-426）→ 同源 ellipse 证据被交叉融合重复增强；`classification.py:500-516`：`CATEGORY_CONFIDENCE_BASIS` 为静态规则档位（0.60/0.75/0.90 等），非现场校准概率。

- **DIA-08 ｜ ✅确认存在（活链）** ｜ `backend/app/services/diagnosis_operators/tuning.py:27-34`：`_DEFAULT_EXPECTED_TAU_SECONDS` 固定类型默认表；:246-258：只取 `step_indices[0]`，响应窗 `response_end = min(step_idx+1+min_len//2, min_len)` **未在下一个 SP 阶跃处截断**；:282-291：拟合失败走 63.2% 近似、reach_idx 空时 τ=window_seconds，叠加 :299-306 `fit_degenerate`（到达率<63.2% → detected=False）→ 响应不足的真迟缓不报警。

- **DIA-09 ｜ ✅确认存在（活链）** ｜ `backend/app/tasks/diagnosis_schedule.py:182`：`task_id = str(uuid4())` 自生成，:186-195 `await create_task(...)` 返回值被丢弃，:196-204 delay 用自生成 ID；`backend/app/services/alert_rule_engine/dispatcher.py:414-424`：EVENT 路径同样自生成 uuid4 + 忽略 create_task 返回；`backend/app/services/task_tracker.py:345`（create_task 内部另生成 uuid4）`:384 return task_id`、:414 `set_celery_task_ids`——登记单 ID（uuid_A）与 Celery 任务收到并回写进度的 ID（uuid_B）不同，登记/执行/结果身份分裂；create_task 为直接 hset 无 CAS（:389-390）。

- **DIA-10 ｜ ✅确认存在（活链）** ｜ `backend/app/tasks/diagnosis_v2.py:29-38`：`autoretry_for=(Exception,) max_retries=3` 整批自动重试；:86-123：逐回路独立执行/落库（失败也 `_record_failed_run` 提交），重试时已提交回路重复执行重复建 run；`backend/app/models/diagnosis_run.py:74-106`：`__table_args__` 只有普通索引，无 (loop_id, trigger_type, 窗口) 唯一约束——queued/running 原子占位无载体；`dispatcher.py:380-401`：EVENT 防抖仅"事后查最近落库 DiagnosisRun.created_at"，占位未闭合。

## §5 整定、实施与验证（TUN-01～TUN-06、ENG-01～ENG-05）

- **TUN-01 ｜ ✅确认存在（活链；fb615f32 不影响本条）** ｜ `backend/app/services/loop.py:1311-1365`（get_loop_detail runtimeParams，PID 分支在当前 :1330-1346；基线 1305 因 fb615f32 +35 漂移）：DCS 原始 PID tag 值直接 `runtime_params["pidP/I/D"] = float(...)` 透出，无 PB/分钟转换（批量路径 :1202-1237 同）；fb615f32 只改 `list_loops` controlMode 筛选（:541-587），未触碰 runtimeParams 逻辑。`frontend/.../views/tuning/composables/use-tuning-workbench.ts:246-252`：`state.currentPid = { kp: rp.pidP, ti: rp.pidI, td: rp.pidD ?? 0 }` 把 DCS 原值直接当标准 kp/ti/td。`backend/app/services/pid_conversion.py:80-105`：`to_standard_pid` 转换器存在（PB→Kp=100/PB、分钟×60）可复用但两处消费链均未接入。

- **TUN-02 ｜ ✅确认存在（活链）** ｜ `backend/app/services/tuning.py:1036-1038`：STEP 辨识拉 `get_waveform`（工程值波形，max_points=10000）；:1085-1091：MV 阶跃为时间零点、**基线保留为负时间**传入算法；`backend/app/services/tuning_algorithms.py:230-248`（`_fopdt_area_method`）：a1 梯形积分从 ts[0]（负基线起点）开始、`theta = ts[response_start_idx] - ts[0]` 从窗口起点度量，K=delta_pv/mv_step 工程值口径（与历史辨识归一化 OP 输入的 K 不统一）；IPDT 同构（:461-462 theta 同式）。

- **TUN-03 ｜ ✅确认存在（活链）** ｜ `backend/app/services/tuning_algorithms.py:752-917`：`_simulate_pid_response` 仅 `is_sopdt` 有专属方程，:901-910 else 分支一律 `_deriv_fopdt` RK4——model_type=="IPDT" 也走 FOPDT 一阶惯性，无 G(s)=K/s 积分器仿真；`disturbance_type`（:759 入参）在仿真循环（:863-917）内**零消费**，无扰动注入。

- **TUN-04 ｜ ✅确认存在（活链）** ｜ `backend/app/services/tuning_identification/pipeline.py:349-359`：60/20/20 时序分割，`n_test` 仅作为计数传入 CandidateModel 元数据（:433/595/1184），全文件无 u_test/y_test 切片与 test 集残差评估——最后段仅计数；STEP 路径无独立 test 终验；`backend/app/services/tuning.py:1369-1388`：`model_type != "FOPDT"` 即 `ERR_MODEL_TYPE_NOT_TUNABLE`（fail-closed，注释明言上游规格给出折算口径前不自造公式）——最佳 SOPDT 不能整定是显式安全限制。

- **TUN-05 ｜ ✅确认存在（活链）** ｜ `backend/app/schemas/tuning.py:224-232`：`TuneRequest.algorithmParams: dict[str, Any] | None` 任意 dict；`backend/app/services/tuning.py:1408-1427`：`params = algorithm_params or {}`，各算法 `params.get(...)` 取默认、未知键静默忽略；`backend/app/services/tuning_algorithms.py:507-511`（tune_imc）：`lam<=0 → 0.1`、`K==0 → K=1.0` 非正参数静默兜底；:1006-1060（`TUNING_METHODS_INFO`）：目录声明 min/max/options 约束但未成为 API 校验契约。

- **TUN-06 ｜ ✅确认存在（活链）** ｜ `frontend/.../use-tuning-workbench.ts:579-598`：saveTuningTaskApi 载荷不含 sourceRecordId/algorithmParams/timeWindowStart/End/excitationScore/residualTestPassed（来源、算法参数、版本、时间窗丢失）；`backend/app/schemas/tuning.py:423-446`：`CreateTuningTaskRequest` 直接接受客户端 recommendedPid/simulationResult/fittingScore/status；`backend/app/services/tuning.py:1679-1734`：create_tuning_task 将客户端结果原样落库，TuningRecord 构造（:1708-1734）不写 time_window_start/end 与 process_model_version_id；`backend/app/models/tuning.py:72-78`：process_model_version_id 字段存在（V62-P3-006，注释"新辨识记录应携带 version_id"）但同步保存链不写。

- **ENG-01 ｜ ✅确认存在（活链）** ｜ `frontend/.../views/loop/manage/LoopEditDrawer.vue:835-862`（`doSaveTagMapping`）：857-858 `catch (error) { console.error(...) }` 吞异常不 re-throw、不提示；:191-199（向导下一步）：`try { await doSaveTagMapping(); } catch { return; }` 因内部永不 reject 而恒推进 `wizardCurrent = 2`——保存失败向导照常推进。

- **ENG-02 ｜ ✅确认存在（活链）** ｜ `frontend/.../views/tuning/verification.vue:95-116`（derivePointTime）：按 loop 拉 TUNING 工单取最近 `submittedAt`（注释自述"无 tuningRecordId 回链时退化取最近提交的 TUNING 工单"），不按 tuningRecordId 精确回链；`backend/app/api/v1/endpoints/handling.py:1281`（submit_order）：`order.submitted_at = _utcnow_naive()`——提交验证时刻非实际实施时刻；:346-367（`_pull_kpi_windows`）：kpi_after 以 submitted_at 为界（[submitted_at, +24h]）。

- **ENG-03 ｜ ✅确认存在（活链）** ｜ `frontend/.../views/handling/components/order-detail-drawer.vue:172-174`：`pidProvided = p != null || i != null || d != null`（任一项非空即过），:250-261 handleSubmit 仅此守卫（:604-622 三个 InputNumber 无必填校验）；`backend/app/api/v1/endpoints/handling.py:384-391`（`_validate_action_detail`）：TUNING 仅要求 pidAfter 为非空 dict，无 p/i/d 结构/数值/单位契约。

- **ENG-04 ｜ ✅确认存在（活链）** ｜ `order-detail-drawer.vue:709-712`：复诊 ID 为手填文本框；`backend/app/api/v1/endpoints/handling.py:1307-1316`：verifyRunId 仅校验 `DiagnosisRun.id` 存在，不校验 `run.loop_id == order.loop_id`；:1318-1326：verifyNote 无必填、verifyRunId 可 None，选 EFFECTIVE 即 CLOSED——"无数据/理由仍可人工闭环"。

- **ENG-05 ｜ ✅确认存在（活链）** ｜ `backend/app/api/v1/endpoints/handling.py:1326-1331`：验证 INEFFECTIVE → order REOPENED 并 `_writeback_tuning_record(..., "ROLLED_BACK")`（注释明示 2026-10-04 用户裁决启用）——ROLLED_BACK 由验证裁决写入，不代表 DCS 侧人工回退已实际发生。

## 补充观察（供 P2/P3 任务卡参考，不扩大 P0 范围）

- TUN-01 是唯一行号实质漂移条目：fb615f32 使 `loop.py` 基线 1305 → 当前 1330-1346，行为不变。
- 708ecc3e（LoopSpine.vue）不在任何受核条目落点内，零影响。
- DIA-02 中 R3 observed_mask（orchestrator:932-953）只透传给算子，门禁 usable 与 assessor validity 两处均不消费，正是台账主张的"未统一"。
- DIA-05 的 0~100 缺省是 S3/G19 修复后与 KPI 侧有意对齐的口径（两链同判据），但诊断侧仍未显式传量程，"单位改变影响轴比"的风险路径仍在。
- TUN-06 的版本/窗口丢失不只在前端：服务端 create_tuning_task 的 TuningRecord 构造同样不写 process_model_version_id 与 time_window 字段。
- DIA-09 落点 task_tracker.py:414（set_celery_task_ids）作为补救钩子存在，且 schedule/event 两条链**均已**在 delay 后调用它（diagnosis_schedule.py:205-207、dispatcher.py:437-439）——但传入的是自生成 task_id（uuid_B）而非 create_task 登记并返回的 ID（uuid_A），即关联被写到了错误的任务 hash 上：**钩子已挂但挂错目标**，修复方向是消费 create_task 返回值（或把自生成 ID 传入 create_task），而非补调用。（非作者复核A 2026-10-10 勘误修订）
- ENG-02 的 verification.vue 已含 2026-09-29 时区修复与反查失败显式提示，但"退化取最近 TUNING 工单"的宽口径路径仍在注释与实现中保留。
