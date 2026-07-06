# 设计文档去重与阅读顺序

日期：2026-07-06（对齐 v6.0 基线）
范围：项目目录下所有设计、需求、调研和评估文档
历史版本：2026-06-16（已废弃，被本版取代）

## 1. 结论

当前文档体系采用 **v6.0 基线**，所有 v2.x 及更早版本已废弃。有效文档统一存放于 `docs/设计文档/`，历史文档存放于 `docs/归档文档/`，过程记录存放于 `docs/过程文档/`。

```text
.
├── README.md                                    # 项目入口与当前共识
├── CLAUDE.md                                    # Agent 指引与基线
├── DESIGN.md                                    # 原型设计基线（v3.0，对齐 v6.0）
├── docs/
│   ├── 设计文档/
│   │   ├── 01-PRD/PRD.md                        # v6.0 唯一事实来源
│   │   ├── 02-FDS/FDS.md                        # v6.0
│   │   ├── 03-ADS/ADS.md                        # v6.0
│   │   ├── 04-DDS/DDS.md                        # v6.0
│   │   ├── 05-IDS/IDS.md                        # v6.0
│   │   ├── 06-UIUX/ui-ux-design-guidelines.md   # v6.0 唯一 UI/UX 输入
│   │   └── prototype/                           # 原型代码库
│   ├── 过程文档/                                 # 过程记录（历史追溯）
│   ├── 归档文档/                                 # 历史失效版本
│   └── 预研文档/                                 # 竞品/市场/标准预研
└── diagrams/                                    # 架构图（部分含旧 P0 模型，待重绘）
```

真正需要作为后续产品设计、研发和投标响应依据的有效文档有 8 份：

| 顺序 | 文档 | 定位 | 版本 |
|---:|---|---|---|
| 1 | `docs/设计文档/01-PRD/PRD.md` | **唯一事实来源**，产品需求规范 | v6.0 |
| 2 | `docs/设计文档/02-FDS/FDS.md` | 功能设计规范，含 RBAC 矩阵与 UAT | v6.0 |
| 3 | `docs/设计文档/03-ADS/ADS.md` | 应用架构与服务设计 | v6.0 |
| 4 | `docs/设计文档/04-DDS/DDS.md` | 数据模型设计（13 PG 表 + TDengine 超表） | v6.0 |
| 5 | `docs/设计文档/05-IDS/IDS.md` | API 接口设计（30+ API 跨 6 模块） | v6.0 |
| 6 | `docs/设计文档/06-UIUX/ui-ux-design-guidelines.md` | **唯一 UI/UX 输入**，25 页面设计规范 | v6.0 |
| 7 | `DESIGN.md` | 原型设计基线（视觉/布局/组件/验收横切约束） | v3.0 |
| 8 | `README.md` | 项目入口与当前共识 | — |

研究与外部依据：

| 文档 | 定位 |
|---|---|
| `docs/预研文档/control-loop-pid-demand-research-2026-06-15.md` | 标准、政策、市场和技术背景 |
| `docs/预研文档/competitor-comparison-2026-06-15.md` | 竞品能力、差异化和定位判断 |

历史评审结论（仅追溯，不再作为现行输入）：

| 文档 | 定位 |
|---|---|
| `/Users/zhangping/.gstack/projects/CLPM/zhangping-unknown-design-20260616-072247.md` | approved 产品化架构基线（历史） |
| `/Users/zhangping/.gstack/projects/CLPM/ceo-plans/2026-06-16-clpm-productization-validation.md` | CEO 评审结论（历史） |
| `/Users/zhangping/.gstack/projects/CLPM/zhangping-unknown-eng-review-20260616-093500.md` | 工程评审结论（历史） |
| `/Users/zhangping/.gstack/projects/CLPM/zhangping-unknown-design-review-20260616-100500.md` | 设计评审结论（历史） |

## 2. 推荐阅读顺序

### 2.1 管理层 / 商务

1. `docs/预研文档/control-loop-pid-demand-research-2026-06-15.md`
2. `docs/预研文档/competitor-comparison-2026-06-15.md`
3. `docs/设计文档/01-PRD/PRD.md` v6.0
4. `README.md`（当前共识）

关注：为什么做、与标准和招标关系、差异化、当前首版范围。

### 2.2 产品经理 / 方案人员

1. `docs/设计文档/01-PRD/PRD.md` v6.0
2. `docs/设计文档/06-UIUX/ui-ux-design-guidelines.md` v6.0
3. `docs/设计文档/02-FDS/FDS.md` v6.0
4. `docs/预研文档/competitor-comparison-2026-06-15.md`

关注：6 模块+门户结构、AAS Tag 模型、产品化自助配置、Phase 1 范围、25 页面清单。

### 2.3 算法 / 控制工程

1. `docs/设计文档/01-PRD/PRD.md` v6.0
2. `docs/设计文档/03-ADS/ADS.md` v6.0
3. `docs/设计文档/04-DDS/DDS.md` v6.0
4. `docs/预研文档/control-loop-pid-demand-research-2026-06-15.md`

关注：性能指标配置、诊断指标配置、引擎规则、PV 质量码处理、整定算法（Phase 2）。

### 2.4 研发 / 架构

1. `docs/设计文档/01-PRD/PRD.md` v6.0
2. `docs/设计文档/03-ADS/ADS.md` v6.0
3. `docs/设计文档/04-DDS/DDS.md` v6.0
4. `docs/设计文档/05-IDS/IDS.md` v6.0
5. `docs/设计文档/02-FDS/FDS.md` v6.0
6. `docs/设计文档/06-UIUX/ui-ux-design-guidelines.md` v6.0
7. `DESIGN.md` v3.0

关注：服务架构、数据模型、API 契约、RBAC 矩阵、原型技术栈（React 19 + Vite + TS）。

### 2.5 前端 / 原型研发

1. `docs/设计文档/06-UIUX/ui-ux-design-guidelines.md` v6.0
2. `DESIGN.md` v3.0
3. `docs/设计文档/05-IDS/IDS.md` v6.0（mock data 契约）
4. `docs/设计文档/01-PRD/PRD.md` v6.0（业务语义）

关注：25 页面路由、组件规范、状态机、PV 质量码渲染、Tag 关联选择器、配置确认对话框。

### 2.6 实施 / 交付

1. `docs/设计文档/01-PRD/PRD.md` v6.0
2. `docs/设计文档/02-FDS/FDS.md` v6.0（UAT 清单 TC-01 至 TC-20）
3. `docs/设计文档/04-DDS/DDS.md` v6.0（数据模型）
4. `docs/预研文档/control-loop-pid-demand-research-2026-06-15.md`

关注：工厂层级配置、回路台账、Tag 关联、PV 数据质量、KPI 状态派生、异常跟踪闭环。

## 3. 文档分层

### 3.1 当前有效主文档（v6.0 基线）

| 文档 | 用途 | 是否必读 |
|---|---|---|
| `docs/设计文档/01-PRD/PRD.md` v6.0 | **唯一事实来源**，产品需求规范 | 是 |
| `docs/设计文档/02-FDS/FDS.md` v6.0 | 功能设计规范，含 RBAC 矩阵与 UAT | 是 |
| `docs/设计文档/03-ADS/ADS.md` v6.0 | 应用架构与服务设计 | 是 |
| `docs/设计文档/04-DDS/DDS.md` v6.0 | 数据模型设计 | 是 |
| `docs/设计文档/05-IDS/IDS.md` v6.0 | API 接口设计 | 是 |
| `docs/设计文档/06-UIUX/ui-ux-design-guidelines.md` v6.0 | **唯一 UI/UX 输入**，25 页面设计规范 | 是 |
| `DESIGN.md` v3.0 | 原型设计基线（横切约束） | 前端必读 |
| `README.md` | 项目入口与当前共识 | 是 |

说明：

- v6.0 已吸收所有 v2.x 内容、approved 产品化架构、CEO/工程/设计三轮评审结论。
- 后续所有功能清单、原型设计、研发拆解、投标响应统一以 v6.0 为准。
- v2.x 及更早版本已退化为历史基线，不再作为现行需求输入。

### 3.2 研究与外部依据

| 文档 | 用途 | 是否必读 |
|---|---|---|
| `docs/预研文档/control-loop-pid-demand-research-2026-06-15.md` | 标准、政策、市场和技术背景 | 新成员建议必读 |
| `docs/预研文档/competitor-comparison-2026-06-15.md` | 竞品能力、差异化和定位判断 | 产品/商务建议必读 |

### 3.3 历史评审结论（仅追溯）

| 文档 | 用途 | 常规是否阅读 |
|---|---|---|
| approved 产品化架构 | 历史产品基线、版本蓝图 | 否 |
| CEO 评审结论 | 历史收口约束 | 否 |
| 工程评审结论 | 历史收口约束 | 否 |
| 设计评审结论 | 历史收口约束 | 否 |

### 3.4 历史追溯（归档文档）

| 文档 | 用途 | 常规是否阅读 |
|---|---|---|
| `docs/归档文档/product-requirements-specification-2026-06-15.md` | v0.1 历史基线 | 否 |
| `docs/归档文档/product-requirements-specification-v0.2-2026-06-16.md` | v0.2 标准增强版 | 否 |
| `docs/归档文档/product-requirements-specification-v0.3-2026-06-16.md` | v0.3 历史基线 | 否 |
| `docs/归档文档/product-requirements-specification-v0.4-2026-06-16.md` | v0.4 历史基线（已被 PRD v6.0 取代） | 否 |
| `docs/归档文档/prs-adjustment-assessment-2026-06-15.md` | 历史过程评估 | 否 |
| `docs/归档文档/tender-requirements-gap-assessment-2026-06-16.md` | 历史过程评估 | 按需 |
| `docs/归档文档/full-product-menu-ia-v0.1-2026-06-16.md` | 历史菜单定稿（已并入 UI/UX v6.0） | 否 |
| `docs/归档文档/p0-contract-backbone-design-v0.1-2026-06-16.md` | 历史 P0 契约（已被 Phase 1/Action Tracker 模型取代） | 否 |
| `docs/归档文档/prototype-design-spec.md` | 历史原型规格（被 DESIGN.md 取代） | 否 |
| `docs/归档文档/prototype-component-inventory-v0.1-2026-06-16.md` | 历史组件清单 | 否 |
| `docs/归档文档/project-assessment-report.md` | 项目现状评估 | 否 |
| `docs/归档文档/full-prototype-planning.md` | 历史原型规划 | 否 |
| `docs/归档文档/prototype-visual-tokens-v0.1-2026-06-16.md` | 历史视觉 token（被 UI/UX v6.0 §3 覆盖） | 否 |
| `docs/归档文档/prototype-responsive-accessibility-v0.1-2026-06-16.md` | 历史响应式（被 UI/UX v6.0 §2 覆盖） | 否 |
| `docs/归档文档/clpm-p0-prototype-spec-package-2026-06-16.md` | 历史 P0 原型规格包 | 否 |
| `docs/归档文档/orbstack-container-deployment-v1-2026-06-16.md` | 历史容器部署 | 按需 |

## 4. 当前最高优先级决策

| 决策 | 当前口径 |
|---|---|
| 产品定位 | 产品化、工具化的控制回路绩效治理与优化闭环平台，非项目型定制化系统 |
| 模块架构 | 6 模块 + 1 门户：工作台/回路管理/性能评估/诊断中心/回路整定/系统管理（任务管理为性能评估子模块）；各模块"配置→运行→分析"三态自包含 |
| AAS 数据模型 | AAS 同步 tag 位号（非回路实体）；回路由用户创建并关联 7 个 OPC tag（PV/SP/OP/MODE/PID_P/PID_I/PID_D）；PID 只读；数据质量主要针对 PV 值 |
| 首版主线 | Phase 1 (MVP/V1.0)：跑通"自动评估、自动诊断、轻量跟踪"闭环 |
| Action Tracker | 诊断中心子模块，状态机 PENDING → IN_PROGRESS → IMPLEMENTED/IGNORED |
| 统计分析 | 分散到各业务模块"分析"态；自动报表归入系统管理 |
| 回路整定 | Phase 2 落地，Phase 1 仅完成原型页面设计 |
| 工程主约束 | PRD v6.0 为唯一事实来源；UI/UX v6.0 为唯一 UI/UX 输入；实现契约 v2.0 为重构后 IA/路由/API/权限/状态机/KPI 基线 |
| 性能边界 | LTTB 降采样 maxPoints=2000，30 天时间窗口 |
| 安全边界 | 平台不写 DCS，只输出建议、证据、风险与回退方案 |

## 5. 下一阶段评审引用规则

| 下一阶段 | 必读顺序 | 使用方式 | 不要引用 |
|---|---|---|---|
| `/plan-eng-review` | PRD v6.0 → ADS v6.0 → DDS v6.0 → IDS v6.0 → FDS v6.0 | 先定产品主线、架构、数据模型，再拆工程模块 | 不要把 v2.x 或 PRS v0.4 当现行需求 |
| `/plan-design-review` | PRD v6.0 → UI/UX v6.0 → FDS v6.0 | 审查 6 模块+门户、25 页面、AAS Tag UI、配置确认对话框 | 不要把 generic dashboard 或旧 P0 双入口工作台当默认设计 |
| `/plan-ceo-review` | PRD v6.0 → README.md | 审查产品定位、Phase 范围、模块架构 | 不要回滚到 P0 治理闭环验证包口径 |
| PRD 后续修订 | PRD v6.0 → FDS/ADS/DDS/IDS v6.0 → UI/UX v6.0 | 在 v6.0 基础上增量修订，保持派生关系 | 不要回滚到 v2.x 口径 |
| 原型开发 | UI/UX v6.0 → `prototype/src/routes/menuConfig.ts` → DESIGN.md v3.0 | 只做 Phase 1 范围内页面 | 不要承诺真实 AAS 接入、真实整定执行 |

## 6. Stale docs 防护

以下文档只做历史追溯，**不再作为现行需求输入**：

### 6.1 已废弃的 v2.x 及更早版本

- `docs/归档文档/product-requirements-specification-*.md`（所有 PRS 历史版本，已被 PRD v6.0 取代）
- `docs/归档文档/full-product-menu-ia-v0.1-2026-06-16.md`（菜单已并入 UI/UX v6.0 §5）
- `docs/归档文档/p0-contract-backbone-design-v0.1-2026-06-16.md`（P0 模型已被 Phase 1/Action Tracker 模型取代）
- `docs/归档文档/prototype-design-spec.md`（被 DESIGN.md 取代）
- `docs/归档文档/prototype-component-inventory-v0.1-2026-06-16.md`
- `docs/归档文档/prototype-visual-tokens-v0.1-2026-06-16.md`（被 UI/UX v6.0 §3 覆盖）
- `docs/归档文档/prototype-responsive-accessibility-v0.1-2026-06-16.md`（被 UI/UX v6.0 §2 覆盖）
- `docs/归档文档/clpm-p0-prototype-spec-package-2026-06-16.md`

### 6.2 已废弃的过程文档

- `docs/过程文档/prototype-development-freeze-v0.1-2026-06-16.md`（任务书已被 UI/UX v6.0 25 页面清单取代）
- `docs/过程文档/prototype-remediation-plan-v0.1-2026-06-16.md`
- `docs/过程文档/clpm-p0-prototype-remediation-brief-2026-06-16.md`
- `docs/过程文档/superpowers/plans/` 与 `docs/过程文档/superpowers/specs/`（历史过程记录，含旧 P0/SampleBatch 模型）

### 6.3 架构图（已重绘）

`diagrams/` 目录下的 8 个 `.mmd` 架构图已于 2026-06-21 按 v3.0/v4.0 基线重绘，并于 2026-07-06 对齐 v6.0 基线，移除所有 P0/SampleBatch/EvidencePackage 旧模型引用。旧 `.excalidraw` 手绘文件和过时的 `.svg`/`.png` 已删除。

`.mmd` 是唯一来源。如需生成 `.svg`/`.png`，在本地运行：

```bash
cd diagrams
for f in *.mmd; do
  name="${f%.mmd}"
  mmdc -i "$f" -o "$name.svg" -b transparent
  mmdc -i "$f" -o "$name.png" -b white -w 1600
done
```

注意：`clpm-p0-closed-loop.mmd` 文件名保留历史命名（避免引用断裂），内容已改为"Phase 1 治理闭环"。

### 6.4 v2.x 设计文档（已被 v6.0 取代）

PRD v2.2、FDS v2.0、ADS v2.0、DDS v2.0、IDS v2.0、UI/UX v3.0 不再作为有效输入；如需追溯历史版本，请使用 git 历史。

## 7. 变更记录

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-06-16 | v1.0 | 初版索引，定义 PRS v0.4 为有效主文档，P0 契约主干为核心 |
| 2026-06-21 | v2.0 | 对齐 v3.0/v4.0 基线：PRD/FDS/ADS/DDS/IDS v3.0 + UI/UX v4.0 为唯一有效文档体系；PRS v0.4、P0 契约、菜单定稿、原型冻结任务书等全部降级为历史追溯；新增 6 模块+门户、AAS Tag 模型、Phase 1 范围等决策；阅读顺序与评审引用规则全面更新 |
| 2026-06-21 | v2.1 | 架构图已重绘：8 个 .mmd 全部对齐 v3.0/v4.0，.excalidraw 已删除，.svg/.png 已重新生成 |
| 2026-07-06 | v3.0 | 对齐 v6.0 基线：PRD/FDS/ADS/DDS/IDS/UIUX 全部升级到 v6.0；DESIGN.md 升级到 v3.0（对齐实现契约 v2.0）；实现契约升级到 v2.0；模块架构明确为 6+1（任务管理为性能评估子模块）；Action Tracker 状态机统一为 PENDING/IN_PROGRESS/IMPLEMENTED/IGNORED；测试用例数更新为 1762；阅读顺序与评审引用规则全面更新 |
