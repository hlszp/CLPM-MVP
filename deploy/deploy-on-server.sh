#!/bin/bash
# ============================================================
# CLPM 客户服务器端部署脚本（离线交付包专用）
#
# 用途：在客户服务器上从预构建镜像包部署 CLPM
# 场景：客户服务器无源码、无法连接远端仓库
# 前置：交付包已解压，.env.prod 已配置
#
# 用法：
#   ./deploy.sh                # 正常部署
#   ./deploy.sh --skip-backup  # 跳过部署前备份
#   ./deploy.sh --health-timeout 60  # 自定义健康检查超时（秒）
#
# 交付包目录结构：
#   ├── deploy.sh              ← 本脚本
#   ├── deploy/                ← 部署工具
#   │   ├── backup.sh
#   │   ├── rollback.sh
#   │   ├── lib-migrate.sh
#   │   ├── nginx.conf
#   │   ├── prometheus/
#   │   └── grafana/
#   ├── docker-compose.prod.yml
#   ├── .env.prod.example
#   ├── db/
#   ├── images/
#   │   └── clpm-images-*.tar.gz
#   └── README.md
# ============================================================
set -euo pipefail

# ------------------------------------------------------------
# 配置与颜色
# ------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

COMPOSE_FILE="docker-compose.prod.yml"
ENV_FILE=".env.prod"
IMAGES_DIR="images"
HEALTH_TIMEOUT=30

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }
log_step()  { echo -e "\n${BLUE}========== $* ==========${NC}"; }

# ------------------------------------------------------------
# 参数解析
# ------------------------------------------------------------
SKIP_BACKUP=false
for arg in "$@"; do
    case $arg in
        --skip-backup)      SKIP_BACKUP=true ;;
        --health-timeout)   shift; HEALTH_TIMEOUT="${1:-30}" ;;
        *) log_error "未知参数: $arg"; exit 1 ;;
    esac
done

# ------------------------------------------------------------
# 全局状态（用于 ERR trap 和自动回滚）
# ------------------------------------------------------------
CURRENT_STEP="初始化"
PREV_BACKEND_ID=""
PREV_FRONTEND_ID=""
SERVICES_STARTED=false
DEPLOY_START_TIME=$(date '+%Y-%m-%d %H:%M:%S')
TROUBLESHOOT_FILE="/tmp/clpm-deploy-troubleshooting-$(date +%Y%m%d-%H%M%S).md"

# ------------------------------------------------------------
# 公共函数库
# ------------------------------------------------------------
if [ ! -f "deploy/lib-migrate.sh" ]; then
    log_error "未找到 deploy/lib-migrate.sh，请确认交付包完整"
    exit 1
fi
# shellcheck source=deploy/lib-migrate.sh
source "deploy/lib-migrate.sh"

# ------------------------------------------------------------
# 核心函数
# ------------------------------------------------------------

# 记录部署前镜像 ID（回滚锚点）
record_pre_deploy_images() {
    PREV_BACKEND_ID=$(docker images clpm-backend:latest --format '{{.ID}}' 2>/dev/null | head -1 || echo "")
    PREV_FRONTEND_ID=$(docker images clpm-frontend:latest --format '{{.ID}}' 2>/dev/null | head -1 || echo "")
    if [ -n "$PREV_BACKEND_ID" ]; then
        log_info "部署前 backend Image ID:  $PREV_BACKEND_ID"
        log_info "部署前 frontend Image ID: $PREV_FRONTEND_ID"
    else
        log_info "首次部署（无旧镜像），自动回滚不可用"
    fi
}

# 从 tarball 加载镜像
load_images() {
    local tarball
    tarball=$(ls "${IMAGES_DIR}"/clpm-images-*.tar.gz 2>/dev/null | sort -V | tail -1)
    if [ -z "$tarball" ]; then
        log_error "未找到镜像包 ${IMAGES_DIR}/clpm-images-*.tar.gz"
        log_error "请确认交付包完整，images/ 目录下包含镜像 tarball"
        exit 1
    fi
    local tar_size
    tar_size=$(du -h "$tarball" | cut -f1)
    log_info "加载镜像: $tarball ($tar_size)"
    docker load < "$tarball"
    log_info "镜像加载完成"

    # 校验核心镜像是否齐全（客户离线环境无法 docker pull 补拉）
    local required_images=(
        "clpm-backend:latest"
        "clpm-frontend:latest"
        "postgres:16-alpine"
        "redis:7-alpine"
        "tdengine/tdengine:3.3.6.6"
    )
    local missing=""
    for img in "${required_images[@]}"; do
        if ! docker image inspect "$img" >/dev/null 2>&1; then
            missing="${missing}  ${img}\n"
        fi
    done
    if [ -n "$missing" ]; then
        log_error "以下核心镜像缺失（交付包不完整或镜像包损坏）："
        echo -e "$missing"
        log_error "请联系开发团队重新获取完整交付包"
        exit 1
    fi
    log_info "核心镜像校验通过（backend + frontend + postgres + redis + tdengine）"

    # 显示已加载的镜像
    docker images | grep -E 'clpm|postgres|redis|tdengine' | head -15
}

# 30 秒健康检查重试（后端 + 前端）
wait_for_health() {
    local timeout=$HEALTH_TIMEOUT
    local interval=3
    local elapsed=0
    log_info "健康检查（超时 ${timeout}s）..."
    while [ $elapsed -lt $timeout ]; do
        if compose_prod exec -T backend curl -fsS http://localhost:7101/health >/dev/null 2>&1 &&
           curl -fsS http://localhost:7141/ >/dev/null 2>&1; then
            log_info "服务就绪（${elapsed}s）"
            return 0
        fi
        sleep $interval
        elapsed=$((elapsed + interval))
        echo "  等待服务就绪... (${elapsed}s/${timeout}s)"
    done
    return 1
}

# 自动回滚（非交互）
auto_rollback() {
    echo ""
    echo "=== 自动回滚开始 ==="
    if [ -z "$PREV_BACKEND_ID" ]; then
        log_warn "无旧镜像可回滚（首次部署），跳过自动回滚"
        return 1
    fi

    log_info "1. 重新 tag 部署前镜像为 latest..."
    docker tag "$PREV_BACKEND_ID" clpm-backend:latest
    if [ -n "$PREV_FRONTEND_ID" ]; then
        docker tag "$PREV_FRONTEND_ID" clpm-frontend:latest
        log_info "  frontend 已 tag 回 $PREV_FRONTEND_ID"
    fi
    log_info "  backend 已 tag 回 $PREV_BACKEND_ID"

    log_info "2. 重启服务（使用旧镜像）..."
    compose_prod up -d

    log_info "3. 等待服务恢复（30s）..."
    sleep 30

    log_info "4. 验证回滚结果..."
    if compose_prod exec -T backend curl -fsS http://localhost:7101/health >/dev/null 2>&1; then
        log_info "[回滚成功] 已恢复到部署前版本"
        return 0
    else
        log_error "[回滚失败] 回滚后健康检查仍失败，需人工介入"
        log_error "诊断命令："
        log_error "  docker compose --env-file $ENV_FILE -f $COMPOSE_FILE logs backend --tail 50"
        log_error "  docker compose --env-file $ENV_FILE -f $COMPOSE_FILE ps"
        return 1
    fi
}

# 生成排查清单（按失败步骤）
generate_troubleshooting_checklist() {
    local step="$1"
    local line_no="${2:-unknown}"
    local exit_code="${3:-unknown}"

    echo ""
    echo "=== 部署失败排查清单 ==="
    echo "失败步骤: $step"
    echo "行号: $line_no | 退出码: $exit_code"
    echo "时间: $(date '+%Y-%m-%d %H:%M:%S')"
    echo ""
    echo "排查清单已保存到: $TROUBLESHOOT_FILE"

    # 写入文件头部
    cat > "$TROUBLESHOOT_FILE" <<CHECKLIST_EOF
# CLPM 部署失败排查清单

- **失败步骤**: $step
- **行号**: $line_no
- **退出码**: $exit_code
- **时间**: $(date '+%Y-%m-%d %H:%M:%S')
- **部署开始**: $DEPLOY_START_TIME

CHECKLIST_EOF

    # 按步骤输出排查内容
    case "$step" in
        "环境配置检查")
            cat >> "$TROUBLESHOOT_FILE" <<'EOF'
## 常见原因

1. **.env.prod 文件不存在**
   - 诊断: `ls -la .env.prod`
   - 修复: `cp .env.prod.example .env.prod`，然后修改配置

2. **JWT_SECRET_KEY 未设置或仍为占位符**
   - 诊断: `grep JWT_SECRET_KEY .env.prod`
   - 修复: `openssl rand -hex 32` 生成密钥，填入 .env.prod

3. **ENV 未设为 production**
   - 诊断: `grep '^ENV=' .env.prod`
   - 修复: 在 .env.prod 中添加 `ENV=production`
   - 原因: backend lifespan 依据 ENV 决定是否自动拉起 Celery；漏配会导致任务双消费

4. **密码字段仍为占位符**
   - 诊断: `grep -E '^(POSTGRES_PASSWORD|REDIS_PASSWORD|TDENGINE_PASSWORD)' .env.prod`
   - 修复: 将所有 `<change-me-*>` 占位符替换为真实密码
EOF
            ;;
        "镜像加载")
            cat >> "$TROUBLESHOOT_FILE" <<'EOF'
## 常见原因

1. **镜像包不存在或路径错误**
   - 诊断: `ls -la images/clpm-images-*.tar.gz`
   - 修复: 确认交付包完整，images/ 目录下有 .tar.gz 文件

2. **磁盘空间不足**
   - 诊断: `df -h`
   - 修复: 清理磁盘空间（`docker system prune -a`）或扩容

3. **镜像包损坏**
   - 诊断: `gzip -t images/clpm-images-*.tar.gz`（测试完整性）
   - 修复: 重新从开发机拷贝交付包

4. **Docker daemon 未运行**
   - 诊断: `docker info`
   - 修复: `systemctl start docker`
EOF
            ;;
        "启动服务")
            cat >> "$TROUBLESHOOT_FILE" <<'EOF'
## 常见原因

1. **端口冲突**
   - 诊断: `ss -tlnp | grep -E '7101|7141|5432|6379'`
   - 修复: 停止占用端口的进程，或修改 docker-compose.prod.yml 端口映射

2. **容器名冲突（上次部署未清理）**
   - 诊断: `docker ps -a | grep clpm`
   - 修复: `docker rm -f clpm-backend clpm-frontend clpm-postgres clpm-tdengine clpm-redis clpm-celery-worker clpm-celery-beat`

3. **卷挂载失败**
   - 诊断: `docker compose --env-file .env.prod -f docker-compose.prod.yml logs`
   - 修复: 检查 docker-compose.prod.yml 中 volumes 路径是否存在

4. **TDengine exit 255 崩溃循环**
   - 诊断: `docker logs clpm-tdengine 2>&1 | tail -20`
   - 修复: 确认 `.td-password-changed` 标记文件已创建（非首次部署时）
   - 详见: `docker volume inspect clpm_tdengine_data` 是否存在
EOF
            ;;
        "健康检查"|"最终验证")
            cat >> "$TROUBLESHOOT_FILE" <<'EOF'
## 常见原因

1. **后端启动崩溃**
   - 诊断: `docker compose --env-file .env.prod -f docker-compose.prod.yml logs backend --tail 50`
   - 常见: 数据库连接失败、Redis 连接失败、环境变量缺失

2. **前端 Nginx 配置错误**
   - 诊断: `docker compose --env-file .env.prod -f docker-compose.prod.yml logs frontend --tail 50`

3. **PostgreSQL 未就绪**
   - 诊断: `docker exec clpm-postgres pg_isready -U clpm`
   - 修复: 等待 PG 健康检查通过（start_period: 30s）

4. **Redis 未就绪**
   - 诊断: `docker exec clpm-redis redis-cli -a "$(grep REDIS_PASSWORD .env.prod | cut -d= -f2)" ping`

5. **Celery Worker 未就绪**
   - 诊断: `docker exec clpm-celery-worker celery -A app.tasks.celery_app inspect ping --timeout 10`

6. **Celery Beat 健康检查失败**
   - 诊断: `docker inspect -f '{{.State.Health.Status}}' clpm-celery-beat`
EOF
            ;;
        "数据库迁移")
            cat >> "$TROUBLESHOOT_FILE" <<'EOF'
## 常见原因

1. **PostgreSQL 未就绪**
   - 诊断: `docker exec clpm-postgres pg_isready -U clpm`

2. **Alembic 版本冲突（multiple heads）**
   - 诊断: `docker exec clpm-backend alembic heads`
   - 修复: 合并多个 head（`alembic merge -m "merge" head1 head2`）后重新部署

3. **迁移文件缺失**
   - 诊断: `docker exec clpm-backend alembic history --verbose | head -20`

4. **数据库被锁定**
   - 诊断: `docker exec clpm-postgres psql -U clpm -d clpm -c "SELECT * FROM pg_locks WHERE NOT granted;"`
   - 修复: 终止阻塞进程（`SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE state = 'idle in transaction';`）

5. **当前 Alembic 版本**
   - 诊断: `docker exec clpm-backend alembic current`
EOF
            ;;
        "TDengine schema 校验")
            cat >> "$TROUBLESHOOT_FILE" <<'EOF'
## 常见原因

1. **TDengine 容器不健康**
   - 诊断: `docker inspect -f '{{.State.Health.Status}}' clpm-tdengine`
   - 诊断: `docker logs clpm-tdengine 2>&1 | tail -30`

2. **exit 255 崩溃循环（密码问题）**
   - 原因: entrypoint 每次启动都用默认密码 taosdata 改密，卷持久化后密码已改导致认证失败
   - 诊断: `docker volume inspect clpm_tdengine_data`（卷是否存在）
   - 修复: 确认 `.td-password-changed` 标记文件已创建
   - 修复: `touch .td-password-changed && docker compose --env-file .env.prod -f docker-compose.prod.yml restart tdengine`

3. **REST API 不可达**
   - 诊断: `curl -s -u "root:$(grep TDENGINE_PASSWORD .env.prod | cut -d= -f2)" http://localhost:6041/rest/sql -d 'SHOW DATABASES'`

4. **手动创建 schema**
   - `curl -s -u "root:<password>" http://localhost:6041/rest/sql -d "CREATE DATABASE IF NOT EXISTS clpm_ts KEEP 365 DURATION 10 PRECISION 'ms'"`
   - `curl -s -u "root:<password>" http://localhost:6041/rest/sql/clpm_ts -d "CREATE STABLE IF NOT EXISTS st_loop_data (ts TIMESTAMP, pv FLOAT, sp FLOAT, op FLOAT, mode TINYINT, pid_p FLOAT, pid_i FLOAT, pid_d FLOAT, pv_quality TINYINT) TAGS (loop_id BINARY(36), unit_id BINARY(36))"`
EOF
            ;;
        *)
            cat >> "$TROUBLESHOOT_FILE" <<'EOF'
## 通用排查

1. **查看所有容器状态**
   `docker compose --env-file .env.prod -f docker-compose.prod.yml ps`

2. **查看所有服务日志**
   `docker compose --env-file .env.prod -f docker-compose.prod.yml logs --tail 50`

3. **Docker 系统状态**
   `docker info && docker system df`

4. **磁盘空间**
   `df -h`
EOF
            ;;
    esac

    # 追加通用信息
    cat >> "$TROUBLESHOOT_FILE" <<CHECKLIST_EOF

## 通用运维命令

\`\`\`bash
# 查看实时日志
docker compose --env-file .env.prod -f docker-compose.prod.yml logs -f

# 查看容器状态
docker compose --env-file .env.prod -f docker-compose.prod.yml ps

# 重启单个服务
docker compose --env-file .env.prod -f docker-compose.prod.yml restart backend

# 手动回滚
./deploy/rollback.sh
\`\`\`

## 回滚状态

CHECKLIST_EOF

    if [ "$SERVICES_STARTED" = "true" ] && [ -n "$PREV_BACKEND_ID" ]; then
        echo "自动回滚已执行。如回滚后仍有问题，请手动检查：" >> "$TROUBLESHOOT_FILE"
        echo "- 旧 backend Image ID: $PREV_BACKEND_ID" >> "$TROUBLESHOOT_FILE"
        echo "- 旧 frontend Image ID: $PREV_FRONTEND_ID" >> "$TROUBLESHOOT_FILE"
    elif [ "$SERVICES_STARTED" = "true" ]; then
        echo "服务已启动但首次部署无旧镜像可回滚。" >> "$TROUBLESHOOT_FILE"
        echo "建议检查日志定位问题，修复后重新执行部署。" >> "$TROUBLESHOOT_FILE"
    else
        echo "服务未启动，无需回滚。" >> "$TROUBLESHOOT_FILE"
    fi

    # 打印到控制台
    cat "$TROUBLESHOOT_FILE"
}

# ERR trap 处理器
on_error_trap() {
    local line_no=$1
    local exit_code=$2
    echo ""
    log_error "部署失败（步骤: $CURRENT_STEP，行号: $line_no，退出码: $exit_code）"
    generate_troubleshooting_checklist "$CURRENT_STEP" "$line_no" "$exit_code"
    if [ "$SERVICES_STARTED" = "true" ]; then
        auto_rollback
    fi
    echo ""
    log_error "排查清单: $TROUBLESHOOT_FILE"
}

# 注册 ERR trap
trap 'on_error_trap $LINENO $?' ERR

# ============================================================
# 主流程
# ============================================================

echo "=== CLPM 客户服务器部署 ==="
echo "开始时间: $DEPLOY_START_TIME"
echo "部署目录: $SCRIPT_DIR"
echo ""

# ------------------------------------------------------------
# Step 1: 环境配置检查
# ------------------------------------------------------------
CURRENT_STEP="环境配置检查"
log_step "Step 1: $CURRENT_STEP"

if [ ! -f "$ENV_FILE" ]; then
    log_error "$ENV_FILE 文件不存在"
    log_error "请执行: cp .env.prod.example .env.prod"
    log_error "然后修改 .env.prod 中的占位符为真实配置"
    exit 1
fi

# CORS 自动检测
ENV_TEMPLATE=".env.prod.example"
if grep -q '"__AUTO__"' "$ENV_TEMPLATE" 2>/dev/null; then
    DETECTED_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
    if [ -n "$DETECTED_IP" ]; then
        log_info "检测到本机 IP: $DETECTED_IP"
        NEW_CORS_LINE=$(grep -E '^CORS_ORIGINS=' "$ENV_TEMPLATE" | sed "s|\"__AUTO__\"|\"http://$DETECTED_IP:7141\"|g")
        sed -i "s|^CORS_ORIGINS=.*|$NEW_CORS_LINE|" "$ENV_FILE"
        log_info "CORS_ORIGINS 已更新"
    fi
fi

# JWT_SECRET_KEY 校验
JWT_SECRET_KEY=$(grep -E "^JWT_SECRET_KEY=" "$ENV_FILE" | cut -d'=' -f2-)
if [ -z "$JWT_SECRET_KEY" ] || [ "$JWT_SECRET_KEY" = "<generate-with-openssl-rand-hex-32>" ]; then
    log_error "JWT_SECRET_KEY 未设置或仍为占位符"
    exit 1
fi
if [ ${#JWT_SECRET_KEY} -lt 32 ]; then
    log_error "JWT_SECRET_KEY 长度不足 32 字符（当前 ${#JWT_SECRET_KEY} 字符）"
    exit 1
fi

# 密码字段校验
check_required_no_placeholder() {
    local var_name="$1"
    local var_value
    var_value=$(grep -E "^${var_name}=" "$ENV_FILE" | cut -d'=' -f2-)
    if [ -z "$var_value" ]; then
        log_error "$var_name 未设置"
        exit 1
    fi
    if [[ "$var_value" == *"<"*">"* ]]; then
        log_error "${var_name} 仍为占位符（${var_value}）"
        exit 1
    fi
}

check_no_placeholder() {
    local var_name="$1"
    local var_value
    var_value=$(grep -E "^${var_name}=" "$ENV_FILE" | cut -d'=' -f2-)
    if [[ "$var_value" == *"<"*">"* ]]; then
        log_error "${var_name} 仍为占位符（${var_value}）"
        exit 1
    fi
}

check_required_no_placeholder "POSTGRES_PASSWORD"
check_required_no_placeholder "REDIS_PASSWORD"
check_required_no_placeholder "TDENGINE_PASSWORD"

# ENV=production 强制校验
ENV_VALUE=$(grep -E "^ENV=" "$ENV_FILE" | cut -d'=' -f2- | tr '[:upper:]' '[:lower:]' || true)
if [ "$ENV_VALUE" != "production" ]; then
    log_error ".env.prod 必须设置 ENV=production（当前: ${ENV_VALUE:-未设置}）"
    exit 1
fi

# 可选配置校验
SIGNALR_ENABLED=$(grep -E "^SIGNALR_ENABLED=" "$ENV_FILE" | cut -d'=' -f2- | tr '[:upper:]' '[:lower:]')
if [ "$SIGNALR_ENABLED" = "true" ]; then
    check_required_no_placeholder "SIGNALR_HUB_URL"
fi
check_no_placeholder "HISTORY_DATA_API_URL"
check_no_placeholder "HISTORY_DATA_API_TOKEN"

# Docker 检查
if ! command -v docker >/dev/null 2>&1; then
    log_error "未安装 Docker"
    exit 1
fi
log_info "Docker 版本: $(docker --version)"
log_info "环境配置检查通过"

# Compose 辅助函数
COMPOSE_PROFILE_ARGS=(--profile tdengine)
compose_prod() {
    docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "${COMPOSE_PROFILE_ARGS[@]}" "$@"
}
backend_exec() { compose_prod exec -T backend "$@"; }
tdengine_exec() { compose_prod exec -T tdengine "$@"; }
postgres_exec() { compose_prod exec -T postgres "$@"; }

# ------------------------------------------------------------
# Step 2: 记录部署前镜像 ID（回滚锚点）
# ------------------------------------------------------------
CURRENT_STEP="记录部署前镜像 ID"
log_step "Step 2: $CURRENT_STEP"
record_pre_deploy_images

# ------------------------------------------------------------
# Step 3: 从 tarball 加载镜像
# ------------------------------------------------------------
CURRENT_STEP="镜像加载"
log_step "Step 3: $CURRENT_STEP"
load_images

# ------------------------------------------------------------
# Step 4: TDengine 密码标记文件（非首次部署）
# ------------------------------------------------------------
CURRENT_STEP="TDengine 密码标记"
log_step "Step 4: $CURRENT_STEP"
if docker volume inspect clpm_tdengine_data >/dev/null 2>&1; then
    touch "$SCRIPT_DIR/.td-password-changed"
    log_info "既有卷检测到，创建 .td-password-changed 标记文件（跳过 ALTER USER）"
else
    log_info "首次部署（卷不存在），entrypoint 正常改密"
fi

# ------------------------------------------------------------
# Step 5: 部署前自动备份（升级场景）
# ------------------------------------------------------------
CURRENT_STEP="部署前备份"
log_step "Step 5: $CURRENT_STEP"
if [ "$SKIP_BACKUP" = true ]; then
    log_warn "已通过 --skip-backup 跳过部署前备份"
elif docker ps --format '{{.Names}}' 2>/dev/null | grep -q '^clpm-postgres$'; then
    log_info "检测到运行中的既有部署，执行备份..."
    bash deploy/backup.sh
    log_info "备份完成"
else
    log_info "首次部署（无运行中容器），跳过备份"
fi

# ------------------------------------------------------------
# Step 6: 启动服务
# ------------------------------------------------------------
CURRENT_STEP="启动服务"
log_step "Step 6: $CURRENT_STEP"

# 停止旧服务并清理残留容器
log_info "停止旧服务..."
compose_prod down --remove-orphans 2>/dev/null || true
for c in clpm-backend clpm-frontend clpm-postgres clpm-tdengine clpm-redis clpm-celery-worker clpm-celery-beat; do
    docker rm -f "$c" 2>/dev/null || true
done

log_info "启动新服务..."
compose_prod up -d
SERVICES_STARTED=true
log_info "服务已启动"

# ------------------------------------------------------------
# Step 7: 健康检查重试（30s）
# ------------------------------------------------------------
CURRENT_STEP="健康检查"
log_step "Step 7: $CURRENT_STEP"
if ! wait_for_health; then
    log_error "健康检查超时（${HEALTH_TIMEOUT}s），触发自动回滚"
    auto_rollback
    exit 1
fi
log_info "后端 API 和前端 Nginx 均已就绪"

# ------------------------------------------------------------
# Step 8: 数据库迁移（Alembic）
# ------------------------------------------------------------
CURRENT_STEP="数据库迁移"
log_step "Step 8: $CURRENT_STEP"
alembic_sync_head
log_info "数据库版本同步完成"

# ------------------------------------------------------------
# Step 9: TDengine schema 校验
# ------------------------------------------------------------
CURRENT_STEP="TDengine schema 校验"
log_step "Step 9: $CURRENT_STEP"
tdengine_ensure_schema
log_info "TDengine schema 校验通过"

# ------------------------------------------------------------
# Step 10: 最终验证
# ------------------------------------------------------------
CURRENT_STEP="最终验证"
log_step "Step 10: $CURRENT_STEP"

log_info "容器运行状态:"
compose_prod ps

# 后端 API
if compose_prod exec -T backend curl -fsS http://localhost:7101/health 2>/dev/null | grep -q '"ok"'; then
    log_info "[OK] 后端 API 健康"
else
    log_error "[FAIL] 后端 API 健康检查失败"
    auto_rollback
    exit 1
fi

# 前端
if curl -fsS http://localhost:7141/ >/dev/null 2>&1; then
    log_info "[OK] 前端服务健康"
else
    log_error "[FAIL] 前端服务健康检查失败"
    auto_rollback
    exit 1
fi

# Celery Worker
if compose_prod exec -T celery-worker celery -A app.tasks.celery_app inspect ping --timeout 10 2>/dev/null | grep -q pong; then
    log_info "[OK] Celery Worker 健康"
else
    log_warn "[WARN] Celery Worker 健康检查超时（可能正在预载，非阻断）"
fi

# Celery Beat
BEAT_HEALTH=$(docker inspect -f '{{.State.Health.Status}}' clpm-celery-beat 2>/dev/null || echo "unknown")
if [ "$BEAT_HEALTH" = "healthy" ]; then
    log_info "[OK] Celery Beat 健康"
else
    log_warn "[WARN] Celery Beat 状态: $BEAT_HEALTH（可能正在启动，非阻断）"
fi

# ------------------------------------------------------------
# 部署完成
# ------------------------------------------------------------
echo ""
echo "=== 部署完成 ==="
SERVER_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
if [ -n "$SERVER_IP" ]; then
    ACCESS_URL="http://$SERVER_IP:7141"
else
    ACCESS_URL="http://localhost:7141"
fi
echo "服务访问地址:  $ACCESS_URL"
echo "默认账号:      admin / admin123（首次登录后请立即修改密码）"
echo ""
echo "常用运维命令:"
echo "  查看日志:  docker compose --env-file .env.prod -f docker-compose.prod.yml logs -f"
echo "  查看状态:  docker compose --env-file .env.prod -f docker-compose.prod.yml ps"
echo "  数据备份:  ./deploy/backup.sh"
echo "  数据回滚:  ./deploy/rollback.sh"
echo ""
echo "部署开始: $DEPLOY_START_TIME"
echo "部署完成: $(date '+%Y-%m-%d %H:%M:%S')"
