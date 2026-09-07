# 接口核查与改进方案（AD01～AD08）实施记录

日期：2026-09-07。分支：`codex/tag-timeseries-refactor`（仍基于 9ee40210，全部改动未提交）。
依据：[算法与数据接口核查及兼容改进方案](../设计文档/2026-09-06-algorithm-data-interface-amendment.md)（下称"补充方案"）。本记录承接 [P4 验收报告](2026-09-06-tag-timeseries-acceptance.md)——不重做已完成的 P0～P4。

## 1. AD 任务完成对照（补充方案 §5）

| 任务 | 落点 | 状态 | 证据 |
|---|---|---|---|
| AD01 SeriesContext | `app/contracts/series_context.py`（新）+ RawTimeSeries/DataBlock 可选字段（默认 None）+ builder 全量填充（覆盖游程/未知归因/改绑边界/数据身份）+ Provider 混合窗标 mixed | ✅ | `test_refactor_contracts.py`（契约冻结范围校正）、`test_refactor_amendment_seams.py::TestCase8`；等价测试 11/11 保持 |
| AD02 全路径透传+缓存 | pipeline→DataBlock→`_derive_from_base`→Bundle 透传；L1/L2 序列化补 `series_context` **并修复 control_type 丢失**（I06 实证 FAST→None）；L1/L2 键增数据版本分量（**仅非 legacy 追加**——legacy 键与历史缓存逐字节一致）；DataPlanner 在 L2 命中前经 router 解析版本（独立短会话） | ✅ | `TestCase9`（冷热往返/键隔离/派生透传）；e2e `TestL1ColdHotCache`（真实 Redis 冷热，control_type 生存） |
| AD03 口径统一+动态守卫 | `quality_summary` 增 `unknown_slot_count`（point 缺失=覆盖未知槽，非行数差；legacy 原口径）；kpi_calc 门禁 point 口径（expected=N、point_count=可用有效样本）；`preprocessing/input_guards.py`（新）缺口敏感动态指标守卫（登记册：time_constant；settling_time 已有分段支持沿用；其余统计类沿用既有 masked 口径——逐项登记见模块 docstring） | ✅ | `TestCase6/7`；`test_refactor_amendment_seams.py` 全 22 例 |
| AD04 诊断同轴组装 | 编排器 point 分支：全网格保留（BAD/未知行标记 `row_valid` 不删行）；异常点**标记**不剔除（`_mark_outlier_preprocessing_point`）；expected=N（上下文）、point_count=可用样本；`_scoped_operator_input` 按算子 required_signals 公共掩码同轴切片（质量算子消费完整 pv_quality 轴）；采样周期=grid_period_s；跨解释边界 gate 显式拒绝 | ✅ | `TestCase5`；e2e `test_diagnosis_chain_point_gate_passes`（真实编排器，expectedPoints=3601）；既有 `test_diagnosis_orchestrator.py` 22 例全过（legacy 零回归） |
| AD05 整定桥接 | `_point_axis_signals`：point 同轴直通（**不再二次 SP/MODE 重采样**，I02）；逐槽有效性（有限值∧validity∧各角色）→最长连续段选取（PV/OP/SP/MODE/时间同索引切片，不拼接两段）；全窗 vs 选段可信度分开记录、对外口径=全窗（I01）；MODE 未知切断段（不填 0）；`identify_from_history` 内核签名零改动 | ✅ | `TestCase2/3/4`；e2e `test_tuning_chain_point_success_and_full_rate`（真实入口 success+validRate=1.0 全窗口径）；既有 `test_tuning_history_seam.py`/`test_tuning_nan_cleaning.py` 全过（legacy helper 原行为独立保留） |
| AD06 解释配置依据 | 上下文携带 `interpretation_consistent` + rebind 分段边界（v1：改绑边界视同解释变化候选——保守近似，登记）；整定跨边界显式 BizError（分段发起）；诊断 gate 显式拒绝 | ✅ | `TestCase11`；代码内显式拒绝路径 |
| AD07 血缘与版本兼容 | DataLineage 可选 `dataset_ref`（序列化缺省兼容）；assembler 从块上下文注入；诊断 `_kpi_window_averages` 按 dataset_ref 过滤不兼容快照（全不兼容→空 dict 走既有"无 KPI 上下文"路径；不自动重算） | ✅ | `TestCase10`；`test_metric_data_bundle.py` 键集更新（补充方案 §4.1 授权的可选扩展） |
| AD08 验收矩阵 | §6 十二条用例全落地 | ✅ | 见 §2 |

## 2. §6 验收矩阵逐项结果

| # | 用例 | 结果 | 测试 |
|---|---|---|---|
| 1 | 密集参考→COV→逻辑宽表→三条真实入口 | ✅ | e2e：评估（DataPlanner→25 指标 vs legacy golden 全等，含可信度）；诊断（编排器 gate 过 + 算子执行）；整定（辨识 success、全窗口径 validRate=1.0） |
| 2 | 有限 PV/OP 局部 Bad/Uncertain | ✅ | `TestCase2`：无效有限值不进段；全窗 0.7 与选段 0.5 分开；对外=全窗 |
| 3 | MODE 同轴缺口/全未知/首点前未知 | ✅ | `TestCase3`：缺口切断段（无 0 填/外推）；全未知→无可用段；legacy helper 旧测试原样保留 |
| 4 | 三秒已知断线 vs 普通 NaN | ✅ | `TestCase4`：point 硬缺口切段不插值；`_clean_nan_segments` 原 short-NaN 插值行为独立验证不变 |
| 5 | PV 全有、SP/OP 单独缺失 | ✅ | `TestCase5`：公共掩码切片三列+时间戳同长同索引；质量轴保持全轴 |
| 6 | mask [0,1,60,61] grid=1s | ✅ | `TestCase6`：time_constant 守卫拦截（value=None+E+原因码）；连续 mask 放行；legacy 豁免；settling_time 不在册（自带均匀性检查） |
| 7 | 10 槽 7 有效 3 未知+Bad 对照 | ✅ | `TestCase7`：missing=3（上下文口径）、valid_rate=0.7；不二次乘（0.7≠0.49——point 网格恒满使 coverage=1.0 结构性保证）；legacy 同输入 missing=0 原样 |
| 8 | 单点/空窗/非整秒/TC 名义 5s | ✅ | `TestCase8`：expected_slots=1、grid_period=1.0（不回落 5s）；空窗空上下文；版本分量 legacy-v1 默认 |
| 9 | L1/L2 冷热往返/派生/导入并发 | ✅（并发部分） | `TestCase9`：L1/L2 往返（context+control_type+lineage ref）；键隔离（point≠legacy 键）；派生透传；e2e 真实 Redis 冷热（I06 场景实测修复）。**导入并发在途竞争**：失效键已接（P3-4 全量失效+版本键），实测场景沿用验收报告 A-4 登记 |
| 10 | 旧 KPI 快照与新 point 同窗 | ✅ | `TestCase10`：IN 参数恰为兼容集；全不兼容→空 dict（不混算、不自动重算） |
| 11 | 改绑/量程 0~100→0~200/编码变化 | ✅（v1 近似） | `TestCase11`：整定/诊断显式拒绝+原因；**量程单位历史版本库未建**（I07 完整闭环需解释配置历史——登记 R-1） |
| 12 | 静态统计/CONFIG 不受可选元数据连带 | ✅ | `TestCase12`：SP/MODE 缺失不阻断 PV/OP；旧构造零影响 |

## 3. 受保护文件差异申报（补充方案 §5/§7 要求逐行解释）

| 文件 | diff | 接缝性质（均为补充方案 §5 明确授权范围） |
|---|---|---|
| `contracts/data_types.py` | +13 | ① RawTimeSeries.series_context 可选字段（默认 None）；② DataBlock.series_context 可选字段；③ DataLineage.dataset_ref 可选字段+to_dict 一行。无既有字段/签名/默认值变化 |
| `services/preprocessing/pipeline.py` | +9 | ① DataBlock 构造透传 series_context 一行；② compute_quality_summary 调用传 unknown_slot_count（point 未知槽）。预处理 8 步数学零改动 |
| `services/diagnosis_orchestrator.py` | +337/-56 | 全部在补充方案 §5"diagnosis_orchestrator.py 的输入/门禁/KPI 上下文"授权接缝：point 全网格组装分支、`_mark_outlier_preprocessing_point`（标记不删行）、point 门禁实参（N/可用样本）、跨解释边界 gate 拒绝、`_scoped_operator_input` 同轴切片、`_run_operators` 分发、`_kpi_context/_kpi_window_averages` 版本过滤。**分类/融合/算子数学/阈值零改动**；legacy（无上下文）代码路径逐语句保持原样（22 例既有测试全过） |

其余触碰文件均在 §5 集中接缝白名单（data_planner.py、metric_data_bundle.py、cache/l1_datablock.py、cache/l2_bundle.py、quality_summary.py、kpi_calc.py、tuning.py、新 builder/context/guards 模块）。受保护清单 golden 已按授权更新（39 文件→同 39 文件，3 文件哈希变更如上申报）。

## 4. 公共接口签名对照（补充方案 §4.1/§7.4）

| 接口 | 状态 |
|---|---|
| `get_provider/make_query_fn/query_fn(loop_id, tag_roles, start, end, interval_s)` | 未变（P0 契约测试 30 例全绿） |
| `query_trend_data` / REST / WS DTO | 未变（OpenAPI golden 未变） |
| `RawTimeSeries/DataBlock` | 既有字段与构造不变；新增可选 `series_context`（默认 None） |
| `DataPlanner.request_bundles` | 签名不变；内部先解析数据版本再查 L2 |
| `Calculator.calculate(bundle)` | 未变；守卫在 kpi_calc 编排处（计算器零改动） |
| `OperatorInput`/算子函数 | 未变；同轴切片在编排器分发处 |
| `identify_from_history(op,pv,sp,mode,ts,...)` | 未变 |
| `evaluate_gate(point_count, expected_points, valid_rate, confidence_level)` | 未变；point 路径实参口径按 §4.2 统一 |
| `DataLineage` | 既有字段不变；新增可选 `dataset_ref` |

## 5. 本轮修正的两个真实缺陷

1. **I06 control_type 缓存丢失**：L1/L2 手工序列化均未保留 `DataBlock.control_type`（内存探针 FAST→None 复现）——本轮修复并回归（`TestCase9`+e2e 真实 Redis 冷热验证）。此缺陷在 legacy 路径同样存在（历史问题），修复对两条路径同时生效。
2. **PV_QUALITY 伪角色**（P3 已修，此处补记）：BASE/QUALITY_HF 组把 pv_quality 列当信号消费，builder 初版缺失导致 point 路径 output_trip 可信度 A→E。

## 6. 登记（未验/近似项）

| # | 项 | 说明 |
|---|---|---|
| R-1 | I07 解释配置历史 | 量程/单位/MODE 编码的历史版本库未建——v1 以改绑边界近似（保守拒绝跨边界动态输入）；现场未改过量程时无影响；完整闭环待解释配置历史（建议与绑定历史同表族演进） |
| R-2 | 导入/迟到在途查询竞争实测 | 失效机制（版本键+全量失效）已实现并单测；真实并发窗口实测沿用验收报告 A-4 |
| R-3 | 缺口敏感指标登记册 | v1 册仅 time_constant（补充方案实证项）；其余统计/比率类沿用既有 masked 口径——如后续发现新的等间隔依赖指标，逐项入册（模块 docstring 已留登记位） |
| R-4 | 环境类未验项 | 沿用 P4 验收报告 A-1~A-8/B-1（本轮未新增环境类未验项） |

## 7. 门禁记录（2026-09-07，本轮终态）

- `ruff check .` / `ruff format --check .`：✅
- `pytest -q`：**4807 passed**（+22 补充方案接缝用例），379 skipped，32 xfailed ✅
- 重构集成：**61 passed**（含新增 e2e 4 例：三链路+L1 冷热）✅
- `alembic check`：无漂移 ✅
- 受保护清单：39 文件，3 文件授权接缝变更（§3 申报）✅
- 前端：仍零改动 ✅
