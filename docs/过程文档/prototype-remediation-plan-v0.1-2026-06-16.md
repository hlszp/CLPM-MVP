# CLPM 原型系统整改计划

日期：2026-06-16

适用范围：`prototype/` 原型系统、`demo-data/control-loop-second-level/` 演示数据、原型运行与验收说明。

## 1. 整改目标

本轮整改目标是把原型从“主链可演示”提升为“P0 评审可验收”：

1. 评审人员能从工程首页连续走到证据链、审核、实施边界、复评、EvidencePackage 和 Sponsor 视图。
2. 可信样本、台账映射、KPI、诊断、闭环和证据包之间的关系清楚，且能追溯到样本和版本。
3. 平台只读 DCS、不写 P/I/D、不切模式、不主动激励的安全边界在相关页面显式可见。
4. 页面不得出现 `NaN`、`undefined`、`Invalid Date`、过期日期、横向溢出等演示硬伤。
5. P1/P2/P3 能力可以作为结构入口出现，但不得被误解为 P0 已完成交付。

## 2. 评审发现

| 编号 | 问题 | 影响 | 整改要求 |
|---|---|---|---|
| R-01 | demo 数据摘要缺少 `sample_interval_seconds` 和 `fields` | 重新导入可能生成异常文案 | 数据生成器和导入脚本都必须可重复生成正确派生数据 |
| R-02 | 单回路证据链曾只展示固定 EvidenceWindow | 三类诊断样例不可验证 | `/diagnosis/loop/:loopId` 必须按路由展示不同证据 |
| R-03 | 部分 P0 页面曾为结构占位 | 台账治理和样本可信表达不足 | 样本冻结、点位映射、台账校核、排除管理、版本管理、质量规则必须具备字段级静态骨架 |
| R-04 | EvidencePackage 表达曾过浅 | manifest-first 和可审计性不足 | 展示 included refs、状态、hash、风险摘要、不可证明事项 |
| R-05 | partial 状态传播不足 | 补证据状态可能被误读为完成闭环 | 审核冲突必须影响实施、复评和证据包状态 |
| R-06 | 移动端存在页面级横向溢出 | 移动端演示观感不达标 | 表格可在容器内滚动，但页面本身不得横向滚动 |
| R-07 | 自动测试偏冒烟 | 难以防回归 | Playwright 必须覆盖业务断言、无异常文案、三类证据链、安全边界和移动端溢出 |

## 3. 整改内容

| 任务 | 整改内容 | 涉及文件 | 验收方式 |
|---|---|---|---|
| T-01 数据源闭环 | 补齐 `dataset_summary.json` 的采样间隔和字段列表，导入脚本使用 fallback，重新生成派生数据 | `demo-data/control-loop-second-level/generate-control-loop-demo-data.mjs`、`prototype/scripts/import-demo-data.mjs`、`prototype/src/data/demoData.generated.ts` | 运行 `node generate-control-loop-demo-data.mjs` 和 `npm run import:demo-data` 后无异常字段 |
| T-02 三类证据链 | 自动派生 PID 疑似、阀门/仪表疑似、数据/工况问题三条 EvidenceWindow | `prototype/scripts/import-demo-data.mjs`、`prototype/src/pages/closureEvidencePages.tsx` | `/diagnosis/loop/TIC-1115`、`/diagnosis/loop/PIC-1122`、`/diagnosis/loop/FIC-1136` 内容不同 |
| T-03 P0 页面补强 | 补齐样本冻结、点位映射、台账校核、排除管理、版本管理、质量规则、导出中心、交付验收页 | `prototype/src/pages/*.tsx` | 页面不再显示“结构展示页”，关键字段可见 |
| T-04 状态传播 | 审核需补证据时，实施、复评、导出和证据包维持 partial | `prototype/src/data/mockData.ts`、`prototype/src/pages/closureEvidencePages.tsx` | 页面展示 `PACKAGE_PARTIAL`、`VALID_WITH_MISSING_REFS` 和桌面评审版说明 |
| T-05 响应式修复 | 消除首页、证据包、数据源页移动端页面级横向溢出 | `prototype/src/styles/app.css` | Playwright 断言 `documentElement.scrollWidth <= innerWidth + 2` |
| T-06 自动验收 | 增加业务验收断言，不只检查页面可打开 | `prototype/tests/smoke.spec.ts` | `npm run test:smoke` 全部通过 |
| T-07 交付说明 | 更新阶段交付说明，记录整改状态、测试结果和仍不做事项 | `prototype/stage-delivery-v0.4-2026-06-16.md` | 文档与实际测试数量一致 |

## 4. 验收标准

| 类别 | 验收标准 |
|---|---|
| 构建 | `npm run build` 通过 |
| 类型检查 | `npm exec tsc -- --noEmit --pretty false` 通过 |
| 自动测试 | `npm run test:smoke` 通过，覆盖桌面和移动端 |
| 页面质量 | 核心路由不得出现 `NaN`、`undefined`、`Invalid Date`、`2026-05` |
| 移动端 | 核心移动端页面不得出现页面级横向溢出 |
| 证据链 | 三类诊断样例可以分别打开，趋势摘要、规则和事件不同 |
| 台账治理 | 点位映射、台账校核、排除管理、版本管理页面具备字段级骨架 |
| 证据包 | 展示 manifest、included refs、状态、hash、风险摘要、不可证明事项和数据溯源 |
| 安全边界 | 安全页、实施页、整定页均明确只读 DCS、人工实施、审计留痕 |
| 范围边界 | 明确 24 回路为开发 smoke 数据，P0 评审目标仍按 50-100/72 回路扩样口径管理 |

## 5. 持续改进机制

本轮采用小循环整改：

```text
评审发现 -> 明确整改项 -> 小步修改 -> 生成/导入数据 -> 自动测试 -> 浏览器复核 -> 更新文档 -> 再评审
```

每一轮必须满足：

1. 只修改与本轮问题直接相关的文件。
2. 每次数据脚本修改后必须重新运行数据生成和导入。
3. 每次 UI 或数据口径修改后必须运行类型检查、构建和 Playwright。
4. 若发现新问题，进入下一轮整改；若验收全部通过，更新阶段说明并停止扩大范围。

## 6. 当前收口状态

本计划对应的本轮整改已完成。已修复数据再生成回归风险、移动端横向溢出风险、状态徽标样式问题，并把移动端溢出纳入 Playwright 验收。

最终验证结果：

| 验证项 | 结果 |
|---|---|
| 数据生成 | `node generate-control-loop-demo-data.mjs` 通过，`dataset_summary.json` 已包含采样间隔和字段清单 |
| 数据导入 | `npm run import:demo-data` 通过，生成三条 EvidenceWindow |
| 类型检查 | `npm exec tsc -- --noEmit --pretty false` 通过 |
| 构建 | `npm exec vite -- build --outDir /tmp/clpm-prototype-dist-remediation-final --emptyOutDir true` 通过 |
| 自动测试 | `npx playwright test --reporter=list --output=/tmp/clpm-playwright-results-remediation-final`，82/82 passed |
| 浏览器复核 | 9 个核心路由在 desktop/mobile 下无异常文案、无 console 错误、无页面级横向溢出 |
