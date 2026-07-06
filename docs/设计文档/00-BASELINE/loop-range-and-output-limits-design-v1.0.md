# 回路量程与 OP 输出限位补全设计规范 v1.0

**版本**：v1.0（增量设计文档，基于 v6.0 体系）
**生效日期**：2026-07-06
**作者**：mb 机器（Trae）
**基线**：PRD v6.0 / FDS v6.0 / ADS v6.0 / DDS v6.0 / IDS v6.0 / UI/UX v6.0
**适用范围**：本规范作为 v6.0 设计体系的增量补充，修订回路配置领域的三项缺失：

1. PV/OP 量程与工程单位的数据来源与传递链路
2. OP 输出限位字段定义与配置规则
3. 饱和率算法对 OP 输出限位的读取调整

本文档与 v6.0 体系冲突时，以本文档为准。后续 v6.1 升级时将吸收本规范内容并入各主文档。

---

## 1. 背景与目标

### 1.1 背景

v6.0 重构后回路管理模块存在以下数据模型缺失：

- **LoopLedger 表**没有 PV/OP 量程字段、OP 输出限位字段
- **饱和率算法**从 `bundle.data_block.signals['op_low'/'op_high']` 读取 OP 上下限，回退到硬编码 `DEFAULT_OP_LOW=0.0` / `DEFAULT_OP_HIGH=100.0`，与实际控制器输出限位脱钩
- **DataPlanner** 已查询 PV Tag 的 `range_min/range_max`，但未填充到 `signals` 字典供下游算法使用
- **回路列表/详情**不显示量程与限位，用户无法在 UI 上核对配置
- **回路编辑表单**不支持 OP 输出限位配置

### 1.2 目标

| 目标 | 验收标准 |
|---|---|
| PV/OP 量程数据来源明确 | 从关联 Tag 的 `range_min/range_max/unit` 引用，不在 Loop 表冗余存储 |
| OP 输出限位字段定义 | LoopLedger 新增 `op_output_lower_limit` / `op_output_upper_limit` |
| OP 输出限位默认值 | 默认等于 OP Tag 量程，用户可在量程范围内自定义更窄的限位 |
| 饱和率算法调整 | 从 Loop 表读取 OP 输出限位，不再使用硬编码 0/100 |
| UI 展示 | 回路列表/详情显示量程与限位；编辑表单可修改限位 |
| 列表筛选增强 | 回路列表支持按 `loopType`（温度/压力/流量等）筛选 |

### 1.3 范围

| 项 | 包含 | 不包含 |
|---|---|---|
| 数据模型 | LoopLedger 新增 2 个字段 | Tag 表结构变更（已具备 range_min/range_max/unit） |
| API | CreateLoop/UpdateLoop/LoopDetail 增量字段 | 监控接口字段变更 |
| 算法 | SaturationRateCalculator OP 上下限来源调整 | 其他 11 个指标计算器 |
| UI | 回路列表/详情/编辑表单 | 监控页面、诊断页面 |
| 列表筛选 | 新增 loopType 筛选 | 其他筛选条件 |

---

## 2. 业务需求（PRD 增量）

### 2.1 需求 1：回路量程与单位的数据来源

**作为** 系统管理员/工程师，
**我希望** 回路配置自动引用 PV/OP 关联 Tag 的量程与工程单位，
**以便** 在回路列表/详情中直接查看量程范围，无需跳转到 Tag 管理页面。

**业务规则**：

- PV 量程 = `loop.tag_mapping[PV].tag.range_min/range_max`
- PV 单位 = `loop.tag_mapping[PV].tag.unit`
- OP 量程 = `loop.tag_mapping[OP].tag.range_min/range_max`
- OP 单位 = `loop.tag_mapping[OP].tag.unit`
- 当 PV/OP Tag 未关联时，量程与单位字段返回 `null`
- 当 Tag 关联变更时，量程与单位自动跟随更新（不冗余存储）

### 2.2 需求 2：OP 输出限位配置

**作为** 系统管理员/工程师，
**我希望** 在回路编辑表单中配置 OP 输出上限位与下限位，
**以便** 饱和率算法能准确判断控制器输出是否饱和。

**业务规则**：

- OP 输出限位是控制器配置，独立于 Tag 量程
- 默认值：等于 OP 关联 Tag 的量程上下限（用户首次配置时自动填充）
- 自定义范围：用户可在 `[OP Tag range_min, OP Tag range_max]` 范围内设置更窄的限位
  - 例如：OP Tag 量程 0-100，用户设置限位为 10-90（控制器在 10% 以下或 90% 以上视为饱和）
- 校验规则：
  - `op_output_lower_limit` 必须 < `op_output_upper_limit`
  - `op_output_lower_limit` 必须 ≥ OP Tag `range_min`
  - `op_output_upper_limit` 必须 ≤ OP Tag `range_max`
  - 违反校验返回 `ERR_OP_LIMIT_OUT_OF_RANGE` (400)
- 当 OP Tag 未关联或 Tag 量程为 null 时，限位字段允许为 null，饱和率算法使用默认值 0/100

### 2.3 需求 3：饱和率算法使用 OP 输出限位

**作为** KPI 计算引擎，
**我希望** 饱和率算法从回路配置的 OP 输出限位字段读取上下限，
**以便** 饱和率计算结果反映实际控制器输出限位，而非硬编码的 0/100。

**业务规则**：

- 饱和率 = `duration(op >= op_upper_limit - epsilon OR op <= op_lower_limit + epsilon) / duration(*) * 100`
- OP 上下限优先级（从高到低）：
  1. `loop.op_output_lower_limit` / `loop.op_output_upper_limit`（非 null 时）
  2. OP Tag `range_min` / `range_max`（OP Tag 已关联且量程非 null 时）
  3. 默认值 `DEFAULT_OP_LOW=0.0` / `DEFAULT_OP_HIGH=100.0`（兜底）
- `epsilon`（容差）保持当前默认值 2.0，未来可通过 `EngineRule` 参数化

### 2.4 需求 4：回路列表按类型筛选

**作为** 工程师，
**我希望** 回路管理列表支持按回路类型（温度/压力/液位/流量/分析/速度/其他）筛选，
**以便** 快速定位某类工艺回路。

**业务规则**：

- 筛选字段：`loopType`（单选）
- 枚举值：`TEMPERATURE` / `PRESSURE` / `LEVEL` / `FLOW` / `ANALYSIS` / `SPEED` / `OTHER`
- 与现有 `controlType` 筛选独立，可组合使用

---

## 3. 功能设计（FDS 增量）

### 3.1 回路量程引用功能

**功能描述**：回路 API 响应中增加 `pvRange` / `pvUnit` / `opRange` / `opUnit` 字段，数据来源为关联 Tag 表的 JOIN 查询。

**功能规则**：

| 规则 | 说明 |
|---|---|
| 数据来源 | `tag_registry.range_min` / `range_max` / `unit`，通过 `loop_tag_mapping` JOIN |
| 不冗余存储 | Loop 表不存储量程字段，避免数据不一致 |
| Tag 未关联 | 量程/单位字段返回 `null` |
| Tag 量程变更 | 自动反映到回路响应中（无需手动同步） |
| 显示格式 | 量程以 `[min, max]` 对象形式返回，单位为字符串 |

**接口影响**：
- `GET /api/v1/loops` 列表响应增加 `pvRange` / `pvUnit` / `opRange` / `opUnit`
- `GET /api/v1/loops/{id}` 详情响应 `basicInfo` 增加 `pvRange` / `pvUnit` / `opRange` / `opUnit`

### 3.2 OP 输出限位配置功能

**功能描述**：回路编辑表单中新增 OP 输出限位配置项，用户可设置限位或使用默认值。

**功能规则**：

| 规则 | 说明 |
|---|---|
| 字段 | `opOutputLowerLimit` / `opOutputUpperLimit`（可选） |
| 默认行为 | 用户首次打开编辑表单时，若限位字段为 null，自动填充 OP Tag 量程 |
| 范围校验 | `OP Tag range_min ≤ lower_limit < upper_limit ≤ OP Tag range_max` |
| 保存触发 | 修改限位字段后保存回路时触发校验 |
| 错误处理 | 违反校验返回 `ERR_OP_LIMIT_OUT_OF_RANGE` (400) |
| 审计日志 | 限位变更记录到 `sys_audit_log`，操作类型 `LOOP_UPDATE` |
| Tag 关联未变 | 修改限位不重新推导 `status` 字段 |

**接口影响**：
- `POST /api/v1/loops` 创建回路请求体增加 `opOutputLowerLimit` / `opOutputUpperLimit`
- `PUT /api/v1/loops/{id}` 更新回路请求体增加 `opOutputLowerLimit` / `opOutputUpperLimit`
- `GET /api/v1/loops/{id}` 详情响应 `basicInfo` 增加 `opOutputLowerLimit` / `opOutputUpperLimit`

### 3.3 饱和率算法调整

**功能描述**：饱和率计算器的 OP 上下限来源从 `signals` 字典改为 `loop` 表字段。

**功能规则**：

| 规则 | 说明 |
|---|---|
| 优先级 1 | `loop.op_output_lower_limit` / `op_output_upper_limit`（非 null） |
| 优先级 2 | OP Tag `range_min` / `range_max`（OP Tag 已关联且量程非 null） |
| 优先级 3 | 默认值 0.0 / 100.0（兜底） |
| epsilon | 保持 2.0，从 `signals` 字典读取或使用默认 |
| 数据传递 | DataPlanner 在 `_assemble_bundles` 时将 OP 上下限填充到 `signals['op_low']` / `signals['op_high']` |

### 3.4 回路列表按类型筛选功能

**功能描述**：回路列表查询参数增加 `loopType` 筛选。

**功能规则**：

| 规则 | 说明 |
|---|---|
| 查询参数 | `loopType`（可选），枚举值 `TEMPERATURE` / `PRESSURE` / `LEVEL` / `FLOW` / `ANALYSIS` / `SPEED` / `OTHER` |
| 筛选方式 | 精确匹配 `loop_ledger.loop_type` |
| 与 `controlType` 关系 | 独立筛选，可组合使用 |
| 已选徽章 | 已选 `loopType` 在筛选区显示徽章 |

---

## 4. 应用设计（ADS 增量）

### 4.1 量程数据流

```
tag_registry (range_min/range_max/unit)
       ↓ JOIN
loop_tag_mapping (tag_id for PV/OP role)
       ↓
loop_ledger (查询时 JOIN，不冗余存储)
       ↓ API 响应
{ pvRange: {min, max}, pvUnit, opRange: {min, max}, opUnit }
```

**应用层规则**：
- `loop.py` service 在构造列表/详情响应时，JOIN `loop_tag_mapping` + `tag_registry` 获取 PV/OP Tag 的量程
- 一次查询批量 JOIN（避免 N+1）：列表查询使用 `IN` 批量获取所有 PV/OP Tag ID 的量程
- 量程字段为 `null` 时不影响其他字段返回

### 4.2 OP 输出限位数据流

```
用户编辑表单
       ↓
API 请求 (opOutputLowerLimit/opOutputUpperLimit)
       ↓ 校验：OP Tag range_min ≤ lower < upper ≤ OP Tag range_max
loop_ledger.op_output_lower_limit / op_output_upper_limit
       ↓ KPI 计算时读取
DataPlanner._assemble_bundles() → signals['op_low'/'op_high']
       ↓
SaturationRateCalculator._read_op_bounds()
```

**应用层规则**：
- `loop.py` service 的 `create_loop` / `update_loop` 函数新增 2 个参数 `op_output_lower_limit` / `op_output_upper_limit`
- 校验逻辑在 service 层实现（不依赖数据库约束）
- DataPlanner 在 `_default_config_loader` 中查询 Loop 时读取这 2 个字段，填充到 `signals`
- 当 Loop 表字段为 null 时，回退到 OP Tag 量程；OP Tag 量程也为 null 时，回退到默认值

### 4.3 饱和率算法应用设计调整

**当前实现**（[saturation.py#L115-L125](file:///Users/zhangping/DEV/CLPM/backend/app/services/metric_calculator/saturation.py#L115-L125)）：
```python
def _read_op_bounds(self, signals: dict) -> tuple[float, float, float]:
    op_low = self._read_float(signals, 'op_low', self.DEFAULT_OP_LOW)
    op_high = self._read_float(signals, 'op_high', self.DEFAULT_OP_HIGH)
    epsilon = self._read_float(signals, 'saturation_epsilon', self.DEFAULT_EPSILON)
    return op_low, op_high, epsilon
```

**调整后实现**：
```python
def _read_op_bounds(self, signals: dict) -> tuple[float, float, float]:
    # 优先级 1: signals['op_low'/'op_high']（由 DataPlanner 从 Loop 表填充）
    # 优先级 2: 默认值（兜底，signals 未填充时）
    op_low = self._read_float(signals, 'op_low', self.DEFAULT_OP_LOW)
    op_high = self._read_float(signals, 'op_high', self.DEFAULT_OP_HIGH)
    epsilon = self._read_float(signals, 'saturation_epsilon', self.DEFAULT_EPSILON)
    return op_low, op_high, epsilon
```

**关键变更点**：
- `SaturationRateCalculator` 本身不变（仍从 `signals` 读取）
- 变更在 `DataPlanner._default_config_loader` / `_assemble_bundles`：将 Loop 表的限位字段填充到 `signals`
- DataPlanner 的填充逻辑：
  ```python
  # 优先级 1: Loop 表字段
  if loop.op_output_lower_limit is not None:
      signals['op_low'] = float(loop.op_output_lower_limit)
  # 优先级 2: OP Tag range_min
  elif op_tag and op_tag.range_min is not None:
      signals['op_low'] = float(op_tag.range_min)
  # 优先级 3: 默认值（不填充，让 SaturationRateCalculator 用 DEFAULT_OP_LOW）
  ```

### 4.4 列表筛选应用设计

**当前实现**：`list_loops` service 已支持 `loop_type` 参数（[loop.py#L172-L174](file:///Users/zhangping/DEV/CLPM/backend/app/services/loop.py#L172-L174)），但前端 `query` 对象未包含 `loopType` 字段。

**调整**：
- 后端：已支持，无需变更
- 前端：`query` reactive 对象增加 `loopType` 字段，`loadList` 传递给 API

---

## 5. 数据模型设计（DDS 增量）

### 5.1 LoopLedger 表新增字段

| 字段名 | 类型 | nullable | 默认值 | 说明 |
|---|---|---|---|---|
| `op_output_lower_limit` | Float | True | NULL | OP 输出下限位（NULL 时取 OP Tag range_min，再 NULL 时取 0.0） |
| `op_output_upper_limit` | Float | True | NULL | OP 输出上限位（NULL 时取 OP Tag range_max，再 NULL 时取 100.0） |

**字段约束**：
- 不使用数据库层 CHECK 约束（限位校验依赖 OP Tag 量程，跨表校验在 service 层实现）
- 不设置默认值（NULL 表示"未配置"，由应用层回退到 OP Tag 量程）

**ORM 模型变更**（[loop.py](file:///Users/zhangping/DEV/CLPM/backend/app/models/loop.py)）：
```python
# v6.1 新增字段：OP 输出限位（用于饱和率算法）
op_output_lower_limit: Mapped[float | None] = mapped_column(
    Float, nullable=True, comment="OP 输出下限位（NULL 时取 OP Tag range_min，再 NULL 时取 0.0）"
)
op_output_upper_limit: Mapped[float | None] = mapped_column(
    Float, nullable=True, comment="OP 输出上限位（NULL 时取 OP Tag range_max，再 NULL 时取 100.0）"
)
```

### 5.2 Alembic 迁移

新增迁移文件 `add_loop_op_output_limits.py`：

```python
"""add loop op_output_lower_limit and op_output_upper_limit columns

Revision ID: v6p1lmt001
Revises: <上一个 revision>
Create Date: 2026-07-06

新增 loop_ledger.op_output_lower_limit / op_output_upper_limit 列，
用于饱和率算法计算控制器输出饱和时长。
"""
from alembic import op
import sqlalchemy as sa

revision = "v6p1lmt001"
down_revision = "<上一个 revision>"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.add_column("loop_ledger", sa.Column("op_output_lower_limit", sa.Float(), nullable=True))
    op.add_column("loop_ledger", sa.Column("op_output_upper_limit", sa.Float(), nullable=True))

def downgrade() -> None:
    op.drop_column("loop_ledger", "op_output_upper_limit")
    op.drop_column("loop_ledger", "op_output_lower_limit")
```

### 5.3 Tag 表量程字段说明（已有，无变更）

| 字段名 | 类型 | nullable | 说明 |
|---|---|---|---|
| `range_min` | Float | True | 量程下限 |
| `range_max` | Float | True | 量程上限 |
| `unit` | String(20) | True | 工程单位（°C、MPa、% 等） |

**数据来源**：AAS 同步时从 OPC 服务器读取，或手工录入。

---

## 6. API 接口设计（IDS 增量）

### 6.1 GET /api/v1/loops — 列表响应增加字段

**响应 `items[]` 增加**：

| 字段 | 类型 | 说明 |
|---|---|---|
| `pvRange` | Object \| null | `{ min: number, max: number }`，PV Tag 量程，未关联时 null |
| `pvUnit` | String \| null | PV 工程单位 |
| `opRange` | Object \| null | `{ min: number, max: number }`，OP Tag 量程，未关联时 null |
| `opUnit` | String \| null | OP 工程单位 |
| `opOutputLowerLimit` | Number \| null | OP 输出下限位 |
| `opOutputUpperLimit` | Number \| null | OP 输出上限位 |

### 6.2 GET /api/v1/loops — 列表查询参数增加

| 参数 | 类型 | 说明 |
|---|---|---|
| `loopType` | String (optional) | 按回路类型筛选：`TEMPERATURE`/`PRESSURE`/`LEVEL`/`FLOW`/`ANALYSIS`/`SPEED`/`OTHER` |

### 6.3 POST /api/v1/loops — 创建回路请求体增加

| 字段 | 类型 | 说明 |
|---|---|---|
| `opOutputLowerLimit` | Number (optional) | OP 输出下限位，默认 null（取 OP Tag range_min） |
| `opOutputUpperLimit` | Number (optional) | OP 输出上限位，默认 null（取 OP Tag range_max） |

### 6.4 PUT /api/v1/loops/{id} — 更新回路请求体增加

同 6.3。

### 6.5 GET /api/v1/loops/{id} — 详情响应 basicInfo 增加

同 6.1。

### 6.6 错误码新增

| 错误码 | HTTP 状态 | 触发场景 | 说明 |
|---|---|---|---|
| `ERR_OP_LIMIT_OUT_OF_RANGE` | 400 | 创建/更新回路时 OP 输出限位超出 OP Tag 量程范围 | `op_output_lower_limit < OP Tag range_min` 或 `op_output_upper_limit > OP Tag range_max` 或 `lower_limit >= upper_limit` |

---

## 7. UI/UX 设计增量

### 7.1 回路列表 — 显示量程与限位

**新增列**（在"控制类型"列后）：

| 列名 | 宽度 | 内容 | 显示格式 |
|---|---|---|---|
| PV 量程 | 100 | `{pvRange.min} ~ {pvRange.max} {pvUnit}` | `0 ~ 100 %` |
| OP 量程 | 100 | `{opRange.min} ~ {opRange.max} {opUnit}` | `0 ~ 100 %` |
| OP 限位 | 100 | `{opOutputLowerLimit} ~ {opOutputUpperLimit}` | `10 ~ 90` |

**视觉规则**：
- 未关联 Tag 时显示 "—"（slate-400）
- 限位字段为 null 时显示 "默认"（slate-500），鼠标 hover tooltip 显示"使用 OP Tag 量程"
- 限位与量程一致时（默认值）显示 "默认" 灰色
- 限位与量程不一致时（自定义）显示具体数值（emerald-600）

### 7.2 回路列表 — 筛选区增加 loopType

在筛选区 Popover 中新增"回路类型"Select：

| 项 | 说明 |
|---|---|
| 标签 | 回路类型 |
| 选项 | 全部 / 温度 / 压力 / 液位 / 流量 / 分析 / 速度 / 其他 |
| 默认 | 全部 |
| 已选徽章 | "类型：温度" |

### 7.3 回路编辑表单 — 增加 OP 输出限位配置

**新增表单项**（在"控制类型"后）：

| 字段 | 类型 | 说明 |
|---|---|---|
| OP 输出下限位 | InputNumber | 默认值 = OP Tag range_min，范围 [OP Tag range_min, OP Tag range_max) |
| OP 输出上限位 | InputNumber | 默认值 = OP Tag range_max，范围 (OP Tag range_min, OP Tag range_max] |

**视觉规则**：
- 标签旁显示"OP Tag 量程：`{opRange.min} ~ {opRange.max} {opUnit}`"提示
- 当 OP Tag 未关联时，限位字段 disabled，提示"请先关联 OP Tag"
- 当限位 = OP Tag 量程时，显示"使用默认"checkbox，勾选后禁用输入框
- 校验失败时输入框红色边框 + 错误提示

---

## 8. 实施计划

### 8.1 实施顺序

| 阶段 | 任务 | 文件 |
|---|---|---|
| 1 | Alembic 迁移：新增 2 个字段 | `backend/alembic/versions/add_loop_op_output_limits.py` |
| 2 | ORM 模型：LoopLedger 新增字段 | `backend/app/models/loop.py` |
| 3 | Schema 更新：LoopCreate / LoopUpdate 增加字段 | `backend/app/schemas/loop.py` |
| 4 | Service 层：create_loop / update_loop 增加参数与校验 | `backend/app/services/loop.py` |
| 5 | Service 层：list_loops / get_loop_detail 增加量程 JOIN | `backend/app/services/loop.py` |
| 6 | DataPlanner：填充 signals['op_low'/'op_high'] | `backend/app/services/data_planner.py` |
| 7 | 饱和率算法：保持不变（从 signals 读取，无需修改） | `backend/app/services/metric_calculator/saturation.py` |
| 8 | 前端 API 类型：LoopListItem / LoopBasicInfo 增加字段 | `frontend/apps/web-antd/src/api/loop.ts` |
| 9 | 前端列表：新增列与筛选 | `frontend/apps/web-antd/src/views/loop/manage.vue` |
| 10 | 前端编辑表单：新增 OP 输出限位配置 | `frontend/apps/web-antd/src/views/loop/manage.vue` |
| 11 | 单元测试：service 层校验、DataPlanner 填充 | `backend/tests/test_loop.py` |
| 12 | 集成测试：端到端验证 | 手动 |

### 8.2 验证清单

- [ ] 创建回路时未传限位字段，数据库为 NULL，列表显示"默认"
- [ ] 创建回路时传入限位字段，保存成功，列表显示具体数值
- [ ] 更新回路限位字段，超出 OP Tag 量程范围，返回 `ERR_OP_LIMIT_OUT_OF_RANGE`
- [ ] 列表显示 PV/OP 量程与单位
- [ ] 列表按 `loopType` 筛选正常
- [ ] 饱和率算法使用 Loop 表限位字段计算（日志验证）
- [ ] 饱和率算法在 Loop 限位为 NULL 时回退到 OP Tag 量程
- [ ] 饱和率算法在 OP Tag 也未关联时回退到默认值 0/100

---

## 9. 与 v6.0 主文档的对齐计划

本规范作为 v6.0 增量，后续 v6.1 升级时将吸收以下内容到主文档：

| 主文档 | 吸收章节 |
|---|---|
| PRD v6.1 | §回路配置增加"量程引用规则"和"OP 输出限位配置"需求 |
| FDS v6.1 | §回路配置功能增加"量程引用"和"限位配置"功能设计 |
| ADS v6.1 | §回路数据来源增加"量程引用"应用设计；§饱和率算法调整 |
| DDS v6.1 | §LoopLedger 表新增 `op_output_lower_limit` / `op_output_upper_limit` 字段 |
| IDS v6.1 | §2.2.7~2.2.10 接口字段更新 |
| UI/UX v6.1 | §回路列表新增列；§回路编辑表单新增限位配置 |

---

## 10. 风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| OP Tag 量程变更后，Loop 限位字段未同步 | 饱和率计算使用过时限位 | 应用层校验时检查 OP Tag 量程范围；UI 提示用户限位需在 Tag 量程范围内 |
| 列表 JOIN Tag 表增加查询开销 | 列表加载变慢 | 批量 IN 查询（避免 N+1）；Tag 量程加入 Redis 缓存 |
| 限位字段为 NULL 时算法回退逻辑复杂 | 饱和率计算结果不一致 | DataPlanner 统一填充 signals，算法层只读 signals |
| 已有数据限位字段为 NULL | 已有回路饱和率计算使用默认值 0/100 | 文档说明；提供批量初始化脚本（可选） |

---

**文档结束**
