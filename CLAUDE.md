# CLPM Agent Guidance

本项目是危化企业控制回路性能评估与优化平台（CLPM v6.0），7 阶段系统重构已全部完成，文档体系已统一升级至 v6.0。

## KPI 优化进行中（2026-07-15）

当前 KPI 性能整改已进入执行阶段，最近已完成并提交：
- 会话生命周期修复
- CUSTOM 单 Celery ID / 旧数组兼容
- 预热结果真实统计与失败告警
- Layer2 完整依赖收紧
- 冻结检测 O(n) 等价实现
- 标准/自定义批处理共享编排器
- 热缓存 1h p95 0.6s 基准脚本
- 27 回路 replay 容量脚本

已知约束：
- 0.6 秒只作为同窗口 L2 热缓存完整端到端 SLO
- 冷缓存受外部历史数据 HTTP 0.7-0.9 秒基线限制，必须单独分阶段验收
- 1000 回路容量测试当前采用多窗口 replay，不应伪装成 1000 个不同回路

后续优先级：
1. 继续完善 1000 回路 replay 容量门禁与统计口径
2. 为热缓存单回路与冷缓存分阶段建立稳定基准
3. 视结果再决定是否引入更深的外部数据源/缓存架构改造

## 必读入口

先读：`README.md`（当前共识与目录说明）、`docs/设计文档/00-BASELINE/implementation-contract.md`、`docs/设计文档/CLPM_v4.0_系统重构实施方案.md` 与 `docs/设计文档/01-PRD/PRD.md` v6.0。

PRD v6.0 是产品需求的事实来源；实现契约 v2.0 是重构后 IA/路由/API/权限/状态机/KPI 事实来源；UI/UX v6.0 是视觉与交互输入文件（已对齐 v6.0 代码）；`CLPM_v4.0_系统重构实施方案.md` 是 7 阶段重构的实施蓝图。

## 当前基线（2026-07-06 修订 — v6.0 文档统一升级完成）

| 类型 | 文件 | 版本 |
|---|---|---|
| 产品需求规范 PRD | `docs/设计文档/01-PRD/PRD.md` | v6.0 |
| 重构后实现契约 | `docs/设计文档/00-BASELINE/implementation-contract.md` | v2.0 |
| **v4.0 重构实施方案** | `docs/设计文档/CLPM_v4.0_系统重构实施方案.md` | v1.0（Phase 0-6 全部完成） |
| 功能设计规范 FDS | `docs/设计文档/02-FDS/FDS.md` | v6.0 |
| 应用设计规范 ADS | `docs/设计文档/03-ADS/ADS.md` | v6.0 |
| 数据模型设计 DDS | `docs/设计文档/04-DDS/DDS.md` | v6.0 |
| API 接口设计 IDS | `docs/设计文档/05-IDS/IDS.md` | v6.0 |
| UI/UX 设计规范 | `docs/设计文档/06-UIUX/ui-ux-design-guidelines.md` | **v6.0**（已对齐 v6.0 代码） |
| **工业桌面端 UI/UX 改造基线** | `docs/设计文档/06-UIUX/CLPM_UIUX_工业桌面端改造方案_v1.0.md` | v1.0（2026-06-27，新 UI/UX 收口基线） |
| 设计基线 | `DESIGN.md` | v3.0（对齐实现契约 v2.0） |
| 原型代码入口 | `docs/设计文档/prototype/README.md` | 已重置为干净基线 |
| 文档索引 | `docs/过程文档/design-documents-index-2026-06-16.md` | v3.0（对齐 v6.0） |
| 已批准产品化架构 | `/Users/zhangping/.gstack/projects/CLPM/zhangping-unknown-design-20260616-072247.md` | 历史参考 |

## v6.0 文档统一升级状态（2026-07-06）

7 阶段重构全部交付，后端 1762 测试用例通过，文档体系已统一升级至 v6.0：

| 阶段 | 核心交付 | Commit |
|---|---|---|
| Phase 0 | ORM 模型层更新 | `02f3c5a` |
| Phase 1 | 数据预处理模块（8步Pipeline + 8类异常值检测 + 180 单元测试） | `bdde45b` |
| Phase 2+3 | DataPlanner+Cache 与 12 个 KPI 指标计算器（3+1+8 体系） | `11d13e6` |
| Phase 4 | kpi_calc.py 整合 DataPlanner + MetricCalculator | `53fc21f` |
| Phase 5 | API 接口层扩展（17 端点 + 任务跟踪/通知 + OpenAPI 文档） | `39859e5` `0dfd37b` |
| Phase 6 | 前端适配（4层架构：类型/API → 组件 → 页面 → 路由） | `86f356c` `3516641` `4bff65b` |
| 修复 | Celery worker 任务注册修复（include 参数替代 autodiscover_tasks） | `207c882` |
| v6.0 升级 | 文档统一升级：PRD/FDS/ADS/DDS/IDS/UIUX → v6.0；实现契约 v1.0 → v2.0；DESIGN v2.1 → v3.0；测试数 1762；TS 错误 0 | 见 `docs/过程文档/superpowers/plans/v6-consistency-check.md` |

## v6.0 核心架构组件

| 组件 | 路径 | 职责 |
|---|---|---|
| DataPlanner | `app/services/data_planner.py` | 统一历史数据读取，按控制类型自动降采样，分发 MetricDataBundle |
| ConfidenceEvaluator | `app/services/confidence_evaluator.py` | 可信度评估 A/B/C/D/E（valid_rate 阈值 95/80/60/20%），INCONCLUSIVE 处理 |
| TaskTracker | `app/services/task_tracker.py` | 任务全生命周期跟踪（create/update_status），Redis 状态存储 + 通知 |
| 预处理 Pipeline | `app/services/preprocessing/` (quality_code/thresholds/outlier_detection/validity_mask/quality_summary/pipeline) | 8 步流水线 + 8 类异常值检测 |
| MetricCalculator | `app/tasks/kpi_calc.py` | 12 个 KPI 指标计算器（3 核心 + 1 综合 + 8 辅助），通过 DataPlanner.request_bundles() 获取数据 |

## 开发环境运行指南

### 启动服务

```bash
# 1. 基础设施
docker compose -f deploy/docker/docker-compose.dev.yml up -d

# 2. 后端 API (port 7101)
cd backend && uv run uvicorn app.main:app --host 0.0.0.0 --port 7101 --reload

# 3. Celery Worker（独立进程，必须单独启动）
cd backend && .venv/bin/celery -A app.tasks.celery_app worker -l info -Q default

# 4. 前端 (port 7100)
cd frontend && pnpm run dev:antd
```

### 测试与验证

```bash
# 后端单元测试（1762 用例）
cd backend && uv run pytest -q

# 前端类型检查
cd frontend && pnpm run check:type

# E2E 测试
cd e2e && pnpm exec playwright test
```

### 关键注意事项

- **Celery worker 是独立进程**：与 FastAPI（`--reload`）分开启动，后端代码更新后需重启 worker
- **前端端口是 7100**（端口统一规划：项目所有端口统一到 7100-7200 段，配置 `frontend/apps/web-antd/.env.development` 中 `VITE_PORT=7100`）
- **前端 TypeScript 错误已全部修复**（v6.0 升级中清零，原 plant-node-tree.vue 3 个 + workbench.vue 3 个已修复）
- **默认账号**：admin / admin123（5 个种子用户详见 README.md）
- **Git 分支**：当前在 `main` 分支

## 核心决策

| 决策 | 当前口径 |
|---|---|
| 产品定位 | 产品化、工具化的控制回路绩效治理与优化闭环平台，非项目型定制化系统；用户（管理员/工程师）可自助完成配置组态，减少开发团队介入 |
| 模块架构 | 6 模块 + 1 门户：工作台 / 回路管理 / 性能评估 / 诊断中心 / 回路整定 / 系统管理；各业务模块遵循"配置→运行→分析"三态自包含原则，减少跨模块依赖 |
| AAS 数据模型 | AAS 同步 tag 位号（非回路实体）；回路由用户创建并关联 7 个 OPC tag（PV/SP/OP/MODE/PID_P/PID_I/PID_D）；PID 参数与控制模式从关联 tag 只读读取；数据质量主要针对 PV 值（Good/Bad/Uncertain 质量码） |
| Action Tracker | 诊断中心子模块（子菜单路由），状态机 PENDING → IN_PROGRESS → IMPLEMENTED/IGNORED，中文显示为待处理/处理中/已实施/已忽略 |
| 统计分析 | 不设独立模块，分散到各业务模块的"分析"态；自动报表归入系统管理 |
| 回路整定 | Phase 1 保留页面与实验/辅助接口，只输出建议、证据、风险和回退方案；不支持 DCS 参数下写，Phase 2 再完成生产级算法闭环 |
| 技术护城河 | 可信数据 + 可解释诊断 + 可验证整定 + 安全闭环 + 规模化交付 |
| 安全边界 | 平台不直接修改 DCS 的 P/I/D 参数，只输出建议、证据、风险和回退方案；参数由授权人员人工实施并留痕 |
| 首版主线 | Phase 1 (MVP/V1.0)：跑通"自动评估、自动诊断、轻量跟踪"闭环 |
| 原型/前端开发 | 当前生产前端为 Vue 3 + Vite + TypeScript + vue-vben-admin；重构后路由/页面以 `docs/设计文档/00-BASELINE/implementation-contract.md` 为准 |
| 性能边界 | LTTB 降采样 maxPoints=2000，30 天时间窗口 |
| 文档权威性 | PRD v6.0 负责产品需求；实现契约 v2.0 负责重构后 IA/路由/API/权限/状态机/KPI；UI/UX v6.0 负责视觉与交互（已对齐 v6.0 代码）；v4.0 重构实施方案负责 7 阶段实施蓝图 |

## 下阶段规则

v6.0 文档统一升级已完成，后续工作方向：

| 方向 | 先读 | 关注点 |
|---|---|---|
| Bug 修复 / 功能增强 | README.md → CLAUDE.md → 相关设计文档 → 对应代码 | 遵循"问题定位-修复实施-测试验证-效果确认"闭环流程 |
| 前端 lint/格式化整理 | 当前工作区有 50+ 未提交的前端格式化改动 | 可考虑统一 `pnpm run lint --fix` 后提交 |
| **工业桌面端 UI/UX 优化** | `docs/设计文档/06-UIUX/CLPM_UIUX_工业桌面端改造方案_v1.0.md` → `frontend/apps/web-antd/src/components/clpm/` → 各业务页面 | 已落地共享组件、工作台/回路/诊断/性能看板样板页与性能评估 IA 收口；后续继续按基线扩展到剩余页面 |
| E2E 测试补充 | `e2e/` 目录 → UI/UX v6.0 → v6.0 新增页面 | 任务管理页面、可信度徽章、INCONCLUSIVE 展示需补 E2E |
| 生产部署 | `docker-compose.prod.yml` → `.env.prod.example` → `deploy/deploy.sh` | Celery worker 容器需验证 include 参数生效 |
| v6.0 文档统一升级 | `docs/过程文档/superpowers/plans/v6-consistency-check.md` → v6.0 各设计文档 | 文档已统一升级至 v6.0，需持续保持文档与代码一致性 |
| 新功能开发 | PRD v6.0 → 实现契约 v2.0 → v4.0 重构实施方案 → 对应设计文档 | 遵循模块"配置→运行→分析"三态自包含原则 |

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

以下 v0.1 文件已于 2026-06-20 删除（概念体系冲突，有价值内容已吸收进 `06-UIUX/ui-ux-design-guidelines.md` v4.0）：
- `docs/设计文档/prototype-state-spec-v0.1-2026-06-16.md`
- `docs/设计文档/prototype-interaction-detail-v0.1-2026-06-16.md`
- `docs/设计文档/prototype-page-wireframes-v0.1-2026-06-16.md`

**v2.x 文档已全部被 v3.0 取代**（2026-06-20 修订）：PRD v2.2、FDS v2.0、ADS v2.0、DDS v2.0、IDS v2.0、UI/UX v3.0 不再作为有效输入；如需追溯历史版本，请使用 git 历史。

**v6.0 升级前的所有版本已全部被 v6.0 取代**（2026-07-06 修订）：PRD v3.1/v4.0、FDS v3.0/v5.1、ADS v3.0/v4.0、DDS v3.0/v4.1、IDS v3.0/v4.0、UI/UX v5.1/v5.3、实现契约 v1.0、DESIGN v2.1 不再作为有效输入；如需追溯历史版本，请使用 git 历史。

这些文件只用于历史追溯。
