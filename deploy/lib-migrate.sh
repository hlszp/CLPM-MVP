#!/bin/bash
# ============================================================
# CLPM 公共部署函数库：数据库 Alembic 版本同步
#
# 设计原则（2026-07-28 Phase 5）：部署=迁移一体。
#   - deploy.sh（服务器本地部署）与 build-and-deploy.sh（离线镜像远程部署）
#     两条路径都必须执行 Alembic 版本同步，且失败即中止（set -e 语义）。
#   - 项目红线：模型变更必须与迁移同批应用。迁移失败却继续部署会让
#     新代码跑在旧 schema 上，属于隐性故障，必须显式中止。
#
# 用法：调用方在 source 本文件前，必须先定义 backend_exec()：
#   # 本地（服务器上直接执行）：
#   backend_exec() { compose_prod exec -T backend "$@"; }
#   # 远程（macOS 构建机通过 SSH 在服务器上执行）：
#   backend_exec() { ssh "$SSH_HOST" "docker exec -i clpm-backend $*"; }
# ============================================================

# Alembic 版本同步：首次部署 stamp head，后续升级 upgrade head。
# 任何一步失败都以非零退出码向上传播。注意显式 || return 1：
# 调用方若以 if alembic_sync_head 形式调用，set -e 在函数体内会失效，
# 必须显式传递失败，不能把"迁移失败"吞成"OK"。
alembic_sync_head() {
    local current_rev
    current_rev=$(backend_exec alembic current 2>/dev/null | grep -oE '^[a-z0-9]+' | head -1 || true)

    if [ -z "$current_rev" ]; then
        echo "  首次部署：执行 alembic stamp head（标记当前版本，不重复执行 DDL）..."
        backend_exec alembic stamp head || return 1
        echo "  [OK] Alembic 版本已标记为 head"
    else
        echo "  当前版本：${current_rev}，执行 alembic upgrade head..."
        backend_exec alembic upgrade head || return 1
        echo "  [OK] 数据库迁移完成（${current_rev} → head）"
    fi
}
