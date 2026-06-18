# 设计文档去重与阅读顺序

日期：2026-06-16
范围：项目目录下现有 Markdown 设计、需求、调研和评估文档

## 1. 结论

当前文档建议采用一级体系拆分：

```text
.
├── product-requirements-specification-v0.4-2026-06-16.md
├── full-product-menu-ia-v0.1-2026-06-16.md
├── prototype-development-freeze-v0.1-2026-06-16.md
├── p0-contract-backbone-design-v0.1-2026-06-16.md
├── core-algorithm-confidence-design-v0.1-2026-06-16.md
├── design-documents-index-2026-06-16.md
├── README.md
├── prototype/
│   └── README.md
├── research/
│   ├── control-loop-pid-demand-research-2026-06-15.md
│   └── competitor-comparison-2026-06-15.md
├── archive/
│   ├── product-requirements-specification-2026-06-15.md
│   ├── product-requirements-specification-v0.2-2026-06-16.md
│   ├── product-requirements-specification-v0.3-2026-06-16.md
│   ├── prs-adjustment-assessment-2026-06-15.md
│   └── tender-requirements-gap-assessment-2026-06-16.md
└── sources/
    ├── GBT44693.1-2024危险化学品企业工艺平稳性第1部分：管理导则上传.pdf
    ├── GBT44693.2-2024危险化学品企业工艺平稳性第2部分：控制回路性能评估与优化技术规范上传.pdf
    ├── PID性能监控评估软件3.1使用手册.pdf
    └── 控制回路性能评估分析软件使用手册.pdf
```

真正需要作为后续产品设计、研发和投标响应依据的有效文档有 10 份：

| 顺序 | 文档 | 定位 |
|---:|---|---|
| 1 | `research/control-loop-pid-demand-research-2026-06-15.md` | 市场、标准、政策和技术背景调研 |
| 2 | `research/competitor-comparison-2026-06-15.md` | 竞品格局和差异化判断 |
| 3 | `product-requirements-specification-v0.4-2026-06-16.md` | 当前唯一有效 PRS 主文档 |
| 4 | `full-product-menu-ia-v0.1-2026-06-16.md` | 正式产品一级/二级菜单和原型展示深度定稿 |
| 5 | `prototype-development-freeze-v0.1-2026-06-16.md` | 原型开发任务书、页面清单、样例数据和技术栈冻结 |
| 6 | `p0-contract-backbone-design-v0.1-2026-06-16.md` | P0 核心对象字段、状态、接口契约 |
| 7 | `core-algorithm-confidence-design-v0.1-2026-06-16.md` | 核心算法与可信度体系补充设计 |
| 8 | `/Users/zhangping/.gstack/projects/CLPM/zhangping-unknown-design-20260616-072247.md` | approved 产品化架构基线 |
| 9 | `/Users/zhangping/.gstack/projects/CLPM/ceo-plans/2026-06-16-clpm-productization-validation.md` | CEO 评审收口结论 |
| 10 | `/Users/zhangping/.gstack/projects/CLPM/zhangping-unknown-eng-review-20260616-093500.md` | 工程评审收口结论 |

设计侧还应补充引用：

| 文档 | 定位 |
|---|---|
| `/Users/zhangping/.gstack/projects/CLPM/zhangping-unknown-design-review-20260616-100500.md` | 设计评审收口结论 |

以下文档已归档，只用于版本演进和决策追溯：

| 文档 | 当前状态 |
|---|---|
| `archive/product-requirements-specification-2026-06-15.md` | v0.1 历史基线 |
| `archive/product-requirements-specification-v0.2-2026-06-16.md` | v0.2 标准增强版 |
| `archive/product-requirements-specification-v0.3-2026-06-16.md` | v0.3 历史基线，已被 v0.4 替代 |
| `archive/prs-adjustment-assessment-2026-06-15.md` | 历史过程评估 |
| `archive/tender-requirements-gap-assessment-2026-06-16.md` | 历史过程评估 |

## 2. 推荐阅读顺序

### 2.1 管理层 / 商务

1. `research/control-loop-pid-demand-research-2026-06-15.md`
2. `research/competitor-comparison-2026-06-15.md`
3. `product-requirements-specification-v0.4-2026-06-16.md`

关注：为什么做、与标准和招标关系、差异化、当前首版范围。

### 2.2 产品经理 / 方案人员

1. `product-requirements-specification-v0.4-2026-06-16.md`
2. `/Users/zhangping/.gstack/projects/CLPM/zhangping-unknown-design-20260616-072247.md`
3. `/Users/zhangping/.gstack/projects/CLPM/ceo-plans/2026-06-16-clpm-productization-validation.md`
4. `full-product-menu-ia-v0.1-2026-06-16.md`
5. `prototype-development-freeze-v0.1-2026-06-16.md`
6. `p0-contract-backbone-design-v0.1-2026-06-16.md`
7. `core-algorithm-confidence-design-v0.1-2026-06-16.md`
8. `research/competitor-comparison-2026-06-15.md`
9. `archive/tender-requirements-gap-assessment-2026-06-16.md`，仅在需要追溯招标条款如何合入 PRS 时阅读

关注：P0/P1/P2/P3 边界、页面与状态、验收标准、关键新增制品。

### 2.3 算法 / 控制工程

1. `product-requirements-specification-v0.4-2026-06-16.md`
2. `p0-contract-backbone-design-v0.1-2026-06-16.md`
3. `core-algorithm-confidence-design-v0.1-2026-06-16.md`
4. `research/control-loop-pid-demand-research-2026-06-15.md`
5. `archive/prs-adjustment-assessment-2026-06-15.md`，仅在需要追溯标准指标来源时阅读

关注：只读边界、可整定性判定、数据窗口、数据激励指数、模型质量、整定可信度。

### 2.4 研发 / 架构

1. `/Users/zhangping/.gstack/projects/CLPM/zhangping-unknown-design-20260616-072247.md`
2. `product-requirements-specification-v0.4-2026-06-16.md`
3. `full-product-menu-ia-v0.1-2026-06-16.md`
4. `prototype-development-freeze-v0.1-2026-06-16.md`
5. `p0-contract-backbone-design-v0.1-2026-06-16.md`
6. `/Users/zhangping/.gstack/projects/CLPM/ceo-plans/2026-06-16-clpm-productization-validation.md`
7. `/Users/zhangping/.gstack/projects/CLPM/zhangping-unknown-eng-review-20260616-093500.md`
8. `/Users/zhangping/.gstack/projects/CLPM/zhangping-unknown-design-review-20260616-100500.md`
9. `core-algorithm-confidence-design-v0.1-2026-06-16.md`
10. `research/control-loop-pid-demand-research-2026-06-15.md`

关注：契约主干、EvidencePackage、状态与版本引用、P0/P2 查询隔离、双入口工作台。

### 2.5 实施 / 交付

1. `product-requirements-specification-v0.4-2026-06-16.md`
2. `p0-contract-backbone-design-v0.1-2026-06-16.md`
3. `core-algorithm-confidence-design-v0.1-2026-06-16.md`
4. `archive/tender-requirements-gap-assessment-2026-06-16.md`，仅在追溯招标原始交付条款时阅读

关注：样本验证包、点位映射、数据质量、证据包、审核与复评口径。

## 3. 文档分层

### 3.1 当前有效主文档

| 文档 | 用途 | 是否必读 |
|---|---|---|
| `product-requirements-specification-v0.4-2026-06-16.md` | 当前唯一有效产品需求规格书 | 是 |

说明：

- v0.4 已吸收 v0.3、approved 产品化架构和 CEO / 工程 / 设计三轮评审结论。
- 后续所有功能清单、原型设计、研发拆解、投标响应统一以 v0.4 为准。
- v0.3 已退化为历史基线，不再作为现行需求输入。

### 3.2 当前有效补充设计

| 文档 | 用途 | 是否必读 |
|---|---|---|
| `core-algorithm-confidence-design-v0.1-2026-06-16.md` | 算法、模型辨识、可信度、交互式整定设计 | 算法 / 产品 / 研发必读 |
| `full-product-menu-ia-v0.1-2026-06-16.md` | 正式产品一级/二级菜单和原型展示深度定稿 | 产品 / 设计 / 研发必读 |
| `prototype-development-freeze-v0.1-2026-06-16.md` | 原型开发任务书、页面清单、样例数据和技术栈冻结 | 产品 / 设计 / 研发必读 |
| `p0-contract-backbone-design-v0.1-2026-06-16.md` | P0 核心对象字段、状态、接口契约 | 产品 / 研发 / 测试必读 |
| approved 产品化架构 | 产品基线、版本蓝图、用户架构 | 产品 / 研发 / 设计必读 |
| CEO / 工程 / 设计评审结论 | 现行收口约束 | 修订 PRS、专项设计必读 |

### 3.3 研究与外部依据

| 文档 | 用途 | 是否必读 |
|---|---|---|
| `research/control-loop-pid-demand-research-2026-06-15.md` | 标准、政策、市场和技术背景 | 新成员建议必读 |
| `research/competitor-comparison-2026-06-15.md` | 竞品能力、差异化和定位判断 | 产品 / 商务建议必读 |

### 3.4 历史追溯

| 文档 | 用途 | 常规是否阅读 |
|---|---|---|
| `archive/product-requirements-specification-2026-06-15.md` | v0.1 历史基线 | 否 |
| `archive/product-requirements-specification-v0.2-2026-06-16.md` | v0.2 标准增强版 | 否 |
| `archive/product-requirements-specification-v0.3-2026-06-16.md` | v0.3 历史基线 | 否 |
| `archive/prs-adjustment-assessment-2026-06-15.md` | 历史过程评估 | 否 |
| `archive/tender-requirements-gap-assessment-2026-06-16.md` | 历史过程评估 | 按需 |

## 4. 当前最高优先级决策

| 决策 | 当前口径 |
|---|---|
| 产品定位 | 控制回路绩效治理与优化闭环平台，不是单机 PID 调参工具 |
| 首版主线 | P0 治理闭环验证包 |
| 首版样本 | `50-100` 回路 |
| P0 入口 | 工程师工作台 + sponsor 证据视图 |
| P0 新增制品 | 样本验证仪表盘、数据可用性与可整定性雷达、验收证据包 |
| 工程主约束 | 契约主干 + EvidencePackage manifest-first |
| 性能边界 | `5` 年任意查询后置至 P2 |
| 安全边界 | 平台不写 DCS，只建议、审核、人工实施、复评 |

## 5. 下一阶段评审引用规则

| 下一阶段 | 必读顺序 | 使用方式 | 不要引用 |
|---|---|---|---|
| `/plan-eng-review` | approved 产品化架构 → PRS v0.4 → P0 契约主干设计 → 核心算法可信度设计 → 工程评审结论 | 先定产品主线、契约主干、性能边界，再拆工程模块 | 不要把 v0.1/v0.2/v0.3 PRS 当现行需求 |
| `/plan-design-review` | approved 产品化架构的 User-Facing Information Architecture → PRS v0.4 页面与状态约束 → 完整菜单定稿 → 设计评审结论 → 竞品对比 | 审查双入口、工作台骨架、证据链、样本验证仪表盘、证据包摘要 | 不要把 generic dashboard 当默认设计 |
| PRS 后续修订 | approved 产品化架构 → PRS v0.4 → P0 契约主干设计 → 核心算法可信度设计 → CEO / 工程 / 设计评审结论 | 在 v0.4 基础上增量修订 | 不要回滚到 v0.3 口径 |
| P0 原型规格 | approved 架构 Assignment / Version Scope / Page Model → PRS v0.4 P0 范围 → 完整菜单定稿 → 原型开发冻结任务书 → P0 契约主干设计 | 只做治理闭环验证包和 `1` 个整定样例 | 不要承诺迁移学习、批量整定、`5` 年查询真实性能 |

## 6. stale docs 防护

以下文档只做历史追溯，不再作为现行需求输入：

- `archive/product-requirements-specification-2026-06-15.md`
- `archive/product-requirements-specification-v0.2-2026-06-16.md`
- `archive/product-requirements-specification-v0.3-2026-06-16.md`
- `archive/prs-adjustment-assessment-2026-06-15.md`
- `archive/tender-requirements-gap-assessment-2026-06-16.md`
