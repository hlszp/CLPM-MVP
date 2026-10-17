# P0-01 台账核对报告·批次4：CFG/PERF/TEST/CODE/DOC + HIST/X

- 核对子智能体：独立只读代理（未参与执行包编写）
- 开工/收工 HEAD：`2c817248fc1d55e85dfd413ecfd9b4f01b9efbad`（一致，全程只读未改任何文件）
- 日期：2026-10-10（P0 执行期）
- 判定统计：✅确认存在 29 ｜ ⚠️漂移 0 ｜ ❌已修复/不可复现 0 ｜ ❓无法核验 0
- 条目数勘误：任务书写"合计 28 条"，实际 §6(9)+§7(11)+§8(9)=**29 条**，已全量核对；P0 汇总以四批加总 **87 条**计（23+14+21+29，与 06 号文"87条台账（78 有效+3 历史+6 排除）"一致）。（非作者复核A 2026-10-10 勘误修订：原注"以 88 条计"有误）
- 行号/路径微漂（事实无漂移）：CFG-09（series_context.py 在 app/contracts/ 非 services/）、PERF-02（1372/1484→调用点 1539/1800）、TEST-01（ci.yml 59/164→61-66）、TEST-02（recall 测试在 tests/compliance/ 子目录）

## §6 工程参数治理（CFG-01～09）

- **CFG-01 ｜ ✅确认存在 ｜** 前端 `frontend/apps/web-antd/src/views/metric/algorithm-params.vue:177-186` 本地清单 accuracy_rate 仍只列旧 key `e_max_percentile`；后端 `backend/app/services/algorithm_config.py:150-156`（默认值）与 `:276-283`（PARAM_META）已改注册新 key `e_max_tolerance_ratio`；且 `algorithm-params.vue:223-234` 对有本地清单的指标仅 `local.map()` 合并远端同 key 元数据，远端新增参数键不进列。**注意：后端注释标 "v2.2（2026-10-10 稳定回路误判整改）"，即该漂移被近期整改扩大，前端未同步。**

- **CFG-02 ｜ ✅确认存在 ｜** `views/diagnosis/config.vue:966-975` 表单可编辑 algorithmType/calcMethod/params；`endpoints/configs.py:352-367` `_apply_diagnosis_update` 可存这些字段；`services/diagnosis_orchestrator.py:661-673` `_run_operators` 只遍历 OPERATOR_REGISTRY+disabled_diag_codes，全文件 0 处引用 calc_method/DiagnosisConfig；`main.py:1166-1170` 旧 diagnosis router 注册被注释（仅 diagnosis_v2 条件注册于 :1175）。

- **CFG-03 ｜ ✅确认存在 ｜** `endpoints/algorithm_config.py:272-275` PUT 提交后仅在当前 API 进程 `apply_runtime`；`tasks/celery_app.py:194-201` Worker 启动预载 `preload_algorithm_params`；`services/algorithm_config.py` 全文无 publish/subscribe/notify，无版本通知与逐任务检查。

- **CFG-04 ｜ ✅确认存在 ｜** `services/algorithm_config.py:462-466` Layer 3 `metric_thresholds`（metric_config.threshold，不分控制类型）最后 update，遮盖四类型；`services/performance.py:188` threshold 原样赋值 raw dict 无结构校验，`:225` 提交后仅 `_invalidate_metric_config_cache()`（:117-124 只删 Redis 旧 key）；`endpoints/algorithm_config.py:174-200` 重置仅清 Layer 2，不触碰 Layer 3。

- **CFG-05 ｜ ✅确认存在 ｜** `services/algorithm_config.py:403-429` `validate_metric_params` 有 min/max 与 bool/数值类型检查，但无 `isfinite`（NaN 两比较均 False 可通过）、无 int 检查、无组合约束；`endpoints/algorithm_config.py:233/247` `existing.version += 1` 无 WHERE version= 乐观锁；`:249` 审计 `before_value=None`。

- **CFG-06 ｜ ✅确认存在 ｜** `services/loop_fitness.py:272-276` 计数器按固定 2%/10% 阈值逐点计数；`:440-455` compute_fitness 用 `band_scale=2.0/th`、`dev_scale=10.0/th` 对固定阈值计数做线性比例缩放，不按配置阈值重新逐点判定。

- **CFG-07 ｜ ✅确认存在 ｜** `tasks/kpi_calc.py:2308-2330` 血缘 dict 仅含版本号字符串，不绑定完整 merged 参数快照（全文无 get_algorithm_params 调用）；`services/diagnosis_orchestrator.py:1072-1074` `threshold_version="default"/f"override:{len(...)}"` 只记数量不记值；`services/dcs_config.py:1003-1012` upsert_pid_structure 对已有模板原地覆盖字段更新，无版本化。

- **CFG-08 ｜ ✅确认存在 ｜** `metric_calculator/ideal_settling_time.py:28-34` DEFAULT_IDEAL_SETTLING 与 `:38` ALPHA_BY_TYPE 为模块级硬编码；`diagnosis_operators/tuning.py:27-34` `_DEFAULT_EXPECTED_TAU_SECONDS` 硬编码；`services/tuning.py:1482+` 风险阈值 50%/20%/θ τ 0.5 硬编码。

- **CFG-09 ｜ ✅确认存在（路径微漂）｜** `services/data_source/logical_wide_builder.py:466-469` dataset_ref=sha1(loop_id+role:point_ids+改绑边界时刻)，不含数值/质量内容；`services/tuning_identification/pipeline.py:679-686` `_compute_data_hash` 仅对数组取哈希、原数组不保存（结果只存 data_hash，:434/:602/:1191）；`contracts/series_context.py:172` dataset_ref 字段存在——实际路径为 `backend/app/contracts/series_context.py`（非 services/ 子目录）。

## §7 性能、测试与代码维护（PERF-01～07、TEST-01～02、CODE-01、DOC-01）

- **PERF-01 ｜ ✅确认存在 ｜** `tasks/arma.py:93` `np.correlate(mode="full")` 后仅取 `autocorr[1:order+1]`；`:274` retry_orders={ar_order,4,6,10} 多阶重试逐次重算全相关；`metric_calculator/stiction.py:273` full correlate 后仅取 `[n-1:n+max_lag]`；`diagnosis_orchestrator.py:1021` 波形快照（`:514/:538` LTTB ≤2000）在算子执行之后构建。

- **PERF-02 ｜ ✅确认存在（行号漂移）｜** `tasks/kpi_calc.py:1539` `_load_fitness_sys_configs` 与 `:1800` `_load_kpi_min_observed_ratio` 均在逐回路函数 `_calculate_loop_kpi`（:1494）内逐回路查 sys_config；`tasks/diagnosis_schedule.py:44` 批大小 50、`:149` 逐回路串行 `_density_ok`（:88 每回路新开 session）；`tasks/diagnosis_v2.py:85-101` 批内 for 串行逐回路、:101 每回路新 session。

- **PERF-03 ｜ ✅确认存在 ｜** `services/workbench_precalc.py:375-400` 三窗口×全 scope×(当前+上一窗) 重复范围聚合；`services/workbench_assessment.py:489-495/503-510` 含子树/子节点分支直接 `.in_()` 查询无最新去重（对照 `workbench_overview.py:396-412` `_query_scope_rows` 有 G15 DISTINCT ON 去重）；`services/agg_cache.py:49-51` Redis 命中重置本地 TTL、`:42-62` 冷请求无合并、`:69-87` invalidate_agg 仅清进程层。

- **PERF-04 ｜ ✅确认存在 ｜** `services/workbench_assessment.py:514/531/538/545` 各聚合块 `except Exception: logger.warning` 后保空/0（:533 注释"ranking 失败时用空 plants 兜底"），无来源不可用显式标记；`views/cockpit/performance.vue:49-65` loadAssessment 无请求序号守卫，旧慢响应可覆盖新筛选结果。

- **PERF-05 ｜ ✅确认存在 ｜** `views/cockpit/loops.vue:54-57` PAGE_SIZE/FETCH_BATCH=30 懒加载首批 30；`:123-124/:148` 筛选/劣化排序激活自动补全全量（1000 回路≈34 页）；`:402` 每条实时消息 `allLoops.value.find(...)` O(n) 线性扫描。

- **PERF-06 ｜ ✅确认存在 ｜** `views/cockpit/wb-comps/EvalTrendChart.vue:42-43` `yMin=78/yMax=92`；`:54` sy() 夹值；`:50-51` sx() 按索引等距分布非真实时间轴。

- **PERF-07 ｜ ✅确认存在 ｜** `backend/scripts/kpi_capacity_benchmark.py:30` DEFAULT_PAGE_SIZE=100 且 `:65` 发送 `size` 参数，而 `endpoints/loops.py:121-122` API 只认 `pageSize`(le=100,默认20)→脚本首屏 20<100 即断，仅取 20 回路；脚本 `:83-92/:164-170` `_replay_windows` 少量 ID 多窗重放计为 loop-evals；`tasks/kpi_calc.py:3078-3092` custom 快照写 kpi_snapshot_custom 不更新 loop_confidence_latest，custom 批尾(:1262-1277)无节点级联（唯一 `.delay()` 在标准路径 :1057）；`:1055-1062` 标准小时父任务对节点聚合 fire-and-forget 派发不等完成；`backend/scripts/benchmark_kpi_optimization.py:361-391` 计时窗仅包 `_calculate_loop_kpi` 调用，不含落库/commit。

- **TEST-01 ｜ ✅确认存在（行号漂移）｜** `.github/workflows/ci.yml:61-66` playwright install 与 `pnpm -F @vben/web-antd run e2e` 均 `continue-on-error: true`；CI 仅调 apps/web-antd 套件，根业务套件 `/e2e/tests/*.spec.ts` 未被调用；`backend/pyproject.toml:81` `addopts="-m 'not integration'"`；两套业务相关 playwright 配置= `/e2e/playwright.config.ts` + `/frontend/apps/web-antd/playwright.config.ts`。

- **TEST-02 ｜ ✅确认存在（路径微漂）｜** `backend/tests/compliance/test_b4_fast_rate.py:51+` TestB4FastRateFormula 用合成依赖测产品公式边界；`tests/compliance/test_diagnosis_recall.py:50` import 自 `app.tasks.diagnosis_engine`（旧引擎，已退役）；`tests/compliance/g2_helpers.py:52` validity 全 `[True]*n`（全有效 A 级）——recall 测试实际位于 tests/compliance/ 子目录。

- **CODE-01 ｜ ✅确认存在 ｜** `backend/app/tasks/kpi_calc.py` 4651 行、`services/tuning.py` 2033 行、`services/diagnosis_orchestrator.py` 1093 行；kpi_calc.py 内任务装饰器/db.add/db.commit 等 61 处，横跨调度/计算/持久化。

- **DOC-01 ｜ ✅确认存在 ｜** `docs/MVP设计/README.md:12/18` 模块索引仍列"工作台 v2.0 单屏 5 Tab `/workbench`"；`docs/MVP设计/00-信息架构.md:10-11/55/62-64` 仍为驾驶舱单页+运维工作台 5 Tab 口径、无 10-05 整合记载；现行代码 `views/cockpit/` 已是 6 页签、`router/routes/modules/clpm.ts:13-14` `/workbench` redirect→`/cockpit`、`views/loop/workbench360/` 存在。

## §8 历史证据与已撤销判断（HIST-01～03、X-01～06）

- **HIST-01 ｜ ✅确认存在 ｜** `docs/过程文档/生产只读检查报告-由zcode-2026-09-28.md:214-219`：C1 节记载 961 回路 15min、并发整改 27 回路 8.611→5.270s，与台账引述一致。
- **HIST-02 ｜ ✅确认存在 ｜** `docs/过程文档/perf-report-MW-P5-04.md:3-7/15-18/24`：2026-08-10、1000 回路/10000 关注项，summary p95 53.1ms ✅、attention 594.7/610.5/648.4ms ❌（未达 500ms）。
- **HIST-03 ｜ ✅确认存在 ｜** `docs/过程文档/kpi-perf-optimization-summary-2026-07-15.md:277-281`：1000 回路小时任务 ≤600s 状态"待容量测试验证"。
- **X-01 ｜ ✅撤销理由成立 ｜** 响应类别链真实存在：`services/data_planner.py:1030-1038` loop.control_type→response_category 进 DataBlock；`services/preprocessing/pipeline.py:269` 派生 bundle control_type=response_category；`pipeline.py:81` 按 control_type 读 get_threshold（参数按类别读取，非全部回退 STABLE）。
- **X-02 ｜ ✅撤销理由成立 ｜** `frontend/apps/web-antd/src/views/diagnosis/tasks.vue:30-34` canTrigger 四角色，发起按钮 :65 v-if=canTrigger。
- **X-03 ｜ ✅撤销理由成立 ｜** 诊断启停可消费：`diagnosis_orchestrator.py:670-672`；全局阈值快照：`:1011` effective_thresholds 贯穿执行；fitness 阈值留存：`loop_fitness.py:388`；confidence 广播：`confidence_evaluator.py:568/647` Redis pub/sub THRESHOLD_CHANNEL。
- **X-04 ｜ ✅撤销理由成立 ｜** `backend/app/main.py:1166-1170` 旧 diagnosis router 注册全部注释，仅 `:1175` diagnosis_v2 条件注册。
- **X-05 ｜ ✅撤销理由成立 ｜** 关注队列/三性/双实体/六页签/360/30 卡懒加载落点全部实存。
- **X-06 ｜ ✅成立（方法论边界声明）｜** LTTB 展示降采样（diagnosis_orchestrator.py:538）、合成样本测试（g2_helpers.py 构造 bundle）确存在；该条为证据层级边界声明，无相悖代码事实。
- 历史源文件四件 + 国标 PDF 全部存在（docs/过程文档/ 4 个 .md 与 docs/预研文档/GBT44693.2-2024*.pdf）。

## 补充观察（供 P2/P5/P7 任务卡参考，不扩大 P0 范围）

- CFG-01 的 key 漂移被基线后整改加深（backend 2026-10-10 v2.2 注释废弃 e_max_percentile），P2-01 执行时前端同步是硬需求。
- 台账 PERF-02/TEST-01 行号已随代码演进偏移，建议按符号而非行号消费。
- 台账两处路径简写与实际有偏差：series_context.py 在 app/contracts/；test_diagnosis_recall.py 在 tests/compliance/。
- 容量脚本 `size` 参数名错配后果比"不符"更重：API 忽略未知参数按默认 20 返回，脚本 20<100 即断循环，实际只装载 20 回路，容量数字系统性偏小。
- PERF-07 中"标准小时父结束"的代码事实是节点聚合 fire-and-forget 派发（kpi_calc.py:1057），小时容量判读须把节点任务纳入屏障口径。
- 任务书写"合计 28 条"与台账实际 29 条不符，已按 29 条全量核对。
