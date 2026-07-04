# CLPM 代码对齐工程计划 v1.0

**制定日期**: 2026-07-04
**设计基线**: FDS v5.1 / DDS v4.1 / 关键算法设计说明 v2.1 / UIUX v5.3
**目标**: 系统性消除 v4.0 重构代码与最新设计文档之间的差距，贯通前端界面层 → 交互逻辑层 → 后端服务层 → 数据访问层全链路

---

## 1. 差距分析总结

基于三路并行调研（后端模型层 / 后端 API 与计算层 / 前端页面与组件层），识别出 **6 类差距共 31 项**：

### 1.1 数据模型层差距（6 项）

| # | 表 | 差距类型 | 现状 | DDS v4.1 要求 |
|---|---|---|---|---|
| M1 | `loop_ledger` | 字段缺失 | 无 `include_in_evaluation` | BOOLEAN NOT NULL DEFAULT TRUE |
| M2 | `loop_ledger` | 字段重命名 | `level`（nullable, default=3） | `importance_level`（NOT NULL, DEFAULT 2, int 1/2/3） |
| M3 | `metric_config` | 字段缺失 | 无 `grading_thresholds` | JSONB（5 级定级阈值） |
| M4 | `unit_kpi_summary` | 字段缺失 ×2 | 无 `excluded_loops` / `status` | INTEGER / VARCHAR(20) |
| M5 | `unit_kpi_summary` | 字段重命名 ×2 | `steady_rate` / `fast_response_rate` | `stability_rate` / `fast_rate` |
| M6 | `kpi_snapshot_hourly` + `kpi_snapshot_custom` + `node_kpi` | 字段重命名 ×4 | `fast_response_rate` / `output_travel_index` / `steady_state_time` / `stiction_coeff` | `fast_rate` / `output_trip_index` / `settling_time` / `stiction_index` |

### 1.2 后端计算层差距（7 项）

| # | 模块 | 差距 | 算法 v2.1 要求 |
|---|---|---|---|
| C1 | `accuracy.py` | e_max 固定值 5 | 数据驱动 `e_max = Σ[max(\|E_i\|) - \|E_i\|] / n` |
| C2 | `stability.py` | std 分母 n（ddof=0） | 无偏估计分母 n-1（ddof=1） |
| C3 | `confidence_evaluator.py` | DEFAULT_WEIGHTS = 0.25/0.20/0.55 | 国标 STABLE = 0.2/0.3/0.5 |
| C4 | `kpi_calc.py` | `_LOOP_TYPE_TO_CONTROL_TYPE` 用 loop_type 映射 | 应使用 `loop_ledger.control_type` 字段 |
| C5 | `kpi_calc.py` + `node_performance.py` + `performance.py` | 无 `include_in_evaluation` 过滤 | 综合评分 / 聚合 / 排行均需过滤 |
| C6 | `node_performance.py` | 装置级聚合写入 `kpi_node_snapshot_hourly` | 应写入 `unit_kpi_summary` |
| C7 | `node_aggregation.py` | 日/月聚合按 `loop_count` 加权 | 应按 `importance_level` 权重 |

### 1.3 后端 API 层差距（9 项）

| # | 端点 | 差距类型 |
|---|---|---|
| A1 | `GET/POST /configs/weight-templates` + 版本历史/回滚/恢复默认 | 完全缺失 |
| A2 | `GET/POST /configs/grading-thresholds` | 完全缺失 |
| A3 | `GET /dashboard/board`（装置级三大 KPI） | 完全缺失 |
| A4 | `GET /dashboard/auto-rate-rt`（每分钟刷新） | 端点位置在 `/performance/realtime-auto-rate` |
| A5 | `GET /aas/sync-status` | 完全缺失 |
| A6 | `GET /aas/sync-logs` | 完全缺失 |
| A7 | `GET /tasks/{task_id}/results`（kpi_snapshot_custom 查询） | 完全缺失 |
| A8 | `formula` 字段 | 仍暴露在 API / Schema 中，未标注废弃 |
| A9 | `RankingItem.compositeScore` | 应统一为 `score` |

### 1.4 前端页面与组件差距（9 项）

| # | 页面/组件 | 差距 | UIUX v5.3 要求 |
|---|---|---|---|
| F1 | 全局看板 `dashboard.vue` | 回路级 3+1+8 聚合 + 饼图自控率 | 装置级三大 KPI + 半圆径向仪表盘 + Top 10 预览 |
| F2 | 权重配置 `weight-config.vue` | 仅 2 Tab（类型权重/级别权重） | 3 Tab（+定级阈值+版本历史）+ 版本化 + 恢复默认 |
| F3 | 指标配置 `config.vue` | 公式编辑器可编辑、控制类型字段存在 | 公式编辑器废弃、控制类型移除 |
| F4 | 回路台账 `manage.vue` | 缺 `importance_level` / `include_in_evaluation` | 补三字段 + 评估配置区块 + 视觉编码 |
| F5 | AAS 同步状态页 | 路由未挂载、页面定位为连接配置 | 新增 `/loop/aas-sync` 路由 + 状态监控页 |
| F6 | 低效排行 `ranking.vue` | 无 `include_in_evaluation` 过滤 | 仅展示参评回路 + "包含不参评"开关 |
| F7 | 字段名 `compositeScore` | 26 处使用 | 统一为 `score` |
| F8 | 字段名 `level` | 前端类型与页面使用 | 统一为 `importanceLevel` |
| F9 | 路由路径 `/metric/*` | 全模块路由前缀 | `/performance/*` |

---

## 2. 阶段划分与依赖关系

```
Phase 1: 数据模型层对齐（ORM + Alembic 迁移）
    ↓ （所有上层的基础）
Phase 2: 后端计算与服务层对齐（算法 + 聚合 + 过滤）
    ↓ （依赖模型字段）
Phase 3: 后端 API 层对齐（端点 + Schema + 废弃标注）
    ↓ （依赖服务层逻辑）
Phase 4: 前端类型与 API 层对齐（TypeScript 类型 + API 调用）
    ↓ （依赖后端 API 契约）
Phase 5: 前端页面与组件对齐（页面重构 + 新页面 + 组件改造）
    ↓ （依赖前端类型层）
Phase 6: 路由对齐与端到端集成验证
      （依赖所有前置阶段）
```

**依赖关系铁律**：禁止跨阶段跳跃执行。每阶段必须完成验证闭环后方可进入下一阶段。

---

## 3. 各阶段详细任务

### Phase 1: 数据模型层对齐（ORM + Alembic 迁移）

**目标**: DDS v4.1 字段结构完整落地，为上层提供数据基础
**依赖**: 无（基础层）
**预计任务数**: 8

#### 1.1 字段重命名（4 张表 × 4 字段）

| 任务 | 文件 | 操作 |
|---|---|---|
| P1-T1 | `app/models/metric.py` | `KpiSnapshotHourly`: `fast_response_rate` → `fast_rate`, `output_travel_index` → `output_trip_index`, `steady_state_time` → `settling_time`, `stiction_coeff` → `stiction_index` |
| P1-T2 | `app/models/metric.py` | `KpiSnapshotCustom`: 同上 4 字段重命名 |
| P1-T3 | `app/models/node_kpi.py` | `KpiNodeSnapshotHourly` / `Daily` / `Monthly`: 同上 4 字段重命名 |
| P1-T4 | `app/models/unit_kpi_summary.py` | `UnitKpiSummary`: `steady_rate` → `stability_rate`, `fast_response_rate` → `fast_rate` |

#### 1.2 字段新增与约束修正

| 任务 | 文件 | 操作 |
|---|---|---|
| P1-T5 | `app/models/loop.py` | `LoopLedger`: `level` → `importance_level`（NOT NULL, DEFAULT 2, CHECK IN (1,2,3)）；新增 `include_in_evaluation`（BOOLEAN NOT NULL DEFAULT TRUE） |
| P1-T6 | `app/models/metric.py` | `MetricConfig`: 新增 `grading_thresholds`（JSONB）；`formula` 字段注释标注 `# DEPRECATED: 对齐 FDS v5.1 §5.3.1.2，12 项指标算法已固化`；`control_type` 字段注释标注 `# MIGRATED: 已迁移至 loop_ledger.control_type，本字段仅保留兼容` |
| P1-T7 | `app/models/unit_kpi_summary.py` | `UnitKpiSummary`: 新增 `excluded_loops`（INTEGER DEFAULT 0）;新增 `status`（VARCHAR(20), CHECK IN ('SUCCESS','PARTIAL','EMPTY')） |

#### 1.3 Alembic 迁移

| 任务 | 文件 | 操作 |
|---|---|---|
| P1-T8 | `alembic/versions/xxx_align_dds_v4_1_fields.py` | 生成统一迁移脚本：①4 表字段重命名（`op.alter_column`）；②3 表新增字段（`op.add_column`）；③约束修正；④数据迁移（`level` → `importance_level` 值不变；`include_in_evaluation` 默认 TRUE） |

#### 验证闭环

| 验证项 | 命令/方法 | 通过标准 |
|---|---|---|
| 单元测试 | `cd backend && uv run pytest tests/unit/test_models.py -v` | 全部通过 |
| 迁移验证 | `cd backend && uv run alembic upgrade head && uv run alembic downgrade -1 && uv run alembic upgrade head` | 升降级无报错 |
| 模型一致性 | `cd backend && uv run python -c "from app.models import *; print('OK')"` | 导入无报错 |

---

### Phase 2: 后端计算与服务层对齐

**目标**: KPI 计算逻辑、聚合逻辑、过滤逻辑完全对齐算法 v2.1 与 FDS v5.1
**依赖**: Phase 1 完成（字段名与新字段可用）
**预计任务数**: 10

#### 2.1 算法修正

| 任务 | 文件 | 操作 |
|---|---|---|
| P2-T1 | `app/services/metric_calculator/accuracy.py` | e_max 改为数据驱动计算 `e_max = Σ[max(\|E_i\|) - \|E_i\|] / n`，保留 CONFIG 覆盖入口 |
| P2-T2 | `app/services/metric_calculator/stability.py` | `np.std(errors)` → `np.std(errors, ddof=1)`（无偏估计） |
| P2-T3 | `app/services/confidence_evaluator.py` | DEFAULT_WEIGHTS 修正为国标值：STABLE 0.2/0.3/0.5 |
| P2-T4 | `app/tasks/kpi_calc.py` | `_LOOP_TYPE_TO_CONTROL_TYPE` 映射逻辑修正：改为直接读取 `LoopLedger.control_type` 字段，移除 loop_type → control_type 错误映射 |

#### 2.2 字段名适配

| 任务 | 文件 | 操作 |
|---|---|---|
| P2-T5 | `app/tasks/kpi_calc.py` | `_DB_TO_CALCULATOR_METRIC_CODE` 映射表更新：数据库列名已改名，映射表需同步修正（`fast_response_rate` → `fast_rate` 等移除中间映射，直接对齐） |
| P2-T6 | 全计算层文件 | 全局替换字段名引用：`fast_response_rate` → `fast_rate`、`output_travel_index` → `output_trip_index`、`steady_state_time` → `settling_time`、`stiction_coeff` → `stiction_index`（涉及 kpi_calc.py / metric_calculator/*.py / confidence_evaluator.py / node_performance.py 等） |

#### 2.3 过滤逻辑

| 任务 | 文件 | 操作 |
|---|---|---|
| P2-T7 | `app/tasks/kpi_calc.py` | `_do_calculate` 过滤条件新增 `LoopLedger.include_in_evaluation.is_(True)`：不参评回路不进入综合评分计算（单回路 KPI 仍计算，但不写入 score） |
| P2-T8 | `app/services/node_performance.py` | `aggregate_node_snapshot` 新增 `include_in_evaluation` 过滤：不参评回路不参与装置级聚合；使用 `importance_level` 替代 `level` 字段 |
| P2-T9 | `app/services/performance.py` | `get_ranking` 新增 `include_in_evaluation` 过滤：不参评回路不出现在低效排行 |

#### 2.4 聚合写入修正

| 任务 | 文件 | 操作 |
|---|---|---|
| P2-T10 | `app/services/node_performance.py` | 装置级聚合结果写入目标改为 `unit_kpi_summary` 表（替代 `kpi_node_snapshot_hourly`）；`node_aggregation.py` 日/月聚合加权方式改为按 `importance_level` 权重（1:3, 2:2, 3:1） |

#### 验证闭环

| 验证项 | 命令/方法 | 通过标准 |
|---|---|---|
| 单元测试 | `cd backend && uv run pytest tests/unit/test_kpi_calc.py tests/unit/test_confidence_evaluator.py tests/unit/test_metric_calculator/ tests/unit/test_node_performance.py -v` | 全部通过 |
| 算法验证 | `cd backend && uv run pytest tests/unit/test_metric_calculator/test_accuracy.py -k "e_max" -v` | e_max 数据驱动逻辑通过 |
| 稳定率验证 | `cd backend && uv run pytest tests/unit/test_metric_calculator/test_stability.py -v` | ddof=1 无偏估计通过 |
| 权重验证 | `cd backend && uv run pytest tests/unit/test_confidence_evaluator.py -k "weights" -v` | 国标默认值通过 |
| 过滤验证 | 新增测试用例：include_in_evaluation=false 的回路不进入综合评分 / 聚合 / 排行 | 过滤逻辑通过 |
| 全量回归 | `cd backend && uv run pytest -q` | 全部通过（更新后的测试用例） |

---

### Phase 3: 后端 API 层对齐

**目标**: 新增缺失端点，修正 Schema 字段名，标注废弃字段
**依赖**: Phase 2 完成（服务层逻辑已对齐）
**预计任务数**: 12

#### 3.1 新增端点

| 任务 | 文件 | 操作 |
|---|---|---|
| P3-T1 | `app/api/v1/endpoints/weight_config.py`（新建） | `GET /configs/weight-templates`：返回 4 类控制类型权重模板；`POST /configs/weight-templates`：保存为新版本；`GET /configs/weight-templates/history`：版本历史；`POST /configs/weight-templates/{version}/rollback`：回滚；`POST /configs/weight-templates/restore-defaults`：恢复国标默认值 |
| P3-T2 | `app/api/v1/endpoints/grading_config.py`（新建） | `GET /configs/grading-thresholds`：返回 5 级定级阈值；`POST /configs/grading-thresholds`：更新阈值（含严格递增校验） |
| P3-T3 | `app/api/v1/endpoints/dashboard.py` | 新增 `GET /dashboard/board`：返回装置级三大 KPI（来自 `unit_kpi_summary`）；新增 `GET /dashboard/auto-rate-rt`：实时自控率（来自 Redis，每分钟刷新） |
| P3-T4 | `app/api/v1/endpoints/aas.py` | 新增 `GET /aas/sync-status`：同步服务状态 + 最近同步时间 + 同步统计；新增 `GET /aas/sync-logs`：同步日志列表（分页） |
| P3-T5 | `app/api/v1/endpoints/tasks.py` | 新增 `GET /tasks/{task_id}/results`：查询非标任务的具体指标计算结果（来自 `kpi_snapshot_custom` 表） |

#### 3.2 Schema 修正

| 任务 | 文件 | 操作 |
|---|---|---|
| P3-T6 | `app/schemas/loop.py` | `LoopCreate` / `LoopUpdate` / `LoopBasicInfo` / `LoopListItem`：`level` → `importanceLevel`；新增 `includeInEvaluation` 字段 |
| P3-T7 | `app/schemas/performance.py` | `RankingItem`：`compositeScore` → `score`；`KpiSnapshotSchema`：字段名同步对齐（`fastResponseRate` → `fastRate` 等 camelCase 对齐）；新增 `includeInEvaluation` 字段 |
| P3-T8 | `app/schemas/config.py` | 新增 `WeightTemplateSchema` / `GradingThresholdSchema` / `VersionHistorySchema`；`MetricConfigSchema`：`formula` 字段标注 `deprecated=True` |

#### 3.3 废弃标注与字段清理

| 任务 | 文件 | 操作 |
|---|---|---|
| P3-T9 | `app/api/v1/endpoints/performance.py` | `PUT /performance/metrics/{metric_id}`：`formula` 参数标注废弃（接收但忽略写入，返回 deprecated 提示）；`controlType` 参数标注废弃 |
| P3-T10 | `app/api/v1/endpoints/configs.py` | `_metric_config_to_dict`：`formula` 字段返回值标注 `"formula": {"value": ..., "deprecated": true}` |
| P3-T11 | `app/api/v1/endpoints/loops.py` | 回路 CRUD 端点支持 `importanceLevel` / `includeInEvaluation` 字段读写 |
| P3-T12 | `app/api/v1/router.py` | 注册新增端点路由 |

#### 验证闭环

| 验证项 | 命令/方法 | 通过标准 |
|---|---|---|
| 单元测试 | `cd backend && uv run pytest tests/unit/test_api_* -v` | 全部通过 |
| 新端点测试 | 为每个新端点编写测试用例（正常 + 边界 + 异常） | 全部通过 |
| OpenAPI 文档 | `cd backend && uv run python -c "from app.main import app; print(app.openapi()['paths'].keys())"` | 新端点路径出现 |
| 废弃标注验证 | `cd backend && uv run pytest tests/unit/test_api_performance.py -k "formula" -v` | formula 废弃行为通过 |
| 全量回归 | `cd backend && uv run pytest -q` | 全部通过 |

---

### Phase 4: 前端类型与 API 层对齐

**目标**: TypeScript 类型定义与 API 调用层完全对齐后端新契约
**依赖**: Phase 3 完成（后端 API 契约已确定）
**预计任务数**: 7

#### 4.1 类型定义修正

| 任务 | 文件 | 操作 |
|---|---|---|
| P4-T1 | `src/api/metric.ts` | `RankingItem.compositeScore` → `score`；`KpiSummary` snake_case → camelCase 对齐；`fastResponseRate` → `fastRate`、`outputTravelIndex` → `outputTripIndex`、`steadyStateTime` → `settlingTime` |
| P4-T2 | `src/api/loop.ts` | `LoopListItem.level` → `importanceLevel`（int 类型）；新增 `includeInEvaluation`（boolean）；`ScoreWeights` 字段名同步对齐 |
| P4-T3 | `src/api/metric.ts`（新增） | 新增 `WeightTemplate` / `GradingThreshold` / `VersionHistory` / `SyncStatus` / `SyncLog` 类型定义 |
| P4-T4 | `src/api/dashboard.ts`（新建或扩展） | 新增 `BoardData`（装置级三大 KPI）/ `AutoRateRt`（实时自控率）类型定义 |

#### 4.2 API 调用函数

| 任务 | 文件 | 操作 |
|---|---|---|
| P4-T5 | `src/api/metric.ts` | 新增 `getWeightTemplates` / `saveWeightTemplate` / `getWeightTemplateHistory` / `rollbackWeightTemplate` / `restoreWeightDefaults` / `getGradingThresholds` / `saveGradingThresholds` |
| P4-T6 | `src/api/dashboard.ts` | 新增 `getBoardApi`（`GET /dashboard/board`）/ `getAutoRateRt`（`GET /dashboard/auto-rate-rt`） |
| P4-T7 | `src/api/aas.ts`（新建或扩展） | 新增 `getSyncStatus` / `getSyncLogs`；`src/api/task.ts` 新增 `getTaskResults`（`GET /tasks/{task_id}/results`） |

#### 验证闭环

| 验证项 | 命令/方法 | 通过标准 |
|---|---|---|
| 类型检查 | `cd frontend && pnpm run check:type` | 无新增类型错误（已有 6 个预存在错误可忽略） |
| API 调用验证 | 手动调用或 mock 验证新 API 函数 | 函数签名与后端 OpenAPI 一致 |

---

### Phase 5: 前端页面与组件对齐

**目标**: UIUX v5.3 的 7 项变更在前端页面层完整落地
**依赖**: Phase 4 完成（类型与 API 层已对齐）
**预计任务数**: 12

#### 5.1 全局看板重构（UIUX v5.3 ①）

| 任务 | 文件 | 操作 |
|---|---|---|
| P5-T1 | `src/views/metric/dashboard.vue` | 重构为装置级三大 KPI 卡片区（综合性能/平均自控率/稳定率，来自 `unit_kpi_summary`）+ 装置评分排名柱状图 + 全厂稳定率趋势双轴折线图 |
| P5-T2 | `src/components/metric/auto-rate-gauge.vue` | 重构为半圆径向仪表盘（ECharts gauge 类型，180° 弧度，指针 + 弧段着色 0-60 红/60-80 黄/80-90 蓝/90-100 绿 + 脉冲动画 + 状态徽章 + 60 分钟 sparkline）；刷新频率改为 60 秒 |
| P5-T3 | `src/views/metric/dashboard.vue` | 新增低效回路 Top 10 预览区（仅 `include_in_evaluation=true`）+ Partial 警告横幅 |

#### 5.2 权重配置管理页完善（UIUX v5.3 ②）

| 任务 | 文件 | 操作 |
|---|---|---|
| P5-T4 | `src/views/metric/weight-config.vue` | Tab 结构改为 3 个：①控制类型权重模板 ②性能定级阈值 ③版本历史；移除旧的"级别权重"Tab |
| P5-T5 | `src/views/metric/type-weight.vue` | 增加国标默认值对比展示 + 归一化校验（a+f+s=1.0）+ R 折扣因子说明 + 适用场景说明 |
| P5-T6 | `src/views/metric/grading-threshold.vue`（新建） | 5 级定级阈值表格（EXCELLENT/GOOD/FAIR/WARNING/POOR），可编辑分数区间，严格递增校验，固定颜色 |
| P5-T7 | `src/views/metric/version-history.vue`（新建） | 版本列表（版本号/变更类型/摘要/操作人/时间/回滚操作）+ 当前版本徽章 + 回滚确认 + 恢复国标默认值按钮 |

#### 5.3 回路台账修订（UIUX v5.3 ③）

| 任务 | 文件 | 操作 |
|---|---|---|
| P5-T8 | `src/views/loop/manage.vue` | 表格列：`level` → `importanceLevel` + 重要等级视觉编码（红/橙/灰徽章）+ 新增 `includeInEvaluation` 列（开关控件）+ 不参评回路行底色淡灰；编辑抽屉：新增"评估配置"区块（控制类型 + 重要等级 + 是否参评）+ 切换确认弹窗；新建回路弹窗：采集三字段（必填） |

#### 5.4 其他页面修订（UIUX v5.3 ④⑤⑥⑦）

| 任务 | 文件 | 操作 |
|---|---|---|
| P5-T9 | `src/views/metric/config.vue` | 公式编辑器改为只读展示 + 标注废弃；控制类型字段移除；权重编辑入口跳转 `/performance/weight-config`；3+1+8 分组展示 |
| P5-T10 | `src/views/loop/aas.vue`（重写） + `src/router/routes/modules/loop.ts` | 重写为 AAS Tag 同步状态页：同步状态卡片 + 手动触发 + Tag 列表 + 质量分布饼图 + 同步日志抽屉；注册 `/loop/aas-sync` 路由 |
| P5-T11 | `src/views/metric/ranking.vue` | 新增 `include_in_evaluation` 过滤（默认仅参评回路）+ "包含不参评回路"开关 + INCONCLUSIVE 显示"—" + `compositeScore` → `score` 字段名替换 |
| P5-T12 | 全前端文件 | 全局字段名清理：`compositeScore` → `score`（26 处）、`level` → `importanceLevel`（回路相关） |

#### 验证闭环

| 验证项 | 命令/方法 | 通过标准 |
|---|---|---|
| 类型检查 | `cd frontend && pnpm run check:type` | 无新增类型错误 |
| 页面渲染 | 手动启动前端 `pnpm run dev:antd`，逐页检查 | 7 项 UIUX 变更页面渲染正确 |
| 交互验证 | 手动操作：权重配置保存/回滚、回路台账三字段编辑、低效排行过滤开关、AAS 同步状态页 | 交互行为符合 UIUX v5.3 规范 |

---

### Phase 6: 路由对齐与端到端集成验证

**目标**: 路由路径对齐 + 全链路贯通验证
**依赖**: Phase 5 完成（所有页面已就绪）
**预计任务数**: 5

#### 6.1 路由路径对齐

| 任务 | 文件 | 操作 |
|---|---|---|
| P6-T1 | `src/router/routes/modules/metric.ts` | 路由前缀 `/metric` → `/performance`；各子路由路径对齐 UIUX v5.3（dashboard → `/performance`、ranking → `/performance/ranking`、config → `/performance/metrics`、weight-config → `/performance/weight-config` 等） |
| P6-T2 | `src/router/routes/modules/loop.ts` | 新增 `/loop/aas-sync` 路由；`/loop/manage` → `/loop/ledger`（或保留整合页但路径对齐）；`/tag/list` → `/loop/mapping` |
| P6-T3 | 全前端文件 | 路由跳转链接全局更新（`router.push('/metric/...')` → `router.push('/performance/...')` 等） |

#### 6.2 端到端集成验证

| 任务 | 文件 | 操作 |
|---|---|---|
| P6-T4 | `e2e/` | 新增 E2E 测试用例：①全局看板装置级 KPI 展示 + 实时自控率仪表盘；②权重配置保存/回滚/恢复默认；③回路台账三字段编辑；④低效排行参评过滤；⑤AAS 同步状态页；⑥非标任务结果查询 |
| P6-T5 | — | 全链路数据验证：后端计算 → DB 写入 → API 返回 → 前端展示，字段名与数值一致 |

#### 验证闭环

| 验证项 | 命令/方法 | 通过标准 |
|---|---|---|
| 后端全量测试 | `cd backend && uv run pytest -q` | 全部通过 |
| 前端类型检查 | `cd frontend && pnpm run check:type` | 无新增类型错误 |
| E2E 测试 | `cd e2e && pnpm exec playwright test` | 全部通过 |
| 路由验证 | 手动访问所有新路由 | 无 404 |
| 数据链路验证 | 后端写入 → API 查询 → 前端展示，字段名与数值贯通 | 全链路无断裂 |

---

## 4. 任务依赖关系图

```
Phase 1 (模型层)
├── P1-T1~T4 (字段重命名) ──┐
├── P1-T5~T7 (字段新增) ────┤
└── P1-T8 (迁移脚本) ───────┘
                             ↓
Phase 2 (计算与服务层)
├── P2-T1~T4 (算法修正) ←── 依赖 P1 字段名
├── P2-T5~T6 (字段名适配) ←─ 依赖 P1
├── P2-T7~T9 (过滤逻辑) ←── 依赖 P1-T5 (include_in_evaluation)
└── P2-T10 (聚合写入修正) ←─ 依赖 P1 (unit_kpi_summary 字段)
                             ↓
Phase 3 (API 层)
├── P3-T1~T5 (新增端点) ←─── 依赖 P2 (服务层逻辑)
├── P3-T6~T8 (Schema 修正) ←─ 依赖 P2 字段名
└── P3-T9~T12 (废弃标注) ←── 依赖 P3-T6~T8
                             ↓
Phase 4 (前端类型与 API)
├── P4-T1~T4 (类型定义) ←─── 依赖 P3 Schema
└── P4-T5~T7 (API 函数) ←─── 依赖 P4-T1~T4
                             ↓
Phase 5 (前端页面与组件)
├── P5-T1~T3 (全局看板) ←─── 依赖 P4-T4/T6 (BoardData/AutoRateRt)
├── P5-T4~T7 (权重配置) ←─── 依赖 P4-T3/T5 (WeightTemplate 等)
├── P5-T8 (回路台账) ←────── 依赖 P4-T2 (LoopListItem)
├── P5-T9~T10 (指标配置/AAS) ← 依赖 P4
├── P5-T11 (低效排行) ←────── 依赖 P4-T1 (RankingItem)
└── P5-T12 (字段名清理) ←──── 依赖 P4
                             ↓
Phase 6 (路由与集成)
├── P6-T1~T3 (路由对齐) ←─── 依赖 P5 所有页面就绪
├── P6-T4 (E2E 测试) ←────── 依赖 P6-T1~T3
└── P6-T5 (全链路验证) ←──── 依赖 P6-T4
```

---

## 5. 风险与注意事项

### 5.1 高风险项

| 风险 | 影响范围 | 缓解措施 |
|---|---|---|
| 字段重命名涉及 4 张表 × 4 字段 | 后端全层 + 前端类型 + 测试 | Phase 1 集中处理，迁移脚本含数据迁移；Phase 2 全局替换引用 |
| `level` → `importance_level` 是 loop_ledger 核心字段 | 装置级聚合加权逻辑 | 迁移脚本保留值不变（1/2/3），仅改字段名 |
| 装置级聚合写入表从 `kpi_node_snapshot_hourly` 改为 `unit_kpi_summary` | 历史数据兼容 | 迁移脚本同步迁移历史数据；保留 `kpi_node_snapshot_hourly` 表用于节点级查询 |
| 路由前缀 `/metric` → `/performance` | 全前端路由 + 书签 | Phase 6 最后执行，可考虑保留 `/metric` 重定向到 `/performance` |
| e_max 计算逻辑变更 | 准确率历史数据不可比 | 记录算法版本号 `algorithm_version`，历史数据保留原值 |

### 5.2 注意事项

1. **每个 Phase 完成后必须执行验证闭环**，禁止跳过验证直接进入下一阶段
2. **测试用例需同步更新**：字段重命名后，现有测试用例中的字段引用需同步修正
3. **Git 提交规范**：每个任务（T1/T2/...）完成后提交一次，commit message 格式 `refactor(phaseN-TN): 简述`
4. **分支策略**：建议在 `feature/align-design-v5` 分支上执行，完成后合并到 `main`
5. **Celery worker 重启**：Phase 2 完成后需重启 Celery worker 进程
6. **前端 6 个预存在 TypeScript 错误**（plant-node-tree.vue 3 个 + workbench.vue 3 个）与本次修改无关，不纳入修复范围

---

## 6. 执行检查清单

### Phase 1 完成标准
- [ ] 4 张表字段重命名完成
- [ ] 3 个新字段添加完成
- [ ] Alembic 迁移升降级无报错
- [ ] 模型导入无报错
- [ ] 模型层单元测试通过

### Phase 2 完成标准
- [ ] e_max 数据驱动计算实现
- [ ] 稳定率 ddof=1 实现
- [ ] DEFAULT_WEIGHTS 国标对齐
- [ ] control_type 映射逻辑修正
- [ ] include_in_evaluation 过滤实现（3 处）
- [ ] 装置级聚合写入 unit_kpi_summary
- [ ] 日/月聚合按 importance_level 加权
- [ ] 后端全量测试通过

### Phase 3 完成标准
- [ ] 7 类新端点实现
- [ ] Schema 字段名对齐
- [ ] formula 废弃标注
- [ ] RankingItem.compositeScore → score
- [ ] 后端全量测试通过
- [ ] OpenAPI 文档包含新端点

### Phase 4 完成标准
- [ ] TypeScript 类型定义对齐
- [ ] API 调用函数实现
- [ ] 前端类型检查通过

### Phase 5 完成标准
- [ ] 全局看板重构完成
- [ ] 权重配置 3 Tab 完成
- [ ] 回路台账三字段完成
- [ ] 指标配置公式编辑器废弃
- [ ] AAS 同步状态页完成
- [ ] 低效排行过滤完成
- [ ] 字段名清理完成
- [ ] 前端类型检查通过
- [ ] 页面手动验证通过

### Phase 6 完成标准
- [ ] 路由路径对齐
- [ ] E2E 测试通过
- [ ] 全链路数据验证通过
- [ ] 后端全量测试通过
- [ ] 前端类型检查通过

---

## 7. 附录：相关文件清单

### 后端文件
- 模型层: `backend/app/models/loop.py`, `metric.py`, `unit_kpi_summary.py`, `node_kpi.py`, `loop_config.py`
- 计算层: `backend/app/tasks/kpi_calc.py`, `backend/app/services/confidence_evaluator.py`, `backend/app/services/metric_calculator/*.py`, `backend/app/services/node_performance.py`, `backend/app/services/node_aggregation.py`, `backend/app/services/performance.py`
- API 层: `backend/app/api/v1/endpoints/loops.py`, `performance.py`, `dashboard.py`, `aas.py`, `configs.py`, `tasks.py`, `loop_type_weight.py`, `loop_level_weight.py`
- Schema 层: `backend/app/schemas/loop.py`, `performance.py`, `config.py`
- 迁移: `backend/alembic/versions/`

### 前端文件
- 路由: `frontend/apps/web-antd/src/router/routes/modules/metric.ts`, `loop.ts`
- API: `frontend/apps/web-antd/src/api/metric.ts`, `loop.ts`, `dashboard.ts`, `aas.ts`
- 页面: `frontend/apps/web-antd/src/views/metric/dashboard.vue`, `ranking.vue`, `config.vue`, `weight-config.vue`, `type-weight.vue`, `level-weight.vue`, `manage.vue`, `aas.vue`
- 组件: `frontend/apps/web-antd/src/components/metric/auto-rate-gauge.vue`, `config-tabs.vue`

### 设计文档
- `docs/设计文档/02-FDS/FDS.md` v5.1
- `docs/设计文档/04-DDS/DDS.md` v4.1
- `docs/设计文档/03-ADS/关键算法设计说明.md` v2.1
- `docs/设计文档/06-UIUX/ui-ux-design-guidelines.md` v5.3
