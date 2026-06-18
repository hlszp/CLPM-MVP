# CLPM 系统架构与交付架构设计

日期：2026-06-16
版本：v1

## 1. 总体架构原则

| 原则 | 说明 |
|---|---|
| 分层 | 前端、API、计算/任务、关系数据、时序数据分层 |
| 只读边界 | 平台不直接写 DCS，只接收只读历史数据/导出/模拟数据 |
| manifest-first | EvidencePackage 只引用结果快照与版本，不直接导出原始长时序 |
| 状态优先 | partial / unavailable / inconclusive 是一等状态 |
| 容器化 | 本机开发统一运行在 OrbStack 容器编排下 |

## 2. 逻辑架构

```text
Frontend App
  -> API Gateway / BFF
    -> Auth & User Service
    -> Sample / Ledger Service
    -> KPI & Diagnosis Service
    -> Closure Governance Service
    -> Evidence & Export Service
    -> Config / Rule / Audit Service
    -> Job Orchestrator / Worker
      -> PostgreSQL
      -> TDengine
      -> Object/File Storage (dev 可先本地卷)
```

## 3. 服务划分

| 服务 | 职责 |
|---|---|
| Frontend App | 工程师与 Sponsor 双入口，状态表达、工作台与导出入口 |
| API / BFF | 统一前端接口、聚合多服务结果、权限裁剪 |
| Auth & User | 用户、角色、组织、权限、登录上下文 |
| Sample Service | 样本批次、导入、验证、冻结 |
| Loop Ledger Service | 回路主数据、点位映射、排除规则、版本链 |
| KPI & Diagnosis Service | KPI 计算、低效排行、诊断规则、证据窗口 |
| Closure Service | 审核、多方会签、实施、复评、回退 |
| Evidence Service | EvidencePackage、样本报告、导出中心 |
| Config / Rule / Audit | 质量规则、阈值、模式映射、审计日志 |
| Job Orchestrator | 导入、验证、计算、组装、导出等异步任务编排 |

## 4. 数据架构

| 存储 | 用途 |
|---|---|
| PostgreSQL | 用户、角色、样本批次、台账、KPI 结果、诊断结果、审核记录、复评记录、EvidencePackage manifest、审计日志 |
| TDengine | PV/SP/OP/MODE/quality/event_marker 等秒级时序数据与窗口查询输入 |
| 本地卷 / 对象存储 | 导入文件、导出文件、附件、任务产物 |

## 5. OrbStack 开发部署

| 组件 | 运行方式 |
|---|---|
| frontend | 容器或本机 dev server，对接容器内 API |
| api | Node / Java / Go 等正式后端容器 |
| worker | 异步任务容器 |
| postgres | OrbStack 容器内 PostgreSQL |
| tdengine | OrbStack 容器内 TDengine |
| volume | PG 数据卷、TDengine 数据卷、import/export 文件卷 |

## 6. 环境建议

| 环境 | 目标 |
|---|---|
| local-dev | OrbStack + compose，一键拉起 PG / TDengine / API / worker |
| integration | 用于接口联调、E2E、验收样本回放 |
| pilot | 试点部署环境，验证现场边界和导出链路 |

## 7. 非功能要求

| 维度 | 要求 |
|---|---|
| 审计 | 所有配置变更、审核、实施、复评、导出均留痕 |
| 一致性 | 结果对象必须携带 `generated_by_run`、版本引用、validity 状态 |
| 可扩展 | P0 到 P2 可逐步扩容，不要求一次完成全量架构 |
| 性能边界 | P0 不承诺 5 年任意查询；P2 再做冷热分层和异步查询 |
