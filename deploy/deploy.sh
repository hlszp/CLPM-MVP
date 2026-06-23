#!/bin/bash
# ============================================================
# CLPM 生产环境部署脚本
# 用法：./deploy/deploy.sh
# 前置条件：
#   1. 已安装 Docker 24+ 与 Docker Compose v2
#   2. 已复制 .env.prod.example 为 .env.prod 并填写真实配置
# ============================================================
set -euo pipefail

# 切换到项目根目录（脚本位于 deploy/ 子目录）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

COMPOSE_FILE="docker-compose.prod.yml"
ENV_FILE=".env.prod"

echo "=== CLPM 生产环境部署 ==="
echo "项目根目录：$PROJECT_ROOT"
echo ""

# ------------------------------------------------------------
# 1. 检查 .env.prod 文件
# ------------------------------------------------------------
if [ ! -f "$ENV_FILE" ]; then
    echo "错误：$ENV_FILE 文件不存在"
    echo "请执行：cp .env.prod.example $ENV_FILE"
    echo "然后修改 $ENV_FILE 中的占位符为真实配置"
    exit 1
fi

# ------------------------------------------------------------
# 2. 检查 JWT_SECRET_KEY 是否已设置
# ------------------------------------------------------------
# 安全地读取 .env.prod（不使用 source，避免执行任意代码）
JWT_SECRET_KEY=$(grep -E "^JWT_SECRET_KEY=" "$ENV_FILE" | cut -d'=' -f2-)
if [ -z "$JWT_SECRET_KEY" ] || [ "$JWT_SECRET_KEY" = "<generate-with-openssl-rand-hex-32>" ]; then
    echo "错误：JWT_SECRET_KEY 未设置或仍为占位符"
    echo "请执行：openssl rand -hex 32"
    echo "然后将输出值填入 $ENV_FILE 的 JWT_SECRET_KEY"
    exit 1
fi

if [ ${#JWT_SECRET_KEY} -lt 32 ]; then
    echo "错误：JWT_SECRET_KEY 长度不足 32 字符（当前 ${#JWT_SECRET_KEY} 字符）"
    exit 1
fi

# ------------------------------------------------------------
# 3. 检查 Docker 环境
# ------------------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
    echo "错误：未安装 Docker"
    exit 1
fi

echo "Docker 版本：$(docker --version)"
echo ""

# ------------------------------------------------------------
# 4. 构建 Docker 镜像
# ------------------------------------------------------------
echo "1. 构建 Docker 镜像..."
docker compose -f "$COMPOSE_FILE" build
echo ""

# ------------------------------------------------------------
# 5. 启动服务
# ------------------------------------------------------------
echo "2. 启动服务..."
docker compose -f "$COMPOSE_FILE" up -d
echo ""

# ------------------------------------------------------------
# 6. 等待健康检查
# ------------------------------------------------------------
echo "3. 等待服务健康检查（30 秒）..."
sleep 30
echo ""

# ------------------------------------------------------------
# 7. 执行数据库迁移（alembic upgrade head）
# ------------------------------------------------------------
echo "4. 执行数据库迁移..."
if docker compose -f "$COMPOSE_FILE" exec -T backend uv run alembic upgrade head 2>/dev/null; then
    echo "  [OK] 数据库迁移完成"
else
    echo "  [WARN] 数据库迁移失败（可能首次启动 schema 已通过 initdb 创建）"
    echo "  查看日志：docker compose -f $COMPOSE_FILE logs backend"
fi
echo ""

# ------------------------------------------------------------
# 8. 验证服务状态
# ------------------------------------------------------------
echo "5. 验证服务状态..."
docker compose -f "$COMPOSE_FILE" ps
echo ""

# ------------------------------------------------------------
# 9. API 健康检查
# ------------------------------------------------------------
echo "6. API 健康检查..."
# S2-B3: 后端端口不暴露到宿主机，通过 docker exec 检查
if docker compose -f "$COMPOSE_FILE" exec -T backend curl -fsS http://localhost:8001/health >/dev/null 2>&1; then
    echo "  [OK] 后端 API 健康"
else
    echo "  [FAIL] 后端 API 健康检查失败"
    echo "  查看日志：docker compose -f $COMPOSE_FILE logs backend"
    exit 1
fi

if curl -fsS http://localhost/ >/dev/null 2>&1; then
    echo "  [OK] 前端服务健康"
else
    echo "  [FAIL] 前端服务健康检查失败"
    echo "  查看日志：docker compose -f $COMPOSE_FILE logs frontend"
    exit 1
fi
echo ""

# ------------------------------------------------------------
# 9. 完成
# ------------------------------------------------------------
echo "=== 部署完成 ==="
echo ""
echo "服务访问地址："
echo "  前端：        http://localhost"
echo "  后端 API：    http://localhost/api/v1（通过 nginx 反向代理）"
echo "  API 文档：    http://localhost/api/docs（生产环境建议关闭）"
echo ""
echo "常用运维命令："
echo "  查看日志：    docker compose -f $COMPOSE_FILE logs -f"
echo "  查看状态：    docker compose -f $COMPOSE_FILE ps"
echo "  停止服务：    docker compose -f $COMPOSE_FILE down"
echo "  重启服务：    docker compose -f $COMPOSE_FILE restart"
echo "  数据备份：    ./deploy/backup.sh"
echo "  数据回滚：    ./deploy/rollback.sh"
