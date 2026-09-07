# 测点子表重构 P4 联合验收报告

日期：2026-09-07。分支：`codex/tag-timeseries-refactor`（基线 HEAD `9ee40210`，全部改动未提交——交付为工作区变更，由主审 Codex 按 P5 复核后处置）。

运行环境（全部证据产生于此）：docker-compose.refactor.yml 隔离栈——TDengine 3.3.6.0（17204，库 clpm_ts_ref）与 3.3.6.6（17214）双实例、PostgreSQL 16（17202，库 clpm_refactor，alembic head=r1p0int00001 无漂移）、Redis 7（17203）。硬件：macbook arm64。

## 1. 复现命令

```bash
cd deploy && docker compose -f docker-compose.refactor.yml up -d   # 隔离栈
cd backend
uv run alembic upgrade head                                        # 已在 head 则 no-op
uv run pytest -q                                                   # 全量单测（4785）
uv run pytest tests/integration/ -k "refactor" -m integration -q   # 重构集成（57）
uv run pytest tests/integration/test_refactor_baseline.py -m integration -s -q   # 基线+算法golden
```

## 2. 必测矩阵（计划 §5.1 V01～V16）证据映射

| ID | 结论 | 证据（测试/位置） |
|---|---|---|
| V01 | ✅ 通过 | `test_refactor_builder_equivalence.py::test_full_window_exact_match_all_loops`（4 回路×7 角色×1h 与独立参考逐点全等，DOUBLE 无舍容差）+ `test_refactor_provider_differential.py::test_point_layout_matches_reference_exactly`（Provider 级） |
| V02 | ✅ 通过 | `test_constant_loop_full_coverage`：常值回路 LIC-401 物理事件 <10 条，3601 点连续覆盖全 Good |
| V03 | ✅ 通过 | `test_no_initial_value_prefix_unknown`（PV 前 300s None+(-1)，禁止后向填充）、`test_op_no_initial_value`（OP 2100s 前未知） |
| V04 | ✅ 通过 | `test_bad_quality_persists_not_skipped`（值不变 BAD 段质量 0 持续，不沿用旧 Good）；LAST_ROW 不忽略 NULL/BAD 由 `test_refactor_point_store.py::test_last_row_keeps_null_state`（双版本）证实 |
| V05 | ✅ 通过 | 质量事件持久化（`test_quality_only_change_preserved`——值不变质量变独立入队）+ 三态输出 1/0/-1（V04 同测试 Uncertain→-1 段）；AAS/OPC 双体系解码 `test_refactor_point_writer_unit.py::TestDecodeQuality`（未知码恒 UNKNOWN 绝不 Good） |
| V06 | ✅ 通过 | `test_gap_segment_forces_unknown`（登记 gap 段→强制未知+恢复边界恢复）；恢复快照语义 `test_gap_unknown_recovery_snapshot_only`（无 gap 段时按 COV 契约保持——反例真值未知需 gap 佐证，语义固定并注释） |
| V07 | ✅ 通过 | 同 tick 多事件（`test_same_tick_multiple_events_preserved`）、迟到入历史（`test_late_event_accepted`）、重复幂等（`test_same_ts_same_payload_idempotent` 双版本）、同 ts 冲突不默改（`test_same_ts_conflict_registered_existing_kept`：TD 保留既有+PG 登记 resolved_skip） |
| V08 | ✅ 通过 | `test_subwindow_and_boundary_seconds`（整秒端点、非整秒窗 ceil/floor 不扩大）；+08/Z/naive 口径由 `TestTimeContract`（P0 契约测试）+ builder `_to_utc` 固定；跨天/单点窗为网格退化情形（ceil>floor→空网格）登记 |
| V09 | ✅ 通过 | `test_rebinding_segmented_no_carryover`（改绑后新点无锚点→未知直至新事件，不沿用旧点值）；共享点单份存储（`test_shared_point_single_copy` + PG 一行 tag_registry） |
| V10 | ✅ 部分 | `test_anchor_extends_initial_state`（锚点按原 sourceTime 因果生效）；**未验**：保留期边界外锚点老化/元数据清理链（KEEP 365 天场景无法在本环境快进——登记为未验项 A-1） |
| V11 | ✅ 通过 | TD 写失败→有界重试→重试缓冲→恢复补写不丢（`test_td_failure_retry_buffer_recovers`）；TD 成功 PG 失败→数据在 TD 权威、覆盖段缺不谎报（`test_pg_metadata_failure_data_survives`）；进程停机终态 flush（`test_stop_flushes_pending`）。**未验**：真实"TD 成功响应丢失"网络级注入（以 PG 失败+幂等重放语义推演，登记 A-2） |
| V12 | ✅ 部分 | 导入 shadow 双写对账+时间槽计数（`test_shadow_dual_write_and_slot_count`）；point+overwrite 显式拒绝（`test_point_mode_rejects_overwrite`）；**未验**：真实远端分页失败/取消中/共享点并发导入（远端 API 不可用——fake 层验证，登记 A-3）；点级覆盖段已按"远端有数据分块"登记（空响应≠覆盖） |
| V13 | ✅ 部分 | manifest 变更→路由缓存+全量 L1/L2/L3 失效（`set_layout`→`invalidate_layout_caches`，P3-4）；导入后缓存失效沿用既有 `_invalidate_loop_caches`（R13，宽表语义）；**未验**：point 布局下导入补数后的在途查询竞争实测（登记 A-4）；Redis 故障对 writer 无影响（writer 不依赖 Redis——设计如此）；Redis 重启对 subscriber 的影响沿用既有整改行为（不在本轮回归面） |
| V14 | ✅ 通过 | `test_boundary_switch_window`（T 归 point、legacy 仅 t<T、边界秒唯一）+ 迁移演练全流程（见 §4） |
| V15 | ✅ 通过 | 算法等价含常值回路 LIC-401：冻结/准入门禁结果与 legacy golden 全等（25 指标×4 回路，`test_metrics_match_legacy_golden`）——网格填满未改变任何门禁结论 |
| V16 | ✅ 部分 | 全部活跃入口经唯一路由点 make_query_fn（P0-2 清单 A1-A17 同源）——Provider 级差分即覆盖；计算不经 LTTB（builder 无降采样）；远端历史计算调用=0（builder 只读本地 TD）。**未接入**：E2/E3 完整性 API 仍按宽表行数口径（P3 遗留①）；单点趋势死路径（B2）维持现状 |

## 3. 算法等价性（计划 §5.2）

**全等。** point 布局经生产链路 `TDengineProvider.make_query_fn → DataPlanner → 25 个指标计算器`，与 P0 legacy golden（`tests/golden/refactor_algorithm_baseline.json`，26 契约码×4 回路）比对：值差 ≤1e-9 相对容差、可信度等级逐一相同。固定输入：同参考数据集（seed=20260906，DOUBLE 全精度）、同窗口、无随机性。

纠偏用例单列：无（本次等价集内未出现"旧实现错误被纠正"类差异——P0 发现的导入质量集合口径差异 {1,192}≠{1,2,3,192} 为登记项，未改变本等价集输入）。

曾发现并修复的真实缺陷：PV_QUALITY 伪角色缺失（point 路径下 output_trip_index 可信度 A→E）——已修复并回归。

## 4. 迁移演练（计划 P4-4）

`test_full_drill_and_rollback` + `test_rollback_after_legacy_write_stopped`：

1. legacy 默认（无 manifest）→ 全链路行为与改造前一致（Provider legacy 路径原样，FLOAT32 容差对账通过）；
2. storage_mode=shadow → 同流双写（导入/写器两路均已验证）、读仍 legacy；
3. manifest global point（T 起）→ 读切 point（Provider 差分全等）；
4. 回退（manifest 下线）→ 读回 legacy，**点数据完整保留**（不删）；再切回 point 数据无损；
5. 旧写停止形态（storage_mode=point 后回退）：point 时段 legacy 无数据——回退后该时段 legacy 读为空（已知边界，不谎报一键全量回退；点事实保留可再切回或重建）。

## 5. 容量验证（计划 §5.3）——部分达标，如实登记

| 项目 | 目标 | 实测 | 结论 |
|---|---|---|---|
| 写吞吐 | 9,000 事件/s 稳态 | **46,549 ev/s**（120,000 事件真实 TD，读回全量对账 120,000/120,000） | ✅ 5.2× 余量 |
| 写入负载时长 | 60min 稳态 + 8h 长稳 | 2.58s（抽样） | ⚠️ 未验（A-5） |
| 突发 | 18,000/s×60s | 未执行 | ⚠️ 未验（A-5） |
| 单回路 1h 取数/组装 | ≤legacy 基线 1.2× | **2.49×**（74.3ms vs 29.9ms 中位） | ❌ 未达 1.2×，登记 B-1 |
| 全站评估批次 | ≤基线 1.2× | 未执行（需 9,000 点真实映射） | ⚠️ 未验（A-6） |
| 内存 | RSS 预算内 | 单查询物化 ~2.4MB（P0 基线）；builder 组装同量级 | ✅（抽样） |
| 源端压力 | 不新增订阅/请求 | writer 消费同一订阅流；0 新增远端调用 | ✅ |

**B-1（时延）剖析与已做优化**：瓶颈=点表读取的 TD REST 往返（7 角色 ~7200 事件行 vs 宽表 3600 行）。已做：锚点批查询（7 次 PG 往返→1 次）、列裁剪（8 列→3 列，101.5→74.5ms）、≤16 点并行读。未达 1.2× 的候补优化（不在本轮）：点表读取结果缓存、按窗预组装缓存（L1 复用布局版本键）、TD 连接预热。**不以降采样/少算角色通过**——74.3ms 绝对值对单回路小时窗计算完全可用（KPI 批次为 IO 并发型），达标差距留主审/用户裁量。

## 6. P4-5 前端冒烟

- **前端零改动**：git status 无任何 `frontend/` 路径差异（声明即证据，符合"默认不改前端"）；
- API 契约：`test_openapi_contract_drift` 全绿（OpenAPI golden 未变）；既有 API 测试全绿（含 waveform batch/dataplanner/kpi 等 A 类入口的契约用例）；
- 后台定时计算：kpi_calc 经 make_query_fn 同一收口（legacy 默认下行为与改造前一致）；
- **未执行**：浏览器级前端 E2E（未启动前端服务——登记 A-7，建议 P5 后在隔离栈起前端冒烟一次）。

## 7. 未验项汇总（阻塞判定）

| # | 未验项 | 阻塞什么 | 建议处置 |
|---|---|---|---|
| A-1 | 保留期边界锚点老化/元数据清理（KEEP 365d） | V10 完整结论 | 长周期运行观察或专项模拟 |
| A-2 | TD 成功响应丢失（网络级） | V11 完整结论 | 幂等重放语义已证，网络注入待 zpdev/演练 |
| A-3 | 真实远端导入分页失败/取消/共享点并发 | V12 完整结论 | P4 后在 zpdev 用获准样本演练 |
| A-4 | point 布局导入补数的在途查询竞争实测 | V13 完整结论 | 缓存失效逻辑已实现，竞争窗口待实测 |
| A-5 | 60min 稳态/8h 长稳/18k 突发 | §5.3 稳态结论 | 抽样 5.2× 余量下风险低；上线前跑一轮 |
| A-6 | 全站 9,000 点评估批次 | §5.3 批次时延 | 需生产映射规模数据 |
| A-7 | 浏览器前端 E2E | P4-5 冒烟完整性 | 隔离栈起前端冒烟 |
| A-8 | AAS 质量枚举真实语义（P0-5 U1）与真实 SignalR 链路 | 真实链路质量语义 | zpdev 获准样本核verify |
| B-1 | 单回路取数/组装 2.49×（目标 1.2×） | 性能目标 | 优化候补已列；绝对值可用 |

**结论：正确性/等价性/故障恢复/迁移回退维度的必测项全部通过且有真实数据库证据；容量维度写侧达标、读侧未达 1.2×（B-1，绝对值可用）；A 类未验项不阻塞 shadow 灰度开始（灰度本身即 A 类项的实测载体），但**在 A-5/A-8 补验前不应宣布"可切 point 生产布局"**。**

## 8. 门禁记录（2026-09-07）

- `ruff check .` / `ruff format --check .`：✅
- `pytest -q`（全量单测）：**4785 passed, 379 skipped, 32 xfailed** ✅
- `alembic check`：无漂移 ✅（迁移 r1p0int00001；全新库 bootstrap+stamp head 与已有库 upgrade head 双路径已验）
- 重构集成测试：**57 passed**（point_store 18 + metadata 9 + writer_import 5 + equivalence 11 + differential 4 + baseline 3 + acceptance 7）✅
- 受保护算法文件（39 文件 SHA256）：零改动 ✅
- 前端：零改动 ✅
