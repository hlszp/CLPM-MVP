# CLPM P0 原型整改任务书

日期：2026-06-16

适用范围：`prototype/` 原型系统

建议执行方式：交给 Claude Code 启用 `/loop` 模式进行一轮集中整改

## 1. 文档目的

本文用于把当前 P0 原型评审发现的问题转化为一轮可执行、可验证、可收口的整改任务。

本轮整改目标不是新增产品范围，也不是推进真实后端、真实算法或真实 DCS 接入，而是把现有原型从“主链演示可用”提升到“P0 评审可验收”的状态。

## 2. 本轮整改边界

| 项目 | 本轮要求 |
|---|---|
| 允许修改 | `prototype/` 下的原型代码、样例数据派生脚本、测试、原型说明文档 |
| 不建议修改 | 根目录 PRS、菜单定稿、契约设计、算法可信度设计等产品基线文档 |
| 不做 | 真实后端服务、真实 OPC/DCS 接入、真实 PID 整定算法、真实长周期历史查询 |
| 必须保持 | React + Vite + TypeScript、本地 mock/demo data、P0 主链、高层全景导航、只读 DCS 安全边界 |
| 版本原则 | P0 只做治理闭环验证包；P1/P2/P3 仍只做结构展示和版本标签 |

## 3. 当前主要问题

| 优先级 | 问题 | 当前表现 | 影响 |
|---|---|---|---|
| P0 | demo 数据展示存在异常值 | 页面可出现 `NaNh`、`undefineds` | 直接破坏评审可信度 |
| P0 | 样本规模口径不一致 | PRS/P0 冻结口径为 `50-100` 或 `72` 回路，当前 demo 为 `24` 回路 | 容易被认为未达到 P0 样本验证门槛 |
| P0 | 单回路证据链不随路由参数切换 | 不同 `loopId` 页面仍读取同一条 `evidenceWindows[0]` | 三类诊断样例不可真实展开 |
| P0 | EvidencePackage manifest 表达过浅 | 只展示对象名，缺少版本字段、状态和 included refs | 未充分体现 manifest-first 和可追溯 |
| P0 | 部分 P0 深做页面仍为占位 | 点位映射、台账校核、排除管理、版本管理、样本冻结等页面未达到评审深度 | 可信数据与契约主干表达不足 |
| P1 | 闭环治理状态不一致 | 多方审核存在“需补证据”，但实施、复评、证据包仍可无条件 success | 弱化 partial/ready 状态规则 |
| P1 | 自动测试偏冒烟 | 主要检查页面可打开，缺少验收级断言 | 无法防止关键口径回归 |
| P1 | 日期和样例口径不统一 | 部分页面仍出现 2026-05 日期 | 与当前 2026-06-16 基线不一致 |
| P2 | 页面代码过度集中 | `GenericPage.tsx` 聚合大量页面 JSX | 后续维护和扩展成本高 |
| P2 | 无障碍与键盘交互不足 | 表格行有 `tabIndex` 但缺少 Enter/Space 行为，图表等价文本不足 | 未完全满足 PRS 无障碍要求 |

## 4. 现阶段要实现的目标

### 4.1 总目标

完成一轮 P0 原型整改，使系统可以清晰证明以下产品主线：

```text
可信样本 -> 台账与映射 -> KPI 与低效排行 -> 三类诊断证据 -> 审核/实施/复评 -> EvidencePackage -> Sponsor 证据视图
```

整改后，评审人员应能在浏览器中明确看到：哪些回路拖累装置稳定性，为什么拖累，下一步由谁处理，处理后是否改善，以及证据如何被版本化固化。

### 4.2 具体目标

| 目标 | 说明 |
|---|---|
| 消除展示硬伤 | 全站不得出现 `NaN`、`undefined`、`Invalid Date` 等明显异常展示 |
| 统一样本口径 | 页面、README、演示脚本和数据摘要必须明确当前样本规模与 P0 目标之间的关系 |
| 补强三类证据链 | PID 疑似、阀门/仪表疑似、数据/工况问题至少各有一条可点击、可区分的证据链 |
| 补强台账治理页面 | 点位映射、台账校核、排除管理、版本管理、样本冻结页面必须展示关键字段、状态和下一步动作 |
| 补强 EvidencePackage | 证据包必须展示版本引用、状态、included refs、风险摘要、manifest hash 或等价摘要 |
| 强化 partial 状态 | 缺审核、缺实施、缺复评、缺专家确认时，页面必须显示 partial/桌面评审版/模拟版，不能伪装成完整闭环 |
| 保持安全边界 | 所有整定、实施、回退相关页面继续明确“不写 DCS、人工实施、审计留痕” |
| 提升自动验收 | Playwright 增加关键业务断言，而不仅是页面打开断言 |
| 改善可维护性 | 至少拆分最重的页面模块，避免所有页面继续堆在单一超长文件中 |
| 改善响应式与无障碍 | 关键链路桌面和移动端可用，表格/审核入口具备基本键盘可达性 |

## 5. 建议整改任务拆分

| 顺序 | 任务 | 交付物 |
|---:|---|---|
| 1 | 修复 demo-data 派生脚本字段来源 | `import-demo-data.mjs` 不再生成异常时间/采样文案 |
| 2 | 统一样本规模与文案口径 | 页面、README、演示脚本、测试断言中的样本口径一致 |
| 3 | 扩展或明确 demo 样本规模 | 优先生成 `72` 回路；若暂不扩展，必须明确 `24` 回路为开发 smoke 数据 |
| 4 | 增加多条 EvidenceWindow | 至少覆盖 PID 疑似、阀门/仪表疑似、数据/工况问题 |
| 5 | 修复证据链路由参数 | `/diagnosis/loop/:loopId` 根据 `loopId` 展示对应证据 |
| 6 | 补齐台账治理关键页面 | `/loops/mapping`、`/loops/verification`、`/loops/exclusions`、`/loops/versions` 可评审 |
| 7 | 补齐样本冻结页面 | `/samples/freeze` 展示冻结条件、版本、风险和状态 |
| 8 | 强化 EvidencePackage 页面 | 展示版本字段、included refs、状态、风险摘要、manifest 摘要 |
| 9 | 调整闭环治理状态表达 | 审核冲突或缺证据时，下游页面和证据包显示 partial |
| 10 | 增加验收级 Playwright 测试 | 菜单、版本标签、主链、三类证据、无异常文案、安全边界、manifest 字段均被断言 |
| 11 | 适度拆分页面文件 | 将诊断、闭环、证据、样本/台账页面拆出独立模块 |
| 12 | 做最终 build 与 smoke 验证 | `npm run build`、`npm run test:smoke` 通过 |

## 6. 页面级验收标准

| 页面/模块 | 验收标准 |
|---|---|
| 全局导航 | 11 个一级菜单可见；P0/P1/P2/P3 标签清晰；P1/P2/P3 不被误导为 P0 已交付 |
| 工程首页 `/` | 三栏工作台成立；可直接进入低效回路证据链；动作区可进入闭环治理和样本验证 |
| 管理首页 `/sponsor` | 第一屏展示样本可信度、闭环完成率、关键风险、代表样例和证据包入口 |
| 样本批次 `/samples` | 展示样本范围、来源、时间窗、回路数、状态和风险 |
| 数据导入 `/samples/import` | 明确当前为 historian/CSV/demo 数据，不接真实 DCS，不写 DCS |
| 就绪校验 `/samples/readiness` | 展示映射率、好值率、事件可用性、质量规则、partial 原因和下一步 |
| 数据雷达 `/samples/radar` | 至少展示可评估、可诊断、可整定、需现场核实、数据不足、不可判定六类状态 |
| 样本冻结 `/samples/freeze` | 展示冻结条件、冻结后不可漂移、版本引用和未满足项 |
| 回路清单 `/loops` | 展示回路、装置、类型、状态、风险、评分、下一步动作 |
| 点位映射 `/loops/mapping` | 展示 PV/SP/OP/MODE 映射状态、缺失项、质量规则和校核入口 |
| 台账校核 `/loops/verification` | 展示人工校核状态、修正记录、待确认字段、冻结前阻断项 |
| 排除管理 `/loops/exclusions` | 展示排除回路、排除原因、有效窗口、审批状态及其对 KPI 的影响 |
| 版本管理 `/loops/versions` | 展示 ledger、mapping、formula、threshold、quality rule、mode mapping、rule version |
| 指标总览 `/performance` | 展示 P0 核心 KPI，并说明公式/阈值版本来源 |
| 低效排行 `/performance/ranking` | 排序逻辑清晰；不可评估/数据不足不得被当作真实 0 分 |
| 指标溯源 `/performance/lineage` | 能追溯公式、阈值、输入数据、窗口和版本 |
| 诊断清单 `/diagnosis` | 至少展示 PID 疑似、阀门/仪表疑似、数据/工况问题三类诊断 |
| 回路证据 `/diagnosis/loop/:loopId` | 根据 `loopId` 展示不同趋势、规则命中、事件线、建议动作和可信度 |
| 建议审核 `/closure/review` | 展示角色、决策、原因；支持通过、驳回、需补证据三类状态表达 |
| 多方审核 `/closure/multi-review` | 工艺、仪表、安全意见可并列查看；冲突意见会影响下游状态 |
| 实施记录 `/closure/implementation` | 明确系统不执行参数写入，只记录授权人员人工实施 |
| 风险回退 `/closure/rollback` | 展示原参数、回退条件、观察要求和人工回退边界 |
| 观察复评 `/closure/reevaluation` | 展示前后 KPI、报警、操作频次等对比；未满足条件时不得显示完整成功 |
| 证据包 `/evidence` | 展示 manifest、版本引用、included refs、状态、风险摘要、结论和数据溯源 |
| 样本报告 `/evidence/sample-report` | 展示样本范围、场景分布、可信度、不能证明事项和安全边界 |
| 安全部署 `/system/safety` | 明确不写 DCS、不切模式、不主动激励、人工实施、审计留痕 |
| 整定样例 `/tuning/sample` | 仅展示一条样例；含当前参数、建议参数、可信度、风险、回退；明确不代表批量整定 |

## 7. 数据与契约验收标准

| 对象 | 最低验收标准 |
|---|---|
| SampleBatch | 有样本 ID、来源、时间窗、回路数、readiness、风险列表 |
| LoopLedger | 有回路身份、PV/SP/OP/MODE 映射状态、数据可用状态、版本引用 |
| KpiResult | 有 P0 核心 KPI；不可算时使用 unavailable/partial/inconclusive，不用 0 伪装 |
| DiagnosisFinding | 有 finding type、severity/confidence、证据引用、建议动作、owner role 或等价展示 |
| EvidenceWindow | 至少三条，能按 loopId 匹配不同趋势、事件、规则 |
| ReviewRecord | 有角色、决策、原因；需补证据应影响闭环状态 |
| Reevaluation | 有前后窗口和 KPI 对比；缺失时证据包降级为 partial |
| TuningCase | 仅一条 P0 样例；有 current/suggested/fallback/confidence/risk/boundary |
| EvidencePackage | manifest-first；有版本引用、included refs、package_status、validity_status、risk_summary、conclusion |

## 8. 自动测试验收标准

| 测试项 | 必须断言 |
|---|---|
| 页面可打开 | 所有 P0 主链页面可打开，主区域和一级标题可见 |
| 无异常文案 | 全站关键路由不得出现 `NaN`、`undefined`、`Invalid Date` |
| 菜单完整 | 11 个一级菜单文本可见 |
| 版本标签 | P0/P1/P2/P3 标签可见，结构页不伪装成深做页 |
| 主链可走通 | 首页 -> 证据链 -> 审核 -> 实施/复评 -> 证据包 -> Sponsor 可连续导航 |
| 三类诊断 | PID 疑似、阀门/仪表疑似、数据/工况问题均可打开不同证据链 |
| EvidencePackage | 页面包含版本引用、included refs、风险摘要、manifest 或 hash 文案 |
| 安全边界 | 安全页、实施页、整定页均包含不写 DCS/人工实施等核心文案 |
| partial 状态 | 缺证据或缺复评场景显示 partial/桌面评审版/模拟版 |
| 移动端 | mobile 项目下核心页面不崩溃，关键按钮和导航可见 |

## 9. 完成定义

本轮整改完成必须同时满足：

| 条件 | 标准 |
|---|---|
| 构建 | `npm run build` 通过 |
| 测试 | `npm run test:smoke` 通过，且新增验收断言覆盖关键口径 |
| 展示 | 核心页面无 `NaN`、`undefined`、日期错乱等明显问题 |
| 主链 | P0 主链可连续点击完成 |
| 证据 | 三类诊断样例可分别展开，并且内容不同 |
| 契约 | 证据包显示版本引用和 manifest-first 结构 |
| 安全 | 所有相关页面保持只读 DCS 与人工实施边界 |
| 文档 | README 或阶段交付说明更新本轮整改后的状态与运行方式 |

## 10. 建议给 `/loop` 的执行提示

可直接使用以下提示启动整改：

```text
请按 `clpm-p0-prototype-remediation-brief-2026-06-16.md` 对 `prototype/` 进行一轮 P0 原型整改。严格遵守：不修改根目录产品基线文档；不新增真实后端、真实 DCS 接入或真实算法；保持 React + Vite + TypeScript 和本地 demo/mock data；优先修复 P0/P1 问题；每轮完成后运行 npm run build 和 npm run test:smoke；若测试失败继续循环修复，直到通过。最终输出变更摘要、验证结果和仍未解决的问题。
```
