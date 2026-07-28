# CLPM 项目交接提示词（vibcoding 智能体用）

> 生成日期：2026-07-28 ｜ 基线提交：`a738147c` ｜ 文档版本：v1.0
>
> 本文件是 vibcoding 智能体进入 CLPM 项目的**首读上下文**。读完此文件后，你应能独立完成系统性检查、代码优化和后续开发任务。如需更深层信息，按 §10 文档索引按需查阅。

---

## 1. 项目背景概述

**CLPM**（Control Loop Performance Monitoring & Optimization）是面向危化企业的**控制回路绩效治理与优化闭环平台**。

**核心价值**：覆盖"监控 → 评估 → 诊断 → 整定"全流程，帮助工程师自助完成回路配置、性能评估、异常诊断和参数整定，减少开发团队介入。

**安全边界**：平台**只读 DCS**，不直接写入 DCS 参数。只输出建议、证据、风险和回退方案，参数由授权人员人工实施并留痕。

**产品定位**：产品化、工具化的平台，**非项目型定制化系统**。用户（管理员/工程师）可自助完成配置组态。

**当前阶段**：Phase 1 (MVP/V1.0) 已基本完成，跑通"自动评估、自动诊断、轻量跟踪"闭环。7 阶段系统重构已全部完成，文档体系统一升级至 v6.1。

---

## 2. 当前开发状态

### 2.1 里程碑

| 里程碑 | 状态 | 日期 |
|---|---|---|
| v4.0 七阶段系统重构 | ✅ Phase 0-6 全部完成 | 2026-06-26 |
| v6.0 文档统一升级 | ✅ PRD/FDS/ADS/DDS/IDS/UIUX 全量升级 | 2026-07-05 |
| 诊断中心 Batch 4-6 | ✅ F1-F7 回路分析+路径修复、D1-D6 管理闭环+入口整合 | 2026-07-27 |
| 诊断中心 Batch 5 | ✅ F8-F13 页面优化 + P0-P2 专项治理 | 2026-07-28 |
| E2E/单测全量修复 | ✅ 55 E2E + 371 单测全通过 | 2026-07-28 |
| E 规范符合性验证 | ⏳ 待启动（GB/T 44693.2 用例验证 ≥90%） | — |

### 2.2 全量测试基线（2026-07-28）

| 测试套件 | 结果 | 命令 |
|---|---|---|
| 后端 pytest | ✅ 2559 passed, 1 skipped, 8 deselected | `cd backend && uv run pytest -q` |
| 前端类型检查 | ✅ 2/2 packages 通过 | `cd frontend && pnpm run check:type` |
| 前端单元测试 | ✅ 371 passed | `cd frontend && pnpm exec vitest run` |
| E2E 端到端 | ✅ 55 passed | `cd e2e && pnpm exec playwright test` |

**三者全绿是提交门禁**（gitea 侧无 CI，本地检查即门禁）。

### 2.3 最近提交

```
a738147c docs: 同步 Batch 5 治理完成状态 + 全量测试基线
8fc3a2d1 fix: Batch 5 专项治理 + E2E/单测既有失败修复（17文件 +1002/-356）
10d96f02 feat(diagnosis): Batch 5 页面优化 F8-F13 — UX 提升 + 架构整洁
245514ad docs: 实现契约 v2.0→v2.1 + AGENTS.md 同步诊断中心 Batch 4-6 交付
```

---

## 3. 已实现功能模块

6 模块 + 1 门户，各模块遵循"**配置 → 运行 → 分析**"三态自包含原则，减少跨模块依赖。

| 模块 | 前端路由前缀 | 核心功能 |
|---|---|---|
| **工作台门户** | `/dashboard` | 12 项 KPI 看板（3 核心+1 综合+8 辅助）+ 低效回路 Top10 + 趋势摘要 + 待办异常 |
| **回路管理** | `/loop` | AAS Tag 同步 / 回路台账 / Tag 关联 / 实时监控 / 链路配置（网络模式切换） |
| **性能评估** | `/metric` | KPI 看板（PID 驾驶舱样式）/ 低效排行 / 统计分析 / 指标配置（定义/引擎规则/类型权重/级别权重/执行记录）/ 可信度标识 / 评估任务全生命周期 / 历史重算 |
| **诊断中心** | `/diagnosis` | 诊断配置（阈值/启停真实生效）/ 异常诊断（振荡/阀门粘滞/参数过激过保守/外扰/质量异常/输出饱和+传感器故障与 Harris 指数，D-S 证据融合）/ 事件+体检双轨自动诊断 / Action Tracker（KPI A/B 对比+PDF 建议书）/ 统计 / 异常跟踪 / 诊断记录 |
| **回路整定** | `/tuning` | FOPDT/SOPDT/IPDT 模型辨识 + IMC/Lambda/Z-N/Cohen-Coon/SIMC 五种整定算法 + 闭环仿真（Phase 1 只输出建议，不写 DCS） |
| **系统管理** | `/system` | 用户管理 / 审计日志 / 权限矩阵 / 自动报表 |

### 权限模型

5 个固定角色，RBAC + 细粒度权限码（`模块:操作`格式，支持通配）：

| 角色 | 权限范围 |
|---|---|
| ADMIN | 全部模块 |
| IC_ENGINEER | 全部业务模块 |
| PE_ENGINEER | 监控/评估/工作台 |
| EXPERT | 诊断/整定 |
| SPONSOR | 工作台只读 |

> 默认账号：admin / admin123（5 个种子用户密码统一为 admin123）

---

## 4. 技术栈详情

| 层 | 技术 | 版本/说明 |
|---|---|---|
| **前端** | Vue 3 + TypeScript + Vite + vue-vben-admin 5.7.0 + Ant Design Vue + ECharts | 端口 5666 |
| **后端** | Python 3.12 + FastAPI + SQLAlchemy 2.0 (async) + Pydantic v2 + uv 包管理 | 端口 7101 |
| **异步任务** | Celery + Celery Beat | KPI 计算 / 诊断引擎 / 报表生成 / AAS 同步 |
| **关系数据库** | PostgreSQL 16 | 业务数据 |
| **时序数据库** | TDengine 3.3.6 | 计算类历史数据唯一来源 |
| **缓存/队列** | Redis 7 | 实时缓存 + Celery Broker + Token 黑名单 |
| **鉴权** | JWT 双 Token（Access 30min / Refresh 7d）+ RBAC + bcrypt + Redis 黑名单 | 5 失败锁定 15 分钟 |
| **算法** | NumPy + SciPy | 模型辨识 / PID 整定 / 闭环仿真 RK4 / ARMA 辨识 |
| **部署** | Docker + Docker Compose + Nginx 反向代理 | 生产端口 7141 |
| **测试** | pytest + vitest + Playwright E2E | 三者全绿为门禁 |

### v4.0 核心架构组件

| 组件 | 路径 | 职责 |
|---|---|---|
| DataPlanner | `app/services/data_planner.py` | 统一历史数据读取，按控制类型自动降采样，分发 MetricDataBundle，L1/L2 缓存 |
| ConfidenceEvaluator | `app/services/confidence_evaluator.py` | 可信度评估 A/B/C/D/E（valid_rate 阈值 95/80/60/20%），INCONCLUSIVE 处理 |
| TaskTracker | `app/services/task_tracker.py` | 任务全生命周期跟踪，Redis 状态存储 + 通知 |
| 预处理 Pipeline | `app/services/preprocessing/` | 8 步流水线 + 8 类异常值检测 |
| MetricCalculator | `app/tasks/kpi_calc.py` | 12 个 KPI 指标计算器（3 核心 + 1 综合 + 8 辅助） |
| 数据完整性检查 | `app/services/data_integrity.py` | 按小时分桶对 7 列分别 COUNT(col) 统计列级缺失 |

### 前端公共工具（本次治理新增/沉淀）

| 工具 | 路径 | 用途 |
|---|---|---|
| runWithConcurrency | `utils/concurrency.ts` | 批量请求并发控制（默认 8），allSettled 语义，避免连接池压力 |
| formatTime / formatLocalTime | `utils/format.ts` | 统一时间格式化（北京时区 / "补 Z 转本地"约定），禁止各视图重复实现 |
| v-permission 指令 | `directives/permission.ts` | 同时支持角色名（精确匹配）+ 权限码（通配匹配） |

---

## 5. 架构设计说明

### 5.1 数据架构（核心决策）

**导入走远端、计算全本地**（2026-07-20 定调）：

| 数据 | 来源 | 规则 |
|---|---|---|
| 历史数据（计算用） | **本地 TDengine** | 所有计算任务唯一来源；数据不完整按 INCONCLUSIVE 提示，**禁止自动降级到远端** |
| 历史数据（采集用） | 远端 AAS 接口 | 仅"数据管理→历史数据导入"手工任务可调用 |
| 实时数据 | SignalR Hub（唯一） | 写入 Redis 实时缓存 + 可选写回 TDengine |

`get_provider()` 恒返回 TDengineProvider，远端历史接口仅 `data_import.py` 调用。

### 5.2 模块架构

各业务模块遵循"配置 → 运行 → 分析"三态自包含原则：
- **配置态**：用户自助完成配置组态（指标定义、诊断阈值、Tag 关联）
- **运行态**：自动执行（KPI 整点计算、双轨诊断、实时监控）
- **分析态**：结果展示与交互（看板、排行、统计、A/B 对比）

### 5.3 诊断双轨调度

| 轨道 | 触发时间 | 用途 |
|---|---|---|
| 事件轨 | 整点 10 分 | 快速异常检测（振荡/饱和/质量异常） |
| 体检轨 | 0/8/16 点 20 分 | 深度诊断（全量指标 + D-S 证据融合） |

### 5.4 KPI 计算体系

- **3 核心指标**：准确度（Accuracy）/ 快速性（Fastness）/ 稳定性（Stability）
- **1 综合评分**：P = (A·a + F·f + S·s)/(a+f+s) × R（R 为有效自控率折扣因子，非加权）
- **8 辅助指标**：自控率/合格率/振荡率/IAE/IAE 均值/稳定时间/设定值响应/输出饱和率
- **5 级评级**：EXCELLENT / GOOD / FAIR / WARNING / POOR
- **装置级聚合**：按回路重要等级加权平均（1 级:3，2 级:2，3 级:1）
- **复杂回路**：MAIN/SUB 分组去重，组内取代表（MAIN 优先）

### 5.5 Action Tracker 状态机

```
PENDING → IN_PROGRESS → IMPLEMENTED
                     └→ IGNORED
```

- IMPLEMENTED 状态必须提供 MOC 变更管理关联（moc_ref 或 moc_not_applicable + moc_reason）
- 已实施后自动弹出 A/B 对比 Drawer
- 整改效果自动验证（改善指标数 > 恶化指标数 → effect_verified=True）

---

## 6. 待解决问题清单

| # | 问题 | 优先级 | 状态 | 说明 |
|---|---|---|---|---|
| 1 | E 规范符合性验证 | 高 | ⏳ 待启动 | GB/T 44693.2 用例验证 ≥90%，诊断整改 Phase E |
| 2 | Celery worker 容器 include 参数 | 中 | ⏳ 待验证 | 生产部署时需验证 worker 容器 include 参数生效 |
| 3 | 公网模式 ping 延迟抖动 | 低 | ⏳ 待优化 | Tailscale 公网模式延迟抖动，仅余此一项 |
| 4 | 后端细粒度权限码校验 | 中 | ⏳ 待统一 | 前端已接入 v-permission，后端部分 API 缺角色检查（实现契约 §5 标注"待统一"） |
| 5 | 前端权限矩阵完整实现 | 中 | ⏳ 待完善 | 权限矩阵未完全实现，部分 API 可能缺角色检查 |

---

## 7. 后续优化方向

| 方向 | 先读 | 关注点 |
|---|---|---|
| Bug 修复 / 功能增强 | README → AGENTS → 设计文档 → 代码 | "问题定位→修复实施→测试验证→效果确认"闭环 |
| 诊断整改 Phase E | `diagnosis-module-review-rectification-plan-2026-07-19.md` §5 | GB/T 44693.2 用例验证 ≥90% |
| E2E 测试补充 | `e2e/` → UI/UX v6.1 → 新增页面 | 现有 55 全通过，后续按 UI/UX v6.1 新页面逐步补 |
| 生产部署 | `docker-compose.prod.yml` → `.env.prod.example` → `deploy/deploy.sh` | Celery worker 容器需验证 include 参数 |
| 新功能开发 | PRD v6.1 → 实现契约 v2.1 → v4.0 实施方案 → 设计文档 | 模块"配置→运行→分析"三态自包含原则 |
| 回路整定 Phase 2 | PRD §回路整定 | 生产级算法闭环（Phase 1 只输出建议） |

---

## 8. 开发规范与代码风格

### 8.1 Git 工作流

- **远端**：`origin` = gitea（主），`github` = GitHub（镜像）；main 跟踪 `origin/main`
- **提交**：Conventional Commits `<type>(<scope>): <subject>`，subject ≤50 字符祈使句，body 解释"为什么"
- **日常开发**：可直接在 main 上小步提交并 `git push origin main`；大改动（>500 行或 DB schema 变更）开 `<type>/<简述>` 分支
- **红线**：禁止 `git push --force` 共享分支；禁止 `git reset --hard` 后推送共享分支

### 8.2 行为红线（始终遵守）

| # | 红线 | 原因 |
|---|---|---|
| 1 | Celery Worker 和 Beat 随后端自动启动，**严禁手工再启动** | 多个 worker/beat 并存导致任务重复消费或双触发 |
| 2 | 修改 Celery 任务代码后**需重启后端** | `uvicorn --reload` 只重载 Python 文件，不重启 Worker/Beat 子进程 |
| 3 | 计算类历史数据查询**一律本地 TDengine** | 禁止计算任务自动降级到远端 API |
| 4 | 模型变更**必须与迁移同批应用** | ORM 改动与 alembic 迁移同批提交，先迁移再运行 |
| 5 | 热路径**禁止对 naive datetime 逐点调 `.timestamp()`** | macOS fork 时区慢路径陷阱 |
| 6 | 断点续传**禁止 overwrite** | gap backfill 必须 `conflict_strategy="skip"` |
| 7 | 前端端口是 **5666**，后端 API 为 **7101** | — |
| 8 | 默认账号 admin / admin123 | 5 个种子用户详见 README |

### 8.3 CI 提交前本地检查（必跑）

```bash
# 后端 ruff
cd backend && uv run ruff check . && uv run ruff format --check .

# 前端格式化 + 类型检查
cd frontend && pnpm run format && pnpm run check:type

# 全量测试
cd backend && uv run pytest -q
cd frontend && pnpm exec vitest run
cd e2e && pnpm exec playwright test
```

### 8.4 代码风格约定

- **后端**：Python 3.12，async 优先，ruff 管理 lint + format
- **前端**：TypeScript 严格模式，Vue 3 `<script setup>`，oxlint 管理 lint
- **组件**：使用 CLPM 统一组件（ClpmPageToolbar / ClpmDataCanvas / ClpmKpiStrip / ClpmToolbarButton）
- **时间格式化**：统一使用 `utils/format.ts` 的 `formatTime` / `formatLocalTime`，禁止各视图重复实现
- **批量请求**：使用 `utils/concurrency.ts` 的 `runWithConcurrency` 控制并发
- **权限控制**：`v-permission` 指令同时支持角色名和权限码；后端用 `require_roles()` 装饰器
- **TODO/FIXME**：当前代码库零 TODO/FIXME，保持此标准

---

## 9. 测试策略

### 9.1 三层测试体系

| 层 | 工具 | 范围 | 命令 |
|---|---|---|---|
| 后端单元测试 | pytest | API / 服务 / 算法 / 模型 | `cd backend && uv run pytest -q` |
| 前端单元测试 | vitest | 组件逻辑 / 指令 / 工具函数 | `cd frontend && pnpm exec vitest run` |
| E2E 端到端 | Playwright | 用户操作流程 / 页面交互 | `cd e2e && pnpm exec playwright test` |

### 9.2 测试规范

- **后端**：每个服务/算法有对应 `test_*.py`；算法测试覆盖 7 种场景（fast_response/slow_response/oscillation/op_saturation/normal/manual_mode/pure_ar2）
- **前端**：`<script setup>` 组件需 `defineExpose` 暴露测试接口；vitest mock `@vben/icons` 时必须导出 `IconifyIcon`
- **E2E**：路由必须与实际代码对齐（772d99a0 重构后路由）；confidence 测试区分 BADGE 缩写和 Tag 全称两种渲染

### 9.3 测试数据

- 开发环境提供 `mock_data_server`（端口 7106），完全模拟工程数据链路
- 后端测试数据含 7 种场景 × 2 小时 1Hz 数据
- E2E 依赖前后端实际运行 + 数据库种子数据

---

## 10. 项目文档位置

### 10.1 必读入口（按顺序）

| 序号 | 文件 | 用途 |
|---|---|---|
| 1 | `README.md` | 项目全貌 + 快速开始 + 目录说明 |
| 2 | `AGENTS.md` | AI 协作指南 + 红线 + 当前基线 + 下阶段规则 |
| 3 | `docs/设计文档/00-BASELINE/implementation-contract.md` | 重构后 IA/路由/API/权限/状态机/KPI 事实来源（v2.1） |
| 4 | `docs/设计文档/01-PRD/PRD.md` | 产品需求事实来源（v6.1） |
| 5 | `docs/设计文档/06-UIUX/ui-ux-design-guidelines.md` | 视觉与交互规范（v6.1，含 ZL 工业设计规范） |

### 10.2 设计文档体系

| 类型 | 文件 | 版本 |
|---|---|---|
| 产品需求 PRD | `docs/设计文档/01-PRD/PRD.md` | v6.1 |
| 功能设计 FDS | `docs/设计文档/02-FDS/FDS.md` | v6.0 |
| 应用设计 ADS | `docs/设计文档/03-ADS/ADS.md` | v6.0 |
| 数据模型 DDS | `docs/设计文档/04-DDS/DDS.md` | v6.0 |
| API 接口 IDS | `docs/设计文档/05-IDS/IDS.md` | v6.0 |
| UI/UX 规范 | `docs/设计文档/06-UIUX/ui-ux-design-guidelines.md` | v6.1 |
| 实现契约 | `docs/设计文档/00-BASELINE/implementation-contract.md` | v2.1 |
| 重构实施方案 | `docs/设计文档/CLPM_v4.0_系统重构实施方案.md` | v1.0 |

### 10.3 过程文档（按需查阅）

| 场景 | 文档 |
|---|---|
| 排障与运维 | `docs/过程文档/ops-runbook.md` |
| v6 交付历史 | `docs/过程文档/v6-delivery-history.md` |
| 诊断整改计划 | `docs/过程文档/diagnosis-module-review-rectification-plan-2026-07-19.md` |
| 数据架构决策 | `docs/过程文档/data-architecture-decision-local-first-2026-07-20.md` |
| Batch 5 治理报告 | `docs/过程文档/batch5-governance-summary-report-2026-07-28.md` |
| E2E 修复计划 | `docs/过程文档/e2e-metric-failures-fix-plan-2026-07-28.md` |
| 引用旧文档前查失效 | `docs/过程文档/stale-docs.md` |

### 10.4 代码目录结构

```
CLPM/
├── backend/                    # FastAPI 后端
│   └── app/
│       ├── api/                # API 路由层
│       ├── core/               # 配置/安全/中间件
│       ├── models/             # SQLAlchemy ORM 模型
│       ├── schemas/            # Pydantic 响应模型
│       ├── services/           # 业务服务层（DataPlanner/ConfidenceEvaluator/...）
│       │   └── preprocessing/  # 8 步预处理流水线
│       ├── tasks/              # Celery 任务（kpi_calc/诊断引擎/...）
│       └── utils/              # 工具函数
├── frontend/                   # Vue 3 前端 monorepo
│   └── apps/web-antd/src/      # 生产应用
│       ├── views/              # 页面（dashboard/loop/metric/diagnosis/tuning/system/task）
│       ├── api/                # API 调用层
│       ├── components/clpm/    # CLPM 统一组件
│       ├── composables/        # 组合式函数（主题/状态）
│       ├── directives/         # 自定义指令（v-permission）
│       └── utils/              # 工具函数（concurrency/format/...）
├── e2e/                        # Playwright E2E 测试
├── docs/                       # 全部文档
├── deploy/                     # 部署脚本 + Nginx + Docker Compose
├── db/                         # 数据库 DDL
└── mock_data_server/           # 模拟远端数据服务（可删除）
```

---

## 快速启动

```bash
# 1. 基础设施
docker compose -f deploy/docker/docker-compose.dev.yml up -d

# 2. 后端（自动启动 Celery Worker + Beat）
cd backend && uv run uvicorn app.main:app --host 0.0.0.0 --port 7101 --reload

# 3. 前端
cd frontend && pnpm run dev:antd

# 4. 验证
# 后端 API 文档：http://localhost:7101/docs
# 前端：http://localhost:5666
# 登录：admin / admin123
```

---

## 给智能体的操作建议

1. **先读 AGENTS.md**：它是最新的协作指南，包含红线和当前基线
2. **改代码前先跑测试**：确认基线全绿，改完再跑一次
3. **后端改了 Celery 任务代码→重启后端**（不是 reload）
4. **提交前必跑**：ruff check + format + pytest + check:type + vitest + E2E
5. **引用旧文档前查 `stale-docs.md`**：避免基于失效需求做决策
6. **遇到排障问题查 `ops-runbook.md`**：worker 挂死、网络切换、数据链路都有详细说明
