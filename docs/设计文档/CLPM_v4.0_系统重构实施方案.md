# CLPM v4.0 系统重构实施方案

| 项目 | 内容 |
|---|---|
| 文档版本 | v1.0 |
| 发布日期 | 2026-06-26 |
| 文档性质 | 重构实施指导文档 |
| 适用范围 | CLPM 后端 + 前端全栈重构 |
| 设计依据 | PRD v4.0 / FDS v4.0 / ADS v4.0 / DDS v4.0 / IDS v4.0 / 关键算法设计说明 v2.0 / 数据流程图 v4.0 |
| 数据库迁移 | ✅ 已完成（k2f3a4b5c6d7） |

---

## 一、重构目标

### 1.1 总体目标

将 CLPM 系统从 v3.x 架构升级到 v4.0 架构，核心变更：

| 变更维度 | v3.x 现状 | v4.0 目标 | 设计依据 |
|---|---|---|---|
| 数据获取 | 按 Tag 逐个查询 TDengine | DataPlanner 统一编排，tagGroup 分组 | ADS §2/§8, FDS §4, PRD §8.1 |
| 质量码处理 | 物理删除 Bad 点 | KEEP_ALL_WITH_VALIDITY + valid 标记 | FDS §5.3.1.2, PRD §5.5.1 |
| 异常值检测 | 无 | 8 类检测 + 按控制类型阈值 | 算法说明 §3.4.3-3.4.4, PRD §5.5.2-5.5.3 |
| 指标体系 | 6 大 KPI 平等加权 | 3+1+8 结构，R 作折扣因子 | PRD §5.1.1, 算法说明 §4.0 |
| 数据血缘 | 仅 algorithmVersion | 8 字段完整血缘 + A/B/C/D/E 可信度 | FDS §5.3.10, ADS §14.9, PRD §5.4 |
| 缓存策略 | setex 单条写入 | L1/L2/L3 三级缓存 + zstd + Pipeline | ADS §10.7, FDS §5.3.9 |
| 任务管理 | 仅标准任务 | 标准 + 自定义任务双轨 | PRD §4.3.7, FDS §5.3.11 |

### 1.2 设计原则

1. **文档驱动**：所有代码变更必须对照设计文档，不得脱离设计自行发挥
2. **分阶段实施**：7 个 Phase 按依赖关系顺序执行，每个 Phase 独立可验证
3. **不破坏现有功能**：重构期间系统持续可用，新旧代码通过特性开关切换
4. **测试先行**：每个 Phase 编写单元测试和集成测试，测试通过后方可进入下一 Phase
5. **可回滚**：每个 Phase 提供明确的回滚方案

---

## 二、范围界定

### 2.1 重构范围

| 层级 | 模块 | 重构程度 | 涉及文件 |
|---|---|---|---|
| **数据库层** | ORM 模型 | 中等（扩字段+新增4个模型） | models/metric.py, models/diagnosis.py, 新建2个模型文件 |
| **数据预处理层** | 8步Pipeline + 异常值检测 | **全新开发** | 新建 services/preprocessing/, services/outlier_detection/ |
| **数据编排层** | DataPlanner + DataBlock Cache | **全新开发** | 新建 services/data_planner.py, services/data_block_cache.py |
| **指标计算层** | kpi_calc.py 重构 | **重大重构** | tasks/kpi_calc.py, 新建 services/metric_calculator/ |
| **API 接口层** | 接口扩展 | 中等（参数扩展+新增接口） | api/v1/endpoints/, schemas/ |
| **任务调度层** | 标准+自定义任务 | 中等（新增自定义任务） | tasks/celery_app.py, 新建任务管理服务 |
| **前端适配** | 可信度展示+诊断标签+任务管理 | 中等 | 前端 Vue 组件 |

### 2.2 不在本次重构范围

| 排除项 | 原因 |
|---|---|
| PID 整定模块重构 | 依赖 DataPlanner 但整定算法本身不变，后续独立迭代 |
| 诊断算法矩阵重构 | Dempster-Shafer 融合算法后续迭代 |
| 前端 UI/UX 全面改版 | 仅适配 v4.0 数据结构，不改版面布局 |
| AAS 实时数据对接 | SignalR 实时推送后续独立实施 |

---

## 三、技术路线

### 3.1 架构变更总览

```
v3.x 架构：
  KPI Task → 直接查 TDengine (4次) → 物理删除Bad点 → 计算指标 → 存快照

v4.0 架构：
  KPI Task → DataPlanner
               ├── 读取指标数据需求契约 (clpm_metric_data_requirement)
               ├── 合并查询计划 (tagGroup分组)
               ├── 查询 DataBlock Cache (L1 Redis)
               │   ├── HIT → 直接复用
               │   └── MISS → 查询 TDengine → 8步预处理 → 写入缓存
               ├── 组装 MetricDataBundle (含数据血缘)
               └── 分发给 Metric Calculator
  Metric Calculator → 消费Bundle → 计算12项指标 → 生成数据血缘+可信度
  → 存储到 kpi_snapshot_hourly (含血缘字段) 或 kpi_snapshot_custom
  → 装置级聚合 → unit_kpi_summary
```

### 3.2 核心新增模块

| 模块 | 职责 | 设计依据 |
|---|---|---|
| DataPlanner | 数据编排：需求契约→合并→缓存→预处理→Bundle | ADS §2, FDS §4, PRD §8.1 |
| PreprocessingPipeline | 8步预处理：质量码→valid标记→归一化→异常值→缺失率→连续性→Mask→Summary | 算法说明 §3.4, PRD §5.5 |
| OutlierDetection | 8类异常值检测 + 按控制类型阈值 | 算法说明 §3.4.3-3.4.4, PRD §5.5.2-5.5.3 |
| DataBlockCache | L1/L2/L3 三级缓存 + zstd + Pipeline | ADS §10.7, FDS §5.3.9 |
| MetricCalculator | 指标计算器（只消费Bundle，不直接查库） | ADS §10.2, 算法说明 §4.1-4.11 |
| ConfidenceEvaluator | 可信度 A/B/C/D/E 五级判定 | 算法说明 §3.7.2, PRD §5.4.2 |
| TaskManager | 标准+自定义任务管理 | PRD §4.3.7, FDS §5.3.11 |
| DiagnosisTagService | 诊断标签管理 | PRD §5.6, IDS §2.4.10-2.4.12 |

### 3.3 技术选型

| 技术 | 用途 | 版本要求 | 设计依据 |
|---|---|---|---|
| zstandard | DataBlock zstd 压缩 | ≥0.22 | FDS §5.3.9 |
| redis (Pipeline) | 批量写入 | ≥4.5 (已有) | ADS §10.7 |
| numpy + scipy | ARMA 模型辨识 | 已有 | 算法说明 §4.5 |
| simpleeval | 表达式引擎沙箱 | 已有 | PRD §5.1.4 |
| Celery Beat | 标准任务定时调度 | 已有 | PRD §4.3.7 |

---

## 四、实施步骤

### Phase 0: SQLAlchemy 模型层更新（前置基础）

**目标**：同步 ORM 模型与 v4.0 数据库结构
**前置依赖**：数据库迁移 k2f3a4b5c6d7（✅ 已完成）
**设计依据**：DDS §2.8/§2.14-2.17

#### 任务清单

| 序号 | 任务 | 文件 | 设计依据 |
|---|---|---|---|
| 0.1 | KpiSnapshotHourly 增加 7 个字段 | models/metric.py | DDS §2.8 |
| 0.2 | 新增 KpiSnapshotCustom 模型 | models/metric.py | DDS §2.14 |
| 0.3 | 新增 ClpmMetricDataRequirement 模型 | 新建 models/metric_data_requirement.py | DDS §2.15 |
| 0.4 | 新增 DiagnosisTag 模型 | models/diagnosis.py | DDS §2.16 |
| 0.5 | 新增 UnitKpiSummary 模型 | 新建 models/unit_kpi_summary.py | DDS §2.17 |
| 0.6 | 模型注册到 models/__init__.py | models/__init__.py | — |

#### KpiSnapshotHourly 新增字段

```python
# models/metric.py — KpiSnapshotHourly 类新增
ideal_settling_time = Column(DECIMAL(8, 2))    # 理想稳态时间
algorithm_version = Column(String(50))           # 算法版本号
sampling_freq = Column(String(10))               # 采样频率
quality_policy = Column(String(30))              # 质量策略
valid_rate = Column(DECIMAL(5, 4))               # 有效数据率
confidence_level = Column(Char(1))               # 可信度等级
data_lineage = Column(JSONB)                      # 数据血缘JSON
```

#### 验收标准
- 所有模型可正常执行 CRUD 操作
- `uv run alembic check` 无差异
- 现有测试全部通过

---

### Phase 1: 数据预处理模块（核心基础）

**目标**：实现 8 步预处理 Pipeline + 8 类异常值检测 + 按控制类型阈值
**前置依赖**：Phase 0
**设计依据**：算法说明 §3.4, PRD §5.5, FDS §5.3.1.2

#### 任务清单

| 序号 | 任务 | 文件 | 设计依据 |
|---|---|---|---|
| 1.1 | 定义预处理数据结构（DataBlock/MetricDataBundle/DataLineage） | 新建 contracts/data_types.py | 算法说明 §3.5-3.7, 数据流程图 §7.5 |
| 1.2 | 实现 8 步预处理 Pipeline | 新建 services/preprocessing/pipeline.py | 算法说明 §3.4.2 |
| 1.3 | 实现 8 类异常值检测 | 新建 services/preprocessing/outlier_detection.py | 算法说明 §3.4.3, PRD §5.5.2 |
| 1.4 | 实现按控制类型阈值表 | 新建 services/preprocessing/thresholds.py | 算说明 §3.4.4, PRD §5.5.3 |
| 1.5 | 实现 Metric Validity Mask 生成 | 新建 services/preprocessing/validity_mask.py | 算法说明 §3.4.2 步骤⑦, PRD §5.5.4 |
| 1.6 | 实现 QualitySummary 生成 | 新建 services/preprocessing/quality_summary.py | 算法说明 §3.4.2 步骤⑧ |
| 1.7 | 单元测试 | 新建 tests/test_preprocessing/ | — |

#### 8 步 Pipeline 实现规范

```
输入：原始时序数据（来自 TDengine）+ 回路配置（控制类型/量程/阈值）
输出：DataBlock（含valid标记 + QualitySummary + MetricMask）

Step ① 质量码识别：OPC质量码 → Good/Bad/Unknown（PRD §5.5.1）
Step ② 有效性标记：基于质量码+异常值，为每个Tag每个时间戳打valid=True/False
Step ③ 量程归一化：PV/SP/OP按量程归一化为百分比（0~100）
Step ④ 异常值识别：8类检测，结果写入valid标记（不删除数据点）
Step ⑤ 缺失率统计：记录缺失时段，计算缺失率
Step ⑥ 连续性检查：标记连续有效段，缺口超过阈值时切断
Step ⑦ Metric Mask生成：根据clpm_metric_data_requirement生成各指标掩码
Step ⑧ QualitySummary生成：valid_rate/bad_rate/missing_rate
```

#### 8 类异常值检测实现规范

| 异常类型 | 检测方法 | 原因码 | 设计依据 |
|---|---|---|---|
| 超量程 | PV/SP/OP 超出量程范围 | OUT_OF_RANGE | 算法说明 §3.4.3 |
| 冻结值 | 按控制类型窗口检测（标准差<阈值） | FROZEN | 算法说明 §3.4.4 |
| 跳变 | 按控制类型阈值检测（相邻变化>阈值） | JUMP | 算法说明 §3.4.4 |
| 尖峰 | 单点突变后立即恢复 | SPIKE | 算法说明 §3.4.3 |
| NaN/NULL | 值为 NaN/Inf/NULL | NaN | 算法说明 §3.4.3 |
| 时间戳异常 | 重复/逆序/间隔异常 | TS_ANOMALY | 算法说明 §3.4.3 |
| 质量码异常 | OPC 质量码为 Bad/Uncertain | QC_BAD | 算法说明 §3.4.3 |
| 高频噪声 | 超过截止频率成分（仅标记不滤波） | HF_NOISE | 算法说明 §3.4.3 |

#### 按控制类型阈值表

| 控制类型 | 基础采样率 | 冻结窗口 | 跳变阈值 | 尖峰阈值 | 噪声截止频率 | 连续有效最短段 |
|---|---|---|---|---|---|---|
| 流量(FC) | 1s | 5s(5点) | 0.8×量程 | 0.5×量程 | 0.2Hz | 30点 |
| 压力(PC) | 2s | 10s(5点) | 0.5×量程 | 0.3×量程 | 0.1Hz | 20点 |
| 温度(TC) | 5s | 30s(6点) | 0.3×量程 | 0.2×量程 | 0.05Hz | 15点 |
| 液位(LC) | 5s | 30s(6点) | 0.3×量程 | 0.2×量程 | 0.05Hz | 15点 |
| 成分(CC) | 10s | 60s(6点) | 0.2×量程 | 0.1×量程 | 0.02Hz | 10点 |

#### 验收标准
- 8 类异常值检测全部通过单元测试
- 按控制类型阈值正确区分（FC/PC/TC/LC/CC）
- KEEP_ALL_WITH_VALIDITY 策略：不删除任何数据点
- valid 标记正确生成（Good→True, Bad→False）
- Metric Validity Mask 按指标差异化生成
- QualitySummary 的 valid_rate 计算正确

---

### Phase 2: DataPlanner 核心（架构中枢）

**目标**：实现 DataPlanner 数据编排器 + DataBlock Cache
**前置依赖**：Phase 1
**设计依据**：ADS §2/§8/§10.1/§10.7, FDS §4/§5.3.9, PRD §8.1-8.3, 数据流程图 §7

#### 任务清单

| 序号 | 任务 | 文件 | 设计依据 |
|---|---|---|---|
| 2.1 | 实现 DataPlanner 核心 | 新建 services/data_planner.py | ADS §2, 数据流程图 §7.1 |
| 2.2 | 实现 tagGroup 分组逻辑 | services/data_planner.py | 算法说明 §3.5, PRD §8.3 |
| 2.3 | 实现查询计划合并 | services/data_planner.py | 数据流程图 §7.1 Phase 3 |
| 2.4 | 实现 DataBlock Cache（L1） | 新建 services/cache/l1_datablock.py | ADS §10.7, FDS §5.3.9 |
| 2.5 | 实现缓存Key生成与分层TTL | services/cache/l1_datablock.py | FDS §5.3.9 |
| 2.6 | 实现 zstd 压缩/解压 | services/cache/l1_datablock.py | FDS §5.3.9 |
| 2.7 | 实现 Pipeline 批量写入 | services/cache/l1_datablock.py | ADS §10.7 |
| 2.8 | 实现配置变更缓存失效 | 新建 services/cache/invalidation.py | ADS §10.7, FDS §5.3.9 |
| 2.9 | 实现 MetricDataBundle 组装 | 新建 services/metric_data_bundle.py | 数据流程图 §7.5 |
| 2.10 | 集成测试 | 新建 tests/test_data_planner/ | — |

#### DataPlanner 核心接口

```python
class DataPlanner:
    async def request_bundles(
        self,
        loop_id: str,
        metrics: list[str],
        time_window: TimeWindow,
        control_type: ControlType
    ) -> list[MetricDataBundle]:
        """
        1. 读取 clpm_metric_data_requirement 契约
        2. 合并相同 tagGroup 的查询计划
        3. 查询 DataBlock Cache（Redis, zstd压缩）
        4. 未命中→查询TDengine + 8步预处理→写缓存
        5. 按指标组装 MetricDataBundle
        """
```

#### tagGroup 复用规则

| 条件 | 查询次数 | 说明 |
|---|---|---|
| BASE 已是 1s（流量回路） | 1 次 | OP_HF/PVOP_HF/MODE_HF/QUALITY_HF 复用 BASE |
| BASE 非 1s（温度/压力等） | 2~4 次 | 高频 tagGroup 单独查询 |

#### 验收标准
- DataPlanner 正确合并查询计划（5个指标→4个tagGroup查询）
- 缓存命中时不查询 TDengine
- 缓存未命中时查询 TDengine + 预处理 + 写入缓存
- tagGroup 复用正确（流量回路仅需1次查询）
- zstd 压缩率 ≥ 60%
- Pipeline 批量写入减少 75% 网络往返
- 配置变更后旧缓存自动失效

---

### Phase 3: 指标计算层重构

**目标**：指标计算器改为只消费 MetricDataBundle，输出含数据血缘和可信度
**前置依赖**：Phase 2
**设计依据**：算法说明 §4.1-4.11, ADS §10.2, PRD §5.1-5.4

#### 任务清单

| 序号 | 任务 | 文件 | 设计依据 |
|---|---|---|---|
| 3.1 | 定义 MetricCalculator 接口 | 新建 contracts/metric_calculator.py | ADS §10.2, 数据流程图 §7.5 |
| 3.2 | 实现 12 个指标计算器 | 新建 services/metric_calculator/ | 算法说明 §4.1-4.11 |
| 3.3 | 实现可信度判定 | 新建 services/confidence_evaluator.py | 算法说明 §3.7.2, PRD §5.4.2 |
| 3.4 | 实现数据血缘生成 | services/confidence_evaluator.py | 算法说明 §3.7.1, FDS §5.3.10 |
| 3.5 | 重构综合评分（强制v2，移除v1回退） | tasks/kpi_calc.py | 算法说明 §4.10, PRD §5.1.2 |
| 3.6 | 实现装置级聚合评分 | services/node_aggregation.py | 算法说明 §4.11, PRD §4.3.3 |
| 3.7 | 单元测试 | 新建 tests/test_metric_calculator/ | — |

#### 12 个指标计算器

| 指标 | tagGroup | Mask | 公式 | 设计依据 |
|---|---|---|---|---|
| accuracy_rate | BASE | pv_valid && sp_valid | $[1 - \frac{|\bar{E}|}{|E|_{max}} \times (1 - \frac{1}{e^r})] \times 100\%$ | §4.4 |
| fast_rate | BASE | pv_valid && sp_valid | $T \leq T': 100\%; T > T': \frac{1}{e^{(T-T')/T'}} \times 100\%$ | §4.5 |
| stability_rate | BASE | pv_valid && sp_valid | $\frac{1}{e^{\sigma/(0.05 \cdot U)}} \times (1-Osc) \times 100\%$ | §4.3 |
| effective_auto_rate | MODE_HF+OP_HF | mode_valid && op_valid | $\frac{T_{auto\_effective}}{T_{total}} \times 100\%$ | §4.2 |
| good_value_rate | QUALITY_HF | 不删除行 | $\frac{T_{good}}{T_{total}} \times 100\%$ | §4.1 |
| oscillation_rate | BASE | pv_valid && sp_valid | IAE零交叉相似率法 | §4.6 |
| saturation_rate | OP_HF | op_valid | $\frac{T_{saturated}}{T_{total}} \times 100\%$ | §4.7 |
| stiction_index | PVOP_HF | pv_valid && op_valid | $\frac{b}{a} \times 100\%$ | §4.8 |
| output_trip_index | OP_HF | op_valid && consecutive_valid | $\frac{\sum|OP_i - OP_{i-1}|}{T \cdot OP_{range}}$ | §4.9 |
| auto_mode_rate | MODE_HF | mode_valid | $\frac{T_{auto}}{T_{total}} \times 100\%$ | §4.0.3 |
| settling_time | BASE | pv_valid && sp_valid | ARMA + Green函数 | §4.5 |
| ideal_settling_time | CONFIG | — | 手动配置>模型参数>类型默认值 | §4.5 |

#### 可信度判定规则

| 等级 | valid_rate | 处理 | 设计依据 |
|---|---|---|---|
| A | ≥ 0.95 | 正常使用 | 算法说明 §3.7.2 |
| B | 0.80 ~ 0.95 | 正常使用，UI标注 | 算法说明 §3.7.2 |
| C | 0.60 ~ 0.80 | 可用，UI显著标注 | 算法说明 §3.7.2 |
| D | 0.20 ~ 0.60 | 仅供参考，UI警告 | 算法说明 §3.7.2 |
| E | < 0.20 | INCONCLUSIVE，score=NULL | 算法说明 §3.7.2 |

#### 综合评分公式（强制v2）

$$P = \frac{A \cdot a + F \cdot f + S \cdot s}{a + f + s} \times R$$

**关键变更**：
- 移除 v1 平等加权回退路径（当前 kpi_calc.py:1099-1195 的 `_compute_composite_score` v1）
- R 作为折扣因子，不参与加权求和
- 可信度取 A/F/S 三个核心指标可信度的最低值

#### 装置级聚合规则

$$Score_{unit} = \frac{\sum_{i=1}^{m} w_i^{level} \cdot Score_i}{\sum_{i=1}^{m} w_i^{level}}$$

- 回路级别权重：一级=3, 二级=2, 三级=1
- INCONCLUSIVE 回路不参与聚合，单独统计
- 仅基于标准任务（kpi_snapshot_hourly）聚合

#### 验收标准
- 12 个指标计算器全部通过单元测试
- 综合评分公式 v2 正确（R 作为折扣因子）
- 数据血缘 8 字段完整写入
- 可信度 A/B/C/D/E 五级正确判定
- E 级时 score=NULL（不以0分掩盖）
- 装置级聚合排除 INCONCLUSIVE 回路

---

### Phase 4: kpi_calc.py 整合

**目标**：将 kpi_calc.py 从直接查库改为通过 DataPlanner 获取数据
**前置依赖**：Phase 3
**设计依据**：ADS §8, FDS §4, PRD §4.3

#### 任务清单

| 序号 | 任务 | 文件 | 设计依据 |
|---|---|---|---|
| 4.1 | 移除直接 TDengine 查询 | tasks/kpi_calc.py:319-322 | ADS §2 DataPlanner约束 |
| 4.2 | 移除 Bad 点物理剔除 | tasks/kpi_calc.py:335 | FDS §5.3.1.2 |
| 4.3 | 接入 DataPlanner | tasks/kpi_calc.py | 数据流程图 §7.1 |
| 4.4 | 移除 v1 评分回退路径 | tasks/kpi_calc.py:1099-1195 | 算法说明 §4.10 |
| 4.5 | _save_snapshot 写入血缘字段 | tasks/kpi_calc.py | DDS §2.8 |
| 4.6 | 实现三层计算（无依赖→有依赖→评分） | tasks/kpi_calc.py | 数据流程图 §7.1 Phase 9 |
| 4.7 | 集成测试 | tests/test_kpi_calc.py | — |

#### 当前代码问题与修复

| 位置 | 当前代码 | 问题 | 修复 |
|---|---|---|---|
| kpi_calc.py:319-322 | `query_trend_fn(pv_tag_name, ...)` 直接查 TDengine | 绕过 DataPlanner | 改为 `data_planner.request_bundles()` |
| kpi_calc.py:335 | `pv_data_filtered = [d for d in pv_data if ... != "BAD"]` | 物理删除 Bad 点 | 改为使用 valid 标记 |
| kpi_calc.py:1099-1195 | `_compute_composite_score` v1 平等加权 R | R 应作折扣因子 | 删除 v1，强制走 v2 |
| kpi_calc.py:1291-1369 | `_save_snapshot` 无血缘字段 | 缺少数据血缘 | 写入 7 个新字段 |

#### 三层计算流程

```
Layer 1（无依赖，并行）：
  good_value_rate, oscillation_rate, saturation_rate,
  stiction_index, output_trip_index, auto_mode_rate,
  settling_time, ideal_settling_time, accuracy_rate

Layer 2（有依赖，串行）：
  stability_rate (依赖 oscillation_rate)
  effective_auto_rate (依赖 auto_mode_rate + saturation_rate)
  fast_rate (依赖 settling_time + ideal_settling_time)

Layer 3（评分）：
  composite_score = (A·a + F·f + S·s)/(a+f+s) × R
  confidence_level = min(A_confidence, F_confidence, S_confidence)
```

#### 验收标准
- kpi_calc.py 不再直接调用 `query_trend_data`
- 不再物理删除 Bad 点
- 综合评分使用 v2 公式（R 作折扣因子）
- 快照写入包含 7 个数据血缘字段
- 三层计算依赖关系正确
- 重构前后结果对比：相同输入应产生相同或更优结果

---

### Phase 5: API 接口层扩展

**目标**：扩展 API 接口以支持 v4.0 数据结构和新增功能
**前置依赖**：Phase 4
**设计依据**：IDS §2.4.5/§2.7/§2.4.10-2.4.12/§2.7.5-2.7.6

#### 任务清单

| 序号 | 任务 | 文件 | 设计依据 |
|---|---|---|---|
| 5.1 | 历史数据接口增加 tagGroup/qualityPolicy 参数 | api/v1/endpoints/tags.py | IDS §2.4.5 |
| 5.2 | 历史数据返回增加 valid_mask | schemas/tag.py | IDS §2.4.5 |
| 5.3 | KPI 接口返回增加数据血缘和可信度 | schemas/performance.py | IDS §2.7.1 |
| 5.4 | 新增 DataPlanner 内部接口 | 新建 api/v1/endpoints/dataplanner.py | IDS §2.7.5 |
| 5.5 | 新增任务管理接口（标准/自定义） | 新建 api/v1/endpoints/tasks.py | IDS §2.7.6 |
| 5.6 | 新增诊断标签接口（查询/处理） | api/v1/endpoints/diagnosis.py | IDS §2.4.10-2.4.12 |
| 5.7 | 新增 Schema 定义 | 新建 schemas/task.py, schemas/dataplanner.py | — |

#### 接口清单

| 接口 | 方法 | URL | 设计依据 |
|---|---|---|---|
| 历史数据（扩展参数） | GET | /api/v1/timeseries/{loopId}/waveform | IDS §2.4.5 |
| KPI 计算（扩展返回） | POST | /api/v1/algorithms/kpi/calculate | IDS §2.7.1 |
| DataPlanner 提交计划 | POST | /api/v1/algorithms/dataplanner/plan | IDS §2.7.5.1 |
| DataPlanner 获取Bundle | POST | /api/v1/algorithms/dataplanner/bundle | IDS §2.7.5.2 |
| 触发标准评估任务 | POST | /api/v1/tasks/standard/evaluate | IDS §2.7.6.1 |
| 触发自定义评估任务 | POST | /api/v1/tasks/custom/evaluate | IDS §2.7.6.2 |
| 查询任务状态 | GET | /api/v1/tasks/{taskId} | IDS §2.7.6.3 |
| 查询任务列表 | GET | /api/v1/tasks | IDS §2.7.6.4 |
| 查询诊断标签列表 | GET | /api/v1/diagnosis/tags | IDS §2.4.10 |
| 查询回路诊断标签 | GET | /api/v1/diagnosis/tags/{loopId} | IDS §2.4.11 |
| 处理诊断标签 | PUT | /api/v1/diagnosis/tags/{tagId}/resolve | IDS §2.4.12 |

#### 验收标准
- 所有接口符合 IDS 定义的参数和返回格式
- DataPlanner 内部接口不对外暴露
- 任务管理接口支持标准/自定义任务
- 诊断标签接口支持多维查询和状态流转

---

### Phase 6: 前端适配

**目标**：前端适配 v4.0 数据结构，增加可信度展示和诊断标签面板
**前置依赖**：Phase 5
**设计依据**：PRD §4.3.4/§5.4/§5.6, IDS §4.4

#### 任务清单

| 序号 | 任务 | 设计依据 |
|---|---|---|
| 6.1 | KPI 卡片增加可信度标识 | PRD §5.4 |
| 6.2 | 波形图按 valid 标记渲染（Bad=灰色虚线） | IDS §4.4 |
| 6.3 | 低效排行表增加可信度列 | PRD §4.3.4 |
| 6.4 | 新增诊断标签面板 | PRD §5.6 |
| 6.5 | 新增任务管理页面 | PRD §4.3.7 |
| 6.6 | INCONCLUSIVE 快照特殊展示 | PRD §5.4.3 |

#### 验收标准
- 可信度等级正确展示（A/B/C/D/E 使用不同颜色标识）
- 波形图 Bad 点渲染为灰色虚线（非断线）
- INCONCLUSIVE 快照在排行中单独标注

---

## 五、资源分配

### 5.1 模块责任人分配

| Phase | 模块 | 复杂度 | 建议人手 |
|---|---|---|---|
| Phase 0 | ORM 模型更新 | 低 | 1人 |
| Phase 1 | 预处理模块 | 高 | 1-2人 |
| Phase 2 | DataPlanner + 缓存 | 高 | 1-2人 |
| Phase 3 | 指标计算器重构 | 高 | 1-2人 |
| Phase 4 | kpi_calc.py 整合 | 中 | 1人 |
| Phase 5 | API 接口扩展 | 中 | 1人 |
| Phase 6 | 前端适配 | 中 | 1人 |

### 5.2 新建文件清单

```
backend/app/
├── contracts/                          # 接口契约（新建）
│   ├── __init__.py
│   ├── data_types.py                   # DataBlock/MetricDataBundle/DataLineage/MetricResult
│   └── metric_calculator.py            # MetricCalculator 接口
├── services/
│   ├── data_planner.py                 # DataPlanner 核心
│   ├── metric_data_bundle.py           # MetricDataBundle 组装
│   ├── confidence_evaluator.py         # 可信度判定
│   ├── task_manager.py                 # 任务管理（标准+自定义）
│   ├── preprocessing/                  # 预处理模块（新建）
│   │   ├── __init__.py
│   │   ├── pipeline.py                 # 8步预处理Pipeline
│   │   ├── outlier_detection.py        # 8类异常值检测
│   │   ├── thresholds.py               # 按控制类型阈值表
│   │   ├── validity_mask.py            # Metric Validity Mask
│   │   └── quality_summary.py          # QualitySummary
│   ├── cache/                          # 缓存模块（新建）
│   │   ├── __init__.py
│   │   ├── l1_datablock.py             # L1 DataBlock缓存
│   │   ├── l2_bundle.py                # L2 MetricDataBundle缓存
│   │   ├── l3_feature.py              # L3 特征缓存
│   │   └── invalidation.py            # 配置变更失效
│   └── metric_calculator/              # 指标计算器（新建）
│       ├── __init__.py
│       ├── accuracy.py                 # 准确率
│       ├── fast_rate.py                # 快速率
│       ├── stability.py                # 稳定率
│       ├── effective_auto.py           # 有效自控率
│       ├── good_value.py               # 好值率
│       ├── oscillation.py              # 振荡率
│       ├── saturation.py              # 饱和率
│       ├── stiction.py                # 粘滞系数
│       ├── output_trip.py             # 输出值行程指数
│       ├── auto_mode.py               # 自控率
│       ├── settling_time.py           # 稳态时间
│       └── ideal_settling_time.py     # 理想稳态时间
├── models/
│   ├── metric_data_requirement.py      # 新增模型
│   └── unit_kpi_summary.py             # 新增模型
├── api/v1/endpoints/
│   ├── dataplanner.py                  # DataPlanner 内部接口
│   └── tasks.py                        # 任务管理接口
├── schemas/
│   ├── task.py                         # 任务管理Schema
│   └── dataplanner.py                  # DataPlanner Schema
└── tests/                              # 测试（新建）
    ├── test_preprocessing/
    ├── test_data_planner/
    ├── test_metric_calculator/
    └── test_kpi_calc/
```

---

## 六、进度计划

### 6.1 Phase 依赖关系

```
Phase 0 (ORM模型)
  ↓
Phase 1 (预处理模块)
  ↓
Phase 2 (DataPlanner + 缓存)     ← Phase 1 完成后可开始
  ↓
Phase 3 (指标计算器重构)          ← Phase 2 完成后可开始
  ↓
Phase 4 (kpi_calc.py 整合)        ← Phase 3 完成后可开始
  ↓
Phase 5 (API 接口扩展)            ← Phase 4 完成后可开始
  ↓
Phase 6 (前端适配)               ← Phase 5 完成后可开始
```

### 6.2 关键里程碑

| 里程碑 | 标志 | 验收内容 |
|---|---|---|
| M0 | Phase 0 完成 | ORM 模型与数据库一致，现有测试通过 |
| M1 | Phase 1 完成 | 8步预处理通过单元测试，KEEP_ALL_WITH_VALIDITY 验证 |
| M2 | Phase 2 完成 | DataPlanner 端到端测试通过，缓存命中率验证 |
| M3 | Phase 3 完成 | 12个指标计算器通过单元测试，数据血缘写入验证 |
| M4 | Phase 4 完成 | kpi_calc.py 重构完成，前后结果对比通过 |
| M5 | Phase 5 完成 | API 接口通过集成测试，符合 IDS 规范 |
| M6 | Phase 6 完成 | 前端适配完成，E2E 测试通过 |

---

## 七、风险评估及应对措施

### 7.1 技术风险

| 风险 | 概率 | 影响 | 应对措施 |
|---|---|---|---|
| DataPlanner 引入增加计算延迟 | 中 | 中 | L1缓存命中时延迟<5ms；未命中时预处理增加~100ms，可接受 |
| zstd 压缩/解压 CPU 开销 | 低 | 低 | 压缩率60%+，CPU开销远小于网络IO节省 |
| ARMA 模型辨识计算量（O(N²)） | 中 | 中 | 直接使用BASE数据块，流量回路3600点≈50ms，可接受 |
| 缓存失效导致批量回源 | 低 | 高 | Pipeline批量写入 + 降级直查TDengine策略 |
| 三层计算依赖链串行瓶颈 | 低 | 中 | Layer 1 并行计算，仅 Layer 2/3 串行 |

### 7.2 业务风险

| 风险 | 概率 | 影响 | 应对措施 |
|---|---|---|---|
| 重构后评分结果变化 | 中 | 高 | Phase 4 前后结果对比测试，差异需可解释 |
| INCONCLUSIVE 快照影响聚合 | 中 | 中 | 装置级聚合排除 INCONCLUSIVE，单独统计展示 |
| 自定义任务并发抢占资源 | 低 | 中 | 并发限制（单用户≤3，全局≤20），优先级低于标准任务 |

### 7.3 跨文档不一致风险

| 不一致点 | 涉及文档 | 处理方案 |
|---|---|---|
| 可信度阈值（B/C/D级） | IDS vs FDS/ADS/DDS | 以 FDS/ADS/DDS 为准（A≥0.95, B≥0.80, C≥0.60, D≥0.20, E<0.20） |
| 指标代码命名（fast_response_rate vs fast_rate） | IDS vs 算法说明 | 统一为 `fast_response_rate`（与数据库现有列名一致） |
| 辅助诊断指标数量（PRD 列出8个 vs 算法说明列出8个） | PRD vs 算法说明 | 当前实现算法说明的8个（含稳态时间/理想稳态时间/自控率） |

---

## 八、质量保障机制

### 8.1 代码审查

| 审查项 | 要求 |
|---|---|
| 设计文档对齐 | 每个函数注释标注设计依据章节号 |
| 命名规范 | metric_code/tag_group/mask_expression 与契约表一致 |
| 类型注解 | 所有新增代码使用完整类型注解 |
| 文档字符串 | 所有新增类/函数有 docstring |

### 8.2 持续集成

| 检查项 | 工具 |
|---|---|
| 代码风格 | ruff / black |
| 类型检查 | mypy |
| 单元测试 | pytest |
| 覆盖率 | pytest-cov（目标 ≥ 80%） |
| 数据库迁移 | alembic check |

### 8.3 日志规范

| 日志级别 | 使用场景 | 设计依据 |
|---|---|---|
| DEBUG | 输入参数、中间结果、输出值 | project_memory 约束 |
| INFO | DataPlanner 查询计划、缓存命中/未命中 | — |
| WARNING | 可信度降级（A→B/C）、缓存失效 | — |
| ERROR | 计算失败、TDengine 查询失败 | — |

---

## 九、测试策略

### 9.1 测试分层

| 层级 | 测试类型 | 覆盖范围 | 时机 |
|---|---|---|---|
| L1 | 单元测试 | 8类异常值检测、12个指标计算器、可信度判定 | 每个 Phase 内 |
| L2 | 集成测试 | DataPlanner 端到端、缓存命中/未命中、三层计算 | Phase 2/3/4 |
| L3 | 回归测试 | 重构前后结果对比 | Phase 4 |
| L4 | 端到端测试 | 标准评估任务全链路 | Phase 5 |
| L5 | 性能测试 | 1000回路批量评估 | Phase 4 完成后 |

### 9.2 测试数据

| 数据场景 | 用途 | 来源 |
|---|---|---|
| fast_response | 快速响应回路 | generate_kpi_test_data.py |
| slow_response | 慢速响应回路 | generate_kpi_test_data.py |
| oscillation | 振荡回路 | generate_kpi_test_data.py |
| op_saturation | 输出饱和 | generate_kpi_test_data.py |
| normal | 正常回路 | generate_kpi_test_data.py |
| manual_mode | 手动模式 | generate_kpi_test_data.py |
| pure_ar2 | 纯AR2过程 | generate_kpi_test_data.py |
| **bad_quality**（新增） | Bad质量码场景 | 需新增 |
| **mixed_control_types**（新增） | 混合控制类型（FC/PC/TC/LC/CC） | 需新增 |

### 9.3 回归测试方案

| 对比项 | 方法 | 容差 |
|---|---|---|
| 综合评分 | 重构前后相同输入对比 | ±1分（因公式变更） |
| 准确率 | 重构前后对比 | ±0.5%（因valid标记） |
| 稳定率 | 重构前后对比 | ±1%（因振荡率传递） |
| 快速率 | 重构前后对比 | ±2%（因ARMA输入数据变化） |
| 有效自控率 | 重构前后对比 | ±0.5%（因饱和检测改进） |

### 9.4 性能测试指标

| 指标 | 目标值 | 设计依据 |
|---|---|---|
| 单回路计算延迟 | < 2秒 | PRD §7.1 |
| 100回路批量评估 | < 5分钟 | PRD §7.1 |
| 1200回路全量评估 | < 20分钟（10并发） | PRD §7.1 |
| DataBlock 缓存命中率 | ≥ 70%（标准任务） | FDS §5.3.9 |
| zstd 压缩率 | ≥ 60% | FDS §5.3.9 |

---

## 十、回滚方案

### 10.1 Phase 级回滚

| Phase | 回滚方法 | 影响 |
|---|---|---|
| Phase 0 | `alembic downgrade j1e2f3a4b5c6` | 无（仅表结构变更） |
| Phase 1 | 移除 preprocessing/ 模块 | 无（新模块未集成） |
| Phase 2 | 移除 data_planner.py，恢复直接查询 | kpi_calc 回退到直接查TDengine |
| Phase 3 | 恢复 v1 评分回退路径 | 评分公式回退到平等加权 |
| Phase 4 | 恢复 kpi_calc.py 旧版本 | 评分回到v3.x公式 |
| Phase 5 | 移除新增 API 端点 | 前端回退到旧接口 |
| Phase 6 | 前端回退到旧版本 | UI 回退到v3.x展示 |

### 10.2 特性开关

```python
# core/config.py
ENABLE_DATA_PLANNER = True      # DataPlanner 开关
ENABLE_KEEP_ALL_VALIDITY = True  # KEEP_ALL_WITH_VALIDITY 开关
ENABLE_CONFIDENCE_LEVEL = True   # 可信度展示开关
ENABLE_CUSTOM_TASK = True       # 自定义任务开关
```

当特性开关关闭时，系统回退到 v3.x 行为。

### 10.3 数据回滚

| 数据 | 回滚策略 |
|---|---|
| kpi_snapshot_hourly 新字段 | 置 NULL，不影响旧字段 |
| kpi_snapshot_custom | 清空表 |
| clpm_metric_data_requirement | 清空表（种子数据可重新导入） |
| diagnosis_tag | 清空表 |
| unit_kpi_summary | 清空表 |

---

## 十一、验收标准

### 11.1 功能验收

| 验收项 | 验收标准 | 设计依据 |
|---|---|---|
| DataPlanner | 正确合并查询计划，缓存命中/未命中正确处理 | ADS §2, 数据流程图 §7.1 |
| KEEP_ALL_WITH_VALIDITY | 不删除任何数据点，valid标记正确 | FDS §5.3.1.2, PRD §5.5.1 |
| 8类异常值检测 | 全部通过测试，按控制类型阈值正确 | 算法说明 §3.4.3-3.4.4 |
| 3+1+8指标体系 | 12个指标全部可计算，metric_code正确 | 算法说明 §4.0, PRD §5.1.1 |
| 综合评分 | P = (A·a+F·f+S·s)/(a+f+s) × R | 算法说明 §4.10, PRD §5.1.2 |
| 数据血缘 | 8字段完整写入kpi_snapshot_hourly | FDS §5.3.10, DDS §2.8 |
| 可信度 | A/B/C/D/E五级正确判定，E级时score=NULL | 算法说明 §3.7.2, PRD §5.4.2 |
| 装置级聚合 | 按级别权重加权平均，排除INCONCLUSIVE | 算法说明 §4.11 |
| 标准任务 | 每小时定时执行，写入kpi_snapshot_hourly | PRD §4.3.7A |
| 自定义任务 | 按需触发，写入kpi_snapshot_custom，不参与聚合 | PRD §4.3.7B |
| 诊断标签 | 支持查询/状态流转 | PRD §5.6, IDS §2.4.10-2.4.12 |
| DataBlock缓存 | zstd压缩+分层TTL+Pipeline写入 | ADS §10.7, FDS §5.3.9 |

### 11.2 性能验收

| 验收项 | 目标值 | 设计依据 |
|---|---|---|
| 单回路计算延迟 | < 2秒 | PRD §7.1 |
| 1200回路全量评估 | < 20分钟（10并发） | PRD §7.1 |
| 缓存命中率 | ≥ 70% | FDS §5.3.9 |
| zstd压缩率 | ≥ 60% | FDS §5.3.9 |

### 11.3 国标合规验收

| 验收项 | 标准 | 设计依据 |
|---|---|---|
| 指标公式 | 对齐 GB/T 44693.2-2024 附录 B/F | PRD §7.2 |
| 综合评分 | 对齐附录 B.6 | PRD §7.2 |
| 权重系数 | 对齐附录 C | PRD §7.2 |
| 性能定级 | 对齐附录 D | PRD §7.2 |
| 回路级别权重 | 对齐附录 E.2 | PRD §7.2 |
| 算法验证 | 国标示例数据用例覆盖率 ≥ 90% | PRD §7.2 |

---

## 附录A：跨文档不一致点汇总与处理决策

| 序号 | 不一致点 | 文档A | 文档B | 处理决策 |
|---|---|---|---|---|
| 1 | 可信度B级阈值 | IDS(B≥0.90) | FDS/ADS/DDS(B≥0.80) | 以FDS/ADS/DDS为准 |
| 2 | 可信度C级阈值 | IDS(C≥0.80) | FDS/ADS/DDS(C≥0.60) | 以FDS/ADS/DDS为准 |
| 3 | 可信度D级阈值 | IDS(D≥0.60) | FDS/ADS/DDS(D≥0.20) | 以FDS/ADS/DDS为准 |
| 4 | 快速率代码 | IDS(fast_response_rate) | 算法说明(FAST_RATE) | 数据库用fast_response_rate，代码用FAST_RATE |
| 5 | PRD辅助指标(8个) | PRD(含过激率等) | 算法说明(含稳态时间等) | 以算法说明为准，PRD后续同步 |
| 6 | 波形Bad点渲染 | IDS v3.x(断线) | IDS v4.0(灰色虚线) | 以IDS v4.0为准 |

---

## 附录B：设计文档对照索引

| 设计文档 | 版本 | 重构相关章节 |
|---|---|---|
| PRD | v4.0 | §5.1(指标体系) §5.4(数据血缘/可信度) §5.5(预处理) §5.6(诊断标签) §4.3.7(任务管理) §8(数据架构) §7.1(性能) |
| FDS | v4.0 | §4(数据流转) §5.3.1.1(指标体系) §5.3.1.2(质量码) §5.3.7(评分) §5.3.9(缓存) §5.3.10(血缘) §5.3.11(任务) |
| ADS | v4.0 | §2(架构分层) §8(算法服务) §10.1(数据接口) §10.2(指标接口) §10.7(缓存) §14.9(可信度) |
| DDS | v4.0 | §2.8(kpi_snapshot_hourly) §2.14-2.17(4张新表) §4.1(质量码) |
| IDS | v4.0 | §2.4.5(历史数据) §2.7.1(KPI) §2.4.10-12(诊断标签) §2.7.5(DataPlanner) §2.7.6(任务) §4.4(质量码) |
| 算法说明 | v2.0 | §3.4(预处理) §3.5(tagGroup) §3.6(契约) §3.7(血缘/可信度) §4.0(指标体系) §4.1-4.11(各指标) |
| 数据流程图 | v4.0 | §6(DataPlanner流程) §7(时序图) |
