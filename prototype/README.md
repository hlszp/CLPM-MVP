# CLPM Prototype

本目录放置 CLPM 原型系统代码、样例数据、运行说明和浏览器验证记录。

当前状态：已初始化 React + Vite + TypeScript 原型工程，具备完整产品导航、P0 主链页面、本地 demo data、绩效趋势分析页、未知路由降级页、Playwright 验收级 smoke test（98/98）、5 分钟演示脚本和阶段性交付 v0.4 说明。

开工依据：

- `../prototype-development-freeze-v0.1-2026-06-16.md`
- `../full-product-menu-ia-v0.1-2026-06-16.md`
- `../clpm-p0-prototype-spec-package-2026-06-16.md`
- `../p0-contract-backbone-design-v0.1-2026-06-16.md`
- `../product-requirements-specification-v0.4-2026-06-16.md`

技术路线：

- React + Vite + TypeScript
- React Router
- Apache ECharts
- lucide-react
- 本地 mock data
- Playwright smoke test

运行命令：

```bash
npm install
npm run import:demo-data
npm run dev
npm run build
npm run test:smoke
```

目录：

```text
prototype/
├── README.md
├── package.json
├── index.html
├── vite.config.ts
├── playwright.config.ts
├── src/
│   ├── App.tsx
│   ├── main.tsx
│   ├── routes/
│   ├── pages/
│   ├── components/
│   ├── data/
│   ├── styles/
│   └── types/
├── public/
└── tests/
```

数据来源：

| 来源 | 用途 |
|---|---|
| `../demo-data/control-loop-second-level` | 24 回路、1 秒采样、1 小时模拟控制回路数据 |
| `src/data/demoData.generated.ts` | 由 `npm run import:demo-data` 生成的原型派生数据 |

样本规模口径：当前 24 回路为开发 smoke 数据，用于验证主链与契约表达；P0 评审目标样本规模仍按 50-100/72 回路口径保留，扩样后需重新生成 EvidencePackage。

交付材料：

| 文件 | 用途 |
|---|---|
| `demo-script-p0-2026-06-16.md` | 5 分钟 P0 原型演示脚本 |
| `stage-delivery-v0.4-2026-06-16.md` | 阶段性交付范围、关键路由和验证记录 |

原型边界：

- P0 主链高保真可点击。
- P1/P2/P3 只做结构展示。
- 不接真实 DCS。
- 不写 DCS 参数。
- 不实现真实后端和真实算法。
