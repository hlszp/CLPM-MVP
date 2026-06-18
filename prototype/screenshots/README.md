# CLPM Prototype Screenshots

日期：2026-06-16
用途：阶段性成果可视化验收截图。

## 本地访问

```bash
cd /Users/zhangping/DEV/CLPM/prototype
npm run dev -- --host 127.0.0.1
```

浏览器打开：`http://127.0.0.1:5173/`

## 截图清单

| 截图 | 页面 | 说明 |
|---|---|---|
| `screenshots/clpm-prototype-home.png` | `/` | 工程首页：优先回路、证据摘要、动作待办 |
| `screenshots/clpm-prototype-evidence-chain.png` | `/diagnosis/loop/TIC-1115` | 单回路证据链：趋势、规则、事件、审核入口 |
| `screenshots/clpm-prototype-readiness.png` | `/samples/readiness` | 样本就绪：事件可用性、质量规则、partial 原因 |
| `screenshots/clpm-prototype-ledger-mapping.png` | `/loops/mapping` | 台账映射：字段矩阵、缺失项、下一步动作 |
| `screenshots/clpm-prototype-evidence-package.png` | `/evidence` | 证据包：manifest 引用、状态、风险摘要 |
| `screenshots/clpm-prototype-delivery-acceptance.png` | `/delivery/acceptance` | 验收清单、演示路径、整改就绪矩阵 |
| `screenshots/clpm-prototype-sponsor.png` | `/sponsor` | 管理首页：样本可信度、闭环率、风险与代表样例 |

## 验证命令

```bash
npm run build
npm run test:smoke
```
