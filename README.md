# CLPM

危化企业控制回路性能治理与优化平台的产品规划资料库。

## 当前有效文档

| 类型 | 文件 |
|---|---|
| 当前 PRS | `product-requirements-specification-v0.4-2026-06-16.md` |
| 已批准产品化架构 | `/Users/zhangping/.gstack/projects/CLPM/zhangping-unknown-design-20260616-072247.md` |
| 完整菜单定稿 | `full-product-menu-ia-v0.1-2026-06-16.md` |
| 原型开发冻结任务书 | `prototype-development-freeze-v0.1-2026-06-16.md` |
| P0 契约主干设计 | `p0-contract-backbone-design-v0.1-2026-06-16.md` |
| 核心算法可信度设计 | `core-algorithm-confidence-design-v0.1-2026-06-16.md` |
| 当前 PRD | `product-requirements-document-v1-2026-06-16.md` |
| 总体 FDS | `functional-design-specification-v1-2026-06-16.md` |
| 交付架构设计 | `system-architecture-delivery-v1-2026-06-16.md` |
| 数据模型设计 | `data-model-design-v1-2026-06-16.md` |
| API 接口设计 | `api-interface-design-v1-2026-06-16.md` |
| 容器化部署与前端技术评估 | `orbstack-container-deployment-v1-2026-06-16.md` |

## 推荐阅读顺序

1. `design-documents-index-2026-06-16.md`
2. `product-requirements-specification-v0.4-2026-06-16.md`
3. `full-product-menu-ia-v0.1-2026-06-16.md`
4. `prototype-development-freeze-v0.1-2026-06-16.md`
5. `p0-contract-backbone-design-v0.1-2026-06-16.md`
6. `/Users/zhangping/.gstack/projects/CLPM/zhangping-unknown-design-20260616-072247.md`
7. `core-algorithm-confidence-design-v0.1-2026-06-16.md`
8. 需要追溯时再读历史文档

## 当前共识

| 主题 | 当前口径 |
|---|---|
| 产品定位 | 控制回路绩效治理与优化闭环平台，不是单机 PID 调参工具 |
| 首版主线 | P0 治理闭环验证包 |
| 首版样本 | `50-100` 回路 |
| P0 入口 | 工程师工作台 + sponsor 证据视图 |
| P0 新增制品 | 样本验证仪表盘、数据可用性与可整定性雷达、验收证据包 |
| 工程主约束 | 契约主干 + EvidencePackage manifest-first |
| 性能边界 | `5` 年任意查询后置至 P2 |
| 安全边界 | 平台不写 DCS，只建议、审核、人工实施、复评 |

## 目录说明

| 文档 | 用途 |
|---|---|
| `product-requirements-specification-v0.4-2026-06-16.md` | 当前唯一有效 PRS |
| `full-product-menu-ia-v0.1-2026-06-16.md` | 正式产品一级/二级菜单和原型展示深度定稿 |
| `prototype-development-freeze-v0.1-2026-06-16.md` | 原型开发任务书、页面清单、样例数据和技术栈冻结 |
| `prototype/README.md` | 原型系统工作区入口说明 |
| `p0-contract-backbone-design-v0.1-2026-06-16.md` | P0 核心对象字段、状态、接口契约 |
| `core-algorithm-confidence-design-v0.1-2026-06-16.md` | 算法与可信度补充设计 |
| `research/control-loop-pid-demand-research-2026-06-15.md` | 市场、标准、政策背景 |
| `research/competitor-comparison-2026-06-15.md` | 竞品与差异化判断 |
| `archive/product-requirements-specification-2026-06-15.md` | 历史版本，仅追溯 |
| `archive/product-requirements-specification-v0.2-2026-06-16.md` | 历史版本，仅追溯 |
| `archive/product-requirements-specification-v0.3-2026-06-16.md` | 历史基线，已被 v0.4 替代 |
| `archive/prs-adjustment-assessment-2026-06-15.md` | 历史过程评估 |
| `archive/tender-requirements-gap-assessment-2026-06-16.md` | 历史过程评估 |

## 维护规则

- 现行需求、原型、研发拆解和投标响应统一以 `product-requirements-specification-v0.4-2026-06-16.md` 为准。
- 旧版 PRS 和过程评估文档只用于历史追溯，不作为新一轮评审输入。
- 新一轮架构、设计或专项设计必须先吸收 approved 产品化架构和 v0.4 的结论，再继续扩写。
- PDF 原始资料统一存放于 `sources/`。
