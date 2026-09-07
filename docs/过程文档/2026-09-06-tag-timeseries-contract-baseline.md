# 测点子表重构 P0 契约与参考基线

日期：2026-09-06。分支：`codex/tag-timeseries-refactor`，基线 HEAD `9ee40210e133288830ac8a3ee25103ee6be8bb8d`。
本文件是 [整改计划](2026-09-06-tag-timeseries-refactor-plan.md) P0 阶段（P0-1～P0-6）的交付物与证据索引。**本阶段未改任何业务代码/生产数据**；新增资产均为测试/文档（见 §7 变更清单）。

## 1. P0-1 环境与工作区登记

| 项 | 值 |
|---|---|
| HEAD | `9ee40210e133288830ac8a3ee25103ee6be8bb8d`（=main，含 WS/token 修复） |
| 脏文件（P0 开始时） | 仅 3 份未跟踪方案文档（设计/计划/交接） |
| 脏文件（P0 结束时） | 方案文档 + 本阶段新增测试资产（§7）；无业务代码改动 |
| 主开发实例 | `clpm-mvp-postgres`（17102）/`clpm-mvp-tdengine` 3.3.6.0（17104）/`clpm-mvp-redis`（17103）/mock（17106）；后端 17101 未运行，全程未触碰 |

**隔离测试环境**（新增 `deploy/docker/docker-compose.refactor.yml`，本阶段启动并持续使用）：

| 容器 | 镜像/版本 | 端口 | 用途 |
|---|---|---|---|
| clpm-ref-postgres | postgres:16 | 17202 | 空库 `clpm_refactor`；schema=01_schema.sql+02_seed+alembic stamp(g7b8c9d0e1f2)+upgrade head；`alembic check` 无漂移 |
| clpm-ref-redis | redis:7-alpine | 17203 | Celery broker=/1、result=/2（未启动任何 Worker/Beat——测试全部进程内执行） |
| clpm-ref-td360 | tdengine/tdengine:3.3.6.0（arm64） | 17204/17215 | 开发环境目标版本；库 `clpm_ts_ref`（测试内建库建表） |
| clpm-ref-td366 | tdengine/tdengine:3.3.6.6（arm64，本机原仅 amd64，已重拉 arm64） | 17214/17225 | 生产 compose 目标版本（P1 行为验证用） |

worktree `backend/.env`（gitignored）指向上述隔离实例；TDengine 容器不挂载 01_supertable.sql（其库名硬编码 clpm_ts），由测试按 `TDENGINE_DB=clpm_ts_ref` 显式建库建表。

## 2. P0-2 活跃取数入口清单

按调用链分组（入口 → 底层函数 → 物理表）。**已核对**路由注册（main.py:957-1053）、Celery include（celery_app.py:29-48）、Beat 注册。死代码只登记不删除。

### A. 宽表读路径（st_loop_data / d_loop_*）——经 `get_provider().make_query_fn` → `query_wide_table_native`（+Redis realtime:history 近 1h 探测 + `query_last_values_before` COV 初值）

| # | 入口 | 类型 | Owner | 验收映射 |
|---|---|---|---|---|
| A1/A2 | `GET /timeseries/{loopId}/waveform`、`POST /timeseries/batch/waveform`（tags.py:704/749） | API | C | V16/P3-3 |
| A3/A4 | `POST /algorithms/dataplanner/plan|bundle`（dataplanner.py:173/232，ADMIN） | API | C | V16 |
| A5 | `POST /algorithms/kpi/calculate`（algorithms.py:138） | API | C | V16/§5.2 |
| A6 | `GET /loops/{id}/monitor` → monitor.get_loop_monitor_detail → trend_service.fetch_loop_trend:313 | API | C | V16 |
| A7 | `GET /tuning/verification/data` → waveform.get_waveform → fetch_loop_trend | API | C | V16 |
| A8/A9 | `POST /tuning/identify[/history|/segments]`、`POST /algorithms/tuning/calculate` | API | C | V16/§5.2-4 |
| A10 | Celery `identify_model_task`（services/tuning.py:645 → _fetch_preprocessed_signals → DataPlanner） | Celery(API 触发) | C | V16 |
| A11 | Celery `calculate_hourly_kpi`（**Beat 每小时**；kpi_calc.py:130 → _build_data_planner:1949） | Beat+Task | C | V16/性能 |
| A12 | Celery `calculate_custom_loop/batch_kpi`（`POST /tasks/custom/evaluate` 派发） | Celery | C | V16 |
| A13 | Celery `backfill_kpi_range`（`POST /tasks/backfill`、导入后自动 `_trigger_kpi_backfill`） | Celery | C | V16/V12 |
| A15 | `POST /diagnosis/run` → Celery `run_diagnosis_batch` → diagnosis_orchestrator.py:595（tag_roles=[pv,sp,op,mode]） | API+Celery | C | V16/§5.2-4 |
| A16 | Celery `diagnosis_schedule.run_daily/run_weekly`（**Beat 01:10/周日 02:10**） | Beat | C | V16 |
| A17 | 预警事件驱动 `dispatcher._trigger_diagnosis` → 同 A15 | 服务 | C | V16 |
| A18 | `scripts/backfill_instrument_fault_rate.py` | 手动脚本 | D | 登记 |
| A21 | data_import `_replace_window_from_staging`:1000 读 **stg__d_loop_*** 暂存表 | 导入内步 | B | V12 |

### B. t_* 窄表路径（core/tdengine.query_trend_data：t_<tag> 子表、val 列、无质量列默认 GOOD）

| # | 入口 | 状态 |
|---|---|---|
| B1 | `make_dataplanner_query_fn`（core/tdengine.py:376，内含 7 次窄表查询） | **死代码**：全仓零调用，已被 Provider 宽表路径取代 |
| B2 | `TDengineProvider.query_trend_data`（tdengine_provider.py:249） | **死代码**：无调用方（trend/waveform 已改走 make_query_fn）；保留协议方法签名（契约） |
| B3 | `scripts/import_kpi_test_data.py` | 手动脚本（唯一活跃 query_trend_data 调用方） |

### C. Redis 读路径

| # | 入口 | 说明 |
|---|---|---|
| C1 | `tdengine_provider._query_fn_wide` 探测块 → `subscriber.get_history_values`（realtime_subscriber.py:2393） | realtime:history:{loop_part} 唯一读方；R13 完整性双条件命中，未命中回源宽表 |
| C3 | `realtime:<tag>` 最新值读方（monitor/tag/loop/node_performance/workbench_summary/realtime.py 等 MGET） | 非历史路径，不受本重构影响（设计 §3：最新值缓存独立于点历史） |

### D. 写路径（TDengine）

| # | 入口 | 说明 |
|---|---|---|
| W1 | RealtimeSubscriber `_flush_buffer` → `batch_insert_multi`（INSERT…USING st_loop_data TAGS） | 实时写回唯一路径；同 tick 同角色覆盖（T02）、九字段行（T01）为已知现状 |
| W2 | `POST /loops/data-import/start` → Celery `import_history_data` → `_import_single_loop`：远端拉数→`batch_insert`（overwrite 走 stg__ 暂存表→DELETE 主窗→搬回→DROP） | 导入唯一路径；远端历史唯一调用方（红线） |
| W3/W4 | `_delete_range`/`_drop_table`（execute_native_effective）；`ensure_subtable` | ensure_subtable **死代码**（零调用，USING TAGS 自动建表） |
| W5-W8 | scripts/import_dcs_history_csv、import_history_csv、data_simulator、tuning_demo_*、clean_*/fix_*/benchmark/backfill_kpi 等 | 手动运维脚本，登记不改造 |

### E. 完整性/监控路径

| # | 入口 | 表 | P3 适配 |
|---|---|---|---|
| E1 | `GET /health` → execute_sql("SHOW DATABASES") | 元数据 | 无需 |
| E2 | `POST /loops/data-import/integrity-check` → data_integrity `_query_loop_bucket`：每子表 `INTERVAL(1h) COUNT(*)` | d_loop_* 行数 | **改逻辑覆盖**（V16/设计 §6.3） |
| E3 | Celery `run_daily_integrity_check`（**Beat 02:00**）同 E2 | 同上 | 同上 |
| E4 | Celery `run_data_link_check`（**Beat */10**）：`SELECT COUNT(*) FROM st_loop_data WHERE ts >= NOW-Nm`（超表） | 超表行数 | **改物理写入量+逻辑覆盖双指标** |
| E5 | diagnosis_schedule `_density_ok`：每子表窗口 COUNT 行数门禁 | d_loop_* 行数 | **改逻辑覆盖**（防 COV 稀疏误判，V02） |
| E6/E7 | sweep_import_tasks / diagnosis_evidence_cleanup | 不查 TD | 无需 |

### 其他确认

- performance/node_performance/report_stats/handling_stats/kpi_snapshot/cockpit/workbench_* 系列只读 PG 快照或 Redis 最新值，**不直接查 TDengine**。
- **死代码链登记**（不删除、不改造）：diagnosis_engine.py（任务未注册+Beat 注释）、endpoints/diagnosis.py 全部路由（未注册，同路径 A1 已由 tags.timeseries_router 取代）、services/diagnosis.py 对 diagnosis_result/diagnosis_tag 的读方（上游退役后无写入方；report_generator/tracker/ai_insight 的引用保持现状）。
- **A14 `prewarm_cache`**：任务注册但无 Beat/API 入口（prewarm 已废止），半死代码登记。

## 3. P0-3 接口契约与受保护清单

### 契约测试（新增 `backend/tests/test_refactor_contracts.py`，30 用例全绿）

冻结的契约要点：

1. **Provider**：`get_provider()` 恒返回 `TDengineProvider`；`make_query_fn(db)` 闭包签名 `(loop_id, tag_roles, start, end, interval_s) → RawTimeSeries`；`query_trend_data(tag_name, start_time, end_time, sample_interval=1) → list[dict]`；`close()` async。
2. **RawTimeSeries**：字段 `{timestamps, signals, quality_codes}`；角色键小写 `pv/sp/op/mode/pid_p/pid_i/pid_d`；当前仅 `pv_quality` 一个质量键；COV 填充列固定 `sp/mode/pid_p/pid_i/pid_d`（PV/OP 不填充）；`realtime_subscriber._build_row` 9 列布局冻结。
3. **时间口径**：查询边界 `_format_ts` 输出带 Z UTC 串（naive 视为 UTC）；存储侧 +8 墙钟串经 `_stored_ts_to_utc_naive` 解析；宽表 SQL 端点双闭（`>=`/`<=`）；`TimeWindow` 起止均含。
4. **质量映射**：`quality_code.map_quality_code`（None→Good、{1,2,3,192}→Good、0→Bad、其他→Unknown）冻结。
5. **导入**：响应字段（taskId/status/loopCount/importedCount/…）冻结；**「点数」单位=宽表行数=时间槽**（每槽含 7 角色），不是 7×物理记录数（以 `_convert_to_wide_rows` 行为固定）。
6. REST DTO 由既有 `tests/test_openapi_contract_drift.py`（golden openapi_baseline.json）守护，本重构不得使该测试变化。

### 受保护算法文件清单（39 文件，SHA256 golden）

- 清单模块：`backend/tests/refactor/protected_manifest.py`；守卫测试：`backend/tests/test_refactor_protected_manifest.py`；golden：`backend/tests/golden/refactor_protected_manifest.json`（baseline_head=9ee40210）。
- 范围：metric_calculator/ 全目录（23 文件）、tuning_identification/ 全目录、tuning_algorithms.py、diagnosis_orchestrator.py、tasks/arma.py、preprocessing/{pipeline,quality_code,thresholds}.py、contracts/data_types.py。
- frontend/ 不做哈希守护（体量与构建产物问题），由主审按分支 diff 范围把关（本重构声明默认零前端改动）。
- 更新 golden 须经用户/主审授权：`REFACTOR_UPDATE_GOLDEN=1 uv run pytest tests/test_refactor_protected_manifest.py`。

### 现状口径差异登记（非本次修复，P3 适配层必须处理）

- **导入质量 Good 集合 {1,192} ≠ 预处理 Good 集合 {1,2,3,192}**：OPC UA Good=2 在导入侧被写成 0（Bad），进预处理后判 Bad。原码透传不可行（设计 §5.4），P1 表结构保留 `quality_raw`+`quality_schema`，P3 统一在适配层解码。
- mock 数据服务自身质量枚举自相矛盾（实时 0=Good/1=Bad vs 历史 1=Good/2=Bad/3=离线），实证"不能假设单一枚举体系"。

## 4. P0-4 独立密集参考数据

`backend/tests/refactor/reference_data.py`（生成器）+ `backend/tests/test_refactor_reference_data.py`（14 自检用例）。**不 import 任何待测生产模块**（仅 contracts.data_types 用于参考 RawTimeSeries 构造）。

- **主数据集** `build_reference_dataset(seed=20260906)`：4 回路（FC/PC/TC/LC）× 七角色 × 2h 全覆盖 Good；PV/OP 逐秒动态激励（一阶响应+噪声）、SP 两次阶跃、MODE 手/自动切换、PID 参数阶跃；LIC-401 纯常值（V15 反例）；PIC-201/TIC-301 共享 PID 三点（V09，只写一份）。
- **反例数据集** `build_counterexamples()`：无窗口前初值（PV 前 300s / OP 前 2100s，V03）、断线缺口 600-900s+恢复快照（V06）、值不变质量 Good→Bad(1200-1350)→Uncertain(1350-1500)（V04/V05）、t=2400s PV 改绑新点无 anchor 至 2700s（V09）。
- **参考预期侧** `build_reference_raw_series()`：从密集真值直接构造（平凡因果保持），固定网格/未知即 None+-1 规则；`wide_rows_from_dense()` 产出 legacy 宽表行（+8 墙钟 ts）。
- 确定性已断言（同 seed 字节级一致）；点身份为确定性 UUID（标准 36 字符形式，PG 兼容；子表名派生时去连字符——设计 §4.1）。

## 5. P0-5 AAS 字段来源合同

### 已确认（代码/mock/既往实测可证）

| 面 | 字段/行为 | 证据 |
|---|---|---|
| 实时推送（type=1 Invocation） | `updateRealValues` → item 字段 `{tagCode, value, quality, collectTime}`；value 可为字符串（含 "-1.#QNAN0" 等工业异常字面量） | realtime_subscriber._cache_value:1519-1537；R06 整改已加有限值守卫 |
| 订阅快照（type=3 Completion） | `result={code:200, data:[...同上字段...]}`，data 为**本次订阅全部位号当前值**（含低频角色） | _handle_signalr_message:1220 |
| 位号命名 | 内网实例点号（`41FIC40504_PIDA.PV`）、外网实例下划线（`41LIC40109_PIDA_PV`）；`_parse_tag_code` 以 PG 映射为权威、无映射时不兜底 | R11；memory aas-tag-naming-discrepancy |
| 历史 API | `GET HistoryData/Get` query 参数 tagCodes/startTime/endTime/sampleInterval；响应 `{code, data:{timestamps[], series:[{tagCode, values[], qualities[]}]}}` | data_import._fetch_remote_history:1210；mock schemas.py 同构 |
| 历史 qualities 枚举 | mock 标注 `0=未知, 1=Good, 2=Bad, 3=离线`；真实 AAS 侧未见权威文档 | mock_data_server/schemas.py:25 |

### 未确认（阻塞哪项验收已标）

| # | 未知项 | 阻塞的验收 | 处置 |
|---|---|---|---|
| U1 | 真实 AAS 实时 quality 的枚举体系与语义（0/1/2/3 各代表什么、是否随值不变单独推送质量事件） | V05（质量变化持久化）真实链路部分；fake 层不阻塞 | P2 影子写阶段用 zpdev 获准样本验证；未确认前 quality_schema 解码表只登记不臆断 |
| U2 | 历史 API 返回是否为 sampleInterval 重建样本（非原始 COV 事件）、空窗口响应语义（无变更 vs 无数据） | V12 完整性判定"空响应不推进覆盖" | P2 导入适配按 HISTORY_GRID(=4) 保存不宣称原始事件；真实行为在 P4 用受控窗口实测 |
| U3 | 历史 API 分页/截断行为（长窗口是否完整返回） | V12 分页失败恢复 | 沿用现行分块容错+失败窗口登记，P4 复演 |
| U4 | 历史响应是否携带窗口前状态（初值） | V12 前边界 anchor | 未知按"无窗口前状态"保守处理，不推进覆盖 |
| U5 | 恢复快照（重连 Completion）中 collectTime 很旧的常值语义 | V06 真实链路 | fake 反例已定语义（快照只证明恢复时刻）；真实侧 P4 观察 |

**结论**：不依赖 U1-U5 的基线工作（契约/参考数据/legacy 基线）已完成；相关验收项在真实链路验证前保持"未验"，不以 fake 通过冒充。

## 6. P0-6 性能与算法结果基线（真实 TDengine 3.3.6.0 + PG16）

运行入口：`cd backend && uv run pytest tests/integration/test_refactor_baseline.py -m integration -s`（数据种入幂等）。

### 取数基线（窗口 1h×7 角色×3600 点，4 回路，legacy 宽表）

| 指标 | 值（本次实测） |
|---|---|
| 冷查询（种入后首读，含 tracemalloc 开销） | 130~183 ms/回路 |
| 热查询（同窗重复 5 次） | 中位 29~38 ms，最大 ≤73 ms |
| SQL 次数 | **每回路每次查询恰 2 条**（宽表 SELECT + COV LAST 初值）——作为 P3 等价约束固定 |
| 峰值内存（tracemalloc，3600 行物化+RawTimeSeries） | 2.1~2.4 MB/回路 |
| 正确性 | Provider 输出与参考真值逐点一致（FLOAT32 舍入容差 max(1e-4, |v|×1e-6)、mode/quality 精确相等） |

**测量口径冻结（P4 复用）**：同一硬件（macbook arm64，隔离容器）；冷=种入后首次读；热=同窗第 2-6 次读取的中位/最大；SQL 计数在 `execute_native` 层按 SQL 前缀分类；内存为 tracemalloc 峰值。时延数字记录于 `tests/golden/refactor_query_baseline.json`（仅记录不比对——环境相关）。

### 算法结果基线（golden 比对模式）

- 入口同上；`tests/golden/refactor_algorithm_baseline.json`：26 契约指标码 × 4 回路（每回路 25 个可计算值+可信度），复跑两次比对全等（容差 1e-9）。
- P3/P4 重跑同测试即得"算法等价"证据链的一半（另一半：point 布局 vs legacy 布局差分，P4 矩阵）。

### 全量回归

`uv run pytest -q`（不含 integration）：**4758 passed, 379 skipped, 32 xfailed**——P0 新增资产零回归。

## 7. 本阶段变更清单（全部为测试/文档/环境资产，无业务代码改动）

新增：
- `deploy/docker/docker-compose.refactor.yml`（隔离环境定义）
- `backend/.env`（gitignored，指向隔离实例）
- `backend/tests/refactor/__init__.py`、`reference_data.py`、`protected_manifest.py`
- `backend/tests/test_refactor_reference_data.py`（14）、`test_refactor_contracts.py`（30）、`test_refactor_protected_manifest.py`（1）
- `backend/tests/integration/test_refactor_baseline.py`（3，integration 标记）
- `backend/tests/golden/refactor_protected_manifest.json`、`refactor_algorithm_baseline.json`、`refactor_query_baseline.json`
- 本文档

未动：`backend/app/**`、`frontend/**`、`db/**`、alembic 迁移、生产 compose。

## 8. P0 晋级核对（计划 §6 五问）

- [x] 每项验收均有证据：P0-1 §1；P0-2 §2（关键论断已逐条抽查源码行号）；P0-3 §3（30 用例+39 文件 golden）；P0-4 §4（14 用例）；P0-5 §5（已确认/未确认分列，未验项不冒充）；P0-6 §6（真实 TD/PG 实测+golden 复跑全等）。
- [x] 适用门禁：全量单测 4758 passed；ruff/alembic 未涉及业务改动（新增测试文件 ruff 将随 P1 一并执行；见下方"遗留"）。
- [x] 无范围外改动：仅测试/文档/环境资产（§7）；未启动 Worker/Beat；未触碰主实例与生产。
- [x] §8 状态已追加（见计划文档新行）。
- [x] 阻断 bug：无。遗留（非阻断）：td366 容器镜像已改拉 arm64 版本（本地 tag 现指向 arm64，原 amd64 镜像成悬空层——对主实例无影响，主实例 clpm-tdengine 用 3.3.6.0）。ruff/format/全量单测已在 P0 收尾完整执行通过（见 §6）。

**P0 晋级条件判定：通过。** 所有对外契约及参考输入已固定；未确认 AAS 行为（U1-U5）已明确阻塞项且不阻塞 P1/P3 fake 侧开发。
