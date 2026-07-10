# ============================================================
# CLPM 项目 Makefile - 统一开发/测试/构建/部署命令
# 用法：make <target>  或  make help 查看所有命令
# ============================================================

# 项目路径
BACKEND_DIR := backend
FRONTEND_DIR := frontend
DEV_COMPOSE := deploy/docker/docker-compose.dev.yml
PROD_COMPOSE := docker-compose.prod.yml

# 默认目标
.DEFAULT_GOAL := help

.PHONY: help dev dev-frontend dev-all test test-frontend test-all test-cov \
        lint format typecheck check-all build build-docker \
        deploy backup rollback \
        migrate migrate-create seed \
        clean clean-all

help: ## 显示所有可用命令
	@awk 'BEGIN {FS = ":.*##"; printf "\n\033[1mCLPM Makefile 命令清单\033[0m\n\n"} \
	      /^[a-zA-Z_-]+:.*##/ { printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2 } \
	      END { printf "\n" }' $(MAKEFILE_LIST)

# ============================================================
# 开发环境
# ============================================================

dev: ## 启动后端开发服务器（热重载，端口 7101）
	cd $(BACKEND_DIR) && uv run uvicorn app.main:app --host 0.0.0.0 --port 7101 --reload

dev-frontend: ## 启动前端开发服务器（端口 7100）
	cd $(FRONTEND_DIR) && pnpm run dev:antd

dev-all: ## 启动所有开发服务（基础设施 + 后端 + 前端）
	@echo "==> 启动开发基础设施（PostgreSQL + TDengine + Redis）..."
	docker compose -f $(DEV_COMPOSE) up -d
	@echo "==> 启动后端开发服务器（后台运行，端口 7101）..."
	cd $(BACKEND_DIR) && uv run uvicorn app.main:app --host 0.0.0.0 --port 7101 --reload &
	@echo "==> 启动前端开发服务器（前台运行，端口 7100，Ctrl+C 退出）..."
	cd $(FRONTEND_DIR) && pnpm run dev:antd

# ============================================================
# 测试
# ============================================================

test: ## 运行后端测试
	cd $(BACKEND_DIR) && uv run pytest -q

test-frontend: ## 运行前端单元测试
	cd $(FRONTEND_DIR) && pnpm run test:unit

test-all: test test-frontend ## 运行所有测试

test-cov: ## 运行后端测试并生成覆盖率报告
	cd $(BACKEND_DIR) && uv run pytest --cov=app --cov-report=term-missing

# ============================================================
# 代码检查
# ============================================================

lint: ## 运行 ruff check（后端代码检查）
	cd $(BACKEND_DIR) && uv run ruff check .

format: ## 运行 ruff format（后端代码格式化）
	cd $(BACKEND_DIR) && uv run ruff format .

typecheck: ## 运行 mypy（后端）+ 前端类型检查
	cd $(BACKEND_DIR) && uv run mypy app
	cd $(FRONTEND_DIR) && pnpm run check:type

check-all: lint typecheck ## 运行所有检查（lint + typecheck）

# ============================================================
# 构建
# ============================================================

build: ## 构建前端生产包
	cd $(FRONTEND_DIR) && pnpm run build:antd

build-docker: ## 构建 Docker 镜像（backend + frontend）
	docker compose -f $(PROD_COMPOSE) build

# ============================================================
# 部署
# ============================================================

deploy: ## 部署到生产环境（执行 deploy/deploy.sh）
	./deploy/deploy.sh

backup: ## 数据备份（PostgreSQL + TDengine）
	./deploy/backup.sh

rollback: ## 回滚到上一版本镜像
	./deploy/rollback.sh

# ============================================================
# 数据库
# ============================================================

migrate: ## 运行数据库迁移（alembic upgrade head）
	cd $(BACKEND_DIR) && uv run alembic upgrade head

migrate-create: ## 创建新迁移，用法：make migrate-create MSG="描述"
	cd $(BACKEND_DIR) && uv run alembic revision --autogenerate -m "$(MSG)"

seed: ## 加载种子数据到开发环境 PostgreSQL
	docker exec -i clpm-postgres psql -U clpm -d clpm < db/postgresql/02_seed_data.sql

# ============================================================
# 清理
# ============================================================

clean: ## 清理构建产物
	rm -rf $(FRONTEND_DIR)/apps/web-antd/dist
	rm -rf $(BACKEND_DIR)/build $(BACKEND_DIR)/dist
	find . -type d -name __pycache__ -prune -exec rm -rf {} +

clean-all: clean ## 清理所有生成文件（含缓存与依赖）
	rm -rf $(BACKEND_DIR)/.pytest_cache $(BACKEND_DIR)/.mypy_cache $(BACKEND_DIR)/.ruff_cache
	rm -rf $(BACKEND_DIR)/.coverage $(BACKEND_DIR)/htmlcov
	rm -rf $(FRONTEND_DIR)/.turbo $(FRONTEND_DIR)/**/.turbo
	rm -rf $(FRONTEND_DIR)/**/node_modules
