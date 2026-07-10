#!/bin/bash
# ============================================================
# CLPM Docker 镜像构建与部署脚本（离线镜像方式）
#
# 用途：在本地（macOS）构建 linux/amd64 镜像，传输到 zpdev 服务器并部署
# 服务器：192.168.13.113（zpdev）
#
# 用法：
#   # 构建并部署（前端+后端+数据库schema+nginx配置）
#   ./deploy/build-and-deploy.sh
#
#   # 仅构建镜像，不部署
#   ./deploy/build-and-deploy.sh --build-only
#
#   # 仅部署（镜像已构建好）
#   ./deploy/build-and-deploy.sh --deploy-only
#
#   # 跳过前端，只构建部署后端
#   ./deploy/build-and-deploy.sh --backend-only
#
#   # 跳过后端，只构建部署前端
#   ./deploy/build-and-deploy.sh --frontend-only
#
# 前置条件：
#   1. 本机已安装 Docker Desktop（启用 linux/amd64 跨平台构建）
#   2. 本机能 SSH 到 root@192.168.13.113（免密或交互式密码）
#   3. 服务器已安装 Docker 24+ 与 Docker Compose v2
#   4. 服务器已有 /opt/clpm 部署目录（含 .env.prod、db/、deploy/）
# ============================================================
set -euo pipefail

# ------------------------------------------------------------
# 配置
# ------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

SERVER_IP="192.168.13.113"
SERVER_USER="root"
SERVER_DEPLOY_DIR="/opt/clpm"
LOCAL_TMP_DIR="/tmp/clpm-deploy-images"

BACKEND_IMAGE="clpm-backend:latest"
FRONTEND_IMAGE="clpm-frontend:latest"

BACKEND_TAR="clpm-backend.tar.gz"
FRONTEND_TAR="clpm-frontend.tar.gz"

BUILD_PLATFORM="linux/amd64"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }
log_step()  { echo -e "\n${BLUE}========== $* ==========${NC}"; }

# ------------------------------------------------------------
# 参数解析
# ------------------------------------------------------------
BUILD_ONLY=false
DEPLOY_ONLY=false
BACKEND_ONLY=false
FRONTEND_ONLY=false

for arg in "$@"; do
    case $arg in
        --build-only)   BUILD_ONLY=true ;;
        --deploy-only)  DEPLOY_ONLY=true ;;
        --backend-only) BACKEND_ONLY=true ;;
        --frontend-only) FRONTEND_ONLY=true ;;
        *) log_error "未知参数: $arg"; exit 1 ;;
    esac
done

# 互斥检查
if [ "$BUILD_ONLY" = true ] && [ "$DEPLOY_ONLY" = true ]; then
    log_error "--build-only 和 --deploy-only 不能同时使用"
    exit 1
fi
if [ "$BACKEND_ONLY" = true ] && [ "$FRONTEND_ONLY" = true ]; then
    log_error "--backend-only 和 --frontend-only 不能同时使用"
    exit 1
fi

# 默认：既构建又部署
if [ "$BUILD_ONLY" = false ] && [ "$DEPLOY_ONLY" = false ]; then
    DO_BUILD=true
    DO_DEPLOY=true
elif [ "$BUILD_ONLY" = true ]; then
    DO_BUILD=true
    DO_DEPLOY=false
else
    DO_BUILD=false
    DO_DEPLOY=true
fi

# 决定构建哪些镜像
BUILD_BACKEND=true
BUILD_FRONTEND=true
if [ "$BACKEND_ONLY" = true ]; then
    BUILD_FRONTEND=false
elif [ "$FRONTEND_ONLY" = true ]; then
    BUILD_BACKEND=false
fi

# ============================================================
# Phase 1: 构建镜像
# ============================================================
if [ "$DO_BUILD" = true ]; then
    log_step "Phase 1: 构建 Docker 镜像（${BUILD_PLATFORM}）"

    # 检查 buildx 是否可用
    if ! docker buildx version >/dev/null 2>&1; then
        log_warn "docker buildx 不可用，将使用普通 docker build（需要 Docker Desktop 启用 linux/amd64）"
        USE_BUILDX=false
    else
        USE_BUILDX=true
        log_info "docker buildx 可用，使用 --platform ${BUILD_PLATFORM} 构建"
    fi

    # --- 构建后端镜像 ---
    if [ "$BUILD_BACKEND" = true ]; then
        log_info "构建后端镜像: ${BACKEND_IMAGE}"
        if [ "$USE_BUILDX" = true ]; then
            docker buildx build \
                --platform "${BUILD_PLATFORM}" \
                --load \
                -t "${BACKEND_IMAGE}" \
                -f Dockerfile.backend \
                .
        else
            docker build \
                --platform "${BUILD_PLATFORM}" \
                -t "${BACKEND_IMAGE}" \
                -f Dockerfile.backend \
                .
        fi
        log_info "后端镜像构建完成: ${BACKEND_IMAGE}"
    fi

    # --- 构建前端镜像 ---
    if [ "$BUILD_FRONTEND" = true ]; then
        log_info "构建前端镜像: ${FRONTEND_IMAGE}"
        if [ "$USE_BUILDX" = true ]; then
            docker buildx build \
                --platform "${BUILD_PLATFORM}" \
                --load \
                -t "${FRONTEND_IMAGE}" \
                -f Dockerfile.frontend \
                .
        else
            docker build \
                --platform "${BUILD_PLATFORM}" \
                -t "${FRONTEND_IMAGE}" \
                -f Dockerfile.frontend \
                .
        fi
        log_info "前端镜像构建完成: ${FRONTEND_IMAGE}"
    fi

    # --- 导出镜像为 tar.gz ---
    log_step "导出镜像为 tar.gz"

    mkdir -p "${LOCAL_TMP_DIR}"

    IMAGES_TO_SAVE=""
    if [ "$BUILD_BACKEND" = true ]; then
        IMAGES_TO_SAVE="${BACKEND_IMAGE}"
    fi
    if [ "$BUILD_FRONTEND" = true ]; then
        if [ -n "$IMAGES_TO_SAVE" ]; then
            IMAGES_TO_SAVE="${IMAGES_TO_SAVE} ${FRONTEND_IMAGE}"
        else
            IMAGES_TO_SAVE="${FRONTEND_IMAGE}"
        fi
    fi

    COMBINED_TAR="${LOCAL_TMP_DIR}/clpm-images-$(date +%Y%m%d-%H%M%S).tar.gz"
    log_info "导出镜像到: ${COMBINED_TAR}"
    log_info "包含镜像: ${IMAGES_TO_SAVE}"
    docker save ${IMAGES_TO_SAVE} | gzip > "${COMBINED_TAR}"

    LOCAL_TAR_SIZE=$(du -h "${COMBINED_TAR}" | cut -f1)
    log_info "镜像包大小: ${LOCAL_TAR_SIZE}"

    # 同时保留固定名称的软链接（方便 --deploy-only 使用）
    ln -sf "${COMBINED_TAR}" "${LOCAL_TMP_DIR}/clpm-images-latest.tar.gz"
    log_info "软链接: ${LOCAL_TMP_DIR}/clpm-images-latest.tar.gz → ${COMBINED_TAR}"
fi

# ============================================================
# Phase 2: 部署到服务器
# ============================================================
if [ "$DO_DEPLOY" = true ]; then
    log_step "Phase 2: 部署到 ${SERVER_IP}"

    # 确定要传输的镜像包
    if [ "$DO_BUILD" = true ]; then
        DEPLOY_TAR="${COMBINED_TAR}"
    else
        DEPLOY_TAR="${LOCAL_TMP_DIR}/clpm-images-latest.tar.gz"
        if [ ! -f "$DEPLOY_TAR" ]; then
            log_error "镜像包不存在: ${DEPLOY_TAR}"
            log_error "请先运行: ./deploy/build-and-deploy.sh --build-only"
            exit 1
        fi
    fi

    log_info "部署镜像包: ${DEPLOY_TAR}"

    # --- 2.1 测试 SSH 连接 + 服务器前置检查 ---
    log_info "测试 SSH 连接..."
    if ! ssh -o ConnectTimeout=5 "${SERVER_USER}@${SERVER_IP}" "echo OK" >/dev/null 2>&1; then
        log_error "无法连接到 ${SERVER_USER}@${SERVER_IP}"
        log_error "请确认 SSH 免密配置或手动输入密码"
        exit 1
    fi
    log_info "SSH 连接正常"

    # --- 2.2 服务器环境预检（端口/权限/目录/Docker） ---
    log_step "服务器环境预检"

    SSH_PREFIX="ssh ${SERVER_USER}@${SERVER_IP}"

    # 检查 1: Docker 可用性
    log_info "检查 Docker 可用性..."
    if ! $SSH_PREFIX "docker info" >/dev/null 2>&1; then
        log_error "服务器上 Docker 不可用或 ${SERVER_USER} 无 docker 权限"
        log_error "修复方法："
        log_error "  1. 确认 Docker 已安装: docker --version"
        log_error "  2. 确认用户在 docker 组: usermod -aG docker ${SERVER_USER}"
        log_error "  3. 重启 Docker: systemctl restart docker"
        exit 1
    fi
    log_info "Docker 可用 ✓"

    # 检查 2: Docker Compose v2 可用性
    log_info "检查 Docker Compose v2..."
    if ! $SSH_PREFIX "docker compose version" >/dev/null 2>&1; then
        log_error "服务器上 docker compose v2 不可用"
        log_error "修复方法：安装 docker-compose-plugin: yum install docker-compose-plugin"
        exit 1
    fi
    log_info "Docker Compose v2 可用 ✓"

    # 检查 3: 端口 7141 占用检测
    log_info "检查端口 7141 占用..."
    PORT_7141_PID=$($SSH_PREFIX "
        # 检查端口 7141 是否被占用（排除 clpm 自身容器）
        ss -tlnp | grep ':7141 ' | grep -v 'docker\|containerd' | head -1
    " 2>/dev/null || echo "")

    if [ -n "$PORT_7141_PID" ]; then
        log_warn "端口 7141 被占用:"
        log_warn "  $PORT_7141_PID"
        log_warn "正在尝试自动处理..."

        # 尝试停止可能冲突的服务（nginx/apache/httpd）
        $SSH_PREFIX "
            # 停止宿主机上的 nginx/apache（如果有）
            systemctl stop nginx 2>/dev/null && echo '已停止 nginx' || true
            systemctl stop apache2 2>/dev/null && echo '已停止 apache2' || true
            systemctl stop httpd 2>/dev/null && echo '已停止 httpd' || true
            # 禁止开机自启（避免重启后再次冲突）
            systemctl disable nginx 2>/dev/null || true
            systemctl disable apache2 2>/dev/null || true
            systemctl disable httpd 2>/dev/null || true
        " 2>&1 | while read -r line; do log_info "  $line"; done

        # 再次检查
        PORT_7141_RECHECK=$($SSH_PREFIX "ss -tlnp | grep ':7141 ' | grep -v 'docker\|containerd'" 2>/dev/null || echo "")
        if [ -n "$PORT_7141_RECHECK" ]; then
            log_error "端口 7141 仍被占用，无法自动释放:"
            log_error "  $PORT_7141_RECHECK"
            log_error "请手动释放端口 7141 后重试: kill \$(lsof -t -i:7141)"
            log_error "或修改 docker-compose.prod.yml 中前端的端口映射"
            exit 1
        fi
        log_info "端口 7141 已释放 ✓"
    else
        log_info "端口 7141 空闲 ✓"
    fi

    # 检查 4: 部署目录存在
    log_info "检查部署目录..."
    if ! $SSH_PREFIX "test -d ${SERVER_DEPLOY_DIR}"; then
        log_error "部署目录不存在: ${SERVER_DEPLOY_DIR}"
        log_error "请在服务器上创建: mkdir -p ${SERVER_DEPLOY_DIR}"
        exit 1
    fi
    log_info "部署目录存在 ✓"

    # 检查 5: .env.prod 文件存在
    log_info "检查 .env.prod 配置文件..."
    if ! $SSH_PREFIX "test -f ${SERVER_DEPLOY_DIR}/.env.prod"; then
        log_error ".env.prod 不存在: ${SERVER_DEPLOY_DIR}/.env.prod"
        log_error "请从 .env.prod.example 复制并填写真实配置:"
        log_error "  scp .env.prod.example ${SERVER_USER}@${SERVER_IP}:${SERVER_DEPLOY_DIR}/.env.prod"
        log_error "  ssh ${SERVER_USER}@${SERVER_IP} 'vi ${SERVER_DEPLOY_DIR}/.env.prod'"
        exit 1
    fi
    log_info ".env.prod 存在 ✓"

    # 检查 6: 检测旧容器名冲突
    log_info "检查旧容器冲突..."
    OLD_CONTAINERS=$($SSH_PREFIX "docker ps -a --format '{{.Names}}' | grep -E '^clpm-(backend|frontend|postgres|redis|celery-)' 2>/dev/null" || echo "")
    if [ -n "$OLD_CONTAINERS" ]; then
        log_warn "发现旧容器，将在部署前自动清理:"
        echo "$OLD_CONTAINERS" | while read -r cname; do
            log_warn "  - $cname"
        done
        # 在后续 docker compose down 步骤中会自动清理
    fi
    log_info "容器冲突检查完成（如有旧容器将在 down 时清理）"

    log_info "服务器环境预检全部通过 ✓"

    # --- 2.3 传输镜像包 ---
    log_step "传输镜像包到服务器"
    log_info "scp ${DEPLOY_TAR} → ${SERVER_USER}@${SERVER_IP}:/tmp/"
    scp "${DEPLOY_TAR}" "${SERVER_USER}@${SERVER_IP}:/tmp/clpm-images-latest.tar.gz"
    log_info "镜像包传输完成"

    # --- 2.4 同步部署文件（docker-compose、nginx 配置、db schema） ---
    log_step "同步部署配置文件"
    log_info "同步 docker-compose.prod.yml"
    scp docker-compose.prod.yml "${SERVER_USER}@${SERVER_IP}:${SERVER_DEPLOY_DIR}/"

    log_info "同步 deploy/nginx.conf"
    $SSH_PREFIX "mkdir -p ${SERVER_DEPLOY_DIR}/deploy"
    scp deploy/nginx.conf "${SERVER_USER}@${SERVER_IP}:${SERVER_DEPLOY_DIR}/deploy/"

    log_info "同步 db/postgresql/*.sql"
    $SSH_PREFIX "mkdir -p ${SERVER_DEPLOY_DIR}/db/postgresql"
    scp db/postgresql/01_schema.sql db/postgresql/02_seed_data.sql \
        "${SERVER_USER}@${SERVER_IP}:${SERVER_DEPLOY_DIR}/db/postgresql/"

    log_info "部署配置文件同步完成"

    # --- 2.5 服务器加载镜像 ---
    log_step "服务器加载 Docker 镜像"
    $SSH_PREFIX "
        echo '[服务器] 加载镜像...'
        docker load < /tmp/clpm-images-latest.tar.gz
        echo '[服务器] 当前镜像列表:'
        docker images | grep clpm
    "
    log_info "镜像加载完成"

    # --- 2.6 重启服务（含错误捕获和自动处理） ---
    log_step "重启 Docker Compose 服务"

    DEPLOY_OUTPUT=$($SSH_PREFIX "
        set -e
        cd ${SERVER_DEPLOY_DIR}

        echo '=== 1. 停止旧服务 ==='
        docker compose -f docker-compose.prod.yml down --remove-orphans 2>&1 || echo '[WARN] down 命令有警告（非首次部署可忽略）'

        echo '=== 2. 清理残留容器（防止容器名冲突） ==='
        for c in clpm-backend clpm-frontend clpm-postgres clpm-redis clpm-celery-worker clpm-celery-beat; do
            if docker ps -a --format '{{.Names}}' | grep -q \"^\${c}\$\"; then
                echo \"  移除残留容器: \$c\"
                docker rm -f \"\$c\" 2>/dev/null || true
            fi
        done

        echo '=== 3. 启动新服务 ==='
        docker compose -f docker-compose.prod.yml up -d 2>&1

        echo '=== 4. 等待服务启动（40秒） ==='
        sleep 40

        echo '=== 5. 服务状态 ==='
        docker compose -f docker-compose.prod.yml ps
    " 2>&1) || DEPLOY_EXIT=$?

    echo "$DEPLOY_OUTPUT"

    if [ "${DEPLOY_EXIT:-0}" -ne 0 ]; then
        log_error "Docker Compose 启动失败（exit code: ${DEPLOY_EXIT}）"
        log_error "常见原因："
        log_error "  1. 端口冲突 → 检查: ssh ${SERVER_USER}@${SERVER_IP} 'ss -tlnp | grep :7141'"
        log_error "  2. .env.prod 配置错误 → 检查: ssh ${SERVER_USER}@${SERVER_IP} 'cat ${SERVER_DEPLOY_DIR}/.env.prod'"
        log_error "  3. 镜像加载失败 → 检查: ssh ${SERVER_USER}@${SERVER_IP} 'docker images | grep clpm'"
        log_error "  4. 磁盘空间不足 → 检查: ssh ${SERVER_USER}@${SERVER_IP} 'df -h'"
        log_error ""
        log_error "查看详细日志: ssh ${SERVER_USER}@${SERVER_IP} 'cd ${SERVER_DEPLOY_DIR} && docker compose -f docker-compose.prod.yml logs'"
        exit 1
    fi

    log_info "服务重启完成"

    # --- 2.7 健康检查 ---
    log_step "健康检查"

    # 检查是否有服务未启动
    log_info "检查容器运行状态..."
    UNHEALTHY_CONTAINERS=$($SSH_PREFIX "
        cd ${SERVER_DEPLOY_DIR}
        docker compose -f docker-compose.prod.yml ps --format '{{.Name}} {{.Status}}' | \
        grep -v 'Up' | grep -v 'healthy' || true
    " 2>/dev/null || echo "")

    if [ -n "$UNHEALTHY_CONTAINERS" ]; then
        log_warn "以下容器状态异常:"
        echo "$UNHEALTHY_CONTAINERS" | while read -r line; do
            log_warn "  $line"
        done
    fi

    log_info "检查后端 API..."
    if $SSH_PREFIX "docker exec clpm-backend curl -fsS http://localhost:7101/health" 2>/dev/null; then
        log_info "后端 API 健康 ✓"
    else
        log_error "后端 API 健康检查失败"
        log_error "查看日志: ssh ${SERVER_USER}@${SERVER_IP} 'docker logs clpm-backend --tail 50'"
        # 输出最后 20 行日志帮助诊断
        log_error "--- 后端日志（最后 20 行）---"
        $SSH_PREFIX "docker logs clpm-backend --tail 20" 2>&1 | while read -r line; do log_error "  $line"; done
    fi

    log_info "检查前端 Nginx..."
    if $SSH_PREFIX "docker exec clpm-frontend curl -fsS http://localhost:7141/" >/dev/null 2>&1; then
        log_info "前端 Nginx 健康 ✓"
    else
        log_error "前端 Nginx 健康检查失败"
        log_error "查看日志: ssh ${SERVER_USER}@${SERVER_IP} 'docker logs clpm-frontend --tail 50'"
        log_error "--- 前端日志（最后 20 行）---"
        $SSH_PREFIX "docker logs clpm-frontend --tail 20" 2>&1 | while read -r line; do log_error "  $line"; done
    fi

    log_info "检查 Celery Worker..."
    if $SSH_PREFIX "docker exec clpm-celery-worker celery -A app.tasks.celery_app inspect ping -d celery@\$(hostname) --timeout 5" 2>/dev/null | grep -q pong; then
        log_info "Celery Worker 健康 ✓"
    else
        log_warn "Celery Worker 健康检查超时（可能仍在启动，非致命）"
    fi

    # --- 2.8 完成 ---
    log_step "部署完成"

    echo ""
    echo "服务访问地址："
    echo "  前端：      http://${SERVER_IP}:7141"
    echo "  后端 API：  http://${SERVER_IP}:7141/api/v1（通过 nginx 反向代理）"
    echo "  默认账号：  admin / admin123"
    echo ""
    echo "常用运维命令："
    echo "  查看日志：  ssh ${SERVER_USER}@${SERVER_IP} 'cd ${SERVER_DEPLOY_DIR} && docker compose -f docker-compose.prod.yml logs -f'"
    echo "  查看状态：  ssh ${SERVER_USER}@${SERVER_IP} 'cd ${SERVER_DEPLOY_DIR} && docker compose -f docker-compose.prod.yml ps'"
    echo "  重启服务：  ssh ${SERVER_USER}@${SERVER_IP} 'cd ${SERVER_DEPLOY_DIR} && docker compose -f docker-compose.prod.yml restart'"
    echo "  停止服务：  ssh ${SERVER_USER}@${SERVER_IP} 'cd ${SERVER_DEPLOY_DIR} && docker compose -f docker-compose.prod.yml down'"
fi

echo ""
log_info "全部操作完成"
