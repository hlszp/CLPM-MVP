# CLPM 系统性重构方案

> 基于参考文档《控制回路性能评估分析软件使用手册》设计理念，对工作台、回路管理、性能评估、诊断中心四大模块进行系统性 UI/UX 优化与功能增强。
>
> - 版本：v1.0
> - 日期：2026-06-24
> - 状态：待实施
> - 任务总数：68 项（P0: 29 / P1: 39）

---

## 一、重构总览

### 1.1 核心问题诊断

| 层面 | 问题 | 根因 |
|---|---|---|
| 信息架构 | 工厂模型与回路台账分散，用户频繁切换页面 | 按技术实体而非用户任务组织页面 |
| 数据通路 | 看板读回路级快照，与节点级 API 割裂（已修复） | 历史遗留，缺乏统一数据源 |
| 监控维度 | 仅 today/7d/30d，缺时/日/月统计维度 | 缺日/月级聚合表 |
| 评分算法 | 6 KPI 全局加权，未按回路类型/级别区分 | 未对齐国标 GB/T 44693.2 |
| 配置能力 | 缺投用定义/MODEATTR/级别权重/位号触发监控 | 配置项不完整 |
| 诊断闭环 | 诊断独立模块，未嵌入回路详情 | 缺乏场景化集成 |

### 1.2 重构原则

1. **按用户任务组织页面**：回路管理整合为单页，性能评估拆为总览/监控/评估/报表
2. **统一数据通路**：所有 KPI 展示统一读 `kpi_node_snapshot_hourly` 及其日/月聚合
3. **对齐国标**：评分公式按回路类型加权，装置级按级别加权
4. **配置驱动**：MODE 映射、权重、监控触发均可配置，不硬编码
5. **诊断闭环**：诊断结果嵌入回路详情，一键创建跟踪任务

### 1.3 架构变更概览

```
重构前                          重构后
─────────                      ─────────
工作台（扁平 6 卡片）            工作台（树+仪表盘+列表）

回路管理                        回路管理（整合单页）
├ 工厂模型页                    ├ 左侧：工厂树（复用）
├ 回路台账页                    ├ 右侧：回路表格（联动）
├ 回路监控页                    ├ 抽屉：回路详情/编辑
├ 回路详情页                    └ 批量配置工具栏
└ Tag 关联页

性能评估                        性能评估（四子模块）
├ 看板                          ├ 总览（树+仪表盘+列表）
├ 排行                          ├ 监控（时/日/月维度）★新建
├ 统计报表                      ├ 评估（故障诊断指标）★新建
├ 指标配置                      ├ 排行（保留）
└ 引擎配置                      ├ 报表（保留）
                                └ 配置（增强：类型/级别权重）

诊断中心                        诊断中心（三段式重构）
├ 诊断列表                      ├ 诊断看板（标签分布+列表）
├ 诊断详情                      ├ 诊断详情（问题→原因→方案）
├ 波形分析                      ├ 闭环跟踪（PDF 实际生成）
├ 异常跟踪                      └ 统计报表（导出实际生成）
└ 统计报表
```

---

## 二、数据库层重构

### 2.1 新增表

#### `loop_mode_mapping`（投用定义配置）

```sql
CREATE TABLE loop_mode_mapping (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    loop_id         UUID NOT NULL REFERENCES loop_ledger(id) ON DELETE CASCADE,
    mode_value      INTEGER NOT NULL,           -- DCS 返回的 MODE 值
    mode_label      VARCHAR(20) NOT NULL,       -- AUTO / CAS / REMOTE / APC / MANUAL
    is_auto         BOOLEAN NOT NULL DEFAULT FALSE,  -- 是否算自动控制
    is_effective    BOOLEAN NOT NULL DEFAULT FALSE,  -- 是否算有效自动（不饱和）
    created_at      TIMESTAMP NOT NULL DEFAULT now(),
    UNIQUE(loop_id, mode_value)
);
COMMENT ON TABLE loop_mode_mapping IS '回路投用定义：MODE 值到控制模式的映射';
```

#### `loop_type_weight`（回路类型权重，对齐国标附表1）

```sql
CREATE TABLE loop_type_weight (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    loop_type       VARCHAR(20) NOT NULL UNIQUE,  -- STABLE / SLOW / FAST / LOGIC
    type_name       VARCHAR(50) NOT NULL,
    weight_a        NUMERIC(3,2) NOT NULL,  -- 准确率权重
    weight_f        NUMERIC(3,2) NOT NULL,  -- 快速率权重
    weight_s        NUMERIC(3,2) NOT NULL,  -- 平稳率权重
    description     TEXT,
    updated_by      VARCHAR(50),
    updated_at      TIMESTAMP DEFAULT now()
);
-- 初始数据（国标附表1）
INSERT INTO loop_type_weight (loop_type, type_name, weight_a, weight_f, weight_s, description) VALUES
    ('STABLE', '稳定型', 0.2, 0.3, 0.5, '温度/压力控制，a/f/s 相似'),
    ('SLOW',   '慢速型', 0.3, 0.1, 0.6, '缓慢调节，f 偏小'),
    ('FAST',   '快速型', 0.2, 0.5, 0.3, '副回路/速度控制，f 偏大'),
    ('LOGIC',  '逻辑型', 0.0, 0.5, 0.6, '逻辑规则控制，a 偏小');
```

#### `loop_level_weight`（回路级别权重，对齐国标附表2）

```sql
CREATE TABLE loop_level_weight (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    level           INTEGER NOT NULL UNIQUE,  -- 1 / 2 / 3
    level_name      VARCHAR(50) NOT NULL,
    weight          NUMERIC(3,1) NOT NULL,    -- 3.0 / 2.0 / 1.0
    description     TEXT,
    updated_by      VARCHAR(50),
    updated_at      TIMESTAMP DEFAULT now()
);
INSERT INTO loop_level_weight (level, level_name, weight, description) VALUES
    (1, '一级', 3.0, '决定性影响：负荷控制/联锁相关'),
    (2, '二级', 2.0, '辅助保障：稳定性/设备安全'),
    (3, '三级', 1.0, '次要辅助：维持辅助设备运行');
```

#### `kpi_node_snapshot_daily` / `kpi_node_snapshot_monthly`（日/月级聚合）

```sql
CREATE TABLE kpi_node_snapshot_daily (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    plant_node_id   UUID NOT NULL REFERENCES plant_node(id) ON DELETE CASCADE,
    stat_date       DATE NOT NULL,
    score           NUMERIC(5,2),
    good_value_rate NUMERIC(5,2),
    auto_mode_rate  NUMERIC(5,2),
    effective_auto_rate NUMERIC(5,2),
    steady_rate     NUMERIC(5,2),
    accuracy_rate   NUMERIC(5,2),
    fast_response_rate NUMERIC(5,2),
    oscillation_rate NUMERIC(5,2),
    saturation_rate NUMERIC(5,2),
    auto_loop_ratio NUMERIC(5,2),
    realtime_auto_rate NUMERIC(5,2),
    loop_count      INTEGER NOT NULL DEFAULT 0,
    status          VARCHAR(20) NOT NULL,
    algorithm_version VARCHAR(30),
    created_at      TIMESTAMP NOT NULL DEFAULT now(),
    UNIQUE(plant_node_id, stat_date)
);
-- kpi_node_snapshot_monthly 结构相同，stat_date 改为 stat_month (DATE, 月初)
```

### 2.2 字段扩展

| 表 | 新增字段 | 类型 | 说明 | 共享表通知 |
|---|---|---|---|---|
| `loop_ledger` | `level` | SMALLINT | 回路级别 1/2/3（默认3） | 否 |
| `loop_ledger` | `modeattr_tag_id` | UUID FK→tag_registry | APC 识别位号 | 否 |
| `loop_ledger` | `data_retention_days` | INTEGER | 数据保存周期（天） | 否 |
| `plant_node` | `monitor_tag_id` | UUID FK→tag_registry | 位号触发监控的位号 | 否 |
| `plant_node` | `monitor_trigger_value` | VARCHAR(20) | 触发监控的位号值 | 否 |
| `kpi_snapshot_hourly` | `stiction_coeff` | NUMERIC(5,2) | 黏滞系数 | ⚠️ **是，通知诊断中心** |
| `kpi_snapshot_hourly` | `steady_state_time` | NUMERIC(8,2) | 稳态时间（秒） | ⚠️ **是，通知诊断中心** |
| `kpi_snapshot_hourly` | `output_travel_index` | NUMERIC(8,2) | 输出值行程指数 | ⚠️ **是，通知诊断中心** |

> ⚠️ `kpi_snapshot_hourly` 为诊断中心与性能评估共享表，新增 3 个字段需同步通知诊断中心模块。新增字段均为 nullable，向后兼容。

### 2.3 迁移脚本

```python
# backend/alembic/versions/x1y2z3_add_loop_config_tables.py
"""add loop config tables and kpi fields

Revision ID: x1y2z3
Revises: f7a8b9c0d1e2
"""
def upgrade():
    # 1. 新建表
    op.create_table("loop_mode_mapping", ...)
    op.create_table("loop_type_weight", ...)
    op.create_table("loop_level_weight", ...)
    op.create_table("kpi_node_snapshot_daily", ...)
    op.create_table("kpi_node_snapshot_monthly", ...)
    # 2. 加字段
    op.add_column("loop_ledger", sa.Column("level", sa.SmallInteger, nullable=True, default=3))
    op.add_column("loop_ledger", sa.Column("modeattr_tag_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("tag_registry.id"), nullable=True))
    op.add_column("loop_ledger", sa.Column("data_retention_days", sa.Integer, nullable=True))
    op.add_column("plant_node", sa.Column("monitor_tag_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("tag_registry.id"), nullable=True))
    op.add_column("plant_node", sa.Column("monitor_trigger_value", sa.String(20), nullable=True))
    # 3. 共享表加字段（通知诊断中心）
    op.add_column("kpi_snapshot_hourly", sa.Column("stiction_coeff", sa.Numeric(5, 2), nullable=True))
    op.add_column("kpi_snapshot_hourly", sa.Column("steady_state_time", sa.Numeric(8, 2), nullable=True))
    op.add_column("kpi_snapshot_hourly", sa.Column("output_travel_index", sa.Numeric(8, 2), nullable=True))
    # 4. 初始数据
    op.bulk_insert(loop_type_weight_table, [...])
    op.bulk_insert(loop_level_weight_table, [...])
```

---

## 三、后端 API 重构

### 3.1 新增 API

| 端点 | 方法 | 功能 | 模块 |
|---|---|---|---|
| `/loops/{id}/mode-mapping` | GET/PUT | 投用定义配置 | 回路管理 |
| `/loops/batch-config` | POST | 批量配置（监控/统计/删除） | 回路管理 |
| `/configs/loop-type-weights` | GET/PUT | 回路类型权重配置（P2 #30 B7 前缀统一为复数） | 性能评估 |
| `/configs/loop-level-weights` | GET/PUT | 回路级别权重配置（P2 #30 B7 前缀统一为复数） | 性能评估 |
| `/performance/nodes/{id}/monitor` | GET | 节点级监控数据（时/日/月） | 性能评估 |
| `/performance/loops/diagnose` | GET | 回路级故障诊断指标监控 | 性能评估 |
| `/performance/nodes/daily` | GET | 日级聚合查询 | 性能评估 |
| `/performance/nodes/monthly` | GET | 月级聚合查询 | 性能评估 |
| `/diagnosis/{loopId}/recommendations` | GET | 解决方案推荐 | 诊断中心 |
| `/diagnosis/{loopId}/report` | POST | 诊断建议书 PDF 生成 | 诊断中心 |

### 3.2 改造 API

| 端点 | 改造内容 |
|---|---|
| `GET /performance/board` | 已完成：改读 `kpi_node_snapshot_hourly` |
| `GET /performance/nodes/overview` | 增加 `realtime_auto_rate`（已完成） |
| `query_realtime_auto_rate()` | 改为读 `loop_mode_mapping` 配置，不硬编码 {1,2,3} |
| `calculate_composite_score()` | 改为按 `loop_type` 查 `loop_type_weight` 获取 a/f/s 权重 |
| `aggregate_node_snapshot()` | 改为按 `loop.level` 查 `loop_level_weight` 获取装置级聚合权重 |
| `GET /loops` | 增加 `level` 筛选、`monitor_status` 筛选 |
| `GET /diagnosis/list` | 增加标签分布概览统计 |

### 3.3 评分算法对齐

**当前算法**（`kpi_calc.py`）：
```python
# 6 大 KPI 全局加权
score = Σ(metric_value × metric_weight) / Σ(enabled_weights)
```

**重构后**（对齐国标公式）：
```python
# 回路级：P = [(A*a)+(F*f)+(S*s)]/(a+f+s) * R
def calculate_composite_score_v2(
    accuracy_rate,      # A 准确率
    fast_response_rate, # F 快速率
    steady_rate,        # S 平稳率
    effective_auto_rate,# R 有效自控率
    loop_type,          # 回路类型 → 查 loop_type_weight
):
    weights = query_loop_type_weight(loop_type)  # {a, f, s}
    a, f, s = weights["weight_a"], weights["weight_f"], weights["weight_s"]
    numerator = (accuracy_rate * a + fast_response_rate * f + steady_rate * s)
    denominator = (a + f + s)
    base_score = numerator / denominator if denominator > 0 else 0
    return base_score * (effective_auto_rate / 100)  # R 作为乘数

# 装置级：Σ(w_i * P_i) / Σw_i，w_i 按回路级别
def aggregate_node_score_v2(loop_scores: list[dict]):
    # loop_scores = [{"score": P_i, "level": 1/2/3}, ...]
    level_weights = query_loop_level_weights()  # {1: 3.0, 2: 2.0, 3: 1.0}
    numerator = sum(s["score"] * level_weights.get(s["level"], 1.0) for s in loop_scores)
    denominator = sum(level_weights.get(s["level"], 1.0) for s in loop_scores)
    return numerator / denominator if denominator > 0 else 0
```

**向后兼容**：新增 `algorithm_version = "KPI_CALC_v2.0"`，历史数据保留 v1.0。

---

## 四、前端页面重构

### 4.1 回路管理整合方案

**合并**：`loop/factory.vue` + `loop/ledger.vue` + `loop/tag-mapping.vue` → `loop/manage.vue`

```
┌─────────────────────────────────────────────────────────────┐
│  回路管理                                                    │
├────────────┬────────────────────────────────────────────────┤
│  工厂树     │  工具栏：[新建回路] [批量配置▼] [导入] [导出]    │
│  🔍 搜索    │  筛选：[控制类型▼] [级别▼] [监控状态▼] [搜索]    │
│  ├ 化工厂   │ ┌──────────────────────────────────────────┐  │
│  │ ├ 催化   │ │☐│Tag │描述 │类型│级别│监控│评分│Tag状态│操作│  │
│  │ │ ├反应  │ │☐│FIC-101│...│流量│2 │ ✓ │88.5│完整 │⋯  │  │
│  │ │ └分馏  │ │☐│TIC-102│...│温度│1 │ ✓ │75.3│完整 │⋯  │  │
│  │ └ 常减压  │ │☐│LIC-201│...│液位│3 │ ✗ │ — │缺MODE│⋯  │  │
│  └ 加氢     │ └──────────────────────────────────────────┘  │
│            │  分页 + 批量操作栏（选中后浮现）                  │
├────────────┴────────────────────────────────────────────────┤
│  右侧抽屉（点击行展开）：                                      │
│  ┌─ 基础信息 ─┬─ Tag 关联 ─┬─ 评估参数 ─┬─ 投用定义 ─┐       │
│  │ Tag/描述/  │ PV/SP/OP/  │ 统计周期/  │ MODE 值映射│       │
│  │ 类型/级别/ │ MODE/PID   │ 稳态参数/  │ APC 识别   │       │
│  │ 权重/监控  │ 7 槽位配置 │ 存储周期   │           │       │
│  └───────────┴───────────┴───────────┴───────────┘       │
└─────────────────────────────────────────────────────────────┘
```

**路由变更**：
- `/loop/factory` → 废弃，重定向到 `/loop/manage`
- `/loop/ledger` → 废弃，重定向到 `/loop/manage`
- `/loop/manage` → 新增（整合页）
- `/loop/monitor` → 保留（实时监控独立页）
- `/loop/detail/:id` → 保留（详情页，增加"智能诊断"Tab）
- `/loop/aas` → 保留（AAS 配置独立页）

### 4.2 性能评估四子模块方案

```
性能评估
├─ 总览 (/metric/dashboard) — 重构
│   ┌──────────┬──────────────────────────────┐
│   │ 工厂树   │ ┌─实时自控率仪表盘──────────┐ │
│   │         │ │  环形图：自动 12/总 15     │ │
│   │         │ │  手动 3 | 自动 12 | APC 0  │ │
│   │         │ └──────────────────────────┘ │
│   │         │ ┌─整点 KPI 卡片─────────────┐ │
│   │         │ │ 评分 80.2 │自控率 88%│平稳│ │
│   │         │ │ 实时自控率 87.5% │ ...   │ │
│   │         │ └──────────────────────────┘ │
│   │         │ ┌─详细列表─────────────────┐ │
│   │         │ │ 等级筛选▼ 参数搜索🔍      │ │
│   │         │ │ 装置│评分│自控│平稳│等级 │ │
│   │         │ └──────────────────────────┘ │
│   └──────────┴──────────────────────────────┘
│
├─ 监控 (/metric/monitor) — 新建★
│   ┌─ 实时监控 ─┬─ 历史查询 ─┐
│   │ 上一个整点  │ 时/日/月▼  │
│   │ 查询类型：  │ 查询类型：  │
│   │ 厂/装置/    │ 厂/装置/    │
│   │ 回路组/回路 │ 回路组/回路 │
│   │            │            │
│   │ KPI 表格   │ KPI 表格   │
│   │ (可排序)   │ (可排序)   │
│   └────────────┴────────────┘
│
├─ 评估 (/metric/diagnose) — 新建★
│   ┌─ 实时监控 ─┬─ 历史查询 ─┐
│   │ 回路级故障诊断指标        │
│   │ 振荡率│黏滞│饱和│稳态时间│
│   │ 行程指数│好值率│投用率   │
│   │ 筛选：指标阈值/投用/等级  │
│   └────────────┴────────────┘
│
├─ 排行 (/metric/ranking) — 保留
├─ 报表 (/metric/statistics) — 保留
└─ 配置 (/metric/config) — 增强
    ├ 指标配置（保留）
    ├ 引擎配置（保留）
    ├ 回路类型权重（新建）★
    └ 回路级别权重（新建）★
```

### 4.3 诊断中心三段式重构

**诊断详情页**（`diagnosis/detail.vue`）重构为三段式：

```
┌─ 问题定位路径 ──────────────────────────────────┐
│ 化工厂 > 催化裂化 > 反应区 > FIC-101 > [振荡]    │
├─────────────────────────────────────────────────┤
│ ┌─ 证据链 ────────────────────────────────────┐ │
│ │ 📊 时序图（PV/SP/OP）标注异常区间            │ │
│ │ 特征值：振荡频率 0.15Hz | 幅值 ±2.3% | ...  │ │
│ └─────────────────────────────────────────────┘ │
│ ┌─ 可能原因 ──────────────────────────────────┐ │
│ │ 1. PID 参数整定不当（置信度 0.82）           │ │
│ │ 2. 阀门粘滞（置信度 0.65）                   │ │
│ │ 3. 外部周期性干扰（置信度 0.43）             │ │
│ └─────────────────────────────────────────────┘ │
│ ┌─ 解决方案推荐 ──────────────────────────────┐ │
│ │ ⭐ 优先级1：重新整定 PID（跳转整定工作台）   │ │
│ │   建议：降低 Kp 30%，增加 Td 15%            │ │
│ │ ⭐ 优先级2：检查阀门执行机构（创建跟踪任务） │ │
│ │ ⭐ 优先级3：排查外部干扰源                   │ │
│ │ [一键创建跟踪任务] [跳转整定] [导出建议书]   │ │
│ └─────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────┘
```

**回路详情页**（`loop/detail.vue`）增加"智能诊断"Tab：

```
回路详情：FIC-101
┌─ 实时数据 ─┬─ PID 参数 ─┬─ 性能指标 ─┬─ 智能诊断 ─┐
│ PV/SP/OP  │ Kp/Ki/Kd  │ 6 大 KPI   │ 诊断结果   │
│ 波形图     │           │            │ 可能原因   │
│           │           │            │ 优化建议   │
└───────────┴───────────┴────────────┴───────────┘
```

### 4.4 工作台优化

```
工作台
┌─ 顶部 KPI 卡片（保留 6 卡片，增加实时自控率）──────────┐
│ 评分 80.2 │自控率 88%│平稳率 85%│实时自控 87%│...   │
├─ 中部 ─────────────────────────────────────────────┤
│ 左：低效回路 Top 10（保留）  │ 右：待处理异常（保留）│
├─ 底部 ─────────────────────────────────────────────┤
│ 平稳率趋势图（保留）│ 装置排名（保留）│ 坏演员分布   │
└────────────────────────────────────────────────────┘
```

---

## 五、改造任务清单

### 5.1 数据库层（DB）

| ID | 任务 | 优先级 | 依赖 | 共享通知 |
|---|---|---|---|---|
| DB-01 | 新建 `loop_mode_mapping` 表 + 迁移脚本 | P0 | — | — |
| DB-02 | 新建 `loop_type_weight` 表 + 初始数据 | P0 | — | — |
| DB-03 | 新建 `loop_level_weight` 表 + 初始数据 | P0 | — | — |
| DB-04 | `loop_ledger` 加 `level`/`modeattr_tag_id`/`data_retention_days` 字段 | P0 | — | — |
| DB-05 | `plant_node` 加 `monitor_tag_id`/`monitor_trigger_value` 字段 | P1 | — | — |
| DB-06 | `kpi_snapshot_hourly` 加黏滞/稳态时间/行程指数字段 | P1 | — | ⚠️ 通知诊断中心 |
| DB-07 | 新建 `kpi_node_snapshot_daily` 表 | P1 | — | — |
| DB-08 | 新建 `kpi_node_snapshot_monthly` 表 | P1 | — | — |
| DB-09 | 同步更新 `db/postgresql/01_schema.sql` | P0 | DB-01~08 | — |

### 5.2 后端模型层（MODEL）

| ID | 任务 | 优先级 | 依赖 |
|---|---|---|---|
| MODEL-01 | 新建 `LoopModeMapping` 模型 | P0 | DB-01 |
| MODEL-02 | 新建 `LoopTypeWeight` 模型 | P0 | DB-02 |
| MODEL-03 | 新建 `LoopLevelWeight` 模型 | P0 | DB-03 |
| MODEL-04 | `LoopLedger` 加 `level`/`modeattr_tag_id`/`data_retention_days` | P0 | DB-04 |
| MODEL-05 | `PlantNode` 加 `monitor_tag_id`/`monitor_trigger_value` | P1 | DB-05 |
| MODEL-06 | `KpiSnapshotHourly` 加 3 个诊断字段 | P1 | DB-06 |
| MODEL-07 | 新建 `KpiNodeSnapshotDaily` 模型 | P1 | DB-07 |
| MODEL-08 | 新建 `KpiNodeSnapshotMonthly` 模型 | P1 | DB-08 |

### 5.3 后端服务层（SVC）

| ID | 任务 | 优先级 | 依赖 |
|---|---|---|---|
| SVC-01 | 投用定义 CRUD 服务 | P0 | MODEL-01 |
| SVC-02 | 回路类型权重 CRUD 服务 | P0 | MODEL-02 |
| SVC-03 | 回路级别权重 CRUD 服务 | P0 | MODEL-03 |
| SVC-04 | `query_realtime_auto_rate()` 改读投用定义 | P0 | SVC-01 |
| SVC-05 | 评分算法 v2：按回路类型加权 | P0 | SVC-02 |
| SVC-06 | 节点聚合 v2：按回路级别加权 | P0 | SVC-03 |
| SVC-07 | 日级聚合服务（小时快照→日快照） | P1 | MODEL-07 |
| SVC-08 | 月级聚合服务（日快照→月快照） | P1 | MODEL-08 |
| SVC-09 | 故障诊断指标计算（黏滞/稳态/行程） | P1 | MODEL-06 |
| SVC-10 | 位号触发监控逻辑 | P1 | MODEL-05 |
| SVC-11 | 诊断解决方案推荐模板服务 | P1 | — |
| SVC-12 | 诊断建议书 PDF 实际生成 | P1 | SVC-11 |
| SVC-13 | 诊断统计报表导出 | P1 | — |
| SVC-14 | 批量配置服务（监控/统计/删除） | P1 | — |

### 5.4 后端 API 层（API）

| ID | 任务 | 优先级 | 依赖 |
|---|---|---|---|
| API-01 | `GET/PUT /loops/{id}/mode-mapping` | P0 | SVC-01 |
| API-02 | `GET/PUT /configs/loop-type-weights` | P0 | SVC-02 |
| API-03 | `GET/PUT /configs/loop-level-weights` | P0 | SVC-03 |
| API-04 | `POST /loops/batch-config` | P1 | SVC-14 |
| API-05 | `GET /performance/nodes/{id}/monitor?dimension=hour/day/month` | P1 | SVC-07/08 |
| API-06 | `GET /performance/loops/diagnose` | P1 | SVC-09 |
| API-07 | `GET /diagnosis/{loopId}/recommendations` | P1 | SVC-11 |
| API-08 | `POST /diagnosis/{loopId}/report` | P1 | SVC-12 |
| API-09 | `GET /loops` 增加 `level`/`monitor_status` 筛选 | P1 | MODEL-04 |

### 5.5 后端任务调度（TASK）

| ID | 任务 | 优先级 | 依赖 |
|---|---|---|---|
| TASK-01 | Celery Beat 增加日级聚合任务（每日 00:05） | P1 | SVC-07 |
| TASK-02 | Celery Beat 增加月级聚合任务（每月 1 日 00:10） | P1 | SVC-08 |
| TASK-03 | KPI 计算任务集成故障诊断指标计算 | P1 | SVC-09 |

### 5.6 前端页面（FE）

| ID | 任务 | 优先级 | 依赖 |
|---|---|---|---|
| FE-01 | 新建 `loop/manage.vue` 整合页（树+表+抽屉） | P0 | API-09 |
| FE-02 | 投用定义配置组件（抽屉内 Tab） | P0 | API-01 |
| FE-03 | 批量配置工具栏组件 | P1 | API-04 |
| FE-04 | 废弃 `loop/factory.vue` + `loop/ledger.vue`，重定向 | P0 | FE-01 |
| FE-05 | `loop/detail.vue` 增加"智能诊断"Tab | P1 | API-07 |
| FE-06 | `metric/dashboard.vue` 重构（树+仪表盘+列表） | P0 | — |
| FE-07 | 实时自控率仪表盘组件（环形图） | P0 | — |
| FE-08 | 新建 `metric/monitor.vue`（时/日/月维度） | P1 | API-05 |
| FE-09 | 新建 `metric/diagnose.vue`（故障诊断指标） | P1 | API-06 |
| FE-10 | 新建 `metric/type-weight.vue`（类型权重配置） | P0 | API-02 |
| FE-11 | 新建 `metric/level-weight.vue`（级别权重配置） | P0 | API-03 |
| FE-12 | `diagnosis/detail.vue` 三段式重构 | P1 | API-07 |
| FE-13 | 诊断解决方案推荐组件 | P1 | API-07 |
| FE-14 | 诊断建议书 PDF 导出按钮 | P1 | API-08 |
| FE-15 | `dashboard/workbench.vue` 增加实时自控率卡片 | P0 | — |
| FE-16 | 路由配置更新（新增/废弃/重定向） | P0 | FE-01/08/09 |

### 5.7 测试（TEST）

| ID | 任务 | 优先级 | 依赖 |
|---|---|---|---|
| TEST-01 | 投用定义 CRUD 单元测试 | P0 | API-01 |
| TEST-02 | 评分算法 v2 单元测试（4 种回路类型） | P0 | SVC-05 |
| TEST-03 | 节点聚合 v2 单元测试（3 种级别加权） | P0 | SVC-06 |
| TEST-04 | 实时自控率读投用定义测试 | P0 | SVC-04 |
| TEST-05 | 日/月级聚合 E2E 测试 | P1 | TASK-01/02 |
| TEST-06 | 故障诊断指标计算测试 | P1 | SVC-09 |
| TEST-07 | 回路管理整合页 E2E 测试 | P1 | FE-01 |
| TEST-08 | 性能监控页 E2E 测试 | P1 | FE-08 |
| TEST-09 | 诊断三段式 E2E 测试 | P1 | FE-12 |

---

## 六、实施顺序与依赖图

```
Phase 1 (P0 基础设施) — 第 1-3 周
─────────────────────────────────
DB-01~04,09 → MODEL-01~04 → SVC-01~06 → API-01~03
                                        ↓
                                    TEST-01~04
                                        ↓
DB-06 ──→ MODEL-06 ──→ SVC-09 ──→ API-06 ──→ TEST-06
  ↓(通知诊断中心)

Phase 2 (P0 前端核心) — 第 3-5 周
─────────────────────────────────
API-01~03 就绪 → FE-01/02/06/07/10/11/15/16 → TEST-07

Phase 3 (P1 数据聚合) — 第 5-7 周
─────────────────────────────────
DB-07/08 → MODEL-07/08 → SVC-07/08 → TASK-01/02 → API-05 → FE-08 → TEST-05/08

Phase 4 (P1 诊断增强) — 第 7-9 周
─────────────────────────────────
SVC-11/12/13 → API-07/08 → FE-05/12/13/14 → TEST-09

Phase 5 (P1 配置增强) — 第 9-10 周
─────────────────────────────────
DB-05 → MODEL-05 → SVC-10/14 → API-04/09 → FE-03
```

---

## 七、关键设计决策

### 7.1 评分算法兼容性

| 决策 | 选择 | 理由 |
|---|---|---|
| 是否保留 v1 算法 | 是，通过 `algorithm_version` 区分 | 历史数据不重算，新数据用 v2 |
| v2 是否作为默认 | 是，配置项可切换 | 对齐国标，但保留回退能力 |
| 6 大 KPI 是否废弃 | 否，保留作为展示指标 | 评分公式用 4 指标，展示仍用 8 指标 |

### 7.2 页面整合策略

| 决策 | 选择 | 理由 |
|---|---|---|
| 工厂模型页是否完全废弃 | 是，功能并入整合页 | 树结构作为侧边栏，不再独立 |
| 回路台账页是否保留 | 否，重定向到整合页 | 整合页表格覆盖台账功能 |
| Tag 关联是否独立页 | 否，并入抽屉 Tab | 减少页面跳转 |
| AAS 配置是否合并 | 否，保留独立页 | 低频管理操作，不属于日常回路管理 |

### 7.3 共享表变更通知

`kpi_snapshot_hourly` 新增 3 个字段（黏滞系数/稳态时间/行程指数），需通知诊断中心模块：

- 字段均为 nullable，向后兼容
- 诊断中心读取这些字段用于故障诊断标签计算
- 性能评估模块负责写入这些字段（KPI 计算任务中）

---

## 八、改造任务清单总览

| 阶段 | 任务数 | P0 | P1 | 周期 |
|---|---|---|---|---|
| 数据库层 | 9 | 5 | 4 | W1-W5 |
| 模型层 | 8 | 4 | 4 | W1-W5 |
| 服务层 | 14 | 6 | 8 | W1-W9 |
| API 层 | 9 | 3 | 6 | W2-W9 |
| 任务调度 | 3 | 0 | 3 | W5-W7 |
| 前端 | 16 | 7 | 9 | W3-W9 |
| 测试 | 9 | 4 | 5 | W3-W10 |
| **合计** | **68** | **29** | **39** | **10 周** |

---

## 九、风险与应对

| 风险 | 概率 | 影响 | 应对 |
|---|---|---|---|
| 共享表 `kpi_snapshot_hourly` 字段变更影响诊断中心 | 中 | 高 | 提前通知，加字段不删字段，向后兼容 |
| 评分算法改国标公式导致历史数据不一致 | 中 | 高 | 新增 `algorithm_version` 区分，历史数据保留原值 |
| 日/月聚合表数据量大查询慢 | 中 | 中 | 分区表 + 物化视图 + Redis 缓存 |
| 前端单页整合后性能下降 | 低 | 中 | 虚拟滚动 + 懒加载 + 按需请求 |
| 用户对新交互不适应 | 中 | 中 | 灰度上线 + 培训 + 旧版保留过渡期 |

---

## 十、参考文档

- 《控制回路性能评估分析软件使用手册》（`docs/预研文档/`）
- GB/T 44693.2-2024 控制回路性能评估与优化技术规范
- 《关键算法设计说明》（`docs/设计文档/03-ADS/`）
- 数据库 DDL（`db/postgresql/01_schema.sql`）
