#!/bin/bash
# ============================================================
# CLPM Docker 镜像构建与部署脚本（离线镜像方式）
#
# 用途：在本地（macOS）构建 linux/amd64 镜像，传输到 zpdev 服务器并部署
# 服务器：192.168.13.111（zpdev 局域网 IP）
# 备选：Tailscale SSH（ssh zpdev，需 Tailscale 认证）
#
# 镜像产物目录：项目根目录下 releases/images/
#   - clpm-images-YYYYMMDD-HHMMSS.tar.gz：每次构建的镜像包
#   - clpm-images-latest.tar.gz：软链接，指向最新构建
# 构建清单：releases/manifest.json（入 git，记录每次构建的版本/commit/大小）
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
#   # 跳过构建前测试门禁（紧急修复用，常规部署不建议）
#   ./deploy/build-and-deploy.sh --skip-gate
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

# SSH 目标：局域网直连（可覆盖为其他地址）
# zpdev 局域网 IP: 192.168.13.111，用户: zhangping（docker 组）
SSH_HOST="${SSH_HOST:-zhangping@192.168.13.111}"
SERVER_DEPLOY_DIR="${SERVER_DEPLOY_DIR:-/home/zhangping/clpm}"
LOCAL_TMP_DIR="${PROJECT_ROOT}/releases/images"

BACKEND_IMAGE="clpm-backend:latest"
FRONTEND_IMAGE="clpm-frontend:latest"

BACKEND_TAR="clpm-backend.tar.gz"
FRONTEND_TAR="clpm-frontend.tar.gz"

BUILD_PLATFORM="linux/amd64"
MANIFEST_FILE="${PROJECT_ROOT}/releases/manifest.json"

# 公共函数库：Alembic 版本同步（部署=迁移一体，失败即中止）
# shellcheck source=deploy/lib-migrate.sh
source "${SCRIPT_DIR}/lib-migrate.sh"

# ------------------------------------------------------------
# 版本标识（2026-07-28 Phase 5：镜像版本 tag + 回滚可用）
# ------------------------------------------------------------
# 构建时除 :latest 外同时打 commit tag 与 manifest 版本 tag，
# 否则服务器上永远只有 :latest，rollback.sh 找不到历史镜像。
GIT_COMMIT="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
GIT_BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
# APP_VERSION：git tag（如 v6.2.0）或 commit，注入镜像 ENV 供排障定位
APP_VERSION="$(git describe --tags --always 2>/dev/null || echo "${GIT_COMMIT}")"
BUILD_VERSION="$(date +%Y%m%d-%H%M%S)"

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
SKIP_GATE=false

for arg in "$@"; do
    case $arg in
        --build-only)   BUILD_ONLY=true ;;
        --deploy-only)  DEPLOY_ONLY=true ;;
        --backend-only) BACKEND_ONLY=true ;;
        --frontend-only) FRONTEND_ONLY=true ;;
        --skip-gate)    SKIP_GATE=true ;;
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
# Phase 0: 构建前测试门禁（2026-07-28 Phase 5）
# ============================================================
# 与 lefthook pre-push 门禁同口径：ruff + pytest + 前端类型检查。
# 任何一项失败即中止，避免把未通过本地门禁的代码构建进生产镜像。
if [ "$DO_BUILD" = true ] && [ "$SKIP_GATE" = false ]; then
    log_step "Phase 0: 构建前测试门禁"
    log_info "后端：ruff check + format check"
    (cd backend && uv run ruff check . && uv run ruff format --check .)
    log_info "后端：pytest -x -q"
    (cd backend && uv run pytest -x -q)
    log_info "前端：check:type"
    (cd frontend && pnpm run check:type)
    log_info "测试门禁全部通过 ✓"
elif [ "$DO_BUILD" = true ]; then
    log_warn "已通过 --skip-gate 跳过构建前测试门禁（仅限紧急修复场景）"
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
        log_info "构建后端镜像: ${BACKEND_IMAGE}（另打 tag: ${GIT_COMMIT}, ${BUILD_VERSION}；APP_VERSION=${APP_VERSION}）"
        if [ "$USE_BUILDX" = true ]; then
            docker buildx build \
                --platform "${BUILD_PLATFORM}" \
                --load \
                -t "${BACKEND_IMAGE}" \
                -t "clpm-backend:${GIT_COMMIT}" \
                -t "clpm-backend:${BUILD_VERSION}" \
                --build-arg "APP_VERSION=${APP_VERSION}" \
                -f Dockerfile.backend \
                .
        else
            docker build \
                --platform "${BUILD_PLATFORM}" \
                -t "${BACKEND_IMAGE}" \
                -t "clpm-backend:${GIT_COMMIT}" \
                -t "clpm-backend:${BUILD_VERSION}" \
                --build-arg "APP_VERSION=${APP_VERSION}" \
                -f Dockerfile.backend \
                .
        fi
        log_info "后端镜像构建完成: ${BACKEND_IMAGE}"
    fi

    # --- 构建前端镜像 ---
    if [ "$BUILD_FRONTEND" = true ]; then
        log_info "构建前端镜像: ${FRONTEND_IMAGE}（另打 tag: ${GIT_COMMIT}, ${BUILD_VERSION}）"
        if [ "$USE_BUILDX" = true ]; then
            docker buildx build \
                --platform "${BUILD_PLATFORM}" \
                --load \
                -t "${FRONTEND_IMAGE}" \
                -t "clpm-frontend:${GIT_COMMIT}" \
                -t "clpm-frontend:${BUILD_VERSION}" \
                --build-arg "APP_VERSION=${APP_VERSION}" \
                -f Dockerfile.frontend \
                .
        else
            docker build \
                --platform "${BUILD_PLATFORM}" \
                -t "${FRONTEND_IMAGE}" \
                -t "clpm-frontend:${GIT_COMMIT}" \
                -t "clpm-frontend:${BUILD_VERSION}" \
                --build-arg "APP_VERSION=${APP_VERSION}" \
                -f Dockerfile.frontend \
                .
        fi
        log_info "前端镜像构建完成: ${FRONTEND_IMAGE}"
    fi

    # --- 导出镜像为 tar.gz ---
    log_step "导出镜像为 tar.gz"

    mkdir -p "${LOCAL_TMP_DIR}"

    # 导出时包含全部 tag（latest + commit + manifest 版本），
    # 服务器 docker load 后才能保留版本 tag 供 rollback.sh 回滚
    IMAGES_TO_SAVE=""
    if [ "$BUILD_BACKEND" = true ]; then
        IMAGES_TO_SAVE="${BACKEND_IMAGE} clpm-backend:${GIT_COMMIT} clpm-backend:${BUILD_VERSION}"
    fi
    if [ "$BUILD_FRONTEND" = true ]; then
        if [ -n "$IMAGES_TO_SAVE" ]; then
            IMAGES_TO_SAVE="${IMAGES_TO_SAVE} ${FRONTEND_IMAGE} clpm-frontend:${GIT_COMMIT} clpm-frontend:${BUILD_VERSION}"
        else
            IMAGES_TO_SAVE="${FRONTEND_IMAGE} clpm-frontend:${GIT_COMMIT} clpm-frontend:${BUILD_VERSION}"
        fi
    fi

    COMBINED_TAR="${LOCAL_TMP_DIR}/clpm-images-${BUILD_VERSION}.tar.gz"
    log_info "导出镜像到: ${COMBINED_TAR}"
    log_info "包含镜像: ${IMAGES_TO_SAVE}"
    docker save ${IMAGES_TO_SAVE} | gzip > "${COMBINED_TAR}"

    LOCAL_TAR_SIZE=$(du -h "${COMBINED_TAR}" | cut -f1)
    log_info "镜像包大小: ${LOCAL_TAR_SIZE}"

    # 同时保留固定名称的软链接（方便 --deploy-only 使用）
    ln -sf "${COMBINED_TAR}" "${LOCAL_TMP_DIR}/clpm-images-latest.tar.gz"
    log_info "软链接: ${LOCAL_TMP_DIR}/clpm-images-latest.tar.gz → ${COMBINED_TAR}"

    # --- 更新构建清单 manifest.json ---
    log_info "更新构建清单: ${MANIFEST_FILE}"

    TAR_FILENAME="$(basename "${COMBINED_TAR}")"
    TAR_SIZE_BYTES="$(stat -f%z "${COMBINED_TAR}" 2>/dev/null || stat -c%s "${COMBINED_TAR}" 2>/dev/null || echo 0)"

    # 获取各镜像大小
    BACKEND_SIZE="$(docker image inspect ${BACKEND_IMAGE} --format '{{.Size}}' 2>/dev/null | awk '{printf "%.0fMB", $1/1048576}' || echo unknown)"
    FRONTEND_SIZE="$(docker image inspect ${FRONTEND_IMAGE} --format '{{.Size}}' 2>/dev/null | awk '{printf "%.0fMB", $1/1048576}' || echo unknown)"

    # 构建 manifest 条目（images 记录全部 tag，回滚时按 commit/版本 tag 定位）
    ENTRY=$(cat <<MANIFEST_EOF
{
  "version": "${BUILD_VERSION}",
  "buildTime": "$(date '+%Y-%m-%d %H:%M:%S')",
  "gitCommit": "${GIT_COMMIT}",
  "gitBranch": "${GIT_BRANCH}",
  "appVersion": "${APP_VERSION}",
  "images": [
    {"name": "${BACKEND_IMAGE}", "tags": ["latest", "${GIT_COMMIT}", "${BUILD_VERSION}"], "size": "${BACKEND_SIZE}"},
    {"name": "${FRONTEND_IMAGE}", "tags": ["latest", "${GIT_COMMIT}", "${BUILD_VERSION}"], "size": "${FRONTEND_SIZE}"}
  ],
  "tarFile": "${TAR_FILENAME}",
  "tarSize": "${LOCAL_TAR_SIZE}"
}
MANIFEST_EOF
)

    # 读取现有 manifest 并追加（兼容空文件或 []）
    if [ -f "${MANIFEST_FILE}" ]; then
        EXISTING="$(cat "${MANIFEST_FILE}")"
        # 去掉首尾的 [ ] 并去除空数组
        EXISTING="$(echo "$EXISTING" | sed 's/^\[//;s/\]$//' | xargs)"
    else
        EXISTING=""
    fi

    if [ -z "$EXISTING" ]; then
        echo "[${ENTRY}]" > "${MANIFEST_FILE}"
    else
        echo "[${EXISTING},${ENTRY}]" > "${MANIFEST_FILE}"
    fi

    log_info "构建清单已更新"
fi

# ============================================================
# Phase 2: 部署到服务器
# ============================================================
if [ "$DO_DEPLOY" = true ]; then
    log_step "Phase 2: 部署到 ${SSH_HOST}"

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
    if ! ssh -o ConnectTimeout=5 "${SSH_HOST}" "echo OK" >/dev/null 2>&1; then
        log_error "无法连接到 ${SSH_HOST}"
        log_error "请确认 SSH 免密配置或手动输入密码"
        exit 1
    fi
    log_info "SSH 连接正常"

    # --- 2.2 服务器环境预检（端口/权限/目录/Docker） ---
    log_step "服务器环境预检"

    SSH_PREFIX="ssh ${SSH_HOST}"

    # 检查 1: Docker 可用性
    log_info "检查 Docker 可用性..."
    if ! $SSH_PREFIX "docker info" >/dev/null 2>&1; then
        log_error "服务器上 Docker 不可用或当前用户无 docker 权限"
        log_error "修复方法："
        log_error "  1. 确认 Docker 已安装: docker --version"
        log_error "  2. 确认用户在 docker 组: usermod -aG docker \$USER"
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
        log_error "  scp .env.prod.example ${SSH_HOST}:${SERVER_DEPLOY_DIR}/.env.prod"
        log_error "  ssh ${SSH_HOST} 'vi ${SERVER_DEPLOY_DIR}/.env.prod'"
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
    log_info "scp ${DEPLOY_TAR} → ${SSH_HOST}:/tmp/"
    scp "${DEPLOY_TAR}" "${SSH_HOST}:/tmp/clpm-images-latest.tar.gz"
    log_info "镜像包传输完成"

    # --- 2.4 同步部署文件（docker-compose、nginx 配置、db schema） ---
    log_step "同步部署配置文件"
    log_info "同步 docker-compose.prod.yml"
    scp docker-compose.prod.yml "${SSH_HOST}:${SERVER_DEPLOY_DIR}/"

    log_info "同步 deploy/nginx.conf"
    $SSH_PREFIX "mkdir -p ${SERVER_DEPLOY_DIR}/deploy"
    scp deploy/nginx.conf "${SSH_HOST}:${SERVER_DEPLOY_DIR}/deploy/"

    log_info "同步监控配置（deploy/grafana、deploy/prometheus）"
    scp -r deploy/grafana deploy/prometheus "${SSH_HOST}:${SERVER_DEPLOY_DIR}/deploy/"

    log_info "同步 db/postgresql/*.sql"
    $SSH_PREFIX "mkdir -p ${SERVER_DEPLOY_DIR}/db/postgresql"
    scp db/postgresql/01_schema.sql db/postgresql/02_seed_data.sql \
        "${SSH_HOST}:${SERVER_DEPLOY_DIR}/db/postgresql/"

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

    # --- 2.55 部署前自动备份（升级场景，失败即中止） ---
    log_step "部署前自动备份"
    BACKUP_OUTPUT=$($SSH_PREFIX "
        if docker ps --format '{{.Names}}' | grep -q '^clpm-postgres$'; then
            if [ -f ${SERVER_DEPLOY_DIR}/deploy/backup.sh ]; then
                cd ${SERVER_DEPLOY_DIR} && bash deploy/backup.sh
            else
                echo '[WARN] 服务器上无 deploy/backup.sh，跳过备份（建议补齐备份脚本）'
            fi
        else
            echo '[INFO] 首次部署（无运行中容器），跳过部署前备份'
        fi
    " 2>&1) || BACKUP_EXIT=$?

    echo "$BACKUP_OUTPUT"

    if [ "${BACKUP_EXIT:-0}" -ne 0 ]; then
        log_error "部署前备份失败（exit code: ${BACKUP_EXIT}），中止部署"
        log_error "避免在无回退数据的情况下变更镜像/schema；请排查备份后重试"
        exit 1
    fi
    log_info "部署前备份完成 ✓"

    # --- 2.6 重启服务（含错误捕获和自动处理） ---
    log_step "重启 Docker Compose 服务"

    DEPLOY_OUTPUT=$($SSH_PREFIX "
        set -e
        cd ${SERVER_DEPLOY_DIR}
        COMPOSE_PROFILE=''
        if grep -qE '^DATA_SOURCE_TYPE=tdengine$' .env.prod; then
            COMPOSE_PROFILE='--profile tdengine'
        fi

        echo '=== 1. 停止旧服务 ==='
        docker compose --env-file .env.prod -f docker-compose.prod.yml \${COMPOSE_PROFILE} down --remove-orphans 2>&1 || echo '[WARN] down 命令有警告（非首次部署可忽略）'

        echo '=== 2. 清理残留容器（防止容器名冲突） ==='
        for c in clpm-backend clpm-frontend clpm-postgres clpm-tdengine clpm-redis clpm-celery-worker clpm-celery-beat; do
            if docker ps -a --format '{{.Names}}' | grep -q \"^\${c}\$\"; then
                echo \"  移除残留容器: \$c\"
                docker rm -f \"\$c\" 2>/dev/null || true
            fi
        done

        echo '=== 3. 启动新服务 ==='
        docker compose --env-file .env.prod -f docker-compose.prod.yml \${COMPOSE_PROFILE} up -d 2>&1

        echo '=== 4. 等待服务启动（40秒） ==='
        sleep 40

        echo '=== 5. 服务状态 ==='
        docker compose --env-file .env.prod -f docker-compose.prod.yml \${COMPOSE_PROFILE} ps
    " 2>&1) || DEPLOY_EXIT=$?

    echo "$DEPLOY_OUTPUT"

    if [ "${DEPLOY_EXIT:-0}" -ne 0 ]; then
        log_error "Docker Compose 启动失败（exit code: ${DEPLOY_EXIT}）"
        log_error "常见原因："
        log_error "  1. 端口冲突 → 检查: ssh ${SSH_HOST} 'ss -tlnp | grep :7141'"
        log_error "  2. .env.prod 配置错误 → 检查: ssh ${SSH_HOST} 'cat ${SERVER_DEPLOY_DIR}/.env.prod'"
        log_error "  3. 镜像加载失败 → 检查: ssh ${SSH_HOST} 'docker images | grep clpm'"
        log_error "  4. 磁盘空间不足 → 检查: ssh ${SSH_HOST} 'df -h'"
        log_error ""
        log_error "查看详细日志: ssh ${SSH_HOST} 'cd ${SERVER_DEPLOY_DIR} && docker compose --env-file .env.prod -f docker-compose.prod.yml logs'"
        exit 1
    fi

    log_info "服务重启完成"

    # --- 2.65 数据库版本同步（部署=迁移一体，失败即中止） ---
    log_step "数据库版本同步（Alembic）"

    # lib-migrate.sh 要求的容器命令执行器：通过 SSH 在服务器 backend 容器内执行
    backend_exec() {
        $SSH_PREFIX "docker exec -i clpm-backend $*"
    }

    if alembic_sync_head; then
        log_info "数据库版本同步完成 ✓"
    else
        log_error "数据库迁移失败，中止部署（新代码不允许跑在旧 schema 上）"
        log_error "排查：ssh ${SSH_HOST} 'docker exec -it clpm-backend alembic current'"
        log_error "      ssh ${SSH_HOST} 'docker logs clpm-backend --tail 50'"
        log_error "迁移修复后可执行 ./deploy/rollback.sh 回滚镜像，再重新部署"
        exit 1
    fi

    # --- 2.7 健康检查 ---
    log_step "健康检查"

    # 检查是否有服务未启动
    log_info "检查容器运行状态..."
    UNHEALTHY_CONTAINERS=$($SSH_PREFIX "
        cd ${SERVER_DEPLOY_DIR}
        COMPOSE_PROFILE=''
        if grep -qE '^DATA_SOURCE_TYPE=tdengine$' .env.prod; then
            COMPOSE_PROFILE='--profile tdengine'
        fi
        docker compose --env-file .env.prod -f docker-compose.prod.yml \${COMPOSE_PROFILE} ps --format '{{.Name}} {{.Status}}' | \
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
        log_error "查看日志: ssh ${SSH_HOST} 'docker logs clpm-backend --tail 50'"
        # 输出最后 20 行日志帮助诊断
        log_error "--- 后端日志（最后 20 行）---"
        $SSH_PREFIX "docker logs clpm-backend --tail 20" 2>&1 | while read -r line; do log_error "  $line"; done
    fi

    log_info "检查前端 Nginx..."
    if $SSH_PREFIX "docker exec clpm-frontend curl -fsS http://localhost:7141/" >/dev/null 2>&1; then
        log_info "前端 Nginx 健康 ✓"
    else
        log_error "前端 Nginx 健康检查失败"
        log_error "查看日志: ssh ${SSH_HOST} 'docker logs clpm-frontend --tail 50'"
        log_error "--- 前端日志（最后 20 行）---"
        $SSH_PREFIX "docker logs clpm-frontend --tail 20" 2>&1 | while read -r line; do log_error "  $line"; done
    fi

    log_info "检查 Celery Worker（inspect ping）..."
    if $SSH_PREFIX "docker exec clpm-celery-worker celery -A app.tasks.celery_app inspect ping -d celery@\$(hostname) --timeout 5" 2>/dev/null | grep -q pong; then
        log_info "Celery Worker 健康 ✓"
    else
        log_error "Celery Worker 健康检查失败（inspect ping 无 pong）"
        log_error "异步任务（KPI 计算/诊断/回填）将不执行，部署视为失败"
        log_error "查看日志: ssh ${SSH_HOST} 'docker logs clpm-celery-worker --tail 50'"
        exit 1
    fi

    log_info "检查 Celery 调度链路（inspect scheduled）..."
    if $SSH_PREFIX "docker exec clpm-celery-worker celery -A app.tasks.celery_app inspect scheduled --timeout 5" 2>/dev/null | grep -q 'celery@'; then
        log_info "Celery 调度链路正常 ✓"
    else
        log_error "Celery inspect scheduled 无响应（broker 链路异常）"
        log_error "查看日志: ssh ${SSH_HOST} 'docker logs clpm-celery-worker --tail 50'"
        exit 1
    fi

    log_info "检查 Celery Beat 容器健康状态..."
    BEAT_HEALTH=$($SSH_PREFIX "docker inspect -f '{{.State.Health.Status}}' clpm-celery-beat" 2>/dev/null || echo "unknown")
    if [ "$BEAT_HEALTH" = "healthy" ]; then
        log_info "Celery Beat 健康 ✓"
    else
        log_error "Celery Beat 容器健康状态异常: ${BEAT_HEALTH}"
        log_error "定时任务（KPI 快照/诊断调度）将不触发，部署视为失败"
        log_error "查看日志: ssh ${SSH_HOST} 'docker logs clpm-celery-beat --tail 50'"
        exit 1
    fi

    # --- 2.8 完成 ---
    log_step "部署完成"

    # 动态获取服务器 IP（通过 SSH 在服务器上执行 hostname -I）
    SERVER_IP=$($SSH_PREFIX "hostname -I 2>/dev/null | awk '{print \$1}'" 2>/dev/null || echo "")
    if [ -z "$SERVER_IP" ]; then
        SERVER_IP="${SSH_HOST}"
    fi

    echo ""
    echo "服务访问地址："
    echo "  前端：      http://${SERVER_IP}:7141"
    echo "  后端 API：  http://${SERVER_IP}:7141/api/v1（通过 nginx 反向代理）"
    echo "  默认账号：  admin / admin123"
    echo ""
    echo "常用运维命令："
    echo "  查看日志：  ssh ${SSH_HOST} 'cd ${SERVER_DEPLOY_DIR} && docker compose --env-file .env.prod -f docker-compose.prod.yml logs -f'"
    echo "  查看状态：  ssh ${SSH_HOST} 'cd ${SERVER_DEPLOY_DIR} && docker compose --env-file .env.prod -f docker-compose.prod.yml ps'"
    echo "  重启服务：  ssh ${SSH_HOST} 'cd ${SERVER_DEPLOY_DIR} && docker compose --env-file .env.prod -f docker-compose.prod.yml restart'"
    echo "  停止服务：  ssh ${SSH_HOST} 'cd ${SERVER_DEPLOY_DIR} && docker compose --env-file .env.prod -f docker-compose.prod.yml down'"
fi

echo ""
log_info "全部操作完成"
