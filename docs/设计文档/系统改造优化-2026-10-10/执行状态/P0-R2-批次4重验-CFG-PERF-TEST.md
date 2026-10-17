# P0-R2 漂移复核报告·批次4：CFG/PERF/TEST + 引导SQL/golden

- 重验子智能体：独立只读代理；区间 `2c817248..baa3f50d`
- 开工/收工 HEAD：`baa3f50d8d883356064d5774c07122ebb5f360eb`（一致，全程只读）
- 判定统计：✅仍存在 5 ｜ ❌已修复 1（CFG-06）｜ ⚠️漂移 1（PERF-03）；另有 TEST-01 相邻（判定不变）与引导 SQL/golden 登记两项不计数——整合文档"26 条重验（含 TEST-01 相邻）"口径以本报告正文条目为准（R2 增量复核注记：统计行 25 与整合 26 的差异即在此）

## 逐条判定

**CFG-01 ｜ ✅仍存在** ｜ 后端 `algorithm_config.py` 本轮（c811b2fd）只新增 `saturation_epsilon`（effective_auto_rate 四类型+新指标 saturation_rate）与 PARAM_META/PARAM_CATEGORY 登记；e_max 部分现状：defaults `:161-165`（`e_max_tolerance_ratio: 0.02`）、PARAM_META `:286`、PARAM_CATEGORY `:396`（行号 +24 来自 saturation 插入）。前端 `algorithm-params.vue` 本轮 **0 变更**：`:180` 本地清单仍只列旧 key `e_max_percentile`，`:223-234` local.map 同 key 合并不变，远端新 key 仍不进列。新增 saturation_epsilon 走注册表生成不产生新漂移——漂移本体原样，P2-01 前端同步仍是硬需求。

**CFG-03 ｜ ✅仍存在** ｜ `endpoints/algorithm_config.py` 本轮 0 变更：PUT 后 `:275` 仍仅当前 API 进程 `apply_runtime`；service 全文 0 处 pub/sub；`tasks/celery_app.py` 0 变更，`:194-201` 启动预载原样。跨进程缺口原样（改参后 Worker 持旧参数直到重启），P2-02 仍有效。可复用样板：confidence_evaluator 的 THRESHOLD_CHANNEL pub/sub + `start_threshold_subscriber`（celery_app.py :205+）。

**CFG-04 ｜ ✅仍存在** ｜ `services/performance.py` 本轮 +155 行全落在参评口径/批量删除/Decimal 修复，未触碰 CFG-04 区域。三处子缺口原样：① `_rebuild_merged` `:490-492` Layer 3 metric_config.threshold（不区分控制类型）最后应用遮盖四类型；② `update_metric_config`（performance.py :150）raw dict 赋值无结构校验（~:188），提交后仅 `_invalidate_metric_config_cache()`（:226→:118-124 只删 Redis 旧 key）；③ 重置仅清 Layer 2（~:171-192）不触碰 Layer 3。

**CFG-05 ｜ ✅仍存在（ε 未引入新校验形态）** ｜ `validate_metric_params` 现 :427-453（+24 漂移）：仍 bool/数值+min/max，**无 isfinite**（NaN 两比较均 False 可通过）、无 int 检查、无组合约束。ε（min 0.0/max 10.0）走既有 min/max 表形态——NaN/inf 仍可经 ε 写入 JSONB。endpoint 侧：`:187/:234` version += 1 无乐观锁、`:255` 审计 before_value=None，三缺口全在。

**CFG-06 ｜ ❌已修复（c811b2fd）** ｜ `loop_fitness.py` `compute_time_ratio_counters`（:226）新增 `op_sat_band_pct`/`sp_pv_dev_pct` 参数，**按实际配置阈值逐点判定**（:285-293：band=clamp(pct,0,50)/100×span、dev=max(pct,0)/100×pv_span）；`compute_fitness` :448-456 传入 sys_config 读出的 `th_op_sat_band`/`th_dev_pct` 后直接使用计数结果；原 `band_scale=2.0/th`、`dev_scale=10.0/th` 线性缩放块已整段删除（0 残留，test_kpi_calc.py 按新口径更新）。附带口径变更：`fitness.op_saturated_band_pct` 默认 2.0→**0.0**（严格贴限位端点）、op_range 语义从归一化量程改回路 OP 限位。**台账 CFG-06 转"已修复"，P2-04 对应子项可销。**

**PERF-02 ｜ ✅仍存在（行号漂移）** ｜ `_load_fitness_sys_configs`（调用点 :1616）与 `_load_kpi_min_observed_ratio`（调用点 :1877）仍在逐回路函数 `_calculate_loop_kpi`（:1571）内逐回路查 sys_config（原 1539/1800）。诊断侧 `diagnosis_schedule.py`/`diagnosis_v2.py` 本轮 0 变更，批内 for 串行+每回路新 session 原样。

**PERF-03 ｜ ⚠️漂移（部分修复+新缺口）** ｜ `agg_cache.py` 本轮 +23 行（fef31fcb）：① Redis 命中重置本地 TTL——仍存在（~:49-53）；② 冷请求无合并——仍存在；③ 仅进程失效——**部分修复**：新增 `invalidate_agg_async`（:91-111，进程+Redis SCAN+DEL 双端），`loop.py:1673-1676`/`loop_batch.py:201-204`（停用/启用/参评切换）已接入，消除 240s TTL 观察盲区；旧 `invalidate_agg` 仅进程语义但生产代码已无调用方。**新缺口**：`batch_delete_snapshots`（performance.py，a10acc45）删快照后未调用任何 agg 失效（靠 TTL 自然过期）；SCAN+DEL 非原子。对照 C28：失效跨进程已有实现基础，命中不重置/20 并发合并待做。

**TEST-01 相邻** ｜ `.github/workflows/ci.yml` 本轮 0 变更（E2E continue-on-error :61-66 原样，TEST-01 判定不变）。`db/postgresql/01_schema.sql` +20 行（28c3b561）：**kpi_snapshot_hourly +5 列**（assess/diagnose/tune_level 三性分离←daa4f06b6e26；source/source_task_id←17fdbfa579af）、**kpi_snapshot_custom 同 +5 列**、**alert_event +2 索引**（triggered_at/status_time←b275ef85dc40）、**diagnosis_run +2 索引**（status_created/finished_at←c9bf79b6868a）＝16 DDL+4 注释，无新表无新约束，四个迁移文件均实存。**对 P0-03 影响**：新装路径 apply_bootstrap_sql 现包含上述 10 列+4 索引，bootstrap→stamp head→alembic check 口径恢复自洽（此前正是这批欠账致 CI 连红）；02_seed_data.sql 未变；存量升级路径不受影响（纯增量经 alembic 交付）。P0-03 §2.3 设计无需改动。

**golden 变更登记** ｜ 4 文件 +1522/-24：① `openapi_baseline.json` +1515/-17（a10acc45 再生成，扫入 11+ 新路径：window-agg/batch-delete/单删/health-flags-recompute/runtime-params/valve-alerts/dashboard 系列/diagnosis runs batch-delete/handling suggestions/alert batch-acknowledge/fitness-dimension-maps）；② `refactor_protected_manifest.json`：baseline_head 259a5666→**7570fb60**，重登记 4 个保护文件哈希（effective_auto.py+saturation.py←c811b2fd；pipeline.py+types.py←5920fe14/a63aa946）；③ 辨识 golden ×2：algorithm_version TUNE_IDENT_v1.0→**v1.4**。04§3 复用测试清单文件全部仍在（test_kpi_calc.py 不在 §3 清单内但有改动，清单无需增补）。

## 台账回写指令（主协调执行）

1. CFG-06 转"已修复（c811b2fd）"——保留原 ID 与新证据。
2. PERF-03 改写："双端失效已部分落地（fef31fcb invalidate_agg_async，接入停用/参评路径），剩 TTL 重置/冷合并缺口；新增：batch_delete_snapshots 删快照无失效、SCAN+DEL 非原子"。
3. TUN-04（批次3）按新形态更新（见批次3 报告）。
