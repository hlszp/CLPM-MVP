---
kind: dependency_management
name: CLPM 多语言依赖管理：uv + pnpm workspace catalog + 子项目 requirements.txt
category: dependency_management
scope:
    - '**'
source_files:
    - backend/pyproject.toml
    - backend/uv.lock
    - backend/.python-version
    - frontend/package.json
    - frontend/pnpm-workspace.yaml
    - frontend/.npmrc
    - frontend/.changeset/config.json
    - frontend/.github/dependabot.yml
    - mock_data_server/requirements.txt
    - perf/requirements.txt
    - e2e/package.json
---

## 1. 使用的系统/工具

本仓库是一个多语言 monorepo，后端使用 Python（uv），前端使用 Node.js（pnpm workspace），另有独立的 Python 小服务与性能测试脚本各自维护依赖。

- **后端（backend）**：Python 包管理与构建由 `pyproject.toml`（Hatchling）声明，锁定文件为根级 `uv.lock`；Python 版本通过 `.python-version` 固定为 `3.12`，运行时要求 `>=3.12,<3.13`。
- **前端（frontend）**：基于 `pnpm workspace` + `turbo` 的 monorepo，顶层 `package.json` 通过 `engines` 强制 Node `^22.18.0 || ^24.0.0`、pnpm `>=10.0.0`，并通过 `packageManager: "pnpm@10.33.4"` 锁定 pnpm 版本；依赖版本集中在 `pnpm-workspace.yaml` 的 `catalog:` 段，各包引用时写 `catalog:` 而非硬编码版本号。
- **独立 Python 子项目**：`mock_data_server/requirements.txt` 与 `perf/requirements.txt` 各自用 `pip` 风格的 `requirements.txt` 声明依赖，未纳入 uv/pnpm 统一管理。
- **E2E 测试（e2e）**：独立 `package.json` + `pnpm-lock.yaml`，仅包含 Playwright 相关依赖。

## 2. 关键文件

| 作用 | 文件路径 |
|---|---|
| 后端依赖声明与构建配置 | `backend/pyproject.toml` |
| 后端依赖锁定 | `backend/uv.lock` |
| 后端 Python 版本 | `backend/.python-version` |
| 前端 Monorepo 入口 | `frontend/package.json` |
| 前端 workspace/catalog/overrides | `frontend/pnpm-workspace.yaml` |
| 前端 npm 镜像源 | `frontend/.npmrc` |
| 前端变更集发布配置 | `frontend/.changeset/config.json` |
| 前端 Dependabot 自动更新 | `frontend/.github/dependabot.yml` |
| 模拟数据服务依赖 | `mock_data_server/requirements.txt` |
| 压测套件依赖 | `perf/requirements.txt` |
| E2E 套件依赖 | `e2e/package.json` |

## 3. 架构与约定

### 后端（Python / uv）
- 所有生产依赖在 `backend/pyproject.toml` 的 `[project].dependencies` 中以 `包名>=最低版本` 形式声明，例如 `fastapi>=0.115`、`celery[redis]>=5.4`、`taospy>=2.7`、`numpy>=2.5.0`、`scipy>=1.18.0` 等。
- 开发依赖通过两个位置声明：`[project.optional-dependencies].dev` 与 `[dependency-groups].dev`，均包含 pytest、pytest-asyncio、ruff、mypy 等。
- 构建系统使用 Hatchling（`build-system.requires = ["hatchling"]`），wheel 打包目标为 `app` 目录。
- 显式排除 `uvicorn[standard]` 中的 `uvloop`，改用 `uvicorn>=0.30` + `httptools` + `watchfiles` 组合，以避免 macOS 上 SIGSEGV 问题（注释中明确说明原因）。
- 依赖锁定由 `uv.lock` 完成，提交到版本库以保证可重现安装。
- 数据库迁移使用 Alembic（`alembic>=1.13`），迁移脚本位于 `backend/alembic/versions/`。

### 前端（pnpm workspace + catalog）
- 采用 pnpm workspace monorepo，workspace 成员包括 `internal/*`、`packages/*`、`apps/*`、`scripts/*`、`docs`、`playground`。
- 公共依赖统一在 `pnpm-workspace.yaml` 的 `catalog:` 段集中声明（如 `vue: ^3.5.34`、`vite: ^8.0.13`、`typescript: ^6.0.3`、`turbo: ^2.9.14` 等），各包引用时写 `"vue": "catalog:"`，避免重复声明版本。
- 通过 `overrides:` 对特定包进行全局覆盖（如 `form-data: ^4.0.6`、`pinia: 'catalog:'`、`vue: 'catalog:'`）。
- 通过 `publicHoistPattern` 将 lefthook、eslint、oxfmt、stylelint 等工具提升到工作区根，减少重复安装。
- `strictPeerDependencies: false`、`autoInstallPeers: true`、`dedupePeerDependents: true` 降低 peer dependency 冲突成本。
- 私有 npm 镜像源通过 `frontend/.npmrc` 设置为 `https://registry.npmmirror.com`。
- 发布流程使用 Changesets（`@changesets/cli`），按 `@vben-core/*`、`@vben/*` 分组 versioning，baseBranch 为 `main`。
- 自动更新通过 GitHub Dependabot 每日扫描 npm 生态，minor/patch 变更合并到 `non-breaking-changes` 组。

### 其他子项目
- `mock_data_server/requirements.txt`：轻量 FastAPI 服务，依赖 `fastapi>=0.110.0`、`uvicorn[standard]>=0.27.0`、`httpx>=0.27.0`、`pydantic>=2.6.0`。
- `perf/requirements.txt`：Locust 压测套件，依赖 `locust>=2.20.0`、`httpx>=0.24.0`、`psycopg2-binary>=2.9.0`、`taospy>=2.8.0`、`redis>=5.0.0`、`playwright>=1.40.0`。
- `e2e/package.json`：Playwright E2E 测试，依赖 `@playwright/test ^1.48.0`、`@types/node ^20.14.0`、`typescript ^5.5.0`，并自带 `pnpm-lock.yaml`。

## 4. 约定与约束

- **Python 版本锁定**：`backend/pyproject.toml` 要求 `requires-python = ">=3.12,<3.13"`，`.python-version` 固定为 `3.12`，确保全仓一致。
- **uv 锁定优先**：后端依赖以 `uv.lock` 为准，新增依赖需通过 `uv add` 修改 `pyproject.toml` 并重新生成锁文件。
- **前端 catalog 唯一来源**：所有跨包共享的 npm 依赖必须写入 `pnpm-workspace.yaml` 的 `catalog:` 段，并通过 `catalog:` 引用，禁止在各子包中直接写死版本号。
- **Node/pnpm 版本锁定**：`frontend/package.json` 的 `engines` 与 `packageManager` 字段强制 CI/本地环境使用指定 Node 与 pnpm 版本。
- **npm 镜像源**：通过 `frontend/.npmrc` 指向 `npmmirror.com`，国内加速。
- **自动化升级**：前端启用 Dependabot 每日扫描 npm 与 GitHub Actions 的 minor/patch 更新，按组聚合 PR。
- **变更发布**：前端使用 Changesets，内部包按 `@vben-core/*`、`@vben/*` 固定版本联动升级。
- **子项目隔离**：`mock_data_server` 与 `perf` 作为独立 Python 环境，不共享 backend 的 uv 环境，需分别 `pip install -r requirements.txt` 安装。
- **无 vendoring**：未发现 vendor/ 或 `--frozen-lockfile` 之外的源码级依赖分发方式，所有第三方库均通过包管理器从远程 registry 拉取。