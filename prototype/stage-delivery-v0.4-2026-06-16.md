# CLPM Prototype 阶段性交付说明

日期：2026-06-16
交付版本：prototype 阶段性交付 v0.4

## 交付范围

| 模块 | 状态 | 说明 |
|---|---|---|
| 工程壳 | 已完成 | React + Vite + TypeScript + React Router |
| 全景导航 | 已完成 | 11 个一级菜单与核心二级菜单，带 P0/P1/P2/P3 标签 |
| Mock data | 已完成 | 已接入 demo-data 秒级样本，并派生样本、回路、KPI、诊断、证据、审核、证据包、整定样例 |
| P0 主链 | 已完成 | 工程首页、风险总览、待办事项、样本验证、排行、诊断证据、闭环、证据包、Sponsor |
| 样本就绪 | 已完成 | 就绪校验展示批次映射率、批次好值率、评审就绪率、质量可用率、事件可用性、partial 原因、质量规则和下一步 |
| 台账治理 | 已完成 | 点位映射、缺失项、人工校核、冻结阻断、排除有效期/审批、版本维度已提升到可评审页面 |
| EvidencePackage | 已完成 | included refs、manifest version/hash、package_status、validity_status、partial 状态、风险摘要、不可证明事项、demo-data 溯源可见 |
| Partial 状态传播 | 已完成 | 多方审核需补证据会影响实施、复评与证据包状态；实施记录模板可继续进入观察复评但不能伪装完成 |
| 交付验收页 | 已完成 | `/delivery` 与 `/delivery/acceptance` 展示验收清单、演示路径、测试与 partial 状态 |
| 无障碍与等价文本 | 已完成 | 表格键盘可达，趋势图补充文字摘要 |
| 可维护性 | 已完成 | 页面已拆分为 shared、样本/台账、诊断/闭环/证据、总览/绩效模块 |
| 构建交付质量 | 已完成 | 调整体积阈值，消除 ECharts 原型包体警告 |
| 绩效趋势 | 已完成 | `/performance/trends` 展示 PV/SP/OP/MODE 关键窗口、诊断边界与只读安全边界 |
| 质量规则页 | 已完成 | `/system/rules` 展示质量码、缺失、冻结、突变规则与安全边界 |
| 导出中心 | 已完成 | `/evidence/export` 展示 PDF/JSON/Excel 结构、manifest 引用与 partial 导出边界 |
| 整定样例 | 已完成 | 展示 current/suggested/fallback/confidence/risk/boundary，只做单条 P0 样例 |
| 安全边界 | 已完成 | 只读 DCS、人工实施、审计留痕、回退说明 |
| P1/P2/P3 | 已完成结构展示 | 保留导航与版本标签，不做深交互 |
| 自动验证 | 已完成 | build + Playwright desktop/mobile smoke，测试状态由 `src/data/deliveryStatus.ts` 集中维护，含替换字符异常文案、未知证据路由降级和移动端页面级横向溢出断言 |

## 关键路由

| 视角 | 路由 | 用途 |
|---|---|---|
| 工程师 | `/` | 优先级工作台 |
| 风险管理 | `/risk` | 数据不足、需现场核实、结果过期、不可证明事项 |
| 待办管理 | `/todos` | 审核、补证据、复评任务队列 |
| 样本可信 | `/samples/readiness` | 映射率、好值率、事件可用性、质量规则、partial 原因 |
| 样本冻结 | `/samples/freeze` | 冻结版本、manifest hash、partial 状态 |
| 台账治理 | `/loops/mapping` | 字段映射矩阵、缺失项、状态口径 |
| 台账校核 | `/loops/verification` | 人工修正、冻结阻断、质量码与现场核实项 |
| 排除管理 | `/loops/exclusions` | 排除原因、有效窗口、审批状态、影响口径 |
| 版本管理 | `/loops/versions` | ledger、mapping、formula、threshold、quality rule、mode mapping |
| 绩效定位 | `/performance/ranking` | 低效回路排序 |
| 绩效趋势 | `/performance/trends` | PV/SP/OP/MODE 关键窗口、诊断边界与安全边界 |
| 诊断解释 | `/diagnosis/loop/TIC-1115` | demo-data 派生主回路证据链 |
| 治理闭环 | `/closure/review` | 审核、实施、复评边界 |
| 证据审计 | `/evidence` | EvidencePackage manifest + demo-data 溯源 |
| 导出中心 | `/evidence/export` | PDF/JSON/Excel 导出结构与 partial 边界 |
| 样本报告 | `/evidence/sample-report` | 样本范围、场景分布、安全边界 |
| 管理汇报 | `/sponsor` | Sponsor 汇报入口 |
| 交付验收 | `/delivery/acceptance` | 验收清单、演示路径、测试状态、partial 状态 |
| 安全说明 | `/system/safety` | 只读与人工实施边界 |
| 质量规则 | `/system/rules` | 质量码、缺失、冻结、突变规则 |
| 整定样例 | `/tuning/sample` | 单条可信整定样例 |

## 验证记录

| 命令 | 结果 |
|---|---|
| `npm run build` | 通过，无 chunk size warning |
| `npm run test:smoke` | 98/98 passed，覆盖页面可打开、菜单/版本标签、无异常文案、移动端多类密集页面横向溢出、样本口径、样本就绪细项、数据雷达状态分组、指标溯源、主链到 Sponsor、Sponsor 证据视图边界、审核模拟态、三类证据链差异、未知证据路由降级、未知页面路由降级、趋势图可访问摘要、排行表键盘导航、展示型表格无空操作焦点、契约一等字段、台账映射/校核/排除/版本细项、manifest、package/validity 状态、partial、安全边界、质量规则、导出中心、整定风险回退、趋势图文字摘要、交付验收页 |

## 当前不做

| 不做项 | 原因 |
|---|---|
| 真实 DCS 接入 | P0 原型只使用本地 mock data |
| 后端服务 | 当前验证用户主链与产品表达 |
| 真实算法计算 | 当前用高仿真样例表达口径 |
| 批量整定 | P0 仅展示单条可信整定样例 |
| P2/P3 深交互 | 避免扩大首版范围 |
