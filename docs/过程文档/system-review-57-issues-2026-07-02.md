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
| 9 | 回路管理 | R1: 单删（硬删+校验Tag）与批删（软删+不校验Tag）行为不一致 | `endpoints/loops.py` + `services/loop_batch.py` | 待修复 |
| 10 | 回路管理 | R2: `batch_update_loops` 的 `is_stat_enabled` 复用 `is_active` 语义，与 `is_monitored` 写入冲突 | `services/loop_batch.py:75-77,133-136` | 待修复 |
| 11 | 性能评估 | R1: 标准任务 `trigger_standard_evaluation` 接收 `body.tsStart` 但调用 `calculate_hourly_kpi.delay()` 时未传参，用户指定时间窗被忽略 | `endpoints/tasks.py:310` | 待修复 |
| 12 | 性能评估 | R2: 自定义任务 `ts_end` 参数存入 Redis 但未传给 Celery 任务，自定义任务时间窗固定为 `cycle_minutes` 长度 | `endpoints/tasks.py:404` | 待修复 |
| 13 | 跨模块 | B3: 实现契约 §6 状态机声称 `ACTIVE/PAUSED/DECOMMISSIONED`，实际代码为 `READY/PARTIAL/INACTIVE` | `docs/.../implementation-contract.md` §6 | 待修复 |
| 14 | 跨模块 | B4: 节点级聚合 `KPI_FIELDS` 仅含 9 项，缺失 `stiction_coeff`/`steady_state_time`/`output_travel_index`/`ideal_settling_time` | `services/node_performance.py:38-48` | 待修复 |
| 15 | 跨模块 | B5: 节点级实时自控率绕过 DataPlanner 直查 TDengine（每回路并发 5 分钟窗口查询），不享缓存且硬编码 `DEFAULT_AUTO_MODES={1,2,3}` | `services/node_performance.py:115-195` | 待修复 |
| 16 | 算法 | 偏差3: `settling_time.py` MIN_POINTS=30，设计要求 100；30 点 AR(10) 模型自由度不足，影响快速率 F | `metric_calculator/settling_time.py:30` | **已修复** |
| 17 | 算法 | 偏差5: `ideal_settling_time.py` 默认值 TC=300(应180)/LC=300(应600)/CC=600(应300)，影响快速率 F 基准 T' | `metric_calculator/ideal_settling_time.py:27-33` | **已修复** |
| 18 | 算法 | 偏差1: R 缺失时降级为基础评分 60%，设计文档 §4.10 未定义此降级逻辑，60% 系数缺乏依据 | `confidence_evaluator.py:222-225` | **已修复** |
| 19 | UX | UX3: `preferences.ts` 中 `THEME_COLORS` 为静态常量（不响应主题切换），`detail.vue` 直接使用而非 `useClpmTheme()` | `preferences.ts:38-49` | 待修复 |
| 20 | UX | UX6: 批量配置入口隐藏在独立 Tab 中，需跨 Tab 操作（选中回路→切换Tab→打开弹窗），流程不直观 | `views/loop/manage.vue:1129-1139` | 待修复 |
| 21 | UX | UX7: 大量 `catch {}` 静默吞错，页面状态不一致且无错误引导，`ClpmDataCanvas` 的 error/retry 能力未使用 | `views/loop/manage.vue` 多处 | 待修复 |
| 22 | 测试 | TC1: 7 场景测试数据（7200点×7）已生成于 `fixtures/kpi_test_data.json`，但**没有任何 pytest 测试引用**，项目记忆硬约束在 CI 中未被验证 | `tests/fixtures/kpi_test_data.json` | 待修复 |

---

## P2 中优先级问题（19 项）

| # | 模块 | 问题 | 文件 | 状态 |
|---|---|---|---|---|
| 23 | 回路管理 | B2: controlMode 后置过滤导致分页 total 返回当前页过滤后条数，大结果集无法翻页 | `services/loop.py:247-250` | 待修复 |
| 24 | 回路管理 | B3: 前端 `LoopQueryParams` 声明 `controlType` 参数但后端未实现，筛选被静默忽略 | `frontend/src/api/loop.ts:109` | 待修复 |
| 25 | 回路管理 | B4: `create_loop` 不接收 `level`/`modeattr_tag_id`/`data_retention_days`，前端声明但被忽略 | `endpoints/loops.py:96-117` | 待修复 |
| 26 | 回路管理 | B9: AasConfig 前后端字段不匹配（`syncInterval` vs `syncIntervalSeconds`、`latency` vs `latencyMs`） | `frontend/src/api/aas.ts` vs `schemas/aas.py` | 待修复 |
| 27 | 性能评估 | R3: `MetricConfig.weight` 字段存在并校验总和=100，但综合评分实际使用 `LoopTypeWeight`，管理员修改不生效 | `services/performance.py:194` vs `kpi_calc.py:1105` | 待修复 |
| 28 | 性能评估 | R4: 节点小时聚合用 `LoopLevelWeight`(1:3,2:2,3:1)，日/月聚合用 `loop_count`，权重体系不一致 | `node_performance.py:261` vs `node_aggregation.py:88` | 待修复 |
| 29 | 跨模块 | B6: 自定义任务快照表 `kpi_snapshot_custom` 缺少 `sampling_freq`/`quality_policy` 字段，数据血缘追溯能力弱于标准任务 | `tasks/kpi_calc.py:1415-1420` | 待修复 |
| 30 | 跨模块 | B7: API 前缀不统一（`/config/loop-type-weights` 单数 vs `/configs/metrics` 复数） | `endpoints/loop_type_weight.py:23` vs `endpoints/configs.py:48` | 待修复 |
| 31 | 跨模块 | B8: 前端路由与实现契约 §2 不一致（`/metric/weight-config` vs 契约 `/metric/type-weight`），孤儿视图 `type-weight.vue`/`level-weight.vue` | `router/routes/modules/metric.ts` | 待修复 |
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
| P1 高优先级 | 14 | 3 | 11 |
| P2 中优先级 | 19 | 0 | 19 |
| P3 低优先级 | 16 | 0 | 16 |
| **合计** | **57** | **9** | **48** |

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
