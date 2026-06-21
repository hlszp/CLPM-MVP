# CLPM

危化企业控制回路性能治理与优化平台的产品规划资料库。

## 当前有效文档

| 类型 | 文件 |
|---|---|
| 当前 PRD | `docs/设计文档/01-PRD/PRD.md` |
| 总体 FDS | `docs/设计文档/02-FDS/FDS.md` |
| 交付架构设计 | `docs/设计文档/03-ADS/ADS.md` |
| 数据模型设计 | `docs/设计文档/04-DDS/DDS.md` |
| API 接口设计 | `docs/设计文档/05-IDS/IDS.md` |
| UI/UX 设计规范 | `docs/设计文档/06-UIUX/ui-ux-design-guidelines.md` |
| 原型设计基线 | `DESIGN.md` |
| 原型代码入口 | `docs/设计文档/prototype/README.md` |
| 已批准产品化架构 | `/Users/zhangping/.gstack/projects/CLPM/zhangping-unknown-design-20260616-072247.md` |
| 原型开发冻结任务书 | `docs/过程文档/prototype-development-freeze-v0.1-2026-06-16.md` |

## 推荐阅读顺序

1. `docs/过程文档/design-documents-index-2026-06-16.md`
2. `docs/设计文档/01-PRD/PRD.md`
3. `docs/设计文档/02-FDS/FDS.md`
4. `docs/设计文档/04-DDS/DDS.md`
5. `docs/设计文档/05-IDS/IDS.md`
6. `docs/设计文档/06-UIUX/ui-ux-design-guidelines.md`
7. 需要追溯时再读 `docs/归档文档/` 目录中的历史文档

## 当前共识

| 主题 | 当前口径 |
|---|---|
| 产品定位 | 控制回路绩效治理与优化闭环平台，不是单机 PID 调参工具 |
| 首版主线 | Phase 1 (MVP/V1.0)：跑通"自动评估、自动诊断、轻量跟踪"闭环 |
| 首版范围 | 工厂模型配置、性能看板、诊断中心、Action Tracker、报表中心、系统管理 |
| 核心模型 | Action Tracker 轻量跟踪（PENDING → IN_PROGRESS → RESOLVED/IGNORED） |
| 工程主约束 | PRD 为唯一事实来源，UI/UX v3.0 为唯一 UI/UX 输入性文件 |
| 性能边界 | LTTB 降采样 maxPoints=2000，30 天时间窗口 |
| 安全边界 | 平台不写 DCS，只输出建议、证据、风险与回退方案 |

## 目录说明

| 文档/目录 | 用途 |
|---|---|
| `docs/预研文档/` | 包含竞品分析、市场研究、行业标准与政策背景预研资料 |
| `docs/设计文档/` | 包含所有核心技术文档，含 PRD、FDS、ADS、DDS、IDS、UIUX 及 Prototype 原型系统 |
| `docs/过程文档/` | 包含需求评审记录、重构计划、任务冻结包等日常过程记录文件 |
| `docs/归档文档/` | 包含历史失效版本的需求文档与过程评估报告，仅供追溯 |
| `docs/设计文档/01-PRD/PRD.md` | 当前唯一有效 PRD |
| `docs/设计文档/02-FDS/FDS.md` | 当前系统功能设计说明总册 |
| `docs/设计文档/03-ADS/ADS.md` | 当前系统架构交付设计 |
| `docs/设计文档/04-DDS/DDS.md` | 当前系统数据模型设计 |
| `docs/设计文档/05-IDS/IDS.md` | 当前系统 API 接口设计 |
| `docs/设计文档/06-UIUX/ui-ux-design-guidelines.md` | 当前可视化设计与用户体验规范 |
| `DESIGN.md` | 原型重构设计基线（第一阶段 1:1 还原标准） |
| `docs/过程文档/prototype-development-freeze-v0.1-2026-06-16.md` | 原型开发任务书、页面清单、样例数据和技术栈冻结 |
| `docs/设计文档/prototype/README.md` | 原型系统代码库入口说明 |

## 维护规则

- 现行需求、原型、研发拆解和投标响应统一以 `docs/设计文档/01-PRD/PRD.md` 为准。
- 归档目录 (`docs/归档文档/`) 下的文件只用于历史追溯，不作为新一轮评审输入。
- 新一轮架构、设计或专项设计必须先吸收 approved 产品化架构和最新 PRD 的结论，再继续扩写。
- PDF 原始资料及外部参考手册统一存放于 `docs/预研文档/`。
