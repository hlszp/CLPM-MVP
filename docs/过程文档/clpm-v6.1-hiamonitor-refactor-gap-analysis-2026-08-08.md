# CLPM v6.1 HiaMonitor 借鉴重构计划 — 代码实现比对分析报告

**文档状态**: 初版
**分析日期**: 2026-08-08
**比对基准**: [CLPM_v6.1_HiaMonitor借鉴重构计划.md](file:///home/zhangping/CLPM/docs/设计文档/CLPM_v6.1_HiaMonitor借鉴重构计划.md) v1.1（2026-07-23）
**分析范围**: 后端 `backend/app/`、前端 `frontend/apps/web-antd/src/`、Alembic 迁移
**分析方式**: 静态代码审查（严禁任何代码变更，本报告仅作差异记录与必要性评估）

---

## 0. 执行摘要

设计文档 v1.1 规划了 Phase 0–5 共 6 个阶段的增量重构。经逐项比对，**核心交付物已大部分落地**：

- **Phase 0（配置化基础设施）**: 主体完成，`algorithm_parameter` 表 + 三层合并链 + 配置 UI + 审计均已实现，但**回路级覆盖层缺失**、4 项硬编码参数未配置化。
- **Phase 1（新增指标）**: 14 个新指标计算器 + 双快照表字段 + 数据需求契约 + 聚合策略全部落地；**`time_constant`（时间常数）仅有 DB 列、无计算器**，恒为 NULL。
- **Phase 2（仪表故障 + 抗扰性）**: 完整落地，且仪表故障率额外实现了 FROZEN 复合判据（超越设计）；抗扰性分析开关可控、默认关闭。
- **Phase 3/4（复杂回路 + PID 模板）**: 后端数据模型/API/聚合去重完整；前端**复杂回路树形展示缺失**（仅扁平标签），PID 模板以抽屉组件实现而非独立页面。
- **前端综合评估页**: 雷达图/散点图/统计柱状图/故障率卡片均已嵌入回路性能页；但**全局看板缺少阀门运行区间告警、趋势图多选对比、置信度标记**。

此外，实现相对设计存在若干**正向偏离**（可信度阈值配置化本被设计列为 P2 延后项，实际已完整实现并配 Redis pub/sub 多进程同步）和**架构偏离**（`algorithm_parameter` 表结构由"逐参数行"改为 `metric_code × control_type` 的 JSONB 宽表）。

> **校正说明（2026-08-08 二次核查）**：初版将"MIN_GOOD_RATIO 与可信度 D 阈值耦合"列为 P0 风险。经深入核查，`MIN_GOOD_RATIO` 在 [kpi_calc.py:62](file:///home/zhangping/CLPM/backend/app/tasks/kpi_calc.py#L62) 中**已成为死代码**（全后端无任何引用），实际的指标级 INCONCLUSIVE 阈值已迁移至 [base.py:39](file:///home/zhangping/CLPM/backend/app/services/metric_calculator/base.py#L39) `_INCONCLUSIVE_THRESHOLD=0.20`。v6.2 可信度统一改造（P2-2）采用两层防御架构——指标级可计算性阈值（固定 0.20，决定"能否算出值"）与回路级可信度分级（可配置 D 阈值，决定"结果是否可信"）语义分离，综合评分层同时检查两者，**已从架构上缓解了设计文档 §2.5 预警的耦合风险**。详见 §4.4。

**总体结论**: 设计规划的技术骨架已贯通，功能完整度约 **85%**。剩余缺口集中在"最后一公里"的前端体验增强、1 个未实现指标（time_constant）、回路级配置覆盖层，以及 4 项硬编码算法参数的配置化收尾。无 P0 级阻断问题。

---

## 1. 分析方法

1. 提取设计文档 §2–§8 的全部功能点、表结构、接口、参数清单、前端布局要求。
2. 在后端按"模型 → 迁移 → 计算器注册表 → 编排层 → 服务 → API"链路逐层核查。
3. 在前端按"路由 → 页面 → 组件 → API 客户端"链路核查。
4. 对每项标注：✅ 已实现 / ⚠️ 部分实现 / ❌ 未实现 / 🔄 实现偏离设计。
5. 对未实现/偏离项做四维必要性评估：功能完整性、性能、可维护性、未来扩展性。

---

## 2. 分阶段实现状态总览

| 阶段 | 设计交付物 | 状态 | 完成度 | 说明 |
|------|-----------|------|--------|------|
| Phase 0 | `algorithm_parameter` 表 | 🔄 偏离 | 90% | 表已建但 schema 由逐参数行改为 JSONB 宽表 |
| Phase 0 | 三层配置优先级链 | ⚠️ 部分 | 67% | 系统级+指标级已实现；**回路级覆盖缺失**（实际为 4 层：代码默认+表+threshold） |
| Phase 0 | 7 项硬编码参数配置化 | ⚠️ 部分 | 2/7+1死代码 | similarity/confidence thresholds 已配置化（含超额）；4 项仍硬编码；MIN_GOOD_RATIO 已成为死代码待清理（见 §4.4） |
| Phase 0 | 配置 UI（分组/范围校验/重置/审计） | ⚠️ 部分 | 80% | 分组+范围校验+审计已有；仅覆盖 3 指标；重置默认未见 |
| Phase 1 | 14 个新指标计算器 | ✅ | 14/14 | 全部注册并接入编排 |
| Phase 1 | `time_constant` 指标 | ❌ | 0% | DB 列存在，无计算器，恒 NULL |
| Phase 1 | 双快照表字段同步 | ✅ | 100% | 两表均含全部新字段（含 time_constant 占位列） |
| Phase 1 | 三处注册点 | ✅ | 100% | 注册表/DB映射/数据需求契约均到位 |
| Phase 1 | 聚合策略（AGGREGATABLE/DISPLAY_ONLY） | ✅ | 100% | instrument_fault_rate 入 AGGREGATE_FIELDS，其余不参与 |
| Phase 2 | InstrumentFaultRateCalculator | ✅ | 100%+ | 复用 outlier_reasons，额外实现 FROZEN 复合判据 |
| Phase 2 | 抗扰性分析可选分支 | ✅ | 100% | disturbance.py + 开关默认关闭，零回归 |
| Phase 3 | 复杂回路数据模型 | 🔄 偏离 | 100% | 用 group_id+complex_role(MAIN/SUB) 替代设计的枚举类型+parent_loop_id |
| Phase 3 | 聚合去重（RFC） | ✅ | 100% | node_performance.py 实现按 group 去重、MAIN 代表 |
| Phase 3 | 复杂回路 API | ✅ | 100% | complex-groups + 批量分组 + CRUD |
| Phase 3 | 前端树形展示 | ❌ | 0% | 仅扁平表格+主副标签，无树形层级 |
| Phase 4 | dcs_pid_structure 表 | ✅ | 100% | 与设计一致，含 CHECK 约束 |
| Phase 4 | PID 结构 API | ✅ | 100% | 完整 CRUD |
| Phase 4 | PID 模板前端页面 | ⚠️ 部分 | 70% | 以抽屉组件实现，非设计所述独立页面 |
| Phase 5 | 综合评估页（雷达/散点/统计/故障卡） | ✅ | 90% | 回路性能页综合评估 Tab 已含全部图表 |
| Phase 5 | 全局看板故障卡 | ✅ | 100% | pid-dashboard 仪表盘含仪表故障率（inverted 配色） |
| Phase 5 | 全局看板阀门运行区间告警 | ❌ | 0% | 未见 |
| Phase 5 | 趋势图多选指标对比(≤5) | ❌ | 0% | 趋势图指标固定，无用户多选 |
| Phase 5 | 看板置信度等级标记 | ❌ | 0% | 未见置信度标记 |
| Phase 5 | 批量配置评价周期/算法参数 | ❌ | 0% | 批量仅支持监控/参评/重要等级 |
| §2.5 | 可信度阈值配置化（P2 延后） | ✅ 超额 | 100% | 设计列为延后，实际已实现（Redis pub/sub 同步） |

---

## 3. 未实现功能清单与必要性评估

### 3.1 ❌ time_constant（时间常数）指标计算器缺失

**设计要求** (§3.1): 新增 `time_constant` 指标（L1，DISPLAY_ONLY，P1），DB 字段 `Numeric(8,2)`，单位秒。

**实际状态**:
- [metric.py:119](file:///home/zhangping/CLPM/backend/app/models/metric.py#L119) 与 [metric.py:205](file:///home/zhangping/CLPM/backend/app/models/metric.py#L205) 两张快照表均有 `time_constant` 列。
- [kpi_calc.py:2063](file:///home/zhangping/CLPM/backend/app/tasks/kpi_calc.py#L2063) 注释明确写"time_constant 无计算器，保持 NULL"。
- CALCULATOR_REGISTRY（[__init__.py:49-77](file:///home/zhangping/CLPM/backend/app/services/metric_calculator/__init__.py#L49-L77)）无 time_constant 注册。
- `clpm_metric_data_requirement` 表无该契约（迁移 [c588a06c1c05](file:///home/zhangping/CLPM/backend/alembic/versions/c588a06c1c05_seed_phase1_metric_data_requirement_.py#L11) 注释"time_constant 计算器延后故契约暂不插入"）。

**必要性评估**:

| 维度 | 影响 | 等级 |
|------|------|------|
| 功能完整性 | 中 — 时间常数是过程动态特性关键指标，HiaMonitor 对照表列为 P1 缺失项；但属 DISPLAY_ONLY 不影响综合评分 | 中 |
| 性能 | 无 — 不新增计算负载 | 低 |
| 可维护性 | 低 — DB 列空置不影响现有逻辑，但形成"死列"技术债，后续开发者可能困惑 | 低 |
| 未来扩展性 | 中 — 回路整定 Phase 2 已有过程对象辨识（ARX/ARMAX/IV）产出时间常数，具备复用基础；补齐可形成"辨识→评估"闭环 | 中 |

**结论**: 建议补齐。整定模块 [tuning_identification/nonparametric.py](file:///home/zhangping/CLPM/backend/app/services/tuning_identification/nonparametric.py) 已有时间常数辨识能力，可抽取为独立 L1 计算器，复用现有 DataPlanner 管线，成本低。

---

### 3.2 ❌ 回路级算法参数覆盖层（loop_ledger.algorithm_params）缺失

**设计要求** (§2.2.3): `loop_ledger` 新增 `algorithm_params JSONB` 字段，作为三层配置链最高优先级（回路级覆盖）。参数加载函数签名为 `get_algorithm_param(param_code, loop_id, metric_code)`。

**实际状态**:
- [loop.py](file:///home/zhangping/CLPM/backend/app/models/loop.py) 模型中**无** `algorithm_params` 字段。
- [algorithm_config.py:140](file:///home/zhangping/CLPM/backend/app/services/algorithm_config.py#L140) `get_algorithm_params(metric_code, control_type)` 签名无 `loop_id` 参数，合并链为三层：代码 `_DEFAULTS` → `algorithm_parameter` 表 → `metric_config.threshold`。
- 计算器调用处（如 [fast_rate.py:98](file:///home/zhangping/CLPM/backend/app/services/metric_calculator/fast_rate.py#L98)）均未传入回路级覆盖。

**必要性评估**:

| 维度 | 影响 | 等级 |
|------|------|------|
| 功能完整性 | 中 — 无法对特殊回路（如已知非线性、特殊采样率）做参数微调；但系统级+指标级已覆盖绝大多数场景 | 中 |
| 性能 | 低 — 回路级需在热路径按 loop_id 查缓存，当前进程内缓存设计可扩展支持 | 低 |
| 可维护性 | 中 — 缺失导致特殊回路只能通过全局参数妥协，长期积累"全局参数为个别回路调优"的腐化 | 中 |
| 未来扩展性 | 中 — 复杂回路（串级主副）可能需要差异化参数，回路级覆盖是前置基础 | 中 |

**结论**: 非紧急。当前两层配置已能满足标准化交付；建议在复杂回路实际投用或出现参数调优诉求时补齐，需同步扩展缓存 key 为 `(metric_code, control_type, loop_id)`。

---

### 3.3 ❌ 4 项硬编码参数未配置化

**设计要求** (§2.1): 以下参数应 P0 配置化：

| 参数 | 当前值 | 位置 | 状态 |
|------|--------|------|------|
| `MIN_ZERO_CROSSINGS` | 4 | [oscillation.py:39](file:///home/zhangping/CLPM/backend/app/services/metric_calculator/oscillation.py#L39) | ❌ 仍硬编码（[L89](file:///home/zhangping/CLPM/backend/app/services/metric_calculator/oscillation.py#L89) 直接引用） |
| `SETTLING_THRESHOLD` | 0.05 | [settling_time.py:44](file:///home/zhangping/CLPM/backend/app/services/metric_calculator/settling_time.py#L44) | ❌ 仍硬编码（[L104](file:///home/zhangping/CLPM/backend/app/services/metric_calculator/settling_time.py#L104)、[L116](file:///home/zhangping/CLPM/backend/app/services/metric_calculator/settling_time.py#L116)） |
| `DEFAULT_E_MAX_RATIO` | 0.05 | [effective_auto.py:27](file:///home/zhangping/CLPM/backend/app/services/metric_calculator/effective_auto.py#L27) | ❌ 仍硬编码（[L166](file:///home/zhangping/CLPM/backend/app/services/metric_calculator/effective_auto.py#L166)） |
| `TRIP_INACTIVE/NORMAL/FREQUENT` | 0.01/0.1/1.0 | [output_trip.py:29-31](file:///home/zhangping/CLPM/backend/app/services/metric_calculator/output_trip.py#L29-L31) | ❌ 仍硬编码（[L123-127](file:///home/zhangping/CLPM/backend/app/services/metric_calculator/output_trip.py#L123-L127)） |

> 注：`SIMILARITY_THRESHOLD` 已配置化（[oscillation.py:81](file:///home/zhangping/CLPM/backend/app/services/metric_calculator/oscillation.py#L81)），`DEFAULT_CONFIDENCE_THRESHOLDS` 已超额实现配置化。

[algorithm_config.py:37-93](file:///home/zhangping/CLPM/backend/app/services/algorithm_config.py#L37-L93) `_DEFAULTS` 仅覆盖 3 个指标（oscillation_rate/fast_rate/accuracy_rate），未含 settling_time/effective_auto_rate/output_trip_index。

**必要性评估**:

| 维度 | 影响 | 等级 |
|------|------|------|
| 功能完整性 | 中 — 这 4 项直接影响振荡判定灵敏度、稳态收敛容差、有效自控偏差带、行程分级边界，不同装置特性差异大时需调优 | 中 |
| 性能 | 无 | 低 |
| 可维护性 | 中 — 硬编码意味着调优必须改代码重启，违背"全面配置化"原则；运维人员无法自助调整 | 中 |
| 未来扩展性 | 中 — 控制类型（STABLE/SLOW/FAST/LOGIC）差异化阈值是国标对齐方向，硬编码阻碍该能力 | 中 |

**结论**: 建议补齐。改动模式已有现成范式（参照 oscillation_rate 的 `get_algorithm_params` 接入），仅需在 `_DEFAULTS` 增加 3 个指标配置块 + 计算器内替换常量引用 + 前端算法参数页扩展元数据。stiction.py 内也有一份 `MIN_ZERO_CROSSINGS`（[stiction.py:60](file:///home/zhangping/CLPM/backend/app/services/metric_calculator/stiction.py#L60)）可一并收敛。

---

### 3.4 ❌ 复杂回路前端树形展示缺失

**设计要求** (§5.3, §7.3): `loop/manage.vue` 增加树形展示与"创建复杂回路"入口。

**实际状态**:
- [manage.vue](file:///home/zhangping/CLPM/frontend/apps/web-antd/src/views/loop/manage.vue) 有复杂回路分组列（[L1580-1587](file:///home/zhangping/CLPM/frontend/apps/web-antd/src/views/loop/manage.vue#L1580-L1587)）显示主/副标签，有"批量分组"按钮（[L1173](file:///home/zhangping/CLPM/frontend/apps/web-antd/src/views/loop/manage.vue#L1173)）。
- 但回路列表为**扁平表格**，无主从层级展开/树形缩进展示串级主副关系。
- 左侧工厂树（PlantNodeTree）是装置/单元层级，非回路间主从关系。

**必要性评估**:

| 维度 | 影响 | 等级 |
|------|------|------|
| 功能完整性 | 中 — 用户难以直观识别哪些回路构成串级/超驰组，主副关系只能靠标签颜色推断 | 中 |
| 性能 | 无 | 低 |
| 可维护性 | 低 | 低 |
| 未来扩展性 | 中 — NooM（多输出）、选择串级等更复杂拓扑需要树形/图形式展示，扁平表格无法承载 | 中 |

**结论**: 复杂回路当前 Phase 仅支持 MAIN/SUB 二元组，扁平标签尚可接受；若要支持设计 §5.1 所列的 8 种复杂回路类型（CASCADE_MASTER/SLAVE、NOOM、OVERRIDE_HIGH/LOW 等），树形展示是必需的。建议随复杂回路类型扩展一并实施。

---

### 3.5 ❌ 全局看板阀门运行区间异常告警缺失

**设计要求** (§7.2): 全局看板新增阀门运行区间异常告警。

**实际状态**: [pid-dashboard.vue](file:///home/zhangping/CLPM/frontend/apps/web-antd/src/views/metric/pid-dashboard.vue) 含仪表故障率仪表盘，但 grep `阀门|valve|op_min|op_max|告警` 无匹配，未见阀门运行区间异常卡片或告警。

**必要性评估**:

| 维度 | 影响 | 等级 |
|------|------|------|
| 功能完整性 | 低-中 — 阀门长期在极限区间运行是粘滞/磨损前兆，后端 `valve_op_min/valve_op_max` 数据已具备，缺前端呈现 | 低-中 |
| 性能 | 无 | 低 |
| 可维护性 | 低 | 低 |
| 未来扩展性 | 低 | 低 |

**结论**: 锦上添花项。后端数据齐备，前端加一个告警卡片即可，成本低。

---

### 3.6 ❌ 趋势图多选指标对比（≤5）缺失

**设计要求** (§7.1, §7.2): 综合评估页与全局看板趋势图支持多选指标对比（≤5），含稳定率/准确率/故障率等复选。

**实际状态**:
- 回路性能页历史趋势（[loop-performance.vue:996](file:///home/zhangping/CLPM/frontend/apps/web-antd/src/views/metric/loop-performance.vue#L996)）渲染固定的"综合评分(柱) + 准确率/快速率/平稳率/有效自控率(线)"，无用户多选。
- 全局看板趋势图（[pid-dashboard.vue](file:///home/zhangping/CLPM/frontend/apps/web-antd/src/views/metric/pid-dashboard.vue)）指标固定。

**必要性评估**:

| 维度 | 影响 | 等级 |
|------|------|------|
| 功能完整性 | 中 — 工程师排查问题时常需对比故障率与振荡率/饱和率的时序相关性，固定指标无法满足 | 中 |
| 性能 | 低 — 多选需控制 ≤5 条序列避免渲染压力，数据已在快照表 | 低 |
| 可维护性 | 低 | 低 |
| 未来扩展性 | 低 | 低 |

**结论**: 体验增强项，建议实施。ECharts 已在用，加 checkbox group + 动态 series 即可。

---

### 3.7 ❌ 批量配置评价周期/算法参数缺失

**设计要求** (§7.3): 回路管理页支持批量配置评价周期、批量配置算法参数。

**实际状态**: [manage.vue:648-753](file:///home/zhangping/CLPM/frontend/apps/web-antd/src/views/loop/manage.vue#L648-L753) 批量配置仅支持 `isMonitored`/`isStatEnabled`/`importanceLevel`/`includeInEvaluation` 四项，无评价周期、无算法参数批量覆盖。

**必要性评估**:

| 维度 | 影响 | 等级 |
|------|------|------|
| 功能完整性 | 中 — 回路数量多时（设计目标规模化交付），逐回路配置算法参数不可行 | 中 |
| 性能 | 无 | 低 |
| 可维护性 | 低 | 低 |
| 未来扩展性 | 中 — 与回路级覆盖层（3.2）联动，二者需同步设计 | 中 |

**结论**: 依赖 3.2 回路级覆盖层落地后才有"批量配置算法参数"的载体。评价周期批量配置可独立先行。

---

## 4. 架构与设计差异点

### 4.1 🔄 algorithm_parameter 表结构偏离设计

**设计** (§2.2.1): 逐参数行模型，每个参数一行，含 `parameter_code`/`parameter_name`/`category`/`default_value`/`min_value`/`max_value`/`unit`。

**实际** ([algorithm_parameter.py](file:///home/zhangping/CLPM/backend/app/models/algorithm_parameter.py)): 按 `metric_code × control_type` 唯一约束，参数以 `params JSONB` 宽存储，含 `is_enabled`/`version`（乐观锁）/`updated_by`。

**差异分析**:

| 对比项 | 设计方案 | 实际方案 | 评价 |
|--------|---------|---------|------|
| 存储粒度 | 每参数一行 | 每指标×控制类型一行，JSONB 存多参数 | 实际方案减少行数，读写一次拿到全部参数 |
| 范围约束 | DB 层 `min_value`/`max_value` 列 | 前端硬编码 min/max（[algorithm-params.vue:63-168](file:///home/zhangping/CLPM/frontend/apps/web-antd/src/views/metric/algorithm-params.vue#L63-L168)） | 实际方案把校验约束放到前端，DB 层无强约束，**是退步** |
| 参数元数据 | `unit`/`description`/`category` 内置 | `description` 有，`unit`/`category` 无 | 元数据缺失，UI 需前端维护 |
| 扩展性 | 加参数需插行 | 加参数只需 JSONB 加 key | 实际方案更灵活 |
| 可查询性 | 可 SQL 直接查某参数 | 需 JSONB 操作符 | 设计方案更易 SQL 分析 |

**必要性评估**:

| 维度 | 影响 | 等级 |
|------|------|------|
| 功能完整性 | 低 — 功能等价 | 低 |
| 性能 | 低 — JSONB 读写在 PG 中高效 | 低 |
| 可维护性 | **中** — min/max 约束散落在前端而非 DB/服务端，多端写入（API/脚本/迁移）时无防护，可能写入越界值 | 中 |
| 未来扩展性 | 低 — JSONB 反而是优势 | 低 |

**结论**: 架构偏离可接受，但建议把参数元数据（min/max/unit）下沉到服务端 schema 校验层（Pydantic model），不要只靠前端。当前 API 层 [algorithm_config.py](file:///home/zhangping/CLPM/backend/app/api/v1/endpoints/algorithm_config.py) 的 `AlgorithmParamsSaveRequest` 未见服务端值域校验。

---

### 4.2 🔄 复杂回路数据模型简化（RFC 方案 A）

**设计** (§5.1): `loop_ledger` 新增 `complex_loop_type`（8 值枚举）+ `parent_loop_id`（FK）+ `complex_loop_group_id`。

**实际** ([loop.py:139-187](file:///home/zhangping/CLPM/backend/app/models/loop.py#L139-L187)): 仅 `complex_loop_group_id` + `complex_role`（MAIN/SUB 二值），无 `complex_loop_type` 枚举、无 `parent_loop_id`。CHECK 约束保证二者同时为空或同时非空。

**差异分析**:
- 实际方案是设计 §5.2 所述"RFC 决策点 1 方案 A"的落地，用 group+role 泛化表达替代了具体类型枚举。
- 优点：模型更简洁，聚合去重逻辑（[node_performance.py:259-284](file:///home/zhangping/CLPM/backend/app/services/node_performance.py#L259-L284)）只需按 group_id 去重。
- 不足：无法区分串级/超驰/NooM/选择串级等具体拓扑类型，前端也无法据此渲染不同拓扑图标。

**必要性评估**:

| 维度 | 影响 | 等级 |
|------|------|------|
| 功能完整性 | 中 — 当前仅支持主副二元组，设计所列 8 种类型无法表达 | 中 |
| 性能 | 无 | 低 |
| 可维护性 | 低 — 简化模型反而更易维护 | 低 |
| 未来扩展性 | **中-高** — NooM（N 个输出）/选择串级需要更丰富的角色语义，MAIN/SUB 不够用 | 中-高 |

**结论**: MVP 阶段合理。若产品路线包含超驰/NooM，需在复杂回路 Phase 4 实施时补充 `complex_loop_type` 字段或独立的组成员表（member_role 枚举）。

---

### 4.3 🔄 oscillation_amplitude 层级归属偏离

**设计** (§3.1): `oscillation_amplitude` 为 L2，`depends_on=oscillation_rate`。

**实际**:
- [setpoint_crossing.py](file:///home/zhangping/CLPM/backend/app/services/metric_calculator/setpoint_crossing.py) 中 `OscillationAmplitudeCalculator` 独立计算 `mean(|PV-SP|)`，不读取 oscillation_rate 结果。
- [kpi_calc.py:116-119](file:///home/zhangping/CLPM/backend/app/tasks/kpi_calc.py#L116-L119) `_LAYER2_DEPENDENCIES` 未登记 oscillation_amplitude，它作为 L1 计算。

**差异分析**: 实现选择了更简单的独立计算路径。设计将其列为 L2 可能是预期"从振荡检测结果中提取幅值"，但 `mean|PV-SP|` 本身无需依赖振荡率判定即可计算，功能等价。

**必要性评估**: 无功能影响。若未来要计算"振荡段内幅值"（仅在检测到振荡时统计），则需改为 L2 依赖。当前实现可接受。

---

### 4.4 ⚠️ MIN_GOOD_RATIO 死代码与 INCONCLUSIVE 阈值架构（已缓解，非 P0）

**设计预警** (§2.5): 可信度阈值 A:0.95/B:0.80/C:0.60/D:0.20 与 `kpi_calc.py` 的 `MIN_GOOD_RATIO=0.20` 耦合，配置化需联动修改两者并加一致性校验，否则风险高。

**实际状态（二次核查修正）**:

经全后端搜索，`MIN_GOOD_RATIO` 在整个代码库中**仅出现在定义处**（[kpi_calc.py:62](file:///home/zhangping/CLPM/backend/app/tasks/kpi_calc.py#L62)），**无任何引用**，是 v6.2 可信度统一改造（P2-2）后遗留的死代码。实际的 INCONCLUSIVE 判定已迁移至两层架构：

1. **指标级可计算性阈值**（[base.py:39](file:///home/zhangping/CLPM/backend/app/services/metric_calculator/base.py#L39)）：`_INCONCLUSIVE_THRESHOLD = 0.20`，在 `_make_result()` 中按**指标级 valid_rate**（该指标 tag 的有效点占比）判定，`< 0.20` 则 `value=None`。这是"能否算出值"的计算下限，有意固定。

2. **回路级可信度分级**（[pipeline.py:155-156](file:///home/zhangping/CLPM/backend/app/services/preprocessing/pipeline.py#L155-L156)）：`loop_valid_rate`（核心 tag pv/sp/op/mode 交集占比）经 `ConfidenceEvaluator.evaluate()` 产出 `loop_confidence_level`，使用**可配置**的 D 阈值，写入 `DataBlock.loop_confidence_level`，所有指标共享。

3. **综合评分门禁**（[confidence_evaluator.py:306-372](file:///home/zhangping/CLPM/backend/app/services/confidence_evaluator.py#L306-L372)）：核心指标 value=None **或** confidence=E 均导致综合评分 None → 快照 INCONCLUSIVE。

**耦合风险复评**：

| 场景 | 指标级阈值(固定0.20) | 回路级 D 阈值(可配) | 综合评分结果 | 是否一致 |
|------|---------------------|---------------------|-------------|---------|
| D=0.30, vr=0.25 | ≥0.20 → 有值 | <0.30 → E | E 级→评分 None | ✅ 一致（INCONCLUSIVE） |
| D=0.10, vr=0.15 | <0.20 → value=None | ≥0.10 → D | 核心指标 None→评分 None | ✅ 一致（INCONCLUSIVE） |
| D=0.20, vr=0.25 | ≥0.20 → 有值 | ≥0.20 → D | 有值且非E→评分正常 | ✅ 一致 |

两层防御在两个方向上都保证了最终快照状态正确：D 阈值调高时，E 级门禁拦截；D 阈值调低时，指标级 None 拦截。设计文档预警的"可信度 E 但仍输出评分"矛盾场景**不会发生**。

**残留问题**:

| 维度 | 影响 | 等级 |
|------|------|------|
| 功能完整性 | 无 — 两层架构已保证一致性 | 低 |
| 性能 | 无 | 低 |
| 可维护性 | **低-中** — `MIN_GOOD_RATIO` 死代码可能误导开发者以为它仍生效；`_INCONCLUSIVE_THRESHOLD` 与 D 阈值语义不同但数值相同，缺注释说明二者为何不需联动 | 低-中 |
| 未来扩展性 | 低 — 若未来要让指标级可计算性阈值也可配置，需引入独立配置项（不应复用 D 阈值，因语义不同） | 低 |

**结论**: 设计 §2.5 预警的耦合风险已被 v6.2 两层防御架构**实质性缓解**，不构成 P0。建议（P3）：① 删除 `MIN_GOOD_RATIO` 死代码；② 在 `_INCONCLUSIVE_THRESHOLD` 处补充注释说明其与回路级 D 阈值的语义区别及不需联动的理由。

---

### 4.5 🔄 setpoint_crossing_count 字段类型偏离

**设计** (§3.3): `setpoint_crossing_count Integer`。

**实际** ([metric.py:118](file:///home/zhangping/CLPM/backend/app/models/metric.py#L118)): `Numeric(10,0)`，注释说明"对齐全 Decimal 管道（_extract_kpi_values），非 Integer"。

**必要性评估**: 合理偏离。Decimal 管道统一处理避免类型转换分支，无功能影响。可接受。

---

### 4.6 ⚠️ 仪表故障率 FROZEN 复合判据（正向偏离）

**设计** (§4.2): 直接统计 outlier_reasons 中 OUT_OF_RANGE/FROZEN/JUMP 三类点数。

**实际** ([instrument_fault.py:129-195](file:///home/zhangping/CLPM/backend/app/services/metric_calculator/instrument_fault.py#L129-L195)): 在设计基础上增加了 FROZEN 复合判据——FROZEN 连续段须同时满足"持续 ≥ frozen_fault_min_minutes"且"同期 OP std > frozen_std_pct×100"才计故障，否则剔除，以抑制平稳回路的误报。还抽取了独立工具函数 `app.utils.instrument_fault_rate`。

**评估**: 这是对设计的**增强**，解决了设计评审中未充分讨论的"平稳回路 PV 低方差被误判冻结"问题。逻辑完备，缺 OP/阈值时回落旧口径避免漏报。建议同步更新设计文档以反映此改进。

---

## 5. 前端实现差异

### 5.1 PID 结构模板以抽屉替代独立页面

**设计** (§6.2): 新增 `loop/pid-template.vue` 独立页面。

**实际**: [loop/components/pid-structure-drawer.vue](file:///home/zhangping/CLPM/frontend/apps/web-antd/src/views/loop/components/pid-structure-drawer.vue) 抽屉组件，从回路编辑场景唤起。

**评估**: 抽屉更贴合工作流（配置回路时就地编辑 DCS 型号 PID 结构），无需跳转。功能等价，交互更优。可接受，建议更新设计文档。

### 5.2 综合评估页嵌入位置

**设计** (§7.1): 独立综合评估页面（参考 HiaMonitor 布局）。

**实际**: 综合评估作为 [loop-performance.vue](file:///home/zhangping/CLPM/frontend/apps/web-antd/src/views/metric/loop-performance.vue) 的抽屉 Tab（"性能详情/综合评估"双 Tab），含雷达图、PV-OP 散点图、信号统计与阀门诊断。

**评估**: 雷达图/散点图/统计柱状图/故障卡均到位（复用 diagnosis-visualization 组件），布局与设计线框图基本对应。嵌入抽屉而非独立页，减少导航跳转，符合 IA 重构"单页四区"方向。可接受。

### 5.3 配置 UI 覆盖范围

**设计** (§2.4): 算法参数配置卡片组（按 category 分组，按 control_type 切换）、范围校验、重置默认、审计入口。

**实际** ([algorithm-params.vue](file:///home/zhangping/CLPM/frontend/apps/web-antd/src/views/metric/algorithm-params.vue)):
- ✅ 按 control_type 切换、范围校验（min/max）
- ✅ 审计入口（后端写 SysAuditLog）
- ⚠️ 仅 3 个指标（oscillation_rate/fast_rate/accuracy_rate）
- ❌ 未见"重置默认"按钮（API 返回了 defaults 但前端未提供一键重置）
- ❌ 无 category 分组（参数直接平铺在指标下）

**评估**: 覆盖范围与 3.3 节硬编码参数缺口对应。补参数时同步扩展前端元数据即可。

---

## 6. 数据流程与接口差异

### 6.1 参数加载函数签名

**设计** (§2.3): `get_algorithm_param(param_code: str, loop_id, metric_code) -> float`（单参数、支持回路级）。

**实际** ([algorithm_config.py:140](file:///home/zhangping/CLPM/backend/app/services/algorithm_config.py#L140)): `get_algorithm_params(metric_code, control_type) -> dict`（返回整组参数字典，无 loop_id）。

**评估**: 批量返回 dict 减少热路径多次调用，性能更优；但缺失 loop_id 维度（对应 3.2 缺口）。

### 6.2 三层注册点完成度

设计 §3.2 要求新指标同步注册到三处：

| 注册点 | 状态 | 说明 |
|--------|------|------|
| ① CALCULATOR_REGISTRY | ✅ | 14 个新计算器已注册（[__init__.py:62-77](file:///home/zhangping/CLPM/backend/app/services/metric_calculator/__init__.py#L62-L77)） |
| ② _DB_TO_CALCULATOR_METRIC_CODE | ✅ | 映射完整（time_constant 无计算器故未映射） |
| ③ _LAYER2_DEPENDENCIES | ⚠️ | oscillation_amplitude 未登记（见 4.3，实际按 L1 独立计算，功能等价） |

### 6.3 UPSERT 写入

设计 §4.3 第 4 项要求在 UPSERT 字典新增 instrument_fault_rate 等字段。实际 [kpi_calc.py:1224-1238](file:///home/zhangping/CLPM/backend/app/tasks/kpi_calc.py#L1224-L1238)（hourly）和 [kpi_calc.py:2267-2281](file:///home/zhangping/CLPM/backend/app/tasks/kpi_calc.py#L2267-L2281)（custom update）均已写入全部新字段，valve_operating_range 通过 `_extract_valve_op_range` 拆分写入 valve_op_min/valve_op_max。✅

---

## 7. 性能指标相关

设计文档未给出具体量化性能指标（如 P95 延迟、吞吐量），仅在 §7.1 提到"时间段查询"和 §9 风险表提及"新指标误入 AGGREGATE_FIELDS 致节点看板失真"。实际实现：

- **聚合隔离**: 仅 instrument_fault_rate 入 AGGREGATE_FIELDS（[node_aggregation.py:74](file:///home/zhangping/CLPM/backend/app/services/node_aggregation.py#L74)），其余 13 项 DISPLAY_ONLY 严格不参与节点加权，符合设计风险缓解要求。✅
- **缓存策略**: 算法参数走进程内缓存 `_merged_cache`（[algorithm_config.py:109](file:///home/zhangping/CLPM/backend/app/services/algorithm_config.py#L109)），热路径不查库；可信度阈值走 Redis pub/sub 实时同步。✅
- **Decimal 统一**: setpoint_crossing_count 用 Numeric(10,0) 而非 Integer，避免 `_extract_kpi_values` 管道类型分支。✅

**未发现性能层面的实现缺口。**

---

## 8. 优先级建议汇总

> 注：二次核查后无 P0 级项。原初版 P0（MIN_GOOD_RATIO 耦合）经核实为死代码且两层架构已缓解，降级为 P3 清理项。

| 优先级 | 项目 | 对应章节 | 建议动作 |
|--------|------|---------|---------|
| **P1** | 4 项硬编码参数配置化 | 3.3 | 扩展 _DEFAULTS + 计算器接入 + 前端元数据 |
| **P1** | time_constant 计算器 | 3.1 | 复用整定辨识能力，补 L1 计算器 |
| **P2** | 回路级算法参数覆盖 | 3.2 | 加 loop_ledger.algorithm_params + 缓存扩展 |
| **P2** | 服务端参数值域校验 | 4.1 | Pydantic schema 加 min/max 校验 |
| **P2** | 趋势图多选对比 | 3.6 | 前端 checkbox + 动态 series |
| **P2** | 阀门运行区间告警 | 3.5 | 前端告警卡片 |
| **P3** | 复杂回路树形展示 | 3.4 | 随复杂回路类型扩展实施 |
| **P3** | 批量配置评价周期/算法参数 | 3.7 | 依赖回路级覆盖层 |
| **P3** | 复杂回路类型枚举扩展 | 4.2 | 随超驰/NooM 需求实施 |
| **P3** | 配置页重置默认按钮 | 5.3 | 前端小改 |
| **P3** | MIN_GOOD_RATIO 死代码清理 | 4.4 | 删除 kpi_calc.py:62 未引用常量；补充 _INCONCLUSIVE_THRESHOLD 注释 |

---

## 9. 正向偏离记录（实现超越设计）

以下实现超出了设计文档 v1.1 的规划，建议回写设计文档以保持文档与代码一致：

1. **可信度阈值配置化 + 两层防御架构**（设计 §2.5 列为 P2 延后并预警耦合风险）：已完整实现可配置阈值（sys_config 持久化 + Redis pub/sub 多进程同步 + 版本号去重 + 前端配置页），且 v6.2 可信度统一改造（P2-2）将 INCONCLUSIVE 判定重构为两层防御——指标级可计算性阈值（固定 0.20，决定"能否算值"）与回路级可信度分级（可配置 D 阈值，决定"结果是否可信"）语义分离，综合评分层同时检查两者。经二次核查验证，D 阈值调高或调低均不会产生"可信度 E 但仍输出评分"的矛盾（详见 §4.4 复评表）。设计预警的耦合风险已被架构性缓解。唯一残留是 `MIN_GOOD_RATIO` 死代码待清理。
2. **仪表故障率 FROZEN 复合判据**（见 4.6）：抑制平稳回路误报，比设计伪代码更稳健。
3. **算法参数乐观锁**（`version` 字段）：设计未提及，实际 [algorithm_parameter.py:64](file:///home/zhangping/CLPM/backend/app/models/algorithm_parameter.py#L64) 已实现。
4. **FROZEN/突变阈值复用 ControlTypeThreshold**：设计 §2.2.3 提到故障检测阈值由 ControlTypeThreshold 承载，实际 [instrument_fault.py:162](file:///home/zhangping/CLPM/backend/app/services/metric_calculator/instrument_fault.py#L162) 确实从 `get_threshold_by_sampling_freq` 读取 `frozen_fault_min_minutes`/`frozen_std_pct`，闭环完整。

---

## 10. 结论

CLPM v6.1 HiaMonitor 借鉴重构计划的**核心架构与算法骨架已高质量落地**：26 个指标计算器（14 新增）、双快照表、三层编排、配置化基础设施、仪表故障复用、抗扰性可选分支、复杂回路聚合去重、DCS PID 结构模板等均已到位，且在仪表故障误报抑制、可信度阈值配置化等方面有所增强。

剩余缺口不构成架构性障碍，主要分三类：

1. **配置化收尾（P1-P2）**：4 项硬编码参数、time_constant 计算器、回路级覆盖层、服务端值域校验，属"全面配置化"原则的未尽事项。
2. **前端体验最后一公里（P2-P3）**：趋势多选、阀门告警、复杂回路树形、批量算法参数，影响使用效率但不阻断核心闭环。
3. **代码整洁（P3）**：`MIN_GOOD_RATIO` 死代码清理（设计 §2.5 预警的耦合风险已被 v6.2 两层防御架构实质性缓解，不构成 P0）。

建议按第 8 章优先级排期，并将第 9 章正向偏离回写设计文档，避免文档与代码长期漂移。

---

*本报告由静态代码审查生成，未执行任何代码变更。所有文件引用均基于分析时（2026-08-08）的代码状态。*
