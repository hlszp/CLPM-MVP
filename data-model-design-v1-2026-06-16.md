# CLPM 数据模型设计（PostgreSQL + TDengine）

日期：2026-06-16
版本：v1

## 1. 设计目标

| 目标 | 说明 |
|---|---|
| PG 承载业务对象 | 样本、台账、KPI、诊断、审核、证据包、权限、审计 |
| TDengine 承载时序数据 | 秒级 PV/SP/OP/MODE、质量码、事件标记 |
| 版本可追溯 | 每个结论都能追到样本、台账、公式、阈值、规则版本 |
| 结果快照可复用 | 前端与证据包读取结果快照，不直接扫原始长时序 |

## 2. PostgreSQL 逻辑模型

| 表 | 用途 |
|---|---|
| users | 用户 |
| roles | 角色 |
| user_roles | 用户-角色关系 |
| sample_batches | 样本批次 |
| sample_source_files | 导入源文件 |
| loop_ledgers | 台账头 |
| loop_records | 回路记录 |
| exception_records | 排除与降级记录 |
| kpi_results | KPI 结果 |
| diagnosis_findings | 诊断结果 |
| evidence_windows | 证据窗口元数据 |
| review_records | 审核与会签记录 |
| implementation_records | 实施记录 |
| reevaluations | 复评记录 |
| evidence_packages | 证据包头 |
| evidence_package_refs | 证据包引用项 |
| quality_rule_sets | 质量规则 |
| threshold_versions | 阈值版本 |
| formula_versions | 公式版本 |
| mode_mapping_versions | MODE 映射版本 |
| job_runs | 异步任务运行 |
| audit_logs | 审计日志 |

## 3. TDengine 逻辑模型

建议使用 super table 组织：

| 超级表 | tag | 字段 |
|---|---|---|
| loop_signal_series | loop_id, unit_name, control_type | ts, pv, sp, op, mode, quality, event_marker |
| loop_pid_params | loop_id | ts, kp, ti, td |
| loop_events | loop_id, event_type | ts, event_name, severity, payload |

## 4. 边界规则

| 数据类型 | 存储 |
|---|---|
| 原始秒级时序 | TDengine |
| 结果快照 | PostgreSQL |
| 证据窗口引用 | PG 元数据 + TDengine 时间窗引用 |
| 导出文件 | 卷 / 对象存储 |

## 5. 核心关系

```text
sample_batches -> loop_ledgers -> loop_records
sample_batches -> kpi_results
loop_records -> diagnosis_findings
loop_records -> evidence_windows
review_records / implementation_records / reevaluations -> evidence_packages
```

## 6. 结果版本字段

| 字段 | 说明 |
|---|---|
| ledger_version | 台账版本 |
| mapping_version | 点位映射版本 |
| formula_version | KPI 公式版本 |
| threshold_version | KPI 阈值版本 |
| quality_rule_version | 质量规则版本 |
| mode_mapping_version | MODE 规则版本 |
| rule_version | 诊断规则版本 |
| generated_by_run | 任务运行 ID |
| validity_status | current / stale / superseded / invalid |

## 7. 开发阶段建议

| 阶段 | 优先实现 |
|---|---|
| P0 正式化 | PG 表结构、TDengine 原始时序表、结果快照表 |
| P1 | 模型质量、整定样例、仿真对比表 |
| P2 | 项目交付、历史查询索引、聚合快照 |
