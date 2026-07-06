# v6.0 文档统一升级 — 差距分析汇总

**生成日期**：2026-07-06
**执行分支**：`mb/doc-v6`
**目的**：汇总 PRD/ADS/IDS 与基准（FDS v5.1 / UIUX v5.3 / DDS v4.1 / 实现契约 v1.0）的差距，作为 v6.0 升级的修复清单

## 详细差距清单

| 文档 | 差距数 | 详细文件 |
|---|---|---|
| PRD v4.0 | 35 条 | `v6-gap-prd.md` |
| ADS v4.0 | 44 条 | `v6-gap-ads.md` |
| IDS v4.0 | 34 条 | `v6-gap-ids.md` |
| **合计** | **113 条** | — |

---

## 1. P0 阻断性差距（必须先修复，影响代码与文档一致性）

### 1.1 状态机枚举不一致

| 对象 | 基准（实现契约 v1.0） | PRD 现状 | ADS 现状 | IDS 现状 |
|---|---|---|---|---|
| Action Tracker | `IMPLEMENTED` | 使用 `RESOLVED` | 缺英文枚举 | 使用 `RESOLVED` |
| Loop | `READY/PARTIAL/INACTIVE` | 缺失 | 缺失 | 误用 `INCONCLUSIVE` |
| Tuning | `DRAFT/RUNNING/COMPLETED/ROLLED_BACK` | 缺失 | 缺失 | 用 `PENDING/IN_PROGRESS/FAILED` |

**修复方案**：所有文档统一使用实现契约 v1.0 的枚举值

### 1.2 KPI 体系口径自相矛盾

| 文档 | 内部矛盾 |
|---|---|
| PRD | §5.1.1 为 3+1+7（11 项），但 §4.1/§4.3.1/§4.3.4 为 3+1+8 |
| ADS | §8.2 称"6 大 KPI"与 §13.1 "3+1+8 体系"并存 |
| IDS | §2.3（6 大 KPI）与 §2.8（3+1+8）口径自相矛盾 |
| FDS v5.1（基准） | 3+1+8 体系（3 核心+1 折扣+8 扩展）= 12 项指标计算器 |

**修复方案**：统一采用 FDS v5.1 的 3+1+8 体系，对外口径仍可强调 6 大核心 KPI

### 1.3 API 路径不一致

| 基准（实现契约 v1.0） | IDS 现状 | 修复方案 |
|---|---|---|
| `/api/v1/users/*` | `/api/v1/system/users` | 改为 `/api/v1/users` |
| `/api/v1/diagnosis/*`（含 tracker） | `/api/v1/tracker/*` 独立 | 归并到 `/api/v1/diagnosis/*` |
| 不新增 `/api/v1/configs/metrics` | 存在该路径 | 删除，用 `/api/v1/performance/*` |
| 不新增 `/api/v1/configs/diagnosis` | 存在该路径 | 删除，用 `/api/v1/diagnosis/*` |

### 1.4 角色枚举不一致

| 基准（实现契约 v1.0） | IDS 现状 | 修复方案 |
|---|---|---|
| `IC_ENGINEER` | `EXECUTOR` | 改为 `IC_ENGINEER` |
| `PE_ENGINEER` | `COLLABORATOR` | 改为 `PE_ENGINEER` |
| `SPONSOR` | `VIEWER` | 改为 `SPONSOR` |
| `EXPERT` | 缺失 | 新增 |
| `ADMIN` | 缺失 | 新增 |

### 1.5 数据血缘字段数量错误

| 文档 | 现状 | 基准（DDS v4.1） |
|---|---|---|
| ADS | 8 个独立字段 | 5 独立字段 + `data_lineage` JSONB 内 6 子字段 |
| PRD | 6 项 | 同上 |

**修复方案**：统一为 DDS v4.1 的 5+JSONB 结构

---

## 2. P1 高优先级差距（数据契约不一致）

### 2.1 数据模型表名/字段不一致

| 基准（DDS v4.1） | PRD 现状 | ADS 现状 | IDS 现状 |
|---|---|---|---|
| `kpi_snapshot_hourly` | `standard_snapshot` | 缺失 | 部分缺失 |
| `kpi_snapshot_custom` | `custom_snapshot` | 缺失 | 部分缺失 |
| `unit_kpi_summary` | 缺失 | 缺失 | 缺失 |
| `clpm_metric_data_requirement` | 缺失 | 缺失 | 缺失 |
| `report_schedule` | 缺失 | 缺失 | 缺失 |
| `sys_user`/`sys_role`/`sys_user_role` | 缺失 | 缺失 | 缺失 |
| loop-level `steady_rate` | `stability_rate` | `stability_rate` | 混用 |
| unit-level `stability_rate` | — | — | 混用 |
| `metric_config.category` | 缺失 | 缺失 | 缺失 |
| `metric_config.is_discount_factor` | 缺失 | 缺失 | 缺失 |
| `metric_config.grading_thresholds` | 缺失 | 缺失 | 缺失 |
| `loop_ledger.control_type` | 缺失 | 缺失 | 缺失 |
| `loop_ledger.importance_level` | 缺失 | 缺失 | 缺失 |
| `loop_ledger.include_in_evaluation` | 缺失 | 缺失 | 缺失 |

### 2.2 可信度阈值不一致

| 等级 | 基准（FDS v5.1） | PRD 现状 | IDS 现状 |
|---|---|---|---|
| A | valid_rate ≥ 95% | 一致 | 一致 |
| B | 80% ≤ valid_rate < 95% | 一致 | 一致 |
| C | 60% ≤ valid_rate < 80% | 一致 | 一致 |
| D | 20% ≤ valid_rate < 60% | **40% ≤ valid_rate < 60%** | 一致 |
| E | valid_rate < 20% | 一致 | 一致 |

### 2.3 scoreWeights 结构过期

| 基准（FDS v5.1） | IDS 现状 |
|---|---|
| 3 核心指标权重（accuracy/fast/steady，sum=100） | 旧 6 KPI 结构 |
| R 为折扣因子，不纳入权重和 | R 纳入权重和 |

### 2.4 性能定级缺失

| 基准（FDS v5.1） | PRD | ADS | IDS |
|---|---|---|---|
| 5 级：EXCELLENT/GOOD/FAIR/WARNING/POOR | 缺失 | 缺失 | 缺失 |

### 2.5 权重模板缺失

| 基准（FDS v5.1） | PRD | ADS | IDS |
|---|---|---|---|
| 4 类：STABLE/SLOW/FAST/LOGIC | 缺失 | 缺失 | 缺失 |

---

## 3. P2 中优先级差距（功能描述不完整）

### 3.1 模块数量口径

| 文档 | 现状 | 基准 | 修复 |
|---|---|---|---|
| PRD | 未明确 | 6 模块+1门户 | 明确声明 |
| ADS | 未明确 | 6 模块+1门户 | 明确声明 |
| IDS | 未明确 | 6 模块+1门户 | 明确声明 |
| README | 7 模块+门户（含任务管理） | 6 模块+1门户 | 改为 6+1 |

### 3.2 路由清单缺失

| 文档 | 现状 | 基准 |
|---|---|---|
| PRD | 未引用 | 实现契约 v1.0 的 32 个路由 |
| ADS | 未列出 | 实现契约 v1.0 的 32 个路由 |
| IDS | 部分列出 | 实现契约 v1.0 的 32 个路由 |

### 3.3 功能缺失

| 功能 | PRD | ADS | IDS |
|---|---|---|---|
| Tag 管理独立入口 `/tag/list` | 缺失 | 缺失 | 缺失 |
| 指标数据需求配置 | 缺失 | 缺失 | 缺失 |
| 任务管理（评估任务） | 描述为独立模块 | 缺失 | 缺失 |
| 历史重算 | 缺失 | 缺失 | 缺失 |

### 3.4 预处理 Pipeline 路径

| 基准 | ADS 现状 |
|---|---|
| `app/services/preprocessing/` | `app/services/` |

### 3.5 MetricCalculator 数量

| 基准（FDS v5.1） | ADS 现状 | AGENTS.md 现状 |
|---|---|---|
| 12 个计算器 | "8 大 KPI" | "8 大 KPI" |

---

## 4. P3 低优先级差距（文档治理）

### 4.1 版本号引用过期

| 文档 | 引用的版本 | 实际版本 |
|---|---|---|
| PRD | FDS v3.0/DDS v3.0 | FDS v5.1/DDS v4.1 |
| ADS | PRD v3.0 | PRD v4.0 |
| IDS | FDS v3.0/DDS v3.0/ADS v3.0/PRD v3.0 | 全部过期 |

### 4.2 引用文档缺失

| 文档 | 缺失引用 |
|---|---|
| PRD | 实现契约 v1.0、UIUX v5.3、GB/T 44693.2-2024 |
| ADS | FDS v5.1、UIUX v5.3、DDS v4.1、实现契约 v1.0 |
| IDS | 实现契约 v1.0、UIUX v5.3、ADS v4.0、GB/T 44693.2-2024 |

### 4.3 术语不一致

| 基准术语 | PRD/ADS/IDS 现状 |
|---|---|
| 快速响应率（fast_rate） | 快速率 |
| 平稳率（stability_rate/steady_rate） | 稳定率 |
| 有效自控率（effective_auto_rate） | 自控率 |
| 实施（IMPLEMENTED） | 解决（RESOLVED） |

---

## 5. v6.0 升级修复优先级路线图

### 第 1 批（P0 阻断性，必须先修复）
1. 统一状态机枚举（Action Tracker/Loop/Tuning）
2. 统一 KPI 体系口径为 3+1+8
3. 统一 API 路径（删除 `/api/v1/configs/*`、`/api/v1/tracker/*`、`/api/v1/system/users`）
4. 统一角色枚举（5 角色）
5. 统一数据血缘字段为 5+JSONB 结构

### 第 2 批（P1 高优先级，数据契约）
6. 补全 DDS v4.1 的 17 张表到 ADS/IDS
7. 统一可信度 D 级阈值为 20%~60%
8. 更新 scoreWeights 结构为 3 核心指标 + R 折扣因子
9. 补全性能定级 5 级
10. 补全权重模板 4 类

### 第 3 批（P2 中优先级，功能描述）
11. 统一模块数量为 6+1
12. 补全路由清单引用
13. 补全缺失功能（Tag 管理/指标数据需求/任务管理/历史重算）
14. 修正预处理 Pipeline 路径
15. 修正 MetricCalculator 数量为 12

### 第 4 批（P3 低优先级，文档治理）
16. 更新所有版本号引用
17. 补全引用文档清单
18. 统一术语用词

---

## 6. 跨文档一致性总结

### 6.1 最严重的不一致（影响代码运行）

1. **状态机枚举**：PRD/IDS 用 `RESOLVED`，代码用 `IMPLEMENTED` — 会导致前后端联调失败
2. **角色枚举**：IDS 用 `EXECUTOR/COLLABORATOR/VIEWER`，代码用 `IC_ENGINEER/PE_ENGINEER/SPONSOR` — 会导致权限校验失败
3. **API 路径**：IDS 用 `/api/v1/system/users`，代码用 `/api/v1/users` — 会导致 API 调用 404

### 6.2 最严重的数据契约不一致

1. **scoreWeights 结构**：IDS 仍用旧 6 KPI 结构，与代码的 3 核心 + R 折扣因子不一致
2. **数据血缘字段**：ADS 描述 8 字段，DDS 实际 5+JSONB
3. **表名**：PRD 用 `standard_snapshot`，DDS 用 `kpi_snapshot_hourly`

### 6.3 最严重的功能缺失

1. **性能定级**：5 级 EXCELLENT/GOOD/FAIR/WARNING/POOR 在 PRD/ADS/IDS 均缺失
2. **权重模板**：4 类 STABLE/SLOW/FAST/LOGIC 在 PRD/ADS/IDS 均缺失
3. **指标数据需求配置**：在 PRD/ADS/IDS 均缺失
