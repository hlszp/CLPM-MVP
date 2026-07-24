# RFC：复杂回路聚合规则（P4 纲要，待评审）

**所属计划**: HiaMonitor 借鉴重构计划 P4（评审报告 §五 P1-5）
**RFC 状态**: 纲要草案，**待用户评审决策后方可进入实施**
**日期**: 2026-07-24
**评审依据**: 评审报告 P1-5「复杂回路聚合规则未对接现有 NodeAggregator 与 loop_count 机制」

---

## 一、问题陈述

当前 CLPM 的回路模型是「一个回路 = 一行 loop_ledger + 关联 7 个 OPC tag」（AAS 同步 tag 位号，回路由用户创建并关联 tag）。化工现场大量存在**复杂回路**：

| 类型 | 结构 | 典型场景 |
|------|------|---------|
| 串级（Cascade） | 主回路（温度）输出作为副回路（流量）设定值 | 反应釜温度-进料流量串级 |
| 超驰/选择（Override/Selector） | 多个控制器输出经选择器切换到执行机构 | 燃料气压力下限保护超驰正常温度控制 |
| NooM（多入多出） | 多个测量/多个操纵变量耦合 | 精馏塔塔顶温度-塔底温度联动 |

**核心矛盾**：若串级主副回路各录成一行 loop_ledger，则：
1. **节点聚合双重计数**：`NodeAggregator.aggregate` 用 `len(loop_scores)` 统计回路数（[node_aggregation.py:627](file:///Users/zhangping/DEV/CLPM/backend/app/services/node_aggregation.py#L627)），主副两行都会进入加权平均 → 一个物理控制回路被计为 2 个，权重翻倍。
2. **loop_count 失真**：节点快照 `loop_count`（[node_aggregation.py:196](file:///Users/zhangping/DEV/CLPM/backend/app/services/node_aggregation.py#L196) `_max_loop_count`）虚高，日/月聚合 `_weighted_average` 以 loop_count 为权重（[node_aggregation.py:87-124](file:///Users/zhangping/DEV/CLPM/backend/app/services/node_aggregation.py#L87-L124)）→ 复杂回路占比高的装置，聚合评分系统性偏移。
3. **评分语义混淆**：副回路（内环）跟踪速度快、精度高，主回路（外环）才是过程控制目标。两者都参与综合评分会扭曲装置级评估。

评审报告 P1-5 明确要求：**Phase 3 前必须补充 RFC，明确 ①NodeAggregator 输入侧去重规则 ②loop_count 计数口径 ③是否引入 complex_loop_group_id**。本文档即此 RFC 纲要。

---

## 二、现状分析（代码证据）

### 2.1 LoopLedger 模型——无任何复杂回路语义

[loop.py](file:///Users/zhangping/DEV/CLPM/backend/app/models/loop.py) 现有字段：

| 字段 | 用途 | 与复杂回路的关系 |
|------|------|-----------------|
| `tag_name` (L36, 唯一索引 L149) | 回路标识 | 一个物理回路 = 一行记录 |
| `loop_type` (L45) | TEMPERATURE/PRESSURE/LEVEL/FLOW... 业务类型 | 不表达回路结构 |
| `control_type` (L52) | STABLE/SLOW/FAST/LOGIC 控制类型 | 决定算法阈值，不表达回路结构 |
| `importance_level` (L68) | 1/2/3，聚合权重 1:3/2:2/3:1 | 单回路权重 |
| `include_in_evaluation` (L75) | true=参与评分与聚合，false=仅算单回路 KPI | **潜在去重开关，但见 2.3** |
| `dcs_model_id` (L121) | 关联 DCS 型号 | — |

**无** `cascade`/`master_loop_id`/`slave_loop_id`/`complex_loop_group_id`/`parent_id` 等任何表达回路间关联的字段。grep 全 backend 无 `串级|主回路|副回路|complex_loop|master_loop|slave_loop|超驰|NooM` 任何痕迹。

### 2.2 NodeAggregator——无去重，loop_count=len(loop_scores)

[aggregate() L590-640](file:///Users/zhangping/DEV/CLPM/backend/app/services/node_aggregation.py#L590-L640)：

```python
def aggregate(self, loop_scores: list[MetricResult], loop_weights=None) -> MetricResult:
    for result in loop_scores:
        if result.value is None or result.confidence_level == "E":
            inconclusive_count += 1; continue      # 仅跳过 INCONCLUSIVE
        level = self._resolve_level(result, loop_weights)
        valid_results.append((result, weight))
    total_loops = len(loop_scores)                  # ← loop_count = 输入长度，无去重
```

- **去重**：仅跳过 INCONCLUSIVE（value=None 或 confidence='E'），不按 loop_id/loop_tag 去重。
- **loop_count**：loop→node 小时聚合 = `len(loop_scores)`；node 小时→日→月 = `_max_loop_count(snaps)`（取最大值，L196）。
- **加权**：`_weighted_average` 以 `loop_count` 为权重（L87-124），loop_count 失真会传导到日/月聚合。

### 2.3 include_in_evaluation——字段存在但聚合链路未接线

| 位置 | 是否使用 |
|------|---------|
| 模型定义 [loop.py:75](file:///Users/zhangping/DEV/CLPM/backend/app/models/loop.py#L75) | ✅ 字段存在，default=True |
| API 增删改 [loops.py](file:///Users/zhangping/DEV/CLPM/backend/app/api/v1/endpoints/loops.py) | ✅ 可配置 |
| Dashboard 计数 [dashboard.py:592-633](file:///Users/zhangping/DEV/CLPM/backend/app/api/v1/endpoints/dashboard.py#L592-L633) | ✅ 统计 excluded_loops 数量 |
| **KPI 计算 loop 查询** [kpi_calc.py:359,758,963](file:///Users/zhangping/DEV/CLPM/backend/app/tasks/kpi_calc.py#L359) | ❌ **仅过滤 is_active+status=READY，未过滤 include_in_evaluation** |
| **NodeAggregator** | ❌ 未读取此字段 |

**结论**：`include_in_evaluation=False` 的回路仍会被计算 KPI 并进入节点聚合输入。这是既有接线缺口，与复杂回路去重强相关——任何去重方案都应先堵此缺口。

---

## 三、决策点（待用户拍板）

### 决策点 1：复杂回路数据模型如何建模？

| 方案 | 描述 | 优点 | 缺点 |
|------|------|------|------|
| **A. complex_loop_group_id 分组键** ⭐推荐 | loop_ledger 新增 `complex_loop_group_id`（可空 UUID）+ `complex_role`（MAIN/SUB，可空）。同 group 的回路归为一个物理控制回路。 | 灵活支持串级/超驰/NooM 任意结构；最小侵入（2 个可空字段）；MAIN 标记明确聚合代表 | 需用户手工分组配置；group 内多 MAIN 需业务校验 |
| B. parent_id 自引用 | loop_ledger 新增 `parent_loop_id` 外键自引用，副回路指向主回路 | 表达串级主副关系直观 | 仅支持树形（串级），超驰/NooM 多对多表达困难；递归查询复杂 |
| C. 复用 include_in_evaluation | 不新增字段，副回路设 include_in_evaluation=False | 零 schema 改动 | 语义错位（副回路仍需算单回路 KPI 供诊断）；丢失主副关联信息；无法做 group 级聚合 |
| D. 独立 complex_loop 表 | 新建 complex_loop 主表 + complex_loop_member 子表 | 结构最规范 | 过度设计；2 周工期难以承接；与「最小化改动」原则冲突 |

**推荐 A**：`complex_loop_group_id` + `complex_role` 两字段即可覆盖三类复杂回路，对齐评审报告 P1-5 建议。副回路 `include_in_evaluation` 仍保持 True（需算 KPI 供诊断），但聚合时按 group 去重。

### 决策点 2：NodeAggregator 输入侧去重规则

采用方案 A 后，`aggregate()` 输入侧去重规则建议：

```
对每个 complex_loop_group_id（非空）：
  - 若 group 内有 complex_role=MAIN 的回路且其结果非 INCONCLUSIVE → 仅取 MAIN 代表该 group
  - 若 group 内 MAIN 缺席或 INCONCLUSIVE → 退化为取 group 内 confidence 最高（或首个非 INCONCLUSIVE）的回路
  - group 内其余回路（SUB）不进入加权平均，但仍计入 inconclusive_count（若其本身 INCONCLUSIVE）
complex_loop_group_id 为空的回路：照常进入聚合（单回路）
```

**loop_count 口径**：`loop_count = 去重后的回路组数`（单回路计 1，串级 group 计 1，不论 group 内几行）。

**实施位置**：在 `aggregate()` 入口新增 `_dedup_complex_groups(loop_scores)` 预处理步骤，而非改各调用方。

### 决策点 3：日/月聚合 loop_count 口径

[node_aggregation.py:196](file:///Users/zhangping/DEV/CLPM/backend/app/services/node_aggregation.py#L196) 当前 `_max_loop_count(snaps)` 取各小时快照 loop_count 最大值。

- 小时快照 loop_count 已是「去重后组数」（决策点 2 落地后）。
- 日/月 `_max_loop_count` 仍合理（取窗口内最大组数，避免某小时数据缺失导致 loop_count 缩水）。
- **无需改动**，但需在迁移后验证：复杂回路配置变更（group 重组）会导致历史快照 loop_count 与现状不一致，属可接受偏差（历史快照保留当时口径）。

### 决策点 4：include_in_evaluation 接线缺口是否一并修复？

**强烈建议一并修复**（独立 commit，先于复杂回路实施）：

- [kpi_calc.py:359,758,963](file:///Users/zhangping/DEV/CLPM/backend/app/tasks/kpi_calc.py#L359) 三处 loop 查询追加 `.where(LoopLedger.include_in_evaluation.is_(True))` 用于「参与聚合的回路筛选」。
- 但需区分：单回路 KPI 计算仍应执行（include_in_evaluation=False 的回路也需算指标供回路详情页展示），只是**不进入节点聚合输入**。
- 因此过滤点应在「构建 loop_scores 传给 aggregate 之前」，而非「KPI 计算遍历阶段」。

### 决策点 5：复杂回路的 tag 关联模型是否需扩展？

当前 1 回路关联 7 个 tag（PV/SP/OP/MODE/PID_P/PID_I/PID_D），串级主副回路各自关联一套 tag 即可工作（副回路 SP 是主回路 OP，但 CLPM 只读 tag 不下写，故无需特殊处理）。

**结论**：tag 关联模型**无需扩展**，复杂回路只需在 loop_ledger 层建立 group 关系。tag 层保持现状。

---

## 四、影响面与风险

| 影响项 | 范围 | 风险等级 |
|--------|------|---------|
| loop_ledger schema（新增 2 可空字段） | alembic 迁移 + ORM | 低（可空，向后兼容） |
| NodeAggregator.aggregate（新增去重预处理） | 1 个方法 | 中（核心聚合逻辑，需充分单测） |
| KPI 计算 loop 查询（include_in_evaluation 接线） | kpi_calc 3 处查询 | 中（可能改变现有装置级评分，需对比前后） |
| 前端回路管理（新增分组配置 UI） | loop 配置页 | 低（增量页面） |
| 历史快照一致性 | 已落库快照 loop_count 不回填 | 低（可接受，标注口径变更点） |
| performance.py 跨模块耦合 | 预诊断标签/坏演员分布查询 | 中（评审报告 D1/D2 已提示，需同步审查） |

**最大风险**：include_in_evaluation 接线 + 复杂回路去重上线后，装置级综合评分会变化（此前被双计的复杂回路权重下降）。需在测试环境用真实数据对比前后评分，确认变化方向合理后再上线。

---

## 五、实施拆分建议（待评审通过后）

| 步骤 | 内容 | 依赖 | 工期估 |
|------|------|------|--------|
| S1 | include_in_evaluation 接线修复（独立 commit，先堵缺口） | 无 | 0.5 天 |
| S2 | loop_ledger 新增 complex_loop_group_id + complex_role 字段 + 迁移 | S1 | 0.5 天 |
| S3 | NodeAggregator 新增 `_dedup_complex_groups` + 单测 | S2 | 1.5 天 |
| S4 | 前端回路管理新增「回路分组」配置 UI | S2 | 1.5 天 |
| S5 | 真实数据前后评分对比验证 | S3,S4 | 1 天 |
| S6 | 文档更新（DDS/实现契约 §聚合章节） | S3 | 0.5 天 |

合计约 5.5 天，低于评审报告估的「4 周 + RFC 1 周」（因采用最小侵入的方案 A，且不涉及 tag 模型重构）。

---

## 六、待用户决策清单

请就以下 6 项逐条确认，确认后进入实施：

1. **数据模型**：采用方案 A（complex_loop_group_id + complex_role）？还是 B/C/D？
2. **去重代表**：group 内取 MAIN 回路代表；MAIN 缺席时退化为「confidence 最高」是否可接受？
3. **loop_count 口径**：去重后组数（单回路 1、串级 1）是否确认？
4. **include_in_evaluation 接线**：是否一并修复（S1 独立先做）？
5. **tag 模型**：确认不扩展（仅 loop_ledger 层建 group）？
6. **历史快照**：确认不回填，保留当时口径？
