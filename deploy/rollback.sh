#!/bin/bash
# ============================================================
# CLPM 回滚脚本 - 回滚到上一版本镜像
# 用法：./deploy/rollback.sh
# 前置条件：
#   1. 已执行过至少两次部署，存在多个版本的镜像 tag
#   2. 镜像命名规则：clpm-backend:<tag>、clpm-frontend:<tag>
# ============================================================
set -euo pipefail

# 切换到项目根目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

COMPOSE_FILE="docker-compose.prod.yml"
ENV_FILE=".env.prod"
COMPOSE_PROFILE_ARGS=()
if grep -qE '^DATA_SOURCE_TYPE=tdengine$' "$ENV_FILE" 2>/dev/null; then
    COMPOSE_PROFILE_ARGS=(--profile tdengine)
fi

compose_prod() {
    docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "${COMPOSE_PROFILE_ARGS[@]}" "$@"
}

echo "=== CLPM 回滚到上一版本 ==="
echo ""

# ------------------------------------------------------------
# 1. 获取当前与历史镜像版本
# ------------------------------------------------------------
echo "可用 backend 镜像版本（按创建时间倒序）："
docker images clpm-backend --format "{{.Tag}}\t{{.CreatedAt}}" | head -10
echo ""

echo "可用 frontend 镜像版本（按创建时间倒序）："
docker images clpm-frontend --format "{{.Tag}}\t{{.CreatedAt}}" | head -10
echo ""

# ------------------------------------------------------------
# 2. 获取当前 latest 之外的上一个版本
# ------------------------------------------------------------
# 列出所有 tag（排除 <none> 与 latest），取第一个作为回滚目标
PREV_TAG=$(docker images clpm-backend --format "{{.Tag}}" | grep -vE "^<none>$|^latest$" | head -1)

if [ -z "$PREV_TAG" ]; then
    echo "错误：找不到可回滚的历史版本镜像"
    echo "请确认已构建过带 tag 的镜像，例如："
    echo "  docker tag clpm-backend:latest clpm-backend:v1.0.0"
    exit 1
fi

echo "回滚目标版本：$PREV_TAG"
echo ""

# ------------------------------------------------------------
# 3. 确认回滚
# ------------------------------------------------------------
read -p "确认回滚到 $PREV_TAG？(y/N) " confirm
if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
    echo "已取消回滚"
    exit 0
fi

# ------------------------------------------------------------
# 4. 标记上一版本为 latest
# ------------------------------------------------------------
echo "1. 标记 $PREV_TAG 为 latest..."
docker tag clpm-backend:"$PREV_TAG" clpm-backend:latest
docker tag clpm-frontend:"$PREV_TAG" clpm-frontend:latest
echo ""

# ------------------------------------------------------------
# 5. 数据库 Schema 回滚（S2-B5）
# ------------------------------------------------------------
echo "2. 数据库 Schema 回滚..."
echo "   当前 Alembic 版本："
docker exec clpm-backend alembic current 2>/dev/null || echo "   （无法获取当前版本，跳过 DB 回滚）"
echo ""
read -p "是否回滚数据库 Schema（alembic downgrade -1）？(y/N) " db_confirm
if [ "$db_confirm" = "y" ] || [ "$db_confirm" = "Y" ]; then
    echo "   执行 alembic downgrade -1..."
    docker exec clpm-backend alembic downgrade -1
    echo "   [OK] 数据库 Schema 已回退一个版本"
else
    echo "   [SKIP] 跳过数据库 Schema 回滚"
fi
echo ""

# ------------------------------------------------------------
# 6. 重启服务
# ------------------------------------------------------------
echo "3. 重启服务..."
compose_prod up -d
echo ""

# ------------------------------------------------------------
# 7. 等待健康检查
# ------------------------------------------------------------
echo "4. 等待服务健康检查（30 秒）..."
sleep 30
echo ""

# ------------------------------------------------------------
# 8. 验证
# ------------------------------------------------------------
echo "5. 验证服务状态..."
compose_prod ps
echo ""

if docker exec clpm-backend curl -fsS http://localhost:7101/health >/dev/null 2>&1; then
    echo "  [OK] 后端 API 健康"
else
    echo "  [FAIL] 后端 API 健康检查失败"
    exit 1
fi

if curl -fsS http://localhost:7141/ >/dev/null 2>&1; then
    echo "  [OK] 前端服务健康"
else
    echo "  [FAIL] 前端服务健康检查失败"
    exit 1
fi
echo ""

echo "=== 回滚完成（当前版本：$PREV_TAG）==="
