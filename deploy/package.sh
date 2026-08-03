#!/bin/bash
# ============================================================
# CLPM 离线交付包打包脚本
#
# 用途：在开发机构建 Docker 镜像，组装自包含交付包
# 产出：releases/clpm-delivery-<version>.tar.gz
#
# 交付包结构：
#   clpm-delivery-<version>/
#   ├── deploy.sh              ← 服务器端部署脚本
#   ├── deploy/                ← 部署工具（backup/rollback/lib-migrate/nginx/监控）
#   ├── docker-compose.prod.yml
#   ├── .env.prod.example
#   ├── db/                    ← 数据库初始化 SQL
#   ├── images/                ← 预构建镜像 tarball
#   │   └── clpm-images-<version>.tar.gz
#   └── README.md              ← 部署说明
#
# 用法：
#   ./deploy/package.sh                        # 构建并打包（含测试门禁 + 核心第三方镜像）
#   ./deploy/package.sh --skip-tests           # 跳过测试门禁（紧急交付）
#   ./deploy/package.sh --include-monitoring   # 额外打包监控镜像（Prometheus/Grafana）
#   ./deploy/package.sh --push-deploy-repo     # 同时同步部署脚本到 clpm-deploy 远端仓库
#
# 交付包镜像内容：
#   自有镜像：clpm-backend / clpm-frontend（各 3 个 tag）
#   核心第三方：postgres:16-alpine / redis:7-alpine / tdengine:tdengine:3.3.6.6
#   可选第三方：prom/prometheus / grafana/grafana / prom/node-exporter（--include-monitoring）
# ============================================================
set -euo pipefail

# ------------------------------------------------------------
# 配置
# ------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

BUILD_PLATFORM="linux/amd64"
RELEASES_DIR="${PROJECT_ROOT}/releases"
DELIVERY_DIR="${RELEASES_DIR}/delivery-staging"

# 远端部署仓库（用于 --push-deploy-repo）
DEPLOY_REPO_REMOTE="${DEPLOY_REPO_REMOTE:-gitea}"
DEPLOY_REPO_URL="${DEPLOY_REPO_URL:-https://gitea.zlinfot.xyz:2087/zp/clpm-deploy.git}"

# 版本标识
GIT_COMMIT="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
GIT_BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
APP_VERSION="$(git describe --tags --always 2>/dev/null || echo "${GIT_COMMIT}")"
BUILD_VERSION="$(date +%Y%m%d-%H%M%S)"

# 颜色
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
SKIP_TESTS=false
PUSH_DEPLOY_REPO=false
INCLUDE_MONITORING=false

for arg in "$@"; do
    case $arg in
        --skip-tests)         SKIP_TESTS=true ;;
        --push-deploy-repo)   PUSH_DEPLOY_REPO=true ;;
        --include-monitoring) INCLUDE_MONITORING=true ;;
        *) log_error "未知参数: $arg"; exit 1 ;;
    esac
done

# ============================================================
# Phase 0: 测试门禁
# ============================================================
if [ "$SKIP_TESTS" = false ]; then
    log_step "Phase 0: 构建前测试门禁"

    log_info "后端：ruff check + format check"
    (cd backend && uv run ruff check . && uv run ruff format --check .)

    log_info "后端：pytest -x -q"
    (cd backend && uv run pytest -x -q)

    log_info "前端：check:type"
    (cd frontend && pnpm run check:type)

    log_info "测试门禁全部通过 ✓"
else
    log_warn "已通过 --skip-tests 跳过测试门禁（仅限紧急交付场景）"
fi

# ============================================================
# Phase 1: 构建 Docker 镜像
# ============================================================
log_step "Phase 1: 构建 Docker 镜像（${BUILD_PLATFORM}）"

# 检查 buildx
if docker buildx version >/dev/null 2>&1; then
    USE_BUILDX=true
    log_info "docker buildx 可用，使用 --platform ${BUILD_PLATFORM} 构建"
else
    USE_BUILDX=false
    log_warn "docker buildx 不可用，使用普通 docker build"
fi

# 构建后端镜像
log_info "构建后端镜像（APP_VERSION=${APP_VERSION}）"
if [ "$USE_BUILDX" = true ]; then
    docker buildx build --platform "$BUILD_PLATFORM" --load \
        -t clpm-backend:latest \
        -t "clpm-backend:${GIT_COMMIT}" \
        -t "clpm-backend:${BUILD_VERSION}" \
        --build-arg "APP_VERSION=${APP_VERSION}" \
        -f Dockerfile.backend .
else
    docker build --platform "$BUILD_PLATFORM" \
        -t clpm-backend:latest \
        -t "clpm-backend:${GIT_COMMIT}" \
        -t "clpm-backend:${BUILD_VERSION}" \
        --build-arg "APP_VERSION=${APP_VERSION}" \
        -f Dockerfile.backend .
fi
log_info "后端镜像构建完成"

# 构建前端镜像
log_info "构建前端镜像"
if [ "$USE_BUILDX" = true ]; then
    docker buildx build --platform "$BUILD_PLATFORM" --load \
        -t clpm-frontend:latest \
        -t "clpm-frontend:${GIT_COMMIT}" \
        -t "clpm-frontend:${BUILD_VERSION}" \
        --build-arg "APP_VERSION=${APP_VERSION}" \
        -f Dockerfile.frontend .
else
    docker build --platform "$BUILD_PLATFORM" \
        -t clpm-frontend:latest \
        -t "clpm-frontend:${GIT_COMMIT}" \
        -t "clpm-frontend:${BUILD_VERSION}" \
        --build-arg "APP_VERSION=${APP_VERSION}" \
        -f Dockerfile.frontend .
fi
log_info "前端镜像构建完成"

# ============================================================
# Phase 1.5: 拉取第三方镜像（客户离线环境无法 docker pull）
# ============================================================
log_step "Phase 1.5: 拉取第三方依赖镜像"

# 核心依赖（必选）：PostgreSQL / Redis / TDengine
THIRD_PARTY_CORE=(
    "postgres:16-alpine"
    "redis:7-alpine"
    "tdengine/tdengine:3.3.6.6"
)

# 监控依赖（可选）：Prometheus / Grafana / Node Exporter
THIRD_PARTY_MONITORING=(
    "prom/prometheus:v2.53.4"
    "grafana/grafana:11.1.0"
    "prom/node-exporter:v1.8.2"
)

for img in "${THIRD_PARTY_CORE[@]}"; do
    log_info "构建单平台镜像: $img"
    # 用 buildx build FROM 替代 docker pull，避免 Docker Desktop 在 Apple Silicon
    # 上保留多架构 manifest list 导致 docker save 报 "content digest not found" 错误
    echo "FROM $img" | docker buildx build --platform "$BUILD_PLATFORM" --load -t "$img" - 2>/dev/null
done

if [ "$INCLUDE_MONITORING" = true ]; then
    for img in "${THIRD_PARTY_MONITORING[@]}"; do
        log_info "构建单平台镜像: $img"
        echo "FROM $img" | docker buildx build --platform "$BUILD_PLATFORM" --load -t "$img" - 2>/dev/null
    done
    ALL_THIRD_PARTY=("${THIRD_PARTY_CORE[@]}" "${THIRD_PARTY_MONITORING[@]}")
else
    ALL_THIRD_PARTY=("${THIRD_PARTY_CORE[@]}")
fi

log_info "第三方镜像拉取完成"

# ============================================================
# Phase 2: 导出镜像 tarball（含自有 + 第三方）
# ============================================================
log_step "Phase 2: 导出镜像 tarball"

IMAGES_TAR="clpm-images-${BUILD_VERSION}.tar.gz"
mkdir -p "${RELEASES_DIR}/images"
IMAGES_TAR_PATH="${RELEASES_DIR}/images/${IMAGES_TAR}"

# 自有镜像（backend + frontend 各 3 个 tag）
OWN_IMAGES=(
    clpm-backend:latest clpm-backend:${GIT_COMMIT} clpm-backend:${BUILD_VERSION}
    clpm-frontend:latest clpm-frontend:${GIT_COMMIT} clpm-frontend:${BUILD_VERSION}
)

log_info "导出镜像到: ${IMAGES_TAR_PATH}"
log_info "自有镜像: ${OWN_IMAGES[*]}"
log_info "第三方镜像: ${ALL_THIRD_PARTY[*]}"
docker save "${OWN_IMAGES[@]}" "${ALL_THIRD_PARTY[@]}" | gzip > "${IMAGES_TAR_PATH}"

IMAGES_TAR_SIZE=$(du -h "${IMAGES_TAR_PATH}" | cut -f1)
log_info "镜像包大小: ${IMAGES_TAR_SIZE}（含 $((${#OWN_IMAGES[@]} + ${#ALL_THIRD_PARTY[@]})) 个镜像）"

# ============================================================
# Phase 3: 组装交付包
# ============================================================
log_step "Phase 3: 组装交付包"

DELIVERY_NAME="clpm-delivery-${BUILD_VERSION}"
DELIVERY_PATH="${RELEASES_DIR}/${DELIVERY_NAME}"

# 清理旧的暂存目录
rm -rf "${DELIVERY_DIR}"
rm -rf "${DELIVERY_PATH}"
mkdir -p "${DELIVERY_PATH}"

log_info "组装交付目录: ${DELIVERY_PATH}"

# 1. 部署脚本（deploy-on-server.sh 重命名为 deploy.sh）
cp "${SCRIPT_DIR}/deploy-on-server.sh" "${DELIVERY_PATH}/deploy.sh"
chmod +x "${DELIVERY_PATH}/deploy.sh"

# 2. deploy/ 子目录（部署工具）
mkdir -p "${DELIVERY_PATH}/deploy"
cp "${SCRIPT_DIR}/backup.sh"        "${DELIVERY_PATH}/deploy/"
cp "${SCRIPT_DIR}/rollback.sh"      "${DELIVERY_PATH}/deploy/"
cp "${SCRIPT_DIR}/lib-migrate.sh"   "${DELIVERY_PATH}/deploy/"
cp "${SCRIPT_DIR}/nginx.conf"       "${DELIVERY_PATH}/deploy/"
cp "${SCRIPT_DIR}/simulate-offline-deploy.sh" "${DELIVERY_PATH}/deploy/"
chmod +x "${DELIVERY_PATH}/deploy/simulate-offline-deploy.sh"

# 监控配置（可选）
if [ -d "${SCRIPT_DIR}/prometheus" ]; then
    cp -r "${SCRIPT_DIR}/prometheus" "${DELIVERY_PATH}/deploy/"
fi
if [ -d "${SCRIPT_DIR}/grafana" ]; then
    cp -r "${SCRIPT_DIR}/grafana" "${DELIVERY_PATH}/deploy/"
fi

# 3. docker-compose 配置
cp "${PROJECT_ROOT}/docker-compose.prod.yml" "${DELIVERY_PATH}/"

# 4. 环境变量模板
cp "${PROJECT_ROOT}/.env.prod.example" "${DELIVERY_PATH}/"

# 5. 数据库初始化 SQL
mkdir -p "${DELIVERY_PATH}/db/postgresql" "${DELIVERY_PATH}/db/tdengine"
cp "${PROJECT_ROOT}/db/postgresql/01_schema.sql"     "${DELIVERY_PATH}/db/postgresql/"
cp "${PROJECT_ROOT}/db/postgresql/02_seed_data.sql"  "${DELIVERY_PATH}/db/postgresql/"
cp "${PROJECT_ROOT}/db/tdengine/01_supertable.sql"   "${DELIVERY_PATH}/db/tdengine/"

# 6. 镜像 tarball
mkdir -p "${DELIVERY_PATH}/images"
cp "${IMAGES_TAR_PATH}" "${DELIVERY_PATH}/images/"

# 7. README（部署快速指南）+ DEPLOYMENT-GUIDE.md（完整操作手册）
if [ -f "${SCRIPT_DIR}/README-deploy.md" ]; then
    cp "${SCRIPT_DIR}/README-deploy.md" "${DELIVERY_PATH}/README.md"
fi
if [ -f "${SCRIPT_DIR}/DEPLOYMENT-GUIDE.md" ]; then
    cp "${SCRIPT_DIR}/DEPLOYMENT-GUIDE.md" "${DELIVERY_PATH}/DEPLOYMENT-GUIDE.md"
fi
# 如果 README-deploy.md 不存在，生成基本 README
if [ ! -f "${DELIVERY_PATH}/README.md" ]; then
    # 生成基本 README
    cat > "${DELIVERY_PATH}/README.md" <<README_EOF
# CLPM 部署包（${BUILD_VERSION}）

## 版本信息
- 构建版本: ${BUILD_VERSION}
- Git Commit: ${GIT_COMMIT}
- App Version: ${APP_VERSION}
- 构建时间: $(date '+%Y-%m-%d %H:%M:%S')

## 环境要求
- Docker 24+ / Docker Compose v2
- Linux x86_64 / 4GB+ RAM / 20GB+ 磁盘

## 部署步骤
1. 解压交付包: tar xzf clpm-delivery-${BUILD_VERSION}.tar.gz
2. 复制配置: cp .env.prod.example .env.prod
3. 修改配置: 编辑 .env.prod，替换所有占位符为真实密码
4. 执行部署: ./deploy.sh
5. 访问: http://<服务器IP>:7141

## 排障
- 部署失败: 查看 /tmp/clpm-deploy-troubleshooting-*.md
- 手动回滚: ./deploy/rollback.sh
- 数据备份: ./deploy/backup.sh
README_EOF
fi

log_info "交付目录组装完成"
log_info "目录结构:"
(cd "${DELIVERY_PATH}" && find . -maxdepth 2 -type f | sort | while read -r f; do
    size=$(du -h "$f" | cut -f1)
    echo "  $f ($size)"
done)

# ============================================================
# Phase 4: 打包
# ============================================================
log_step "Phase 4: 打包交付包"

DELIVERY_TAR="${RELEASES_DIR}/${DELIVERY_NAME}.tar.gz"
log_info "打包: ${DELIVERY_TAR}"
tar czf "${DELIVERY_TAR}" -C "${RELEASES_DIR}" "${DELIVERY_NAME}"

DELIVERY_TAR_SIZE=$(du -h "${DELIVERY_TAR}" | cut -f1)
log_info "交付包大小: ${DELIVERY_TAR_SIZE}"

# 清理暂存目录
rm -rf "${DELIVERY_PATH}"

# ============================================================
# Phase 5: 可选——同步到 clpm-deploy 远端仓库
# ============================================================
if [ "$PUSH_DEPLOY_REPO" = true ]; then
    log_step "Phase 5: 同步部署脚本到 clpm-deploy 仓库"

    DEPLOY_REPO_DIR="${RELEASES_DIR}/clpm-deploy-repo"

    # 克隆或更新
    if [ -d "${DEPLOY_REPO_DIR}" ]; then
        log_info "更新现有 clpm-deploy 仓库..."
        (cd "${DEPLOY_REPO_DIR}" && git pull --ff-only origin main 2>/dev/null || true)
    else
        log_info "克隆 clpm-deploy 仓库..."
        git clone "${DEPLOY_REPO_URL}" "${DEPLOY_REPO_DIR}"
    fi

    # 同步文件（注意：deploy.sh / docker-compose.prod.yml / .env.prod.example / README.md
    # 由 clpm-deploy 仓库独立维护——registry 拉取模式定制版，不同步覆盖）
    log_info "同步部署脚本（工具脚本 + SQL + 文档）..."
    mkdir -p "${DEPLOY_REPO_DIR}/deploy" "${DEPLOY_REPO_DIR}/db/postgresql" "${DEPLOY_REPO_DIR}/db/tdengine"

    cp "${SCRIPT_DIR}/backup.sh"                     "${DEPLOY_REPO_DIR}/deploy/backup.sh"
    cp "${SCRIPT_DIR}/rollback.sh"                   "${DEPLOY_REPO_DIR}/deploy/rollback.sh"
    cp "${SCRIPT_DIR}/lib-migrate.sh"                "${DEPLOY_REPO_DIR}/deploy/lib-migrate.sh"
    cp "${SCRIPT_DIR}/simulate-offline-deploy.sh"    "${DEPLOY_REPO_DIR}/deploy/simulate-offline-deploy.sh"
    cp "${SCRIPT_DIR}/nginx.conf"                    "${DEPLOY_REPO_DIR}/deploy/nginx.conf"

    cp "${PROJECT_ROOT}/db/postgresql/01_schema.sql"    "${DEPLOY_REPO_DIR}/db/postgresql/"
    cp "${PROJECT_ROOT}/db/postgresql/02_seed_data.sql" "${DEPLOY_REPO_DIR}/db/postgresql/"
    cp "${PROJECT_ROOT}/db/tdengine/01_supertable.sql"  "${DEPLOY_REPO_DIR}/db/tdengine/"

    if [ -d "${SCRIPT_DIR}/prometheus" ]; then
        cp -r "${SCRIPT_DIR}/prometheus" "${DEPLOY_REPO_DIR}/deploy/prometheus"
    fi
    if [ -d "${SCRIPT_DIR}/grafana" ]; then
        cp -r "${SCRIPT_DIR}/grafana" "${DEPLOY_REPO_DIR}/deploy/grafana"
    fi

    # README.md 由 clpm-deploy 独立维护（registry 拉取模式专用文档）
    if [ -f "${SCRIPT_DIR}/DEPLOYMENT-GUIDE.md" ]; then
        cp "${SCRIPT_DIR}/DEPLOYMENT-GUIDE.md" "${DEPLOY_REPO_DIR}/DEPLOYMENT-GUIDE.md"
    fi

    # 提交并推送
    (cd "${DEPLOY_REPO_DIR}" && \
        git add -A && \
        git commit -m "sync: 部署脚本同步自 CLPM ${GIT_COMMIT} (${BUILD_VERSION})" && \
        git push origin main)

    log_info "clpm-deploy 仓库已同步"
fi

# ============================================================
# 完成
# ============================================================
echo ""
echo "=== 打包完成 ==="
echo "交付包: ${DELIVERY_TAR}"
echo "大小:   ${DELIVERY_TAR_SIZE}"
echo "版本:   ${BUILD_VERSION} (commit: ${GIT_COMMIT})"
echo ""
echo "下一步:"
echo "  1. 传输到服务器: scp ${DELIVERY_TAR} <user>@<server>:/tmp/"
echo "  2. 服务器解压:   cd /tmp && tar xzf ${DELIVERY_NAME}.tar.gz"
echo "  3. 配置环境:     cd ${DELIVERY_NAME} && cp .env.prod.example .env.prod && vi .env.prod"
echo "  4. 执行部署:     ./deploy.sh"
echo ""
echo "交付包内容:"
echo "  - deploy.sh:           服务器端部署脚本（自动回滚+排查清单）"
echo "  - deploy/:             备份/回滚/迁移工具"
echo "  - docker-compose.prod.yml: 生产编排配置"
echo "  - .env.prod.example:   环境变量模板"
echo "  - db/:                  数据库初始化 SQL"
echo "  - images/:              预构建 Docker 镜像"
echo "  - README.md:            部署说明"
