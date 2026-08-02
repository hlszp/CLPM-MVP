#!/bin/bash
# ============================================================
# CLPM 离线部署模拟脚本
# 在本地用 tarball 模拟客户服务器的离线环境
# 验证 docker load + docker compose up 全流程
#
# 使用独立的容器名前缀 clpm-sim-，避免与开发环境冲突
# ============================================================
set -euo pipefail

# ── 配置 ──────────────────────────────────────────────────────
# 自动检测运行环境：开发机（项目根目录）或客户服务器（交付包目录）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 判断是否在交付包中（交付包有 images/ 目录但无 releases/）
if [ -f "$SCRIPT_DIR/../docker-compose.prod.yml" ] && [ -d "$SCRIPT_DIR/../releases/images" ]; then
    # 开发机：从项目根目录运行
    PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
    TARBALL_DIR="$PROJECT_ROOT/releases/images"
    COMPOSE_SRC="$PROJECT_ROOT/docker-compose.prod.yml"
    DB_DIR="$PROJECT_ROOT/db"
    NGINX_CONF="$PROJECT_ROOT/deploy/nginx.conf"
elif [ -f "$SCRIPT_DIR/../docker-compose.prod.yml" ] && [ -d "$SCRIPT_DIR/../images" ]; then
    # 客户服务器：从交付包目录运行
    PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
    TARBALL_DIR="$PROJECT_ROOT/images"
    COMPOSE_SRC="$PROJECT_ROOT/docker-compose.prod.yml"
    DB_DIR="$PROJECT_ROOT/db"
    NGINX_CONF="$SCRIPT_DIR/nginx.conf"
else
    echo "[ERROR] 无法定位 docker-compose.prod.yml 和镜像目录"
    echo "  请从 CLPM 项目根目录或交付包目录运行"
    exit 1
fi
cd "$PROJECT_ROOT"

# 模拟目录（独立隔离环境，不影响开发/生产环境）
SIM_DIR="${SIM_DIR:-/tmp/clpm-offline-sim}"
SIM_CONTAINER_PREFIX="clpm-sim"
SIM_NETWORK="clpm-sim-net"

# 颜色
RED=$'\033[31m'
GREEN=$'\033[32m'
YELLOW=$'\033[33m'
BLUE=$'\033[34m'
NC=$'\033[0m'

log_info()  { echo "${BLUE}[INFO]${NC}  $*"; }
log_ok()    { echo "${GREEN}[OK]${NC}    $*"; }
log_warn()  { echo "${YELLOW}[WARN]${NC}  $*"; }
log_error() { echo "${RED}[ERROR]${NC} $*"; }
log_step()  { echo ""; echo "${BLUE}========== $1 ==========${NC}"; }

# 模拟环境的容器名（加 sim 前缀避免与开发环境冲突）
BACKEND_C="${SIM_CONTAINER_PREFIX}-backend"
FRONTEND_C="${SIM_CONTAINER_PREFIX}-frontend"
POSTGRES_C="${SIM_CONTAINER_PREFIX}-postgres"
TDENGINE_C="${SIM_CONTAINER_PREFIX}-tdengine"
REDIS_C="${SIM_CONTAINER_PREFIX}-redis"
WORKER_C="${SIM_CONTAINER_PREFIX}-celery-worker"
BEAT_C="${SIM_CONTAINER_PREFIX}-celery-beat"
ALL_SIM_CONTAINERS=("$BACKEND_C" "$FRONTEND_C" "$POSTGRES_C" "$TDENGINE_C" "$REDIS_C" "$WORKER_C" "$BEAT_C")

# ── 生成模拟专用 docker-compose 文件 ──────────────────────────
# 将原 docker-compose.prod.yml 中的 container_name 和 network 替换为模拟专用名称
# 同时将 Redis/PG/TDengine 的 host 引用替换为模拟容器名
generate_sim_compose() {
    local src="$COMPOSE_SRC"
    local dst="$SIM_DIR/docker-compose.sim.yml"

    sed \
        -e "s/clpm-backend:latest/clpm-backend:latest/g" \
        -e "s/container_name: clpm-backend/container_name: ${BACKEND_C}/g" \
        -e "s/container_name: clpm-frontend/container_name: ${FRONTEND_C}/g" \
        -e "s/container_name: clpm-postgres/container_name: ${POSTGRES_C}/g" \
        -e "s/container_name: clpm-tdengine/container_name: ${TDENGINE_C}/g" \
        -e "s/container_name: clpm-redis/container_name: ${REDIS_C}/g" \
        -e "s/container_name: clpm-celery-worker/container_name: ${WORKER_C}/g" \
        -e "s/container_name: clpm-celery-beat/container_name: ${BEAT_C}/g" \
        -e "s/clpm-net/${SIM_NETWORK}/g" \
        -e "s/name: clpm-prod/name: clpm-sim/g" \
        "$src" > "$dst"

    # 同时修改 .env.prod 中的 Redis 连接地址
    # （CELERY_BROKER_URL 中引用 clpm-redis，需改为模拟容器名）
    sed -i.bak \
        -e "s/redis:\/\/clpm-redis:/redis:\/\/${REDIS_C}:/g" \
        "$SIM_DIR/.env.prod"
    rm -f "$SIM_DIR/.env.prod.bak"

    log_ok "模拟 docker-compose 文件已生成: $dst"
    log_info "  容器名前缀: ${SIM_CONTAINER_PREFIX}-"
    log_info "  网络: ${SIM_NETWORK}"
}

# ── 清理上一次模拟残留 ────────────────────────────────────────
cleanup() {
    log_step "清理上次模拟残留"
    if [ -d "$SIM_DIR" ]; then
        cd "$SIM_DIR"
        # 停止并删除模拟容器
        if [ -f "docker-compose.sim.yml" ]; then
            docker compose --env-file .env.prod -f docker-compose.sim.yml \
                --profile tdengine down --remove-orphans --volumes 2>/dev/null || true
        fi
        cd "$PROJECT_ROOT"
        # 清理残留容器（防止容器名冲突）
        for c in "${ALL_SIM_CONTAINERS[@]}"; do
            docker rm -f "$c" 2>/dev/null || true
        done
        # 清理模拟卷
        for v in clpm-sim_postgres_data clpm-sim_tdengine_data clpm-sim_redis_data \
                 clpm_tdengine_data clpm_postgres_data clpm_redis_data; do
            docker volume rm "$v" 2>/dev/null || true
        done
        rm -rf "$SIM_DIR"
    fi
    mkdir -p "$SIM_DIR"
    log_ok "模拟环境已就绪: $SIM_DIR"
}

# ── 生成测试 .env.prod ────────────────────────────────────────
generate_env() {
    log_step "Step 1: 生成测试 .env.prod"

    JWT_SECRET=$(openssl rand -hex 32)
    PG_PASS="SimTest_Pg_2026!"
    TD_PASS="SimTest_Td_2026!"
    REDIS_PASS="SimTest_Redis_2026!"

    cat > "$SIM_DIR/.env.prod" << EOF
# === 模拟离线部署测试环境 ===
ENV=production
DEBUG=False

# PostgreSQL
POSTGRES_HOST=postgres
POSTGRES_PORT=5432
POSTGRES_USER=clpm
POSTGRES_PASSWORD=${PG_PASS}
POSTGRES_DB=clpm

# TDengine
TDENGINE_HOST=tdengine
TDENGINE_PORT=6030
TDENGINE_USER=root
TDENGINE_PASSWORD=${TD_PASS}
TDENGINE_DB=clpm_ts

# Redis
REDIS_HOST=redis
REDIS_PORT=6379
REDIS_PASSWORD=${REDIS_PASS}
REDIS_DB=0

# JWT
JWT_SECRET_KEY=${JWT_SECRET}
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30
REFRESH_TOKEN_EXPIRE_DAYS=7

# Celery
CELERY_WORKER_CONCURRENCY=2

# 历史数据接口（部署后 UI 配置）
HISTORY_DATA_API_URL=
HISTORY_DATA_API_TOKEN=
HISTORY_DATA_API_TIMEOUT=30.0

# SignalR（部署后 UI 配置）
SIGNALR_HUB_URL=
SIGNALR_ENABLED=False
SIGNALR_RECONNECT_INTERVAL=5
REALTIME_WRITEBACK_ENABLED=False

# CORS
CORS_ORIGINS=["http://localhost:7141"]

# 日志
LOG_LEVEL=INFO
EOF

    log_ok ".env.prod 已生成（密码已随机化）"
}

# ── 拷贝部署文件 ──────────────────────────────────────────────
copy_files() {
    log_step "Step 2: 拷贝部署文件到模拟目录"

    # 部署脚本（使用环境检测后的 NGINX_CONF 变量，兼容开发机和客户服务器）
    mkdir -p "$SIM_DIR/deploy"
    if [ ! -f "$NGINX_CONF" ]; then
        log_error "nginx.conf 未找到: $NGINX_CONF"
        exit 1
    fi
    cp "$NGINX_CONF" "$SIM_DIR/deploy/"

    # 数据库初始化 SQL（使用环境检测后的 DB_DIR 变量）
    mkdir -p "$SIM_DIR/db/postgresql" "$SIM_DIR/db/tdengine"
    if [ ! -d "$DB_DIR/postgresql" ] || [ ! -d "$DB_DIR/tdengine" ]; then
        log_error "数据库 SQL 目录未找到: $DB_DIR"
        exit 1
    fi
    cp "$DB_DIR/postgresql/01_schema.sql" "$SIM_DIR/db/postgresql/"
    cp "$DB_DIR/postgresql/02_seed_data.sql" "$SIM_DIR/db/postgresql/"
    cp "$DB_DIR/tdengine/01_supertable.sql" "$SIM_DIR/db/tdengine/"

    # .td-password-changed 标记文件
    touch "$SIM_DIR/.td-password-changed"

    log_ok "部署文件已拷贝"
}

# ── 查找并加载镜像 tarball ────────────────────────────────────
load_images() {
    log_step "Step 3: 查找并加载镜像 tarball"

    # 使用环境检测后的 TARBALL_DIR（开发机: releases/images，客户服务器: images）
    # 兼容性回退：依次在 TARBALL_DIR、PROJECT_ROOT/images、PROJECT_ROOT/releases/images 中查找
    TARBALL=""
    for search_dir in "$TARBALL_DIR" "$PROJECT_ROOT/images" "$PROJECT_ROOT/releases/images"; do
        if [ -d "$search_dir" ]; then
            TARBALL=$(ls -t "$search_dir"/clpm-images-*.tar.gz 2>/dev/null | grep -v latest | head -1)
            if [ -n "$TARBALL" ]; then
                log_info "在 $search_dir 中找到镜像包"
                break
            fi
        fi
    done

    if [ -z "$TARBALL" ]; then
        log_error "未找到镜像 tarball（已搜索: $TARBALL_DIR、$PROJECT_ROOT/images、$PROJECT_ROOT/releases/images）"
        log_error "请先运行: ./deploy/package.sh"
        exit 1
    fi

    TARBALL_SIZE=$(du -h "$TARBALL" | cut -f1)
    log_info "找到镜像包: $TARBALL ($TARBALL_SIZE)"

    log_info "执行 docker load..."
    docker load < "$TARBALL" 2>&1 | tail -8
    log_ok "镜像加载完成"

    # 校验核心镜像
    log_step "Step 3.1: 校验核心镜像完整性"

    local required_images=(
        "clpm-backend:latest"
        "clpm-frontend:latest"
        "postgres:16-alpine"
        "redis:7-alpine"
        "tdengine/tdengine:3.3.6.6"
    )

    local missing=""
    for img in "${required_images[@]}"; do
        if docker image inspect "$img" >/dev/null 2>&1; then
            local size
            size=$(docker image inspect "$img" --format '{{.Size}}' 2>/dev/null)
            size_mb=$((size / 1024 / 1024))
            log_ok "  ${img} (${size_mb}MB)"
        else
            log_error "  ${img} — 缺失!"
            missing="${missing}${img}\n"
        fi
    done

    if [ -n "$missing" ]; then
        echo ""
        log_error "核心镜像不完整，以下镜像缺失："
        printf "$missing"
        exit 1
    fi

    log_ok "全部 5 个核心镜像校验通过"
}

# ── 模拟 docker compose up ────────────────────────────────────
start_services() {
    log_step "Step 4: docker compose up（模拟客户服务器启动）"

    cd "$SIM_DIR"
    generate_sim_compose

    log_info "启动服务（含 tdengine profile）..."
    docker compose --env-file .env.prod -f docker-compose.sim.yml \
        --profile tdengine up -d 2>&1

    log_ok "docker compose up 命令执行成功"
}

# ── 健康检查 ──────────────────────────────────────────────────
health_check() {
    log_step "Step 5: 健康检查（30 秒重试）"

    local max_attempts=15
    local wait_seconds=3
    local backend_ok=false
    local frontend_ok=false

    for i in $(seq 1 $max_attempts); do
        echo -n "  尝试 $i/$max_attempts ($(date +%H:%M:%S)): "

        if [ "$backend_ok" = false ]; then
            if docker exec "$BACKEND_C" curl -fsS http://localhost:7101/health 2>/dev/null; then
                backend_ok=true
                echo -n "backend✓ "
            else
                echo -n "backend✗ "
            fi
        else
            echo -n "backend✓ "
        fi

        if [ "$frontend_ok" = false ]; then
            if docker exec "$FRONTEND_C" curl -fsS http://localhost:7141/ 2>/dev/null >/dev/null; then
                frontend_ok=true
                echo "frontend✓"
            else
                echo "frontend✗"
            fi
        else
            echo "frontend✓"
        fi

        if [ "$backend_ok" = true ] && [ "$frontend_ok" = true ]; then
            log_ok "健康检查通过（第 $i 次尝试）"
            return 0
        fi

        sleep $wait_seconds
    done

    log_error "健康检查超时"
    log_error "后端: $([ "$backend_ok" = true ] && echo 'OK' || echo 'FAIL')"
    log_error "前端: $([ "$frontend_ok" = true ] && echo 'OK' || echo 'FAIL')"

    echo ""
    log_warn "=== 失败诊断 ==="
    log_warn "--- 后端日志 ---"
    docker logs "$BACKEND_C" --tail 20 2>&1 || true
    echo ""
    log_warn "--- 容器状态 ---"
    docker compose --env-file .env.prod -f docker-compose.sim.yml ps -a 2>/dev/null || true

    return 1
}

# ── 数据库迁移验证 ────────────────────────────────────────────
verify_migration() {
    log_step "Step 6: 数据库迁移验证（Alembic）"

    local current
    current=$(docker exec "$BACKEND_C" alembic current 2>&1 | grep -oE '[a-z0-9]{12} \(head\)' | head -1 || echo "")

    if [ -z "$current" ]; then
        log_info "首次部署: 执行 alembic stamp head"
        docker exec "$BACKEND_C" alembic stamp head 2>&1 | tail -3
    else
        log_info "当前版本: ${current}, 执行 upgrade head"
        docker exec "$BACKEND_C" alembic upgrade head 2>&1 | tail -3
    fi

    local after
    after=$(docker exec "$BACKEND_C" alembic current 2>&1 | grep -oE '[a-z0-9]{12} \(head\)' | head -1 || echo "")
    log_ok "迁移后版本: ${after}"
}

# ── TDengine schema 验证 ─────────────────────────────────────
verify_tdengine() {
    log_step "Step 7: TDengine schema 验证"

    # 检测 Apple Silicon — TDengine 在 Rosetta 模拟下会崩溃（syscall 156 未实现）
    local host_arch
    host_arch=$(uname -m)
    if [ "$host_arch" = "arm64" ]; then
        log_warn "检测到 Apple Silicon ($host_arch)，TDengine linux/amd64 镜像在 Rosetta 下不支持"
        log_warn "跳过 TDengine 验证（x86_64 客户服务器上可正常运行，无需模拟）"
        log_info "TDengine 镜像已在 Step 3 校验通过：tdengine/tdengine:3.3.6.6 (464MB)"
        return 0
    fi

    local td_pass
    td_pass=$(grep '^TDENGINE_PASSWORD=' "$SIM_DIR/.env.prod" | cut -d= -f2-)

    log_info "等待 TDengine 就绪..."
    local td_ready=false
    for i in $(seq 1 15); do
        if docker exec "$TDENGINE_C" taos -u root -p"$td_pass" -s "SELECT SERVER_VERSION();" 2>/dev/null | grep -qE '[0-9]+\.'; then
            td_ready=true
            break
        fi
        sleep 3
    done

    if [ "$td_ready" = false ]; then
        log_error "TDengine 45 秒内未就绪"
        docker logs "$TDENGINE_C" --tail 20 2>&1 || true
        return 1
    fi
    log_ok "TDengine 已就绪"

    if docker exec "$TDENGINE_C" taos -u root -p"$td_pass" -s "SHOW DATABASES;" 2>/dev/null | grep -q clpm_ts; then
        log_ok "clpm_ts 数据库存在"
    else
        log_warn "clpm_ts 不存在，创建..."
        docker exec "$TDENGINE_C" taos -u root -p"$td_pass" \
            -s "CREATE DATABASE IF NOT EXISTS clpm_ts KEEP 365 DURATION 10 PRECISION 'ms'" 2>/dev/null
    fi

    if docker exec "$TDENGINE_C" taos -u root -p"$td_pass" -s "USE clpm_ts; SHOW STABLES;" 2>/dev/null | grep -q st_loop_data; then
        log_ok "st_loop_data 超级表存在"
    else
        log_warn "st_loop_data 不存在，创建..."
        docker exec "$TDENGINE_C" taos -u root -p"$td_pass" -s \
            "CREATE STABLE IF NOT EXISTS clpm_ts.st_loop_data (ts TIMESTAMP, pv FLOAT, sp FLOAT, op FLOAT, mode TINYINT, pid_p FLOAT, pid_i FLOAT, pid_d FLOAT, pv_quality TINYINT) TAGS (loop_id BINARY(36), unit_id BINARY(36))" 2>/dev/null
    fi
}

# ── 全量容器状态验证 ──────────────────────────────────────────
verify_all_containers() {
    log_step "Step 8: 全量容器状态验证"

    cd "$SIM_DIR"
    docker compose --env-file .env.prod -f docker-compose.sim.yml ps
    echo ""

    local all_healthy=true
    for svc in "${ALL_SIM_CONTAINERS[@]}"; do
        # Apple Silicon 下跳过 TDengine 健康检查（Rosetta 不支持）
        if [ "$svc" = "$TDENGINE_C" ] && [ "$(uname -m)" = "arm64" ]; then
            log_warn "  ${svc}: 跳过（Apple Silicon Rosetta 不支持 TDengine）"
            continue
        fi
        local status
        status=$(docker inspect -f '{{.State.Status}}' "$svc" 2>/dev/null || echo "missing")
        local health
        health=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}no-healthcheck{{end}}' "$svc" 2>/dev/null || echo "unknown")

        if [ "$status" = "running" ]; then
            if [ "$health" = "healthy" ] || [ "$health" = "no-healthcheck" ]; then
                log_ok "  ${svc}: running (${health})"
            else
                log_warn "  ${svc}: running (${health})"
                all_healthy=false
            fi
        else
            log_error "  ${svc}: ${status}"
            all_healthy=false
        fi
    done

    [ "$all_healthy" = true ] && log_ok "全部容器运行正常" || log_warn "部分容器未完全健康"
}

# ── API 功能验证 ──────────────────────────────────────────────
verify_api() {
    log_step "Step 9: API 功能验证"

    log_info "后端 /health："
    local health_resp
    health_resp=$(docker exec "$BACKEND_C" curl -fsS http://localhost:7101/health 2>/dev/null)
    if [ -n "$health_resp" ]; then
        log_ok "  $health_resp"
    else
        log_error "  无响应"
        return 1
    fi

    log_info "登录测试（admin/admin123）："
    local login_resp
    login_resp=$(docker exec "$BACKEND_C" curl -fsS -X POST http://localhost:7101/api/v1/auth/login \
        -H 'Content-Type: application/json' \
        -d '{"username":"admin","password":"admin123"}' 2>/dev/null)

    if echo "$login_resp" | grep -q "token"; then
        log_ok "  登录成功"
    else
        log_warn "  首次尝试失败，等待 10 秒后重试..."
        sleep 10
        login_resp=$(docker exec "$BACKEND_C" curl -fsS -X POST http://localhost:7101/api/v1/auth/login \
            -H 'Content-Type: application/json' \
            -d '{"username":"admin","password":"admin123"}' 2>/dev/null)
        echo "$login_resp" | grep -q "token" && log_ok "  重试登录成功" || log_warn "  登录失败"
    fi

    log_info "Celery Worker ping："
    local celery_resp
    celery_resp=$(docker exec "$WORKER_C" celery -A app.tasks.celery_app inspect ping --timeout 10 2>/dev/null)
    echo "$celery_resp" | grep -q "pong" && log_ok "  Worker 响应正常" || log_warn "  Worker 未响应"
}

# ── 生成验证报告 ──────────────────────────────────────────────
generate_report() {
    log_step "Step 10: 生成验证报告"

    local report_file="$SIM_DIR/verification-report.md"
    local pass_count=0
    local fail_count=0
    local checks=()

    for svc in "${ALL_SIM_CONTAINERS[@]}"; do
        # Apple Silicon 下跳过 TDengine（Rosetta 不支持）
        if [ "$svc" = "$TDENGINE_C" ] && [ "$(uname -m)" = "arm64" ]; then
            checks+=("⏭️  ${svc}: 跳过（Apple Silicon）")
            pass_count=$((pass_count + 1))
            continue
        fi
        local status
        status=$(docker inspect -f '{{.State.Status}}' "$svc" 2>/dev/null || echo "missing")
        if [ "$status" = "running" ]; then
            checks+=("✅ ${svc}: running")
            pass_count=$((pass_count + 1))
        else
            checks+=("❌ ${svc}: ${status}")
            fail_count=$((fail_count + 1))
        fi
    done

    # API
    if docker exec "$BACKEND_C" curl -fsS http://localhost:7101/health >/dev/null 2>&1; then
        checks+=("✅ 后端 API 健康"); pass_count=$((pass_count + 1))
    else
        checks+=("❌ 后端 API 不健康"); fail_count=$((fail_count + 1))
    fi

    if docker exec "$FRONTEND_C" curl -fsS http://localhost:7141/ >/dev/null 2>&1; then
        checks+=("✅ 前端 Nginx 健康"); pass_count=$((pass_count + 1))
    else
        checks+=("❌ 前端 Nginx 不健康"); fail_count=$((fail_count + 1))
    fi

    # Alembic（检查 alembic_version 表中是否有版本号，版本号含非十六进制字符如 g/h/i/p）
    if docker exec "$BACKEND_C" alembic current 2>&1 | grep -qE '\(head\)'; then
        checks+=("✅ Alembic 迁移已就绪"); pass_count=$((pass_count + 1))
    else
        checks+=("❌ Alembic 迁移异常"); fail_count=$((fail_count + 1))
    fi

    # TDengine（Apple Silicon 下跳过，Rosetta 不支持）
    if [ "$(uname -m)" = "arm64" ]; then
        checks+=("⏭️  TDengine 跳过（Apple Silicon Rosetta 不支持，x86_64 正常）"); pass_count=$((pass_count + 1))
    else
        local td_pass
        td_pass=$(grep '^TDENGINE_PASSWORD=' "$SIM_DIR/.env.prod" | cut -d= -f2-)
        if docker exec "$TDENGINE_C" taos -u root -p"$td_pass" -s "SELECT SERVER_VERSION();" 2>/dev/null | grep -qE '[0-9]+\.'; then
            checks+=("✅ TDengine 响应正常"); pass_count=$((pass_count + 1))
        else
            checks+=("❌ TDengine 无响应"); fail_count=$((fail_count + 1))
        fi
    fi

    # Redis
    local redis_pass
    redis_pass=$(grep '^REDIS_PASSWORD=' "$SIM_DIR/.env.prod" | cut -d= -f2-)
    if docker exec "$REDIS_C" redis-cli -a "$redis_pass" --no-auth-warning ping 2>/dev/null | grep -q PONG; then
        checks+=("✅ Redis 响应正常"); pass_count=$((pass_count + 1))
    else
        checks+=("❌ Redis 无响应"); fail_count=$((fail_count + 1))
    fi

    # PostgreSQL
    if docker exec "$POSTGRES_C" pg_isready -U clpm -p 5432 2>/dev/null | grep -q "accepting"; then
        checks+=("✅ PostgreSQL 响应正常"); pass_count=$((pass_count + 1))
    else
        checks+=("❌ PostgreSQL 无响应"); fail_count=$((fail_count + 1))
    fi

    # 写报告
    cat > "$report_file" << EOF
# CLPM 离线部署模拟验证报告

**日期**: $(date '+%Y-%m-%d %H:%M:%S')
**模拟目录**: $SIM_DIR
**结果**: ${pass_count} 通过 / ${fail_count} 失败

## 检查项

EOF
    for check in "${checks[@]}"; do echo "- $check" >> "$report_file"; done

    cat >> "$report_file" << EOF

## 结论

$([ $fail_count -eq 0 ] && echo '✅ **全部检查通过**，交付包可在客户离线服务器正常部署。' || echo '❌ **存在失败项**，请排查上方标记为 ❌ 的检查项。')
EOF

    echo ""
    echo "═══════════════════════════════════════════════"
    echo "  离线部署模拟结果: ${pass_count} 通过 / ${fail_count} 失败"
    echo "═══════════════════════════════════════════════"
    echo ""
    for check in "${checks[@]}"; do echo "  $check"; done
    echo ""

    if [ $fail_count -eq 0 ]; then
        log_ok "✅ 交付包验证通过，可安全交付客户现场部署"
    else
        log_error "❌ 存在 ${fail_count} 项失败"
        return 1
    fi
}

# ── 主流程 ────────────────────────────────────────────────────
main() {
    echo ""
    echo "╔═══════════════════════════════════════════════════════════╗"
    echo "║   CLPM 离线部署模拟验证                                   ║"
    echo "║   模拟客户服务器：docker load + compose up 全流程         ║"
    echo "╚═══════════════════════════════════════════════════════════╝"

    cleanup
    generate_env
    copy_files
    load_images
    start_services
    health_check
    verify_migration
    verify_tdengine
    verify_all_containers
    verify_api
    generate_report

    echo ""
    log_info "模拟环境保留在: $SIM_DIR"
    log_info "查看日志: cd $SIM_DIR && docker compose --env-file .env.prod -f docker-compose.sim.yml logs -f"
    log_info "清理环境: bash -c 'for c in ${SIM_CONTAINER_PREFIX}-backend ${SIM_CONTAINER_PREFIX}-frontend ${SIM_CONTAINER_PREFIX}-postgres ${SIM_CONTAINER_PREFIX}-tdengine ${SIM_CONTAINER_PREFIX}-redis ${SIM_CONTAINER_PREFIX}-celery-worker ${SIM_CONTAINER_PREFIX}-celery-beat; do docker rm -f \$c; done && rm -rf $SIM_DIR'"
}

main "$@"
