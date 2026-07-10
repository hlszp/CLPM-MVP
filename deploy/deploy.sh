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
# 1.5 CORS 自动检测：将 __AUTO__ 替换为本机 IP
# ------------------------------------------------------------
# 支持 .env.prod 中 CORS_ORIGINS=["__AUTO__", "http://localhost"]
# 部署时自动检测本机 IP，替换为 http://<IP>
if grep -q '"__AUTO__"' "$ENV_FILE" 2>/dev/null; then
    # 检测本机 IP（Linux: hostname -I；macOS: ipconfig getifaddr）
    DETECTED_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
    if [ -z "$DETECTED_IP" ]; then
        DETECTED_IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "")
    fi
    if [ -n "$DETECTED_IP" ]; then
        echo "检测到本机 IP：$DETECTED_IP"
        echo "  替换 CORS_ORIGINS 中的 __AUTO__ → http://$DETECTED_IP"
        # 使用 sed 替换 __AUTO__ 为实际 IP（保留其他来源）
        # 兼容 macOS sed（需要 -i ''）和 Linux sed（-i 直接使用）
        if [[ "$OSTYPE" == "darwin"* ]]; then
            sed -i '' "s|\"__AUTO__\"|\"http://$DETECTED_IP\"|g" "$ENV_FILE"
        else
            sed -i "s|\"__AUTO__\"|\"http://$DETECTED_IP\"|g" "$ENV_FILE"
        fi
        echo "  最终 CORS_ORIGINS=$(grep -E '^CORS_ORIGINS=' "$ENV_FILE" | cut -d'=' -f2-)"
    else
        echo "  [WARN] 无法自动检测本机 IP，请手动修改 $ENV_FILE 中的 CORS_ORIGINS"
        echo "  将 \"__AUTO__\" 替换为 http://<服务器IP>"
        # 移除 __AUTO__ 避免 Pydantic 校验失败（JSON 数组中不能有非 URL 字符串）
        if [[ "$OSTYPE" == "darwin"* ]]; then
            sed -i '' 's|"__AUTO__", ||g' "$ENV_FILE"
            sed -i '' 's|, "__AUTO__"||g' "$ENV_FILE"
            sed -i '' 's|"__AUTO__"|"http://localhost"|g' "$ENV_FILE"
        else
            sed -i 's|"__AUTO__", ||g' "$ENV_FILE"
            sed -i 's|, "__AUTO__"||g' "$ENV_FILE"
            sed -i 's|"__AUTO__"|"http://localhost"|g' "$ENV_FILE"
        fi
    fi
    echo ""
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
# 2.5 检查必填密码字段是否仍为占位符或为空
# ------------------------------------------------------------
# 校验：必须非空且不能是 <change-me-*> / <generate-*> 占位符
check_required_no_placeholder() {
    local var_name="$1"
    local var_value
    var_value=$(grep -E "^${var_name}=" "$ENV_FILE" | cut -d'=' -f2-)
    if [ -z "$var_value" ]; then
        echo "错误：$var_name 未设置"
        echo "请修改 $ENV_FILE 中的 $var_name 为真实值"
        exit 1
    fi
    if [[ "$var_value" == *"<change-me"* || "$var_value" == *"<generate"* ]]; then
        echo "错误：${var_name} 仍为占位符（${var_value}）"
        echo "请修改 $ENV_FILE 中的 ${var_name} 为真实值"
        exit 1
    fi
}

# 校验：不能是占位符（允许为空，用于可选字段如 TOKEN）
check_no_placeholder() {
    local var_name="$1"
    local var_value
    var_value=$(grep -E "^${var_name}=" "$ENV_FILE" | cut -d'=' -f2-)
    if [[ "$var_value" == *"<change-me"* || "$var_value" == *"<generate"* ]]; then
        echo "错误：${var_name} 仍为占位符（${var_value}）"
        echo "请修改 $ENV_FILE 中的 ${var_name} 为真实值或留空"
        exit 1
    fi
}

# 必填密码类：不能为空也不能为占位符
check_required_no_placeholder "POSTGRES_PASSWORD"
check_required_no_placeholder "REDIS_PASSWORD"

# ------------------------------------------------------------
# 2.6 检查条件必填字段（开关启用时才校验）
# ------------------------------------------------------------
DATA_SOURCE_TYPE=$(grep -E "^DATA_SOURCE_TYPE=" "$ENV_FILE" | cut -d'=' -f2-)
if [ "$DATA_SOURCE_TYPE" = "remote_api" ]; then
    check_required_no_placeholder "HISTORY_DATA_API_URL"
    check_no_placeholder "HISTORY_DATA_API_TOKEN"
fi

SIGNALR_ENABLED=$(grep -E "^SIGNALR_ENABLED=" "$ENV_FILE" | cut -d'=' -f2- | tr '[:upper:]' '[:lower:]')
if [ "$SIGNALR_ENABLED" = "true" ]; then
    check_required_no_placeholder "SIGNALR_HUB_URL"
fi

AAS_SYNC_ENABLED=$(grep -E "^AAS_SYNC_ENABLED=" "$ENV_FILE" | cut -d'=' -f2- | tr '[:upper:]' '[:lower:]')
if [ "$AAS_SYNC_ENABLED" = "true" ]; then
    check_required_no_placeholder "AAS_ENDPOINT"
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
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" build
echo ""

# ------------------------------------------------------------
# 5. 启动服务
# ------------------------------------------------------------
echo "2. 启动服务..."
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d
echo ""

# ------------------------------------------------------------
# 6. 等待健康检查
# ------------------------------------------------------------
echo "3. 等待服务健康检查（30 秒）..."
sleep 30
echo ""

# ------------------------------------------------------------
# 7. 数据库版本同步
# ------------------------------------------------------------
# PostgreSQL 容器首次启动时已通过 docker-entrypoint-initdb.d 自动执行
# 01_schema.sql（建表）和 02_seed_data.sql（种子数据），无需手工 DDL。
#
# Alembic 版本同步策略：
#   - 首次部署（alembic_version 表不存在）：stamp head 标记当前版本
#   - 后续升级（alembic_version 表已存在）：upgrade head 执行增量迁移
echo "4. 数据库版本同步..."
CURRENT_REV=$(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" exec -T backend alembic current 2>/dev/null | grep -E '^[a-z0-9]' | head -1 || echo "")
if [ -z "$CURRENT_REV" ]; then
    echo "  首次部署：执行 alembic stamp head（标记当前版本，不重复执行 DDL）..."
    if docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" exec -T backend alembic stamp head 2>/dev/null; then
        echo "  [OK] Alembic 版本已标记为 head"
    else
        echo "  [WARN] alembic stamp head 失败（非首次部署时可忽略）"
    fi
else
    echo "  当前版本：$CURRENT_REV，执行 alembic upgrade head..."
    if docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" exec -T backend alembic upgrade head 2>/dev/null; then
        echo "  [OK] 数据库迁移完成"
    else
        echo "  [WARN] 数据库迁移失败"
        echo "  查看日志：docker compose -f $COMPOSE_FILE logs backend"
    fi
fi
echo ""

# ------------------------------------------------------------
# 8. 验证服务状态
# ------------------------------------------------------------
echo "5. 验证服务状态..."
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" ps
echo ""

# ------------------------------------------------------------
# 9. API 健康检查
# ------------------------------------------------------------
echo "6. API 健康检查..."
# S2-B3: 后端端口不暴露到宿主机，通过 docker exec 检查
if docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" exec -T backend curl -fsS http://localhost:8001/health >/dev/null 2>&1; then
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
# 自动获取服务器 IP（Linux）
SERVER_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
if [ -n "$SERVER_IP" ]; then
    ACCESS_URL="http://$SERVER_IP"
else
    ACCESS_URL="http://localhost"
fi
echo "服务访问地址："
echo "  前端：        $ACCESS_URL"
echo "  后端 API：    $ACCESS_URL/api/v1（通过 nginx 反向代理）"
echo "  默认账号：    admin / admin123（首次登录后请立即修改密码）"
echo ""
echo "常用运维命令："
echo "  查看日志：    docker compose -f $COMPOSE_FILE logs -f"
echo "  查看状态：    docker compose -f $COMPOSE_FILE ps"
echo "  停止服务：    docker compose -f $COMPOSE_FILE down"
echo "  重启服务：    docker compose -f $COMPOSE_FILE restart"
echo "  数据备份：    ./deploy/backup.sh"
echo "  数据回滚：    ./deploy/rollback.sh"
