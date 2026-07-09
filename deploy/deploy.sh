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
check_required_no_placeholder "TDENGINE_PASSWORD"
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
# 7. 执行数据库迁移（alembic upgrade head）
# ------------------------------------------------------------
echo "4. 执行数据库迁移..."
if docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" exec -T backend uv run alembic upgrade head 2>/dev/null; then
    echo "  [OK] 数据库迁移完成"
else
    echo "  [WARN] 数据库迁移失败（可能首次启动 schema 已通过 initdb 创建）"
    echo "  查看日志：docker compose -f $COMPOSE_FILE logs backend"
fi
echo ""

# ------------------------------------------------------------
# 7.5 初始化 TDengine：修改 root 密码 + 创建超级表
# ------------------------------------------------------------
echo "4.5 初始化 TDengine..."

# 从 .env.prod 读取 TDengine 密码
TDENGINE_PASSWORD=$(grep -E "^TDENGINE_PASSWORD=" "$ENV_FILE" | cut -d'=' -f2-)

# 修改 root 密码（首次部署：taosdata → 新密码；已修改过会报错但忽略）
echo "  修改 TDengine root 密码..."
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" exec -T tdengine \
    taos -u root -ptaosdata -s "ALTER user root PASS '${TDENGINE_PASSWORD}'" 2>/dev/null \
    && echo "  [OK] TDengine root 密码已设置" \
    || echo "  [INFO] TDengine 密码可能已修改，跳过"

# 初始化超级表（使用新密码）
echo "  初始化 TDengine 超级表..."
if docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" exec -T tdengine \
    taos -u root -p${TDENGINE_PASSWORD} -f /init/01_supertable.sql 2>/dev/null; then
    echo "  [OK] TDengine 超级表初始化完成"
else
    echo "  [WARN] TDengine 初始化失败（可能已存在，忽略）"
    echo "  手动执行：docker compose --env-file $ENV_FILE -f $COMPOSE_FILE exec tdengine taos -u root -p<TDENGINE_PASSWORD> -f /init/01_supertable.sql"
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
echo "  API 文档：    $ACCESS_URL/api/docs（生产环境建议关闭）"
echo ""
echo "常用运维命令："
echo "  查看日志：    docker compose -f $COMPOSE_FILE logs -f"
echo "  查看状态：    docker compose -f $COMPOSE_FILE ps"
echo "  停止服务：    docker compose -f $COMPOSE_FILE down"
echo "  重启服务：    docker compose -f $COMPOSE_FILE restart"
echo "  数据备份：    ./deploy/backup.sh"
echo "  数据回滚：    ./deploy/rollback.sh"
