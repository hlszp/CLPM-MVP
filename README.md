# CLPM

危化企业控制回路性能治理与优化平台（Control Loop Performance Monitoring & Optimization）。

版本：**v1.0.0**（正式版）

## 项目简介

CLPM 是面向危化企业控制回路的绩效治理与优化闭环平台，覆盖"监控 → 评估 → 诊断 → 整定"全流程，提供：

- **工作台门户**：6 大 KPI 看板 + 低效回路 Top10 + 趋势摘要 + 待办异常
- **回路管理**：AAS Tag 同步 / 回路台账 / Tag 关联 / 实时监控
- **性能评估**：指标配置 / KPI 计算引擎 / 全局看板 / 低效排行 / 统计报表
- **诊断中心**：诊断配置 / 异常诊断（FFT 振荡检测 + D-S 证据融合）/ Action Tracker / 统计
- **回路整定**：FOPDT/SOPDT/IPDT 模型辨识 + IMC/Lambda/Z-N/Cohen-Coon/SIMC 五种整定算法 + 闭环仿真
- **系统管理**：用户管理 / 审计日志 / 权限矩阵 / 自动报表

平台遵循"只读 DCS、只输出建议"的安全边界，不直接写入 DCS。

## 技术栈

| 层 | 技术 |
|---|---|
| 前端 | Vue 3 + TypeScript + Vite + vue-vben-admin 5.7.0 + Ant Design Vue + ECharts |
| 后端 | Python 3.12 + FastAPI + SQLAlchemy 2.0 (async) + Pydantic v2 + uv 包管理 |
| 异步任务 | Celery + Celery Beat（KPI 计算 / 诊断引擎 / 报表生成 / AAS 同步） |
| 关系数据库 | PostgreSQL 16 |
| 时序数据库 | TDengine 3.3.6 |
| 缓存/队列 | Redis 7 |
| 鉴权 | JWT 双 Token（Access 30min / Refresh 7d）+ RBAC 五角色 + bcrypt + Redis 黑名单 |
| 算法 | NumPy + SciPy（模型辨识 / PID 整定 / 闭环仿真 RK4） |
| 部署 | Docker + Docker Compose + Nginx 反向代理 |
| 测试 | pytest（292 用例）+ Playwright E2E（27 用例）|

## 快速开始（开发环境）

### 环境要求

- Node.js ≥ 22.18.0 + pnpm ≥ 10.0.0
- Python 3.12 + [uv](https://docs.astral.sh/uv/) ≥ 0.4
- Docker 24+（推荐 [Orbstack](https://orbstack.dev/) 作为容器运行时）

### 1. 启动基础设施（PostgreSQL + TDengine + Redis）

```bash
docker compose -f deploy/docker/docker-compose.dev.yml up -d
```

### 2. 启动后端

```bash
cd backend
cp .env.example .env          # 首次执行
uv sync                        # 安装依赖
uv run alembic upgrade head    # 执行数据库迁移
uv run uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload
```

后端 API 文档：http://localhost:8001/docs

### 3. 启动前端

```bash
cd frontend
pnpm install
pnpm run dev:antd              # 默认端口 5666
```

前端访问地址：http://localhost:5666

### 4. 默认账号

5 个种子用户，密码统一为 `admin123`：

| 用户名 | 角色 | 权限范围 |
|---|---|---|
| admin | ADMIN | 全部模块 |
| ic_engineer | IC_ENGINEER | 全部业务模块 |
| pe_engineer | PE_ENGINEER | 监控/评估/工作台 |
| expert | EXPERT | 诊断/整定 |
| sponsor | SPONSOR | 工作台只读 |

### 5. 运行测试

```bash
# 后端单元测试
cd backend && uv run pytest -q

# 前端类型检查
cd frontend && pnpm run check:type

# E2E 测试（需先启动前后端）
cd e2e && pnpm install && pnpm exec playwright install chromium
pnpm exec playwright test
```

## 生产部署

### 环境要求

- Docker 24+ 与 Docker Compose v2
- 服务器最低配置：4 核 CPU / 8GB 内存 / 50GB 磁盘
- 开放端口：80（前端）、8001（后端 API，建议仅内网）、6030/6041（TDengine，建议仅内网）

### 部署步骤

#### 1. 准备配置文件

```bash
cp .env.prod.example .env.prod
```

编辑 `.env.prod`，**必须修改**以下占位符：

| 配置项 | 说明 | 生成命令 |
|---|---|---|
| `POSTGRES_PASSWORD` | PostgreSQL 密码 | 自定义强密码 |
| `TDENGINE_PASSWORD` | TDengine 密码 | 自定义强密码 |
| `JWT_SECRET_KEY` | JWT 签名密钥（≥32 字符） | `openssl rand -hex 32` |
| `CORS_ORIGINS` | 允许的前端域名 | `["https://your-domain.com"]` |

#### 2. 一键部署

```bash
./deploy/deploy.sh
```

部署脚本会自动完成：
1. 校验 `.env.prod` 与 `JWT_SECRET_KEY`
2. 构建 backend / frontend Docker 镜像（多阶段构建）
3. 启动 6 个服务容器（backend / frontend / postgres / tdengine / redis / celery-worker / celery-beat）
4. 等待健康检查并通过
5. 输出服务访问地址

#### 3. 验证部署

```bash
# 查看服务状态
docker compose -f docker-compose.prod.yml ps

# 后端健康检查
curl http://localhost:8001/health

# 前端访问
curl http://localhost/
```

### 服务架构

| 服务 | 容器 | 端口 | 说明 |
|---|---|---|---|
| frontend | clpm-frontend | 80 | Nginx 静态托管 + /api/v1 反代 |
| backend | clpm-backend | 8001 | FastAPI + Uvicorn |
| celery-worker | clpm-celery-worker | - | 异步任务执行 |
| celery-beat | clpm-celery-beat | - | 定时任务调度 |
| postgres | clpm-postgres | 5432 | 关系型业务数据 |
| tdengine | clpm-tdengine | 6030/6041 | 时序数据 |
| redis | clpm-redis | 6379 | 缓存 + Celery Broker |

### 常用运维命令

```bash
# 查看实时日志
docker compose -f docker-compose.prod.yml logs -f

# 查看指定服务日志
docker compose -f docker-compose.prod.yml logs -f backend

# 重启服务
docker compose -f docker-compose.prod.yml restart backend

# 停止所有服务
docker compose -f docker-compose.prod.yml down

# 停止并清除数据卷（慎用，会丢失数据）
docker compose -f docker-compose.prod.yml down -v

# 重新构建并启动
docker compose -f docker-compose.prod.yml up -d --build
```

### 版本回滚

```bash
./deploy/rollback.sh
```

回滚脚本会列出历史镜像版本，确认后回滚到上一版本。

### HTTPS 配置

生产环境建议启用 HTTPS，编辑 `deploy/nginx.conf` 取消底部 HTTPS server 块的注释，配置 SSL 证书路径后重新构建 frontend 镜像：

```bash
docker compose -f docker-compose.prod.yml build frontend
docker compose -f docker-compose.prod.yml up -d frontend
```

### 数据库初始化

PostgreSQL 容器首次启动会自动执行 `db/postgresql/01_schema.sql` 和 `db/postgresql/02_seed_data.sql` 完成建表与种子数据导入。如需重新初始化，需先清除数据卷：

```bash
docker compose -f docker-compose.prod.yml down -v
docker compose -f docker-compose.prod.yml up -d
```

## 当前有效文档

| 类型 | 文件 |
|---|---|
| 当前 PRD | `docs/设计文档/01-PRD/PRD.md` |
| 总体 FDS | `docs/设计文档/02-FDS/FDS.md` |
| 交付架构设计 | `docs/设计文档/03-ADS/ADS.md` |
| 数据模型设计 | `docs/设计文档/04-DDS/DDS.md` |
| API 接口设计 | `docs/设计文档/05-IDS/IDS.md` |
| UI/UX 设计规范 | `docs/设计文档/06-UIUX/ui-ux-design-guidelines.md` |
| 原型设计基线 | `DESIGN.md`（v2.0，对齐 v3.0/v4.0） |
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
| 产品定位 | 产品化、工具化的控制回路绩效治理与优化闭环平台，非项目型定制化系统 |
| 首版主线 | Phase 1 (MVP/V1.0)：跑通"自动评估、自动诊断、轻量跟踪"闭环 |
| 首版范围 | 工作台门户、回路管理（AAS tag 同步/回路创建/tag 关联/监控）、性能评估（指标配置/引擎规则/看板/排行/统计）、诊断中心（指标配置/诊断/异常跟踪/统计）、系统管理；回路整定原型页面设计 |
| 模块架构 | 6 模块+门户：工作台/回路管理/性能评估/诊断中心/回路整定/系统管理，各模块"配置→运行→分析"三态自包含 |
| AAS 数据模型 | AAS 同步 tag 位号（非回路实体），回路由用户创建并关联 7 个 OPC tag（PV/SP/OP/MODE/PID_P/PID_I/PID_D），数据质量主要针对 PV 值 |
| 核心模型 | Action Tracker 轻量跟踪（PENDING → IN_PROGRESS → RESOLVED/IGNORED），诊断中心子模块 |
| 工程主约束 | PRD v3.0 为唯一事实来源，UI/UX v4.0 为唯一 UI/UX 输入性文件 |
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
| `DESIGN.md` | 原型设计基线 v2.0（视觉/布局/组件/验收横切约束，对齐 v3.0/v4.0） |
| `docs/过程文档/prototype-development-freeze-v0.1-2026-06-16.md` | 原型开发任务书、页面清单、样例数据和技术栈冻结 |
| `docs/设计文档/prototype/README.md` | 原型系统代码库入口说明 |
| `backend/` | FastAPI 后端（API + Celery 任务 + 算法引擎） |
| `frontend/` | Vue 3 前端 monorepo（web-antd 为生产应用） |
| `e2e/` | Playwright E2E 测试 |
| `db/` | 数据库 DDL 脚本（PostgreSQL + TDengine） |
| `deploy/` | 部署脚本 + Nginx 配置 + 开发环境容器编排 |
| `docker-compose.prod.yml` | 生产环境 Docker Compose 编排 |
| `Dockerfile.backend` / `Dockerfile.frontend` | 多阶段构建镜像定义 |
| `.env.prod.example` | 生产环境配置模板 |

## 维护规则

- 现行需求、原型、研发拆解和投标响应统一以 `docs/设计文档/01-PRD/PRD.md` 为准。
- 归档目录 (`docs/归档文档/`) 下的文件只用于历史追溯，不作为新一轮评审输入。
- 新一轮架构、设计或专项设计必须先吸收 approved 产品化架构和最新 PRD 的结论，再继续扩写。
- PDF 原始资料及外部参考手册统一存放于 `docs/预研文档/`。
- 版本发布使用 git tag 标记（如 `v1.0.0`），遵循语义化版本规范。
