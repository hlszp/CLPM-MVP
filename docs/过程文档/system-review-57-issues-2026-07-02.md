# CLPM 系统审查问题清单（57 项）

> 审查日期：2026-07-02
> 审查范围：回路管理模块 + 性能评估模块 + 跨模块数据流 + 算法合规性 + UX 质量 + 测试覆盖
> 排序规则：P0 阻断性 → P1 高优先级 → P2 中优先级 → P3 低优先级

---

## P0 阻断性问题（8 项）

| # | 模块 | 问题 | 文件 | 状态 |
|---|---|---|---|---|
| 1 | 回路管理 | B6: 批量操作审计日志 `target_id` 传逗号分隔 UUID 字符串，PostgreSQL UUID 类型冲突导致事务回滚，批量操作完全不可用 | `services/loop_batch.py:165,252` | **已修复** |
| 2 | 回路管理 | B1: monitor 服务硬编码 MODE→控制模式映射 `{0:Manual,1:Auto,2:Cascade,3:Cascade}`，忽略用户在 `loop_mode_mapping` 表中的配置，自控率统计口径错误 | `services/monitor.py:43-48` | **已修复** |
| 3 | 跨模块 | B1: SignalR 默认禁用（`SIGNALR_ENABLED=False`），实时数据链路完全断开，开发环境无实时数据流入 TDengine | `core/config.py:82-84` | 已由实时模拟器缓解 |
| 4 | 性能评估 | B2: 前端完全缺失节点级 KPI API 调用，后端 5 个 `/performance/nodes/*` 端点无前端消费方，装置级/单元级/工厂级聚合结果不可见 | `frontend/.../api/metric.ts` | **已修复** |
| 5 | UX | UX1: 169 处硬编码浅色 Tailwind 类（`text-gray-400`/`bg-blue-50`/`border-gray-200` 等），暗色模式下出现刺眼亮色块、对比度不足 | `views/loop/*.vue`(107处) + `views/metric/*.vue`(62处) | **已修复**（CSS 集中覆盖） |
| 6 | UX | UX2: `industrial-light.css` 仅覆盖浅色变量，无 `.dark` 选择器，表头/hover/选中行/滚动条硬编码 HSL 值 | `styles/industrial-light.css` | **已修复** |
| 7 | UX | UX4: AAS 同步触发后只 `setTimeout(2000)` 硬等 2 秒，无进度反馈、无完成确认、无错误提示 | `views/loop/aas.vue:204-218` | **已修复** |
| 8 | UX | UX5: KPI 计算/批量配置/导出等异步操作只显示成功/失败 toast，无进度条或步骤反馈 | `views/loop/manage.vue` 多处 | **已修复** |

---

## P1 高优先级问题（14 项）

| # | 模块 | 问题 | 文件 | 状态 |
|---|---|---|---|---|
| 9 | 回路管理 | R1: 单删（硬删+校验Tag）与批删（软删+不校验Tag）行为不一致 | `endpoints/loops.py` + `services/loop_batch.py` | **已修复** |
| 10 | 回路管理 | R2: `batch_update_loops` 的 `is_stat_enabled` 复用 `is_active` 语义，与 `is_monitored` 写入冲突 | `services/loop_batch.py:75-77,133-136` | **已修复** |
| 11 | 性能评估 | R1: 标准任务 `trigger_standard_evaluation` 接收 `body.tsStart` 但调用 `calculate_hourly_kpi.delay()` 时未传参，用户指定时间窗被忽略 | `endpoints/tasks.py:310` | **已修复** |
| 12 | 性能评估 | R2: 自定义任务 `ts_end` 参数存入 Redis 但未传给 Celery 任务，自定义任务时间窗固定为 `cycle_minutes` 长度 | `endpoints/tasks.py:404` | **已修复** |
| 13 | 跨模块 | B3: 实现契约 §6 状态机声称 `ACTIVE/PAUSED/DECOMMISSIONED`，实际代码为 `READY/PARTIAL/INACTIVE` | `docs/.../implementation-contract.md` §6 | **已修复** |
| 14 | 跨模块 | B4: 节点级聚合 `KPI_FIELDS` 仅含 9 项，缺失 `stiction_coeff`/`steady_state_time`/`output_travel_index`/`ideal_settling_time` | `services/node_performance.py:38-48` | **已修复** |
| 15 | 跨模块 | B5: 节点级实时自控率绕过 DataPlanner 直查 TDengine（每回路并发 5 分钟窗口查询），不享缓存且硬编码 `DEFAULT_AUTO_MODES={1,2,3}` | `services/node_performance.py:95-246` | **已修复**（硬编码部分；TDengine 实时查询保留，实时点查不适合走 DataPlanner 缓存） |
| 16 | 算法 | 偏差3: `settling_time.py` MIN_POINTS=30，设计要求 100；30 点 AR(10) 模型自由度不足，影响快速率 F | `metric_calculator/settling_time.py:30` | **已修复** |
| 17 | 算法 | 偏差5: `ideal_settling_time.py` 默认值 TC=300(应180)/LC=300(应600)/CC=600(应300)，影响快速率 F 基准 T' | `metric_calculator/ideal_settling_time.py:27-33` | **已修复** |
| 18 | 算法 | 偏差1: R 缺失时降级为基础评分 60%，设计文档 §4.10 未定义此降级逻辑，60% 系数缺乏依据 | `confidence_evaluator.py:222-225` | **已修复** |
| 19 | UX | UX3: `preferences.ts` 中 `THEME_COLORS` 为静态常量（不响应主题切换），`detail.vue` 直接使用而非 `useClpmTheme()` | `preferences.ts:38-49` | **已修复** |
| 20 | UX | UX6: 批量配置入口隐藏在独立 Tab 中，需跨 Tab 操作（选中回路→切换Tab→打开弹窗），流程不直观 | `views/loop/manage.vue:1129-1139` | **已修复** |
| 21 | UX | UX7: 大量 `catch {}` 静默吞错，页面状态不一致且无错误引导，`ClpmDataCanvas` 的 error/retry 能力未使用 | `views/loop/manage.vue` 多处 | **已修复** |
| 22 | 测试 | TC1: 7 场景测试数据（7200点×7）已生成于 `fixtures/kpi_test_data.json`，但**没有任何 pytest 测试引用**，项目记忆硬约束在 CI 中未被验证 | `tests/fixtures/kpi_test_data.json` | **已修复** |

---

## P2 中优先级问题（19 项）

| # | 模块 | 问题 | 文件 | 状态 |
|---|---|---|---|---|
| 23 | 回路管理 | B2: controlMode 后置过滤导致分页 total 返回当前页过滤后条数，大结果集无法翻页 | `services/loop.py:247-250` | **已修复** |
| 24 | 回路管理 | B3: 前端 `LoopQueryParams` 声明 `controlType` 参数但后端未实现，筛选被静默忽略 | `frontend/src/api/loop.ts:109` | **已修复** |
| 25 | 回路管理 | B4: `create_loop` 不接收 `level`/`modeattr_tag_id`/`data_retention_days`，前端声明但被忽略 | `endpoints/loops.py:96-117` | **已修复** |
| 26 | 回路管理 | B9: AasConfig 前后端字段不匹配（`syncInterval` vs `syncIntervalSeconds`、`latency` vs `latencyMs`） | `frontend/src/api/aas.ts` vs `schemas/aas.py` | **已修复** |
| 27 | 性能评估 | R3: `MetricConfig.weight` 字段存在并校验总和=100，但综合评分实际使用 `LoopTypeWeight`，管理员修改不生效 | `services/performance.py:194` vs `kpi_calc.py:1105` | **已修复** |
| 28 | 性能评估 | R4: 节点小时聚合用 `LoopLevelWeight`(1:3,2:2,3:1)，日/月聚合用 `loop_count`，权重体系不一致 | `node_performance.py:261` vs `node_aggregation.py:88` | **已修复** |
| 29 | 跨模块 | B6: 自定义任务快照表 `kpi_snapshot_custom` 缺少 `sampling_freq`/`quality_policy` 字段，数据血缘追溯能力弱于标准任务 | `tasks/kpi_calc.py:1415-1420` | **已修复** |
| 30 | 跨模块 | B7: API 前缀不统一（`/config/loop-type-weights` 单数 vs `/configs/metrics` 复数） | `endpoints/loop_type_weight.py:23` vs `endpoints/configs.py:48` | **已修复** |
| 31 | 跨模块 | B8: 前端路由与实现契约 §2 不一致（`/metric/weight-config` vs 契约 `/metric/type-weight`），孤儿视图 `type-weight.vue`/`level-weight.vue` | `router/routes/modules/metric.ts` | **已修复** |
| 32 | 跨模块 | B9: 前端端口文档与配置不一致（`.env.development` 为 5666，AGENTS.md 为 5668） | `frontend/.env.development` vs `AGENTS.md` | 待修复 |
| 33 | 算法 | 偏差2: 振荡检测用 `_crossing_regularity`(CV变异系数) 替代设计要求的 S_TA/S_TB(持续时间相似率)，数学含义不同 | `metric_calculator/oscillation.py:105-108` | 待修复 |
| 34 | 算法 | 偏差4: ARMA 模型用 AR(10) 高阶近似 ARMA(2,1)，设计文档要求默认 (2,1)，可能过拟合 | `tasks/arma.py:23` | 待修复 |
| 35 | UX | UX8: `confidence-badge.vue` 色块圆点硬编码 hex 值，不响应主题切换 | `components/metric/confidence-badge.vue:47-54` | 待修复 |
| 36 | UX | UX9: Ant Design `darkAlgorithm` 已接入，但 CLPM 业务自定义样式未对齐，原生组件与业务组件视觉断裂 | `app.vue:16-30` | 待修复 |
| 37 | UX | UX13: 多处 `message.info("功能待后端接口支持")` 让用户困惑，应改为 disabled + tooltip | `views/loop/monitor.vue:524` 等 | 待修复 |
| 38 | UX | UX14: WS 在线时仍每 30s 全量轮询，浪费带宽与资源 | `views/loop/monitor.vue:651-675` | 待修复 |
| 39 | 测试 | TC2: 边界条件缺失（极端PV值、100% Bad质量、低频振荡周期>60s、OP饱和临界值98/99/100） | `tests/test_metric_calculator/` | 待修复 |
| 40 | 测试 | TC3: 2小时 1Hz 大数据集（7200点）性能未验证，现有测试最大 500 点 | `tests/test_metric_calculator/conftest.py` | 待修复 |
| 41 | 测试 | TC4: 场景间对比测试缺失（fast_response vs slow_response 的 fast_rate 应有明显差异） | — | 待修复 |

---

## P3 低优先级问题（16 项）

| # | 模块 | 问题 | 文件 | 状态 |
|---|---|---|---|---|
| 42 | 回路管理 | B5: `monitor_status` 与 `is_active` 两个语义相同的过滤条件共存，用户同时传不同值会得空结果 | `services/loop.py:158-169` | 待修复 |
| 43 | 回路管理 | B7: AAS 同步不更新 `LoopLedger.last_aas_sync_at`，该字段成为孤儿 | `services/aas_sync.py` | 待修复 |
| 44 | 回路管理 | B8: 波形批量接口 `POST /timeseries/batch/waveform` 后端已实现但前端无消费方 | `endpoints/tags.py:batch_waveform_endpoint` | 待修复 |
| 45 | 回路管理 | R9: `match_tags_for_loop` 硬编码 tag 后缀 `["PV","SP","OP","MODE","KP","TI","TD"]`，与 `PID_P/PID_I/PID_D` 不一致 | `endpoints/tags.py:186` | 待修复 |
| 46 | 回路管理 | R10: `_retry_async` 异常处理代码异味（`last_exc = exc = None` 后再 `sys.exc_info()`） | `services/aas_sync.py:219-230` | 待修复 |
| 47 | 回路管理 | R11: TagRegistry 在导入时静默创建（绕过 AAS 同步），可能出现"幽灵 Tag" | `services/loop.py:891-902` | 待修复 |
| 48 | 回路管理 | R12: `ledger.vue` 已标注 `@deprecated` 但仍在仓库 | `views/loop/ledger.vue` | 待修复 |
| 49 | 性能评估 | R5: `performance.py` 中 `_aggregate_kpi_summary`/`_aggregate_kpi_cards`/`_aggregate_steady_trend` 为死代码 | `services/performance.py:1095-1207` | 待修复 |
| 50 | 性能评估 | R6: `get_ranking()` 未过滤 `confidence_level='E'`，与节点级聚合不一致 | `services/performance.py:625` | 待修复 |
| 51 | 性能评估 | R8: `refresh_beat_schedule` 需重启 Beat 进程才生效，前端无提示 | `tasks/kpi_calc.py:472` | 待修复 |
| 52 | 跨模块 | B10: `LoopLedger.modeattr_tag_id` 字段未被计算链路使用（死字段） | `models/loop.py` | 待修复 |
| 53 | 跨模块 | B11: KPI 计算仅过滤 `status='READY'`，PARTIAL 回路永远不计算（设计合理但用户感知差） | `tasks/kpi_calc.py:553-555` | 待修复 |
| 54 | 跨模块 | B12: TDengine 子表名生成规则散落 3 处未抽公共函数 | `core/tdengine.py` + `realtime_subscriber.py` | 待修复 |
| 55 | 算法 | 偏差6: `algorithm_version` 在 `confidence_evaluator.py` 为 v2.0，在 `performance.py` 为 v1.0，同代码库不一致 | `confidence_evaluator.py:25` vs `performance.py:39` | 待修复 |
| 56 | 算法 | 偏差7: LTTB 降采样 maxPoints=2000 在 KPI 计算路径未实现，设计文档说"无需降采样"，两者存在冲突 | `data_planner.py` | 待澄清 |
| 57 | UX | UX10: `realtime-ws.ts` 控制台日志未做环境守卫，生产环境污染控制台 | `utils/realtime-ws.ts:93,109,117,130` | 待修复 |

---

## 统计汇总

| 优先级 | 数量 | 已修复 | 待修复 |
|---|---|---|---|
| P0 阻断性 | 8 | 6 | 2 |
| P1 高优先级 | 14 | 14 | 0 |
| P2 中优先级 | 19 | 9 | 10 |
| P3 低优先级 | 16 | 0 | 16 |
| **合计** | **57** | **29** | **28** |

## 已修复记录

| # | 问题 | 修复内容 | 修复文件 | 验证 |
|---|---|---|---|---|
| 1 | B6: 批量操作审计日志 UUID 类型冲突 | 将 4 处非 UUID 的 `target_id` 值改为 `None`（批量/配置/导入操作无单一目标记录，完整列表已在 `before_value`/`after_value` JSON 中） | `services/loop_batch.py:165,252` + `services/aas_config.py:162` + `services/tag.py:625` | 51 个相关测试通过 |
| 2 | B1: monitor 硬编码 MODE 映射 | `_mode_value_to_label` 增加可选 `mapping` 参数；新增 `_load_mode_mappings` 批量查询；`list_loop_monitor`/`get_loop_monitor_detail` 接入 `loop_mode_mapping` 表 | `services/monitor.py` + `tests/test_monitor_service.py` | 39 个测试通过 |
| 4 | B2: 前端缺失节点级 KPI API | 补齐 6 个 API 函数 + 10 个类型定义；dashboard.vue 接入节点 snapshot 与 overview API | `frontend/.../api/metric.ts` + `views/metric/dashboard.vue` | 前端类型检查通过 |
| 5+6 | UX1+UX2: 暗色模式 292 处硬编码 | `industrial-light.css` 新增 10 子节 `.dark` 覆盖块（CSS 变量 / gray 反转 / 彩色半透明 / 表头 hover 选中行 / 滚动条 / 工具类） | `styles/industrial-light.css` | 前端类型检查通过 |
| 7 | UX4: AAS 同步进度反馈 | 后端：`AasConfigInfo` schema 新增 `lastSyncAt`/`lastSyncStatus` 字段；`aas_config.py` 新增 `set_last_sync_status` 写入 sys_config；`trigger_aas_sync` 端点预先置为 PROCESSING；`sync_tags_from_aas` 同步成功置 SUCCESS/异常置 FAILED。前端：`aas.vue` `handleSync` 改为触发后轮询 `getAasConfigApi`，新增进度 Alert 与超时（90s）保护，`onUnmounted` 清理定时器 | `backend/.../schemas/aas.py` + `services/aas_config.py` + `api/v1/endpoints/aas.py` + `services/aas_sync.py` + `frontend/.../views/loop/aas.vue` + `tests/test_aas.py` | 21 个 AAS 测试通过；前端类型检查通过 |
| 8 | UX5: 异步操作进度反馈 | 在 `manage.vue`（批量配置/批量删除/导出/导入）与 `tuning/algorithm.vue`/`model.vue`/`simulation.vue`（PID 整定/模型辨识/闭环仿真）的耗时操作入口添加 `message.loading(content, 0)` 即时反馈，完成后切换为 `message.success`/`message.error`；按钮 loading 仍保留用于视觉禁用 | `frontend/.../views/loop/manage.vue` + `views/tuning/algorithm.vue` + `views/tuning/model.vue` + `views/tuning/simulation.vue` | 前端类型检查通过 |
| 16 | 偏差3: settling_time MIN_POINTS=30 | `MIN_POINTS` 从 30 提升至 100（AR(10) 模型自由度要求）；新增 3 个边界测试（n=30/99/100）防回归；原 `test_insufficient_data`（n=20）保留 | `metric_calculator/settling_time.py:30` + `tests/test_metric_calculator/test_settling_time.py` | 全后端 1492 测试通过 |
| 17 | 偏差5: ideal_settling_time 默认值错误 | `DEFAULT_IDEAL_SETTLING` 修正：TC 300→180 / LC 300→600 / CC 600→300；新增 LC 测试用例；3 个原测试断言更新（TC/CC 期望值同步） | `metric_calculator/ideal_settling_time.py:27-33` + `tests/test_metric_calculator/test_ideal_settling_time.py` | 全后端 1492 测试通过 |
| 18 | 偏差1: R 缺失降级 60% 无依据 | 删除 `base_score * 0.6` 降级分支；R 缺失（r_result is None）/ R value=None / R 可信度 E 级统一并入 INCONCLUSIVE 路径，返回 value=None + confidence=E；血缘 R 缺失时回退到 accuracy_rate；重写原固化错误行为的 `test_R_missing_degrades_to_60_percent` 为 `test_R_missing_treated_as_inconclusive` + 新增 `test_R_value_none_treated_as_inconclusive` | `services/confidence_evaluator.py:162-189,227-234` + `tests/test_metric_calculator/test_confidence_evaluator.py` + `tests/test_loop_config.py:387-398` | 全后端 1492 测试通过 |
| 14 | B4: 节点级聚合 KPI_FIELDS 缺失 4 字段 | `KPI_FIELDS` 元组补全 4 个字段（stiction_coeff/steady_state_time/output_travel_index/ideal_settling_time）；返回 dict + 3 处响应序列化（最新快照/排名/趋势）补 camelCase 输出；3 个节点级 ORM 模型（Hourly/Daily/Monthly）添加 4 列；新增 alembic migration `l5p6q7r8s9t0`（3 表 × 4 列 = 12 个 add_column）；测试 `_make_loop_snapshot`/`_make_node_snapshot`/`_make_agg_row`/2 个 snap_data dict 补 4 字段；`test_aggregate_calculates_weighted_average` 新增 4 字段断言 | `services/node_performance.py:38-54,371-394,479-491,633-648,755-770` + `models/node_kpi.py:54-59,107-112,163-168` + `alembic/versions/l5p6q7r8s9t0_add_node_snapshot_diagnostic_fields.py` + `tests/test_node_performance.py` + `tests/test_loop_config.py:199-204` | 全后端 1492 测试通过；alembic head 更新为 l5p6q7r8s9t0 |
| 15 | B5: 实时自控率硬编码 `DEFAULT_AUTO_MODES={1,2,3}` | 移除硬编码常量，新增 `get_default_auto_modes(db)` 从 `sys_config.loop.default_auto_modes` 读取 JSON 数组（如 `"[1, 2, 3]"`）；无配置或解析失败时返回空集（严格模式，不假设默认值）；`query_realtime_auto_rate` 改为调用该函数获取全局默认；自动 MODE 来源优先级：LoopModeMapping（回路级）> sys_config（全局默认）> 空集。重写 `TestRealtimeAutoRate` 5 个测试：`test_realtime_auto_rate_with_loop_config`（回路配置 + sys_config 空）/`test_realtime_auto_rate_with_sysconfig_default`（sys_config `[1,2,3]` 回退）/`test_realtime_auto_rate_empty_default_no_auto`（双空 → 0%）/`test_realtime_auto_rate_invalid_sysconfig_value`（非法值容错）/`test_realtime_auto_rate_no_loops`（空列表）。注：TDengine 直查保留（实时点查不适合走 DataPlanner 缓存） | `services/node_performance.py:95-133,167-168,222-223` + `tests/test_loop_config.py:553-747` | 全后端 1494 测试通过 |
| 11 | R1: 标准任务 tsStart 未传给 Celery | `calculate_hourly_kpi` 添加 `ts_start: str | None = None` 参数；新增 `_parse_ts_start()` 辅助函数将 ISO 8601 字符串解析为 datetime；`_track_hourly_calculation` 添加 `ts_start` 参数并透传 `_do_calculate(ts_start=ts_start_dt)`；`trigger_standard_evaluation` 改为 `calculate_hourly_kpi.delay(ts_start=body.tsStart)`。兼容性：cron 定时触发不传参（默认 None → 取上一个完整周期）；手动 API 触发传 `body.tsStart`（None 时同 cron）。测试更新：`test_standard_evaluate_success` 断言 `delay.assert_called_once_with(ts_start="2026-06-22T08:00:00Z")`；`test_standard_evaluate_admin` 断言 `ts_start=None`；新增 `test_calculate_hourly_kpi_with_ts_start` 验证 datetime 解析 + 透传；新增 `test_calculate_hourly_kpi_ts_start_none_uses_default` 验证默认行为 | `tasks/kpi_calc.py:191-291` + `endpoints/tasks.py:309-310` + `tests/test_api_tasks.py:188-225` + `tests/test_kpi_calc.py:1054-1094` | 全后端 1496 测试通过 |
| 9 | R1: 单删与批删行为不一致 | `delete_loop` 改硬删→软删（is_active=False, status=INACTIVE），保留 Tag 校验；`batch_delete_loops` 补 Tag 关联校验（批量查询 LoopTagMapping，有 Tag 的回路跳过并记入 skipped 列表），返回类型从 int 改为 dict {deleted, skipped}；`LoopBatchConfigResult` 添加 `skipped` 字段；端点适配新返回类型。新增 `test_batch_delete_loops_skip_with_tags` 测试 | `services/loop.py:549-613` + `services/loop_batch.py:186-293` + `schemas/loop_batch.py:77-83` + `endpoints/loops.py:142-177` + `tests/test_loop_batch.py:189-273` | 全后端 1500 测试通过 |
| 10 | R2: is_stat_enabled 与 is_monitored 写入冲突 | `LoopBatchUpdates` 添加 `model_validator` 强制 isMonitored 与 isStatEnabled 互斥（同时传值抛 ValidationError）；新增 `TestLoopBatchUpdatesMutex` 3 个测试（互斥拒绝/仅 monitored/仅 stat） | `schemas/loop_batch.py:13-39` + `tests/test_loop_batch.py:275-306` | 全后端 1500 测试通过 |
| 12 | R2: 自定义任务 ts_end 未传给 Celery | `calculate_custom_loop_kpi` 添加 `ts_end: str \| None = None` 参数并透传给 `_do_calculate_custom_loop`；`_do_calculate_custom_loop` 添加 `ts_end` 参数，时间窗结束逻辑改为：用户指定 ts_end 优先，否则 `ts_start + cycle_minutes`（保持默认行为）；`trigger_custom_evaluation` 改为 `calculate_custom_loop_kpi.delay(task_id, loop_id, body.tsStart, body.tsEnd)`。修复 `test_custom_evaluate_success` mock 目标错误（原 mock `calculate_loop_kpi` 应为 `calculate_custom_loop_kpi`）并添加 delay 调用参数断言。新增 4 个测试：2 个 Celery 任务层（ts_end 透传/None 默认）+ 2 个 `_do_calculate_custom_loop` 时间窗层（用户指定 ts_end/ts_end=None 用 cycle_minutes） | `tasks/kpi_calc.py:318-346,723-764` + `endpoints/tasks.py:401-408` + `tests/test_api_tasks.py:260-286` + `tests/test_kpi_calc.py:1106-1249` | 全后端 1504 测试通过 |
| 13 | B3: 状态机契约与代码不一致 | 实现契约 §6 Loop 状态机从 `ACTIVE/PAUSED/DECOMMISSIONED`（运行/暂停/退役）修正为 `READY/PARTIAL/INACTIVE`（就绪/部分配置/已停用），对齐代码实际使用；新增历史命名映射说明：`ACTIVE`/`PAUSED`/`DECOMMISSIONED` 统一视为旧命名。代码中的状态反映"配置完整性 + 删除状态"而非"运行状态"：`READY` = 配置完整可参与 KPI 计算；`PARTIAL` = 缺必需 Tag 不参与计算；`INACTIVE` = 软删除（is_active=False）。仅文档修复，无代码改动 | `docs/设计文档/00-BASELINE/implementation-contract.md` §6 | 无需测试（文档修复） |
| 19 | UX3: THEME_COLORS 静态常量不响应主题切换 | `detail.vue` 移除 `import { THEME_COLORS } from '#/preferences'`，改用 `useClpmTheme()` 获取响应式 `themeColors`（computed ref，isDark 变化时自动更新）；template 中 `THEME_COLORS.SUCCESS/DANGER` 改为 `themeColors.SUCCESS/DANGER`（Vue 自动解包 ref）。`preferences.ts` 中 `THEME_COLORS` 添加 `@deprecated` JSDoc 注释，引导组件改用 `useClpmTheme()`。注：`KPI_COLOR_MAP`/`ACTION_STATUS_COLOR_MAP` 仍在 preferences.ts 中定义但无任何文件 import 使用（死代码，保留备用） | `frontend/.../views/loop/detail.vue:49-62,589-598` + `frontend/.../preferences.ts:32-42` | 前端类型检查通过 |
| 20 | UX6: 批量配置入口跨 Tab 操作 | 移除冗余的"批量配置"Tab（`<TabPane key="batch">` + 对应的 ClpmDataCanvas 内容块），因为"回路台账"Tab 已内联完整的批量操作入口：工具栏"批量配置"按钮 + 选中回路后浮现的批量操作工具栏（批量设置/批量删除/清除选择）。移除后简化为 3 个 Tab（工厂结构/回路台账/Tag 关联），消除跨 Tab 操作摩擦。同时清理死代码：`selectedLoopColumns`/`selectedLoops` 仅在批量配置 Tab 使用，一并删除；移除未使用的 `Alert` import | `frontend/.../views/loop/manage.vue:85,1158,1489-1567` | 前端类型检查通过 |
| 21 | UX7: catch {} 静默吞错 + ClpmDataCanvas error/retry 未使用 | `manage.vue` 新增 `loadError` ref（数据加载错误状态）；`loadList()` 在 catch 中置 `loadError=true` + `console.error('[回路列表] 加载失败:', error)`；ClpmDataCanvas 绑定 `:error="loadError"` + `@retry="loadList"`，让用户在加载失败时看到 error 态 + 重试按钮。`loadPlantNodes()` 添加 `console.error('[工厂节点] 加载失败:', error)`。批量替换 11 处 `} catch {` → `} catch (error) {`，11 处 `// 错误已由拦截器处理` → `console.error('操作失败:', error);`，让操作失败有控制台证据（拦截器仍负责 UI toast）。Promise 链 `.catch(() => {` → `.catch((error) => {` + `console.error('导入失败:', error)` | `frontend/.../views/loop/manage.vue:101,237,251-253,455,560,595,630,653,843,867,890,939,983,1081,1090,1157` | 前端类型检查通过 |
| 22 | TC1: 7 场景测试数据未被 pytest 引用 | 新增 `tests/test_metric_calculator/test_scenarios.py`（20 个测试），引用 `tests/fixtures/kpi_test_data.json` 中 7 个场景数据。session 级 `kpi_scenarios` fixture 加载 JSON；`_scenario_to_bundle()` 辅助函数将场景 data 数组转为 MetricDataBundle（pv/sp/op/mode + pv_valid 掩码）。测试覆盖：①7 场景齐全性（数据点数/control_type/ar_signal 字段）；②fast_response（settling_time ≤ 60s + fast_rate ≥ 80）；③slow_response（settling_time ≥ 30s + fast_rate < fast_response + fast_rate < 50）；④normal fast_rate ≥ 70；⑤oscillation（zero_crossings ≥ 20 + rate ≥ 0）；⑥op_saturation（type=HIGH + epsilon=5 时 rate ∈ [25,60]）；⑦normal accuracy/good_value/auto_mode（accuracy > 70, good_value ≥ 95, auto_mode ≥ 85）；⑧manual_mode（全 mode=0 + auto_rate ∈ [0,5]）；⑨pure_ar2（AR(2) 前 2 系数匹配 [-0.5,0.3] ±0.15 + 剩余 8 系数 |coeff|<0.05 + settling_time ≤ 100s）。注：测试考虑生成脚本注入的 0.5% 坏质量点 + 5% 手动段 | `backend/tests/test_metric_calculator/test_scenarios.py` | 全后端 1524 测试通过（1504 原有 + 20 新增） |
| 23 | B2: controlMode 后置过滤导致分页 total 错误 | 将 controlMode 过滤从 Python 后置过滤下沉到 SQL 层（EXISTS 子查询），让 `count_stmt` 与 `stmt` 共享同一过滤条件，`total` 自动反映全表匹配数。新增 `_control_mode_to_values(control_mode)` 反向映射函数（Manual→[0] / Auto→[1] / Cascade→[2,3]，与 `_mode_value_to_label` 保持一致）；未识别标签返回空列表，调用方据此直接返回空结果。EXISTS 子查询结构：`WHERE EXISTS (SELECT 1 FROM LoopTagMapping JOIN TagRegistry ON tag_id=TagRegistry.id WHERE loop_id=LoopLedger.id AND tag_role='MODE' AND current_value IN :mode_values)`。新增 8 个单元测试覆盖反向映射（Auto/Manual/Cascade/大小写/Unknown/空输入/双向一致性/Unknown 模式值） | `backend/app/services/loop.py:179-193,269-300` + `backend/tests/test_loop.py:259-318` | 全后端 1532 测试通过（1524 原有 + 8 新增） |
| 24+25 | B3+B4: controlType/level/modeattrTagId/dataRetentionDays 前端声明但被忽略 | 数据库迁移 `m6q7r8s9t0u1` 在 `loop_ledger` 表新增 `control_type` 字段（STABLE/SLOW/FAST/LOGIC，与 `loop_type` 业务类型独立，对齐 GB/T 44693.2-2024 附表1）。ORM 模型 `LoopLedger` 添加 `control_type` 列。`LoopCreate`/`LoopUpdate` schema 新增 `controlType`/`level`/`modeattrTagId`/`dataRetentionDays` 字段；`LoopListItem` 新增 `controlType`；`LoopBasicInfo`/`LoopUpdateResult` 新增 `controlType`/`level`/`modeattrTagId`/`dataRetentionDays`。service 层 `list_loops` 添加 `control_type` 参数 + SQL 过滤（`func.upper(LoopLedger.control_type) == control_type.upper()`）；`create_loop`/`update_loop`/`get_loop_detail` 接收并透传新字段，审计日志 before/after 包含完整字段。endpoint 层 `list_loops_endpoint` 新增 `controlType` 查询参数；`create_loop_endpoint`/`update_loop_endpoint` 透传新字段。新增 6 个测试：schema 字段存在性（Create/Update 各 1）+ service 签名（create/update/list 各 1）+ endpoint 接受 controlType 查询（1）。mock 对象 LOOP_001 补 `control_type`/`modeattr_tag_id`/`data_retention_days` 字段 | `backend/alembic/versions/m6q7r8s9t0u1_add_loop_control_type.py` + `backend/app/models/loop.py:50-55` + `backend/app/schemas/loop.py:42-77,113,140-143,208-211` + `backend/app/services/loop.py:141,168,257,319-322,359-362,395-411,495-521,533-537,553-563,590-600,618-631` + `backend/app/api/v1/endpoints/loops.py:70-72,91,120-123,298-301` + `backend/tests/test_loop.py:37-40,324-407` + `backend/tests/test_s8_supplement.py:130-133` | 全后端 1538 测试通过（1532 原有 + 6 新增） |
| 26 | B9: AasConfig 前后端字段不匹配 | 前端 `aas.ts` 对齐后端字段命名（含单位更清晰）：`AasConfig.syncInterval` → `syncIntervalSeconds`；`UpdateAasConfigParams.syncInterval` → `syncIntervalSeconds`；`AasConfigTestResult.latency` → `latencyMs`（类型同步改为 `number \| null`）。`aas.vue` 配套更新：`configForm.syncInterval` → `configForm.syncIntervalSeconds`；`data.syncInterval` → `data.syncIntervalSeconds`；模板 `FormItem name="syncInterval"` → `name="syncIntervalSeconds"`；`InputNumber v-model:value` 同步；`testResult.latency` → `testResult.latencyMs`；注释中的字段名同步更新 | `frontend/.../api/aas.ts:54-77` + `frontend/.../views/loop/aas.vue:8,53,222,239,263,269,388,390,429` | 前端类型检查通过 |
| 27 | R3: MetricConfig.weight 修改不生效 | `_build_weights_map` 函数签名新增 `metric_configs` 参数，权重解析改为三级优先链：① `MetricConfig.weight` 全局配置（管理员通过 PUT /configs/metrics 设置的 3 核心指标权重，sum=100，归一化为 a+f+s=1.0），② `LoopTypeWeight` 控制类型模板（STABLE/SLOW/FAST/LOGIC），③ `DEFAULT_WEIGHTS`（ConfidenceEvaluator 内部 STABLE 模板，返回 None 触发）。回退条件：MetricConfig.weight 任一为 null/0/缺失 → 回退到 LoopTypeWeight；type_weights 不含 score_type → 返回 None。`_calculate_loop_kpi` 调用 `_build_weights_map(type_weights, score_type, metric_configs)` 传入 metric_configs。新增 `TestBuildWeightsMapMetricConfigPriority` 测试类 8 个用例：覆盖优先（MetricConfig 全配置覆盖 LoopTypeWeight）/容错归一化（sum≠100 按比例）/部分 null 回退/含 0 回退/缺指标回退/仅 MetricConfig（type_weights=None）/metric_configs=None 兼容/双 None 返回 None | `backend/app/tasks/kpi_calc.py:1150-1223,875-880` + `backend/tests/test_kpi_calc.py:1370-1522` | 全后端 1546 测试通过（1538 原有 + 8 新增） |
| 28 | R4: 节点小时/日/月聚合权重体系不一致 | 经设计文档研究（FDS §5.3.7 + ADS + 实现契约 + 项目记忆约束）确认：两套权重体系处理不同维度的聚合，**非缺陷而是设计选择**。① 小时聚合（回路→节点小时）使用 LoopLevelWeight（1:3,2:2,3:1），依据 FDS §5.3.7 "装置级聚合评分" + GB/T 44693.2-2024 附录 E.2 + 项目记忆约束 "Plant-level"；② 日/月聚合（节点小时→节点日/月）使用 loop_count，因 KpiNodeSnapshotHourly/Daily 表不含 level 字段（节点级快照已无回路维度，无法按 LoopLevelWeight 加权），loop_count 反映节点规模与代表性。修复内容：在 `node_aggregation.py` 模块 docstring 补充完整权重体系说明（含两套权重的设计依据、目的、结构性约束、项目记忆约束边界）；新增 `TestNodeAggregationWeightDesign` 测试类 3 个用例：① loop_count 加权与简单平均产生不同结果（证明加权有意义）/② loop_count 更高的快照主导结果（验证加权方向）/③ 模块 docstring 包含关键设计说明（防回归文档） | `backend/app/services/node_aggregation.py:1-40` + `backend/tests/test_node_aggregation.py:543-601` | 全后端 1549 测试通过（1546 原有 + 3 新增） |
| 29 | B6: 自定义任务快照表缺少 sampling_freq/quality_policy | 数据库迁移 `n7q8r9s0t1u2` 在 `kpi_snapshot_custom` 表新增 `sampling_freq`（String(10) NULL）+ `quality_policy`（String(30) NULL）两列，与 `kpi_snapshot_hourly` 对齐。ORM 模型 `KpiSnapshotCustom` 添加 `sampling_freq`/`quality_policy` 两字段。`_save_custom_snapshot` 函数签名新增 `sampling_freq`/`quality_policy` 参数，existing 更新路径与 new 创建路径均写入这两字段。`_persist_snapshot` 移除剔除 `sampling_freq`/`quality_policy` 的逻辑，自定义任务路径直接透传完整 kwargs。新增 `TestSaveCustomSnapshotLineage` 测试类 3 个用例（新增写入/更新写入/未提供时 None）+ `TestPersistSnapshotLineagePassThrough` 测试类 2 个用例（custom 路径透传/standard 路径不剔除），共 5 个测试 | `backend/alembic/versions/n7q8r9s0t1u2_add_lineage_to_custom_snapshot.py` + `backend/app/models/metric.py:151-153` + `backend/app/tasks/kpi_calc.py:1500-1515,1681-1683,1730-1732,1764-1766` + `backend/tests/test_kpi_calc.py:1931-2115` | 全后端 1554 测试通过（1549 原有 + 5 新增） |
| 30 | B7: API 前缀不统一 /config vs /configs | `loop_type_weight.py` 与 `loop_level_weight.py` router prefix 从 `/config/loop-type-weights` / `/config/loop-level-weights` 统一为复数 `/configs/loop-type-weights` / `/configs/loop-level-weights`，与 `/configs/metrics` / `/configs/diagnosis` 对齐。前端 `metric.ts` 4 处 API 路径同步更新（getLoopTypeWeightsApi/updateLoopTypeWeightApi/getLoopLevelWeightsApi/updateLoopLevelWeightApi）。schema docstring `LoopTypeWeightUpdate` / `LoopLevelWeightUpdate` 引用同步。前端注释 `type-weight.vue` / `level-weight.vue` 同步。设计文档 `CLPM系统重构方案.md` 路由清单与任务清单同步。新增 4 个路由可达性测试：`TestLoopTypeWeightRoutesReachable` + `TestLoopLevelWeightRoutesReachable` 各 2 个用例（新路径 200/旧路径 404） | `backend/app/api/v1/endpoints/loop_type_weight.py:7-12,27` + `backend/app/api/v1/endpoints/loop_level_weight.py:7-12,26` + `backend/app/schemas/loop_config.py:69,96` + `frontend/.../api/metric.ts:591-637` + `frontend/.../views/metric/type-weight.vue:9` + `frontend/.../views/metric/level-weight.vue:9` + `docs/设计文档/04-重构方案/CLPM系统重构方案.md:208-209,458-459` + `backend/tests/test_api_configs.py:636-695` | 全后端 1558 测试通过（1554 原有 + 4 新增）；前端类型检查通过 |
| 31 | B8: 前端路由与实现契约不一致 + 孤儿视图 | 经文档研究确认：代码遵循 UI/UX 改造方案 v1.0 §6.1.4 "5 Tab 聚合"设计（指标定义/权重配置/引擎规则/任务策略/执行记录），`type-weight.vue` / `level-weight.vue` 是 `weight-config.vue` 的子组件（非孤儿视图）。问题源于实现契约 §2 路由清单未同步 UI/UX 改造方案 v1.0 的合并决策，仍列旧路由 `/metric/type-weight` / `/metric/level-weight`。修复内容：实现契约 §2 性能评估行的"当前主要路由"更新为 `/metric/dashboard`、`/metric/ranking`、`/metric/statistics`、`/metric/config`、`/metric/weight-config`、`/metric/engine-config`、`/metric/task-strategy`、`/metric/tasks`；§3 路由命名决策新增"指标配置 Tab 聚合"行，说明合并决策依据与子组件关系。无代码改动（代码已正确），仅文档追认 | `docs/设计文档/00-BASELINE/implementation-contract.md:27,38` | 前端类型检查通过（无代码改动） |
