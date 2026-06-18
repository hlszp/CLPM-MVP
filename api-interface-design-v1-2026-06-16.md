# CLPM API 接口设计

日期：2026-06-16
版本：v1

## 1. 接口原则

| 原则 | 说明 |
|---|---|
| 前端不直接读时序库 | 所有页面经 API 获取聚合结果或窗口引用 |
| 结果对象带版本 | 任何响应都必须返回状态与版本引用 |
| partial 显式返回 | 不允许用 0 或空白代替 unavailable / partial |
| 异步任务统一建模 | 导入、验证、计算、导出统一走 JobRun |

## 2. 核心资源

| 资源 | 路径前缀 |
|---|---|
| 样本批次 | `/api/v1/sample-batches` |
| 回路台账 | `/api/v1/loop-ledgers` |
| KPI 结果 | `/api/v1/kpis` |
| 诊断结果 | `/api/v1/diagnosis-findings` |
| 审核闭环 | `/api/v1/closure` |
| 证据包 | `/api/v1/evidence-packages` |
| 导出中心 | `/api/v1/exports` |
| 质量规则 | `/api/v1/quality-rules` |
| 任务运行 | `/api/v1/jobs` |

## 3. 代表性接口

### 3.1 样本批次

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/api/v1/sample-batches` | 创建样本批次 |
| POST | `/api/v1/sample-batches/{id}/import` | 发起导入 |
| POST | `/api/v1/sample-batches/{id}/validate` | 发起验证 |
| POST | `/api/v1/sample-batches/{id}/freeze` | 冻结样本 |
| GET | `/api/v1/sample-batches/{id}` | 获取样本详情 |
| GET | `/api/v1/sample-batches/{id}/readiness` | 获取就绪摘要 |

### 3.2 台账与绩效

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/v1/loop-ledgers/{ledgerId}` | 获取台账头 |
| GET | `/api/v1/loop-ledgers/{ledgerId}/loops` | 获取回路清单 |
| PATCH | `/api/v1/loop-ledgers/{ledgerId}/loops/{loopId}` | 修改映射/校核字段 |
| GET | `/api/v1/kpis?sampleBatchId=...` | 获取 KPI 列表 |
| GET | `/api/v1/kpis/ranking?sampleBatchId=...` | 获取低效排行 |

### 3.3 诊断与证据

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/v1/diagnosis-findings?sampleBatchId=...` | 获取诊断清单 |
| GET | `/api/v1/diagnosis-findings/{id}` | 获取诊断详情 |
| GET | `/api/v1/evidence-windows/{loopId}` | 获取单回路证据窗口 |
| GET | `/api/v1/evidence-packages/{id}` | 获取证据包详情 |
| POST | `/api/v1/evidence-packages/{id}/export` | 发起导出 |

### 3.4 闭环治理

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/v1/closure/reviews?loopId=...` | 获取审核记录 |
| POST | `/api/v1/closure/reviews` | 创建审核决策 |
| POST | `/api/v1/closure/implementations` | 创建实施记录 |
| POST | `/api/v1/closure/reevaluations` | 创建复评记录 |
| GET | `/api/v1/closure/reevaluations/{id}` | 获取复评结果 |

## 4. 响应字段要求

| 字段 | 说明 |
|---|---|
| status | available / partial / unavailable / inconclusive / error |
| validity_status | current / stale / superseded / invalid |
| generated_by_run | 任务运行 ID |
| version_refs | 所有关联版本引用 |
| missing_refs | 缺失引用项 |

## 5. 错误码建议

| 代码 | 场景 |
|---|---|
| `SAMPLE_NOT_READY` | 样本未冻结或未完成验证 |
| `LEDGER_NOT_FROZEN` | 台账未冻结 |
| `EVIDENCE_PARTIAL` | 证据包仍处于 partial |
| `INSUFFICIENT_DATA` | 输入不足，不能计算或导出 |
| `DCS_WRITE_FORBIDDEN` | 任何写 DCS 请求必须拒绝 |
