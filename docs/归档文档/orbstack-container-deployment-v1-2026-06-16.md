# CLPM 事件计算与容器化部署设计

日期：2026-06-16
版本：v1

## 1. 事件与计算链路

| 阶段 | 输入 | 输出 |
|---|---|---|
| 导入 | CSV / historian / simulated | raw import record, TDengine 写入 |
| 验证 | 样本窗口、字段映射、质量规则 | readiness summary |
| 台账生成 | 样本批次、识别规则 | LoopLedger |
| KPI 计算 | frozen 样本 + frozen 台账 | KpiResult |
| 诊断计算 | KPI + 证据窗口 + 规则版本 | DiagnosisFinding |
| 证据组装 | KPI / 诊断 / 审核 / 复评 | EvidencePackage |
| 导出 | EvidencePackage + format | PDF / JSON / Excel |

## 2. JobRun 模型

| 字段 | 说明 |
|---|---|
| job_id | 任务 ID |
| job_type | import / validate / ledger / kpi / diagnosis / evidence / export |
| status | pending / running / success / failed / partial |
| started_at / ended_at | 时间 |
| input_refs | 输入对象引用 |
| output_refs | 输出对象引用 |
| error_message | 失败原因 |

## 3. OrbStack 容器化部署

建议 `docker-compose.yml`（由 OrbStack 执行）至少包含：

| 服务 | 作用 |
|---|---|
| postgres | 主业务库 |
| tdengine | 时序库 |
| api | 主 API 服务 |
| worker | 异步任务服务 |
| frontend | 前端开发或预览服务 |

## 4. 本地卷与网络

| 资源 | 说明 |
|---|---|
| pg-data | PostgreSQL 数据卷 |
| tdengine-data | TDengine 数据卷 |
| import-data | 导入源文件卷 |
| export-data | 导出文件卷 |
| internal-net | 容器内部网络 |

## 5. 前端技术选型评估

| 路线 | 结论 |
|---|---|
| React 延续 | 与当前原型复用程度最高，页面状态与测试迁移成本最低 |
| Vue + vue-vben-admin | 适合中后台壳层，但对复杂图表工作台、证据链、状态表达需要较重二次定制 |

### 5.1 vue-vben-admin 评估

| 维度 | 评估 |
|---|---|
| 优势 | 权限、菜单、表格、表单、中后台壳层成熟 |
| 劣势 | 从 React 原型迁移成本高；证据链/工作台图表场景需要大量自定义 |
| 风险 | 设计系统重做、团队学习成本、未来复杂页面适配负担 |
| 建议 | 除非团队明确转向 Vue 且接受重构成本，否则正式前端优先延续 React 路线 |

## 6. 推荐正式前端路线

| 项目 | 建议 |
|---|---|
| 前端框架 | React |
| 路由 | React Router |
| 图表 | Apache ECharts |
| 设计系统 | 在现有原型基础上抽象正式组件 |
| 状态与接口 | 以后端 API 驱动，逐步替换 mock data |
