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

# 公共函数库：Alembic 版本同步（部署=迁移一体，失败即中止）
# shellcheck source=deploy/lib-migrate.sh
source "${SCRIPT_DIR}/lib-migrate.sh"

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
# 1.5 CORS 自动检测：每次部署从 .env.prod.example 模板重新生成
# CORS_ORIGINS 行（幂等：服务器 IP 变化后旧 IP 不会残留）
# ------------------------------------------------------------
# 模板中 CORS_ORIGINS=["__AUTO__", "http://localhost:7141"]
# 部署时检测本机 IP，替换 __AUTO__ 为 http://<IP>:7141 后整行写入 .env.prod；
# 额外来源请维护在模板数组中（见 .env.prod.example 注释）
ENV_TEMPLATE=".env.prod.example"
if grep -q '"__AUTO__"' "$ENV_TEMPLATE" 2>/dev/null; then
    # 检测本机 IP（Linux: hostname -I；macOS: ipconfig getifaddr）
    DETECTED_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
    if [ -z "$DETECTED_IP" ]; then
        DETECTED_IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "")
    fi
    if [ -n "$DETECTED_IP" ]; then
        echo "检测到本机 IP：$DETECTED_IP"
        echo "  重新生成 CORS_ORIGINS：__AUTO__ → http://$DETECTED_IP:7141"
        NEW_CORS_LINE=$(grep -E '^CORS_ORIGINS=' "$ENV_TEMPLATE" | sed "s|\"__AUTO__\"|\"http://$DETECTED_IP:7141\"|g")
    else
        echo "  [WARN] 无法自动检测本机 IP，CORS_ORIGINS 仅保留模板中的静态来源"
        echo "  请在 $ENV_TEMPLATE 的 CORS_ORIGINS 数组中补充 http://<服务器IP>:7141"
        # 移除 __AUTO__ 避免 Pydantic 校验失败（JSON 数组中不能有非 URL 字符串）
        NEW_CORS_LINE=$(grep -E '^CORS_ORIGINS=' "$ENV_TEMPLATE" | sed -e 's|"__AUTO__", ||g' -e 's|, "__AUTO__"||g' -e 's|"__AUTO__"|"http://localhost:7141"|g')
    fi
    # 整行替换 .env.prod 中的 CORS_ORIGINS（兼容 macOS sed 与 Linux sed）
    if [[ "$OSTYPE" == "darwin"* ]]; then
        sed -i '' "s|^CORS_ORIGINS=.*|$NEW_CORS_LINE|" "$ENV_FILE"
    else
        sed -i "s|^CORS_ORIGINS=.*|$NEW_CORS_LINE|" "$ENV_FILE"
    fi
    echo "  最终 $(grep -E '^CORS_ORIGINS=' "$ENV_FILE")"
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
    if [[ "$var_value" == *"<"*">"* ]]; then
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
    if [[ "$var_value" == *"<"*">"* ]]; then
        echo "错误：${var_name} 仍为占位符（${var_value}）"
        echo "请修改 $ENV_FILE 中的 ${var_name} 为真实值或留空"
        exit 1
    fi
}

# 必填密码类：不能为空也不能为占位符
check_required_no_placeholder "POSTGRES_PASSWORD"
check_required_no_placeholder "REDIS_PASSWORD"

# ------------------------------------------------------------
# 2.55 ENV=production 强制校验（2026-07-28 Phase 5）
# ------------------------------------------------------------
# backend lifespan 依据 ENV 判断是否自动拉起 Celery Worker/Beat 子进程；
# 生产环境由 compose 独立 celery-worker / celery-beat 容器接管调度与执行。
# 若 .env.prod 漏配 ENV=production，backend 容器会在 lifespan 里再拉一套
# Worker/Beat，与独立容器双消费任务、双触发定时任务，必须部署前拦截。
# 注意 || true：.env.prod 缺失 ENV 行时 grep 返回 1，pipefail 下赋值会
# 触发 set -e 静默中止，漏配 ENV（本校验的主要拦截场景）就看不到提示。
ENV_VALUE=$(grep -E "^ENV=" "$ENV_FILE" | cut -d'=' -f2- | tr '[:upper:]' '[:lower:]' || true)
if [ "$ENV_VALUE" != "production" ]; then
    echo "错误：$ENV_FILE 必须设置 ENV=production（当前：${ENV_VALUE:-未设置}）"
    echo "原因：backend lifespan 依据 ENV 决定是否自动拉起 Celery Worker/Beat；"
    echo "      生产环境由独立 celery-worker/celery-beat 容器接管，漏配会导致任务双消费。"
    exit 1
fi

# ------------------------------------------------------------
# 2.6 数据源与 Profile（2026-07-20 架构决策：导入走远端、计算全本地）
# ------------------------------------------------------------
# 计算类历史数据查询一律走本地 TDengine，生产环境必须启动内置 TDengine
# （或提供外部实例凭据），因此 TDENGINE_PASSWORD 恒为必填且恒启用
# tdengine profile。历史数据导入接口 URL/Token 改由 sys_config 管理
# （部署后在 UI「链路配置」页填写），不再从 .env.prod 校验。
DATA_SOURCE_TYPE=$(grep -E "^DATA_SOURCE_TYPE=" "$ENV_FILE" | cut -d'=' -f2-)
check_required_no_placeholder "TDENGINE_PASSWORD"
COMPOSE_PROFILE_ARGS=(--profile tdengine)

compose_prod() {
    docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "${COMPOSE_PROFILE_ARGS[@]}" "$@"
}

# lib-migrate.sh 要求的容器命令执行器：在 backend 容器内执行命令
backend_exec() {
    compose_prod exec -T backend "$@"
}

SIGNALR_ENABLED=$(grep -E "^SIGNALR_ENABLED=" "$ENV_FILE" | cut -d'=' -f2- | tr '[:upper:]' '[:lower:]')
if [ "$SIGNALR_ENABLED" = "true" ]; then
    check_required_no_placeholder "SIGNALR_HUB_URL"
fi

AAS_SYNC_ENABLED=$(grep -E "^AAS_SYNC_ENABLED=" "$ENV_FILE" | cut -d'=' -f2- | tr '[:upper:]' '[:lower:]')
if [ "$AAS_SYNC_ENABLED" = "true" ]; then
    check_required_no_placeholder "AAS_ENDPOINT"
fi

# 历史数据导入接口 URL/Token：允许留空（部署后可在 UI「链路配置」页维护），
# 但不允许带 <...> 占位符部署（占位符会导致导入任务带病运行）
check_no_placeholder "HISTORY_DATA_API_URL"
check_no_placeholder "HISTORY_DATA_API_TOKEN"

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
# 3.5 部署前自动备份（升级场景）
# ------------------------------------------------------------
# 已有运行中容器时才备份（首次部署无数据可备）。备份失败即中止部署，
# 避免在无回退数据的情况下变更镜像/schema。
if docker ps --format '{{.Names}}' 2>/dev/null | grep -q '^clpm-postgres$'; then
    echo "0. 部署前自动备份（检测到运行中的既有部署）..."
    ./deploy/backup.sh
    echo ""
else
    echo "0. 首次部署（无运行中容器），跳过部署前备份"
    echo ""
fi

# ------------------------------------------------------------
# 4. 构建 Docker 镜像
# ------------------------------------------------------------
echo "1. 构建 Docker 镜像..."
compose_prod build
echo ""

# ------------------------------------------------------------
# 5. 启动服务
# ------------------------------------------------------------
echo "2. 启动服务..."
compose_prod up -d
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
# Alembic 版本同步策略（公共函数 alembic_sync_head，deploy/lib-migrate.sh）：
#   - 首次部署（alembic_version 表不存在）：stamp head 标记当前版本
#   - 后续升级（alembic_version 表已存在）：upgrade head 执行增量迁移
#   - 任一步失败即中止部署（set -e），不允许新代码跑在旧 schema 上
echo "4. 数据库版本同步（部署=迁移一体，失败即中止）..."
alembic_sync_head
echo ""

# ------------------------------------------------------------
# 8. 验证服务状态
# ------------------------------------------------------------
echo "5. 验证服务状态..."
compose_prod ps
echo ""

# ------------------------------------------------------------
# 9. API 健康检查
# ------------------------------------------------------------
echo "6. API 健康检查..."
# S2-B3: 后端端口不暴露到宿主机，通过 docker exec 检查
if compose_prod exec -T backend curl -fsS http://localhost:7101/health >/dev/null 2>&1; then
    echo "  [OK] 后端 API 健康"
else
    echo "  [FAIL] 后端 API 健康检查失败"
    echo "  查看日志：docker compose --env-file $ENV_FILE -f $COMPOSE_FILE logs backend"
    exit 1
fi

if curl -fsS http://localhost:7141/ >/dev/null 2>&1; then
    echo "  [OK] 前端服务健康"
else
    echo "  [FAIL] 前端服务健康检查失败"
    echo "  查看日志：docker compose --env-file $ENV_FILE -f $COMPOSE_FILE logs frontend"
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
    ACCESS_URL="http://$SERVER_IP:7141"
else
    ACCESS_URL="http://localhost:7141"
fi
echo "服务访问地址："
echo "  前端：        $ACCESS_URL"
echo "  后端 API：    $ACCESS_URL/api/v1（通过 nginx 反向代理）"
echo "  默认账号：    admin / admin123（首次登录后请立即修改密码）"
echo ""
echo "常用运维命令："
echo "  查看日志：    docker compose --env-file $ENV_FILE -f $COMPOSE_FILE logs -f"
echo "  查看状态：    docker compose --env-file $ENV_FILE -f $COMPOSE_FILE ps"
echo "  停止服务：    docker compose --env-file $ENV_FILE -f $COMPOSE_FILE down"
echo "  重启服务：    docker compose --env-file $ENV_FILE -f $COMPOSE_FILE restart"
echo "  数据备份：    ./deploy/backup.sh"
echo "  数据回滚：    ./deploy/rollback.sh"
