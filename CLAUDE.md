# CLPM Agent Guidance

本项目当前是危化企业控制回路性能评估与优化平台的产品规划资料库。

## 必读入口

先读：`README.md`（当前共识与目录说明）与 `docs/设计文档/01-PRD/PRD.md` v3.0。

PRD v3.0 是后续所有设计、研发、原型与投标响应的**唯一事实来源**；UI/UX v4.0 是原型与正式研发的**唯一 UI/UX 输入性文件**。其他设计文档（FDS/ADS/DDS/IDS）均从 PRD 派生并对齐。

## 当前基线（2026-06-20 修订）

| 类型 | 文件 | 版本 |
|---|---|---|
| 产品需求规范 PRD | `docs/设计文档/01-PRD/PRD.md` | v3.0 |
| 功能设计规范 FDS | `docs/设计文档/02-FDS/FDS.md` | v3.0 |
| 应用设计规范 ADS | `docs/设计文档/03-ADS/ADS.md` | v3.0 |
| 数据模型设计 DDS | `docs/设计文档/04-DDS/DDS.md` | v3.0 |
| API 接口设计 IDS | `docs/设计文档/05-IDS/IDS.md` | v3.0 |
| UI/UX 设计规范 | `docs/设计文档/06-UIUX/ui-ux-design-guidelines.md` | v4.0 |
| 原型设计基线 | `DESIGN.md` | v2.0（对齐 v3.0/v4.0） |
| 原型代码入口 | `docs/设计文档/prototype/README.md` | 已重置为干净基线 |
| 文档索引 | `docs/过程文档/design-documents-index-2026-06-16.md` | v2.0（对齐 v3.0/v4.0） |
| 已批准产品化架构 | `/Users/zhangping/.gstack/projects/CLPM/zhangping-unknown-design-20260616-072247.md` | 历史参考 |
| CEO 评审结论 | `/Users/zhangping/.gstack/projects/CLPM/ceo-plans/2026-06-16-clpm-productization-validation.md` | 历史参考 |
| 工程评审结论 | `/Users/zhangping/.gstack/projects/CLPM/zhangping-unknown-eng-review-20260616-093500.md` | 历史参考 |
| 设计评审结论 | `/Users/zhangping/.gstack/projects/CLPM/zhangping-unknown-design-review-20260616-100500.md` | 历史参考 |

## 核心决策

| 决策 | 当前口径 |
|---|---|
| 产品定位 | 产品化、工具化的控制回路绩效治理与优化闭环平台，非项目型定制化系统；用户（管理员/工程师）可自助完成配置组态，减少开发团队介入 |
| 模块架构 | 6 模块 + 1 门户：工作台 / 回路管理 / 性能评估 / 诊断中心 / 回路整定 / 系统管理；各业务模块遵循"配置→运行→分析"三态自包含原则，减少跨模块依赖 |
| AAS 数据模型 | AAS 同步 tag 位号（非回路实体）；回路由用户创建并关联 7 个 OPC tag（PV/SP/OP/MODE/PID_P/PID_I/PID_D）；PID 参数与控制模式从关联 tag 只读读取；数据质量主要针对 PV 值（Good/Bad/Uncertain 质量码） |
| Action Tracker | 降级为诊断中心子模块（子菜单路由），状态机 PENDING → IN_PROGRESS → RESOLVED/IGNORED |
| 统计分析 | 不设独立模块，分散到各业务模块的"分析"态；自动报表归入系统管理 |
| 回路整定 | Phase 2 落地，Phase 1 仅完成原型页面设计；含工作台/模型辨识/整定算法/闭环仿真 4 子模块 |
| 技术护城河 | 可信数据 + 可解释诊断 + 可验证整定 + 安全闭环 + 规模化交付 |
| 安全边界 | 平台不直接修改 DCS 的 P/I/D 参数，只输出建议、证据、风险和回退方案；参数由授权人员人工实施并留痕 |
| 首版主线 | Phase 1 (MVP/V1.0)：跑通"自动评估、自动诊断、轻量跟踪"闭环 |
| 原型开发 | React 19 + Vite + TypeScript，本地 mock data，不接真实 DCS；菜单结构以 `prototype/src/routes/menuConfig.ts` 为单一事实来源 |
| 性能边界 | LTTB 降采样 maxPoints=2000，30 天时间窗口 |
| 文档权威性 | PRD v3.0 为唯一事实来源；UI/UX v4.0 为唯一 UI/UX 输入；FDS/ADS/DDS/IDS v3.0 与 PRD/UI-UX 完全对齐，跨文档术语/路由/API/数据字段已通过一致性校验 |

## 下阶段规则

| 阶段 | 先读 |
|---|---|
| `/plan-eng-review` | PRD v3.0 → ADS v3.0 → DDS v3.0 → IDS v3.0 → FDS v3.0 |
| `/plan-design-review` | PRD v3.0 → UI/UX v4.0 → FDS v3.0 |
| `/plan-ceo-review` | PRD v3.0 → README.md（当前共识） |
| PRD 后续修订 | PRD v3.0 → FDS/ADS/DDS/IDS v3.0 → UI/UX v4.0（保持派生关系） |
| 原型开发 | UI/UX v4.0 → `prototype/src/routes/menuConfig.ts` → `DESIGN.md`（注意 DESIGN.md 待复核） |

## Stale docs 防护

不要把以下文件当作现行需求输入：

- `archive/product-requirements-specification-2026-06-15.md`
- `archive/product-requirements-specification-v0.2-2026-06-16.md`
- `archive/prs-adjustment-assessment-2026-06-15.md`
- `archive/tender-requirements-gap-assessment-2026-06-16.md`
- `docs/归档文档/project-assessment-report.md`（项目现状评估，重构建议已落地）
- `docs/归档文档/prototype-design-spec.md`（被 `DESIGN.md` 取代）
- `docs/归档文档/full-prototype-planning.md`（被 `docs/过程文档/superpowers/plans/` 取代）
- `docs/归档文档/prototype-visual-tokens-v0.1-2026-06-16.md`（被 `06-UIUX/ui-ux-design-guidelines.md` §3 覆盖）
- `docs/归档文档/prototype-responsive-accessibility-v0.1-2026-06-16.md`（被 `06-UIUX/ui-ux-design-guidelines.md` §2 覆盖）
- `product-requirements-specification-v0.4-2026-06-16.md`（PRS v0.4 已被 PRD v3.0 取代）
- `full-product-menu-ia-v0.1-2026-06-16.md`（菜单已并入 UI/UX v4.0 §5）
- `prototype-development-freeze-v0.1-2026-06-16.md`（任务书已被 UI/UX v4.0 25 页面清单取代）
- `p0-contract-backbone-design-v0.1-2026-06-16.md`（P0 模型已被 Phase 1/Action Tracker 模型取代）
- `core-algorithm-confidence-design-v0.1-2026-06-16.md`（可信度设计已并入 ADS v3.0 与 DDS v3.0）
- `DESIGN.md`（仍含旧 SampleBatch/EvidencePackage 模型，待复核后再启用）

以下 v0.1 文件已于 2026-06-20 删除（概念体系冲突，有价值内容已吸收进 `06-UIUX/ui-ux-design-guidelines.md` v4.0）：
- `docs/设计文档/prototype-state-spec-v0.1-2026-06-16.md`
- `docs/设计文档/prototype-interaction-detail-v0.1-2026-06-16.md`
- `docs/设计文档/prototype-page-wireframes-v0.1-2026-06-16.md`

**v2.x 文档已全部被 v3.0 取代**（2026-06-20 修订）：PRD v2.2、FDS v2.0、ADS v2.0、DDS v2.0、IDS v2.0、UI/UX v3.0 不再作为有效输入；如需追溯历史版本，请使用 git 历史。

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
