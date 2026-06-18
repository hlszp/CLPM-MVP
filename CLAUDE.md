# CLPM Agent Guidance

本项目当前是危化企业控制回路性能评估与优化平台的产品规划资料库。

## 必读入口

先读：`design-documents-index-2026-06-16.md`。

该索引定义当前有效文档、历史文档、阅读顺序和下一阶段评审引用规则。

## 当前基线

| 类型 | 文件 |
|---|---|
| 当前 PRS | `product-requirements-specification-v0.4-2026-06-16.md` |
| 已批准产品化架构 | `/Users/zhangping/.gstack/projects/CLPM/zhangping-unknown-design-20260616-072247.md` |
| 完整菜单定稿 | `full-product-menu-ia-v0.1-2026-06-16.md` |
| 原型开发冻结任务书 | `prototype-development-freeze-v0.1-2026-06-16.md` |
| P0 契约主干设计 | `p0-contract-backbone-design-v0.1-2026-06-16.md` |
| 核心算法可信度设计 | `core-algorithm-confidence-design-v0.1-2026-06-16.md` |
| 文档索引 | `design-documents-index-2026-06-16.md` |
| CEO 评审结论 | `/Users/zhangping/.gstack/projects/CLPM/ceo-plans/2026-06-16-clpm-productization-validation.md` |
| 工程评审结论 | `/Users/zhangping/.gstack/projects/CLPM/zhangping-unknown-eng-review-20260616-093500.md` |
| 设计评审结论 | `/Users/zhangping/.gstack/projects/CLPM/zhangping-unknown-design-review-20260616-100500.md` |

## 核心决策

| 决策 | 当前口径 |
|---|---|
| 产品定位 | 控制回路性能治理与优化闭环平台，不是单机 PID 调参工具 |
| 技术护城河 | 可信数据 + 可解释诊断 + 可验证整定 + 安全闭环 + 规模化交付 |
| 安全边界 | 平台不直接修改 DCS 的 P/I/D 参数，只输出建议、证据、风险和回退方案；参数由授权人员人工实施并留痕 |
| 首版主线 | P0 治理闭环验证版，目标是证明产品不是报表工具 |
| 菜单结构 | 已形成 `full-product-menu-ia-v0.1-2026-06-16.md`，作为用户视角信息架构，不等同于 P0 交付范围 |
| 原型开发 | 已形成 `prototype-development-freeze-v0.1-2026-06-16.md`，技术栈冻结为 React + Vite + TypeScript，本地 mock data，不接真实 DCS |

## 下阶段规则

| 阶段 | 先读 |
|---|---|
| `/plan-eng-review` | approved 产品化架构 → PRS v0.4 → 完整菜单定稿 → 原型开发冻结任务书 → P0 契约主干设计 → 核心算法可信度设计 → 工程评审结论 |
| `/plan-design-review` | approved 产品化架构的 User-Facing Information Architecture → PRS v0.4 页面与状态约束 → 完整菜单定稿 → 原型开发冻结任务书 → 设计评审结论 → 竞品对比 |
| PRS 后续修订 | approved 产品化架构 → PRS v0.4 → 核心算法可信度设计 → CEO/工程/设计评审结论 |

## Stale docs 防护

不要把以下文件当作现行需求输入：

- `archive/product-requirements-specification-2026-06-15.md`
- `archive/product-requirements-specification-v0.2-2026-06-16.md`
- `archive/prs-adjustment-assessment-2026-06-15.md`
- `archive/tender-requirements-gap-assessment-2026-06-16.md`

这些文件只用于历史追溯。

## Skill routing

When the user's request matches an available skill, invoke it via the Skill tool. When in doubt, invoke the skill.

Key routing rules:
- Product ideas/brainstorming → invoke /office-hours
- Strategy/scope → invoke /plan-ceo-review
- Architecture → invoke /plan-eng-review
- Design system/plan review → invoke /design-consultation or /plan-design-review
- Full review pipeline → invoke /autoplan
- Bugs/errors → invoke /investigate
- QA/testing site behavior → invoke /qa or /qa-only
- Code review/diff check → invoke /review
- Visual polish → invoke /design-review
- Ship/deploy/PR → invoke /ship or /land-and-deploy
- Save progress → invoke /context-save
- Resume context → invoke /context-restore
- Author a backlog-ready spec/issue → invoke /spec
