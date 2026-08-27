---
kind: build_system
name: CLPM 多语言 Monorepo 构建、镜像打包与一键部署流水线
category: build_system
scope:
    - '**'
source_files:
    - Makefile
    - .github/workflows/ci.yml
    - Dockerfile.backend
    - Dockerfile.frontend
    - docker-compose.prod.yml
    - deploy/build-and-deploy.sh
    - backend/pyproject.toml
    - frontend/turbo.json
    - frontend/package.json
    - deploy/nginx.conf
    - db/postgresql/01_schema.sql
    - db/tdengine/01_supertable.sql
---

## 1. 整体方案

CLPM 采用 **后端 Python (FastAPI) + 前端 Vue Monorepo (pnpm workspace + Turbo)** 的双栈架构，通过根级 `Makefile` 统一入口，以 **Docker 多阶段镜像** 为交付物，由 `deploy/build-and-deploy.sh` 完成跨平台构建、离线传输、服务器部署、数据库迁移与健康检查的一体化流水线。CI 使用 GitHub Actions 在 push/PR 到 `main`/`develop` 时并行执行前后端 lint、类型检查、构建与测试。

## 2. 关键文件与职责

- `Makefile`：开发/测试/构建/部署的统一命令入口（`dev`、`test`、`build`、`migrate`、`deploy`、`backup`、`rollback` 等）。
- `docker-compose.prod.yml`：生产编排，定义 backend/frontend/postgres/tdengine/redis/celery-worker/celery-beat/prometheus/grafana/node-exporter 服务，含资源限制、健康检查、日志轮转与 `monitoring`/`tdengine` profile。
- `Dockerfile.backend`：Python 3.12-slim 多阶段镜像，Builder 阶段用 `uv sync --frozen` 安装依赖到 `.venv`，Runtime 阶段以非 root 用户 `clpm` 运行 Uvicorn 4 worker，暴露 `/health` 健康检查。
- `Dockerfile.frontend`：Node 22-slim 构建 + nginx:alpine 运行时，将 `apps/web-antd/dist` 托管于 Nginx，反向代理后端 API，暴露 7141 端口。
- `deploy/build-and-deploy.sh`：核心发布脚本，实现 Phase0 门禁 → Phase1 镜像构建（支持 buildx 跨 `linux/amd64`）→ 导出 tar.gz → SCP 传输 → 服务器加载镜像 → 自动备份 → docker compose up → Alembic/TDengine schema 校验 → 健康检查（含 Celery inspect ping/scheduled）→ 写入 `releases/manifest.json` 并打 `latest`/commit/时间戳 三标签。
- `.github/workflows/ci.yml`：Frontend CI（pnpm install → eslint → typecheck → build → Playwright E2E 非阻塞）+ Backend CI（uv sync → ruff check/format → mypy 非阻塞 → pytest --cov --cov-fail-under=60）。
- `backend/pyproject.toml`：依赖声明、ruff/mypy/pytest 配置，指定 `requires-python = ">=3.12,<3.13"`，排除 uvloop 以避免 macOS SIGSEGV。
- `frontend/turbo.json` + `frontend/package.json`：Turbo 任务缓存、`build:antd` 仅构建 web-antd 应用；pnpm workspace 管理 apps/packages/internal/playground/docs。
- `db/postgresql/*.sql`、`db/tdengine/01_supertable.sql`：容器启动时自动注入的初始化 SQL。
- `deploy/nginx.conf`：Nginx 静态资源托管 + `/api/v1` 反向代理至 backend 7101。

## 3. 架构与约定

### 3.1 版本与可追溯性
- 镜像同时打三个 tag：`latest`、`<git-commit>`、`YYYYMMDD-HHMMSS`，并通过 `APP_VERSION` ARG 注入镜像 ENV（来自 `git describe --tags`），供 `/health` 与日志排障。
- `releases/manifest.json` 记录每次构建的版本、commit、branch、appVersion、镜像大小与 tar 文件名，纳入 git 追踪。

### 3.2 构建管线
- 本地开发：`make dev-all` 启动 Docker Compose 基础设施（PostgreSQL/TDengine/Redis），再分别起后端 `uv run uvicorn --reload` 与前端 `pnpm dev:antd`。
- 生产构建：`./deploy/build-and-deploy.sh [--build-only|--deploy-only|--backend-only|--frontend-only]`，默认先跑 `ruff check/format --check` + `pytest -x -q` + 前端 `check:type` 作为门禁（可用 `--skip-gate` 跳过）。
- CI：GitHub Actions 中 Frontend/Backend 两个 job 并行，均基于 `ubuntu-latest`，缓存 pnpm/uv lockfile。

### 3.3 部署流程
- 脚本通过 SSH 连接目标服务器（默认 `zhangping@192.168.13.111`，可通过 `SSH_HOST` 覆盖），预检 Docker/Docker Compose v2/端口 7141/部署目录/.env.prod。
- 自动停止旧服务、清理残留容器、`docker compose down --remove-orphans`，再 `up -d` 并等待 40s。
- 同步 `APP_VERSION` 到服务器 `.env.prod`，执行 Alembic 迁移（失败即中止），校验 TDengine schema，最后对 backend/frontend/celery-worker/celery-beat 做健康检查。
- 所有服务通过 `profiles` 控制：`tdengine` 恒启用，`monitoring`（Prometheus/Grafana/Node Exporter）按需 `--profile monitoring` 启动。

### 3.4 资源与安全约束
- 每个服务在 compose 中声明 `deploy.resources.limits`（如 backend 1G/2核、celery-worker 6G/8核、frontend 256M/0.5核）。
- 除 frontend 7141 外，其余服务仅 `expose` 不映射宿主机端口；backend 通过 nginx 反代访问。
- 后端镜像以非 root 用户 `clpm` 运行，系统依赖通过清华源加速安装。

## 4. 约定与约束

- **依赖锁定**：前后端均使用 `--frozen-lockfile` / `uv sync --frozen`，禁止动态升级依赖。
- **镜像不可变**：每次构建生成带 commit 和时间戳的独立镜像，回滚通过 `deploy/rollback.sh` 按 manifest 中的历史 tag 切换。
- **数据库即代码**：PG schema 通过 `docker-entrypoint-initdb.d` 注入；Alembic 迁移在部署阶段自动 `upgrade head`，失败即中止。
- **TDengine 密码标记**：非首次部署时创建 `.td-password-changed` 标记文件，使 entrypoint 跳过 ALTER USER，避免卷持久化后默认密码登录失败导致崩溃循环。
- **Celery 并发**：worker 默认 `--concurrency=8`，对应 6G/8核资源限制；beat 通过 `/proc/1/cmdline` 检测存活。
- **监控可选**：Prometheus/Grafana/Node Exporter 仅在 `--profile monitoring` 时拉起，默认不占用资源。
- **CI 质量门槛**：后端覆盖率要求 `--cov-fail-under=60`；mypy 与 Playwright E2E 在 CI 中标记 `continue-on-error` 为非阻塞。
- **环境变量隔离**：生产通过 `--env-file .env.prod` 注入，示例见 `.env.prod.example`；开发环境使用 `deploy/docker/docker-compose.dev.yml`。
