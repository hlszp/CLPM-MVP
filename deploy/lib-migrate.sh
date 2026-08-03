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

# Alembic 版本同步 + 种子数据加载：首次部署 stamp head，后续升级 upgrade head，
# 之后加载种子数据。任何一步失败都以非零退出码向上传播。注意显式 || return 1：
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

    # 种子数据加载（2026-08-03 修复：升级部署也需加载，详见 load_seed_data）
    load_seed_data || return 1
}

# ============================================================
# 种子数据加载（2026-08-03：修复升级部署不加载种子数据的设计缺陷）
#
# 背景：02_seed_data.sql 原本仅由 postgres 容器 entrypoint 在首次初始化
# （数据卷为空）时自动执行。升级部署时数据卷已有数据，entrypoint 跳过，
# 导致新增/修改的种子数据（diagnosis_rule、dcs_vendor、metric_config 等）
# 无法同步，引发"表为空""指标缺失"等运行时故障（2026-08-03 zpdev 事故）。
#
# 本函数在每次部署（首次+升级）后显式加载 02_seed_data.sql。
# 脚本 v1.5 已全表 ON CONFLICT 幂等化，可安全重复执行。
#
# 前置条件：调用方须定义 postgres_exec()，与 backend_exec()/tdengine_exec() 同模式：
#   postgres_exec() { compose_prod exec -T postgres "$@"; }           # 本地部署
#   postgres_exec() { ssh "$SSH_HOST" "docker exec -i clpm-postgres $*"; }  # 远程部署
# postgres 容器内 psql 经 unix socket 连接，pg_hba local trust 免密。
# 种子文件由 docker-compose.prod.yml 挂载至 /docker-entrypoint-initdb.d/02_seed_data.sql:ro，
# 该路径在容器生命周期内始终可用。
# ============================================================
load_seed_data() {
    local pg_user pg_db seed_file
    seed_file="/docker-entrypoint-initdb.d/02_seed_data.sql"

    pg_user=$(postgres_exec printenv POSTGRES_USER 2>/dev/null | tr -d '\r\n' || true)
    pg_db=$(postgres_exec printenv POSTGRES_DB 2>/dev/null | tr -d '\r\n' || true)

    if [ -z "$pg_user" ] || [ -z "$pg_db" ]; then
        echo "  [WARN] 无法读取 POSTGRES_USER/POSTGRES_DB，跳过种子数据加载"
        return 0
    fi

    echo "  加载种子数据 ${seed_file}（v1.5 幂等，ON CONFLICT 安全重复执行）..."

    local seed_output
    seed_output=$(postgres_exec psql -U "$pg_user" -d "$pg_db" \
        -v ON_ERROR_STOP=1 -f "$seed_file" 2>&1) || {
        echo "  [FAIL] 种子数据加载失败"
        echo "$seed_output" | tail -30
        return 1
    }

    echo "  [OK] 种子数据加载完成"
}

# ============================================================
# TDengine schema 校验（2026-08-01：兜底 init 脚本未执行场景）
#
# 背景：TDengine Docker 镜像 entrypoint 从 /docker-entrypoint-initdb.d/
# 读取初始化 SQL。若挂载路径不对（曾经误挂到 /root/init/）或卷重置后
# 容器未完全重建，init 脚本不会执行，导致 clpm_ts 数据库和 st_loop_data
# 超级表缺失，后端写入报 [0x0200]: db is not specified。
#
# 本函数在部署时显式校验并补建，作为 entrypoint init 的兜底。
#
# 前置条件：调用方须定义 tdengine_exec()，与 backend_exec() 同模式：
#   tdengine_exec() { compose_prod exec -T tdengine "$@"; }
#   tdengine_exec() { ssh "$SSH_HOST" "docker exec clpm-tdengine $*"; }
# 以及 backend_exec()（用于读取 TDENGINE_PASSWORD/TDENGINE_PORT 环境变量）。
# ============================================================
tdengine_ensure_schema() {
    local td_pass td_port td_rest_port
    td_pass=$(backend_exec printenv TDENGINE_PASSWORD 2>/dev/null | tr -d '\r\n' || true)
    td_port=$(backend_exec printenv TDENGINE_PORT 2>/dev/null | tr -d '\r\n' || true)
    td_rest_port=$((td_port + 11))  # REST API 端口 = 原生端口 + 11

    if [ -z "$td_pass" ] || [ -z "$td_port" ]; then
        echo "  [WARN] 无法读取 TDENGINE_PASSWORD/TDENGINE_PORT，跳过 TDengine schema 校验"
        return 0
    fi

    echo "  检查 TDengine clpm_ts 数据库（REST :${td_rest_port}）..."

    # 通过 REST API 检查数据库是否存在
    local db_check
    db_check=$(tdengine_exec curl -s -u "root:${td_pass}" \
        "http://localhost:${td_rest_port}/rest/sql" -d 'SHOW DATABASES' 2>/dev/null || echo "")

    if echo "$db_check" | grep -q '"clpm_ts"'; then
        echo "  [OK] clpm_ts 数据库已存在"
    else
        echo "  [WARN] clpm_ts 数据库不存在，执行初始化 DDL..."
        tdengine_exec curl -s -u "root:${td_pass}" \
            "http://localhost:${td_rest_port}/rest/sql" \
            -d "CREATE DATABASE IF NOT EXISTS clpm_ts KEEP 365 DURATION 10 PRECISION 'ms'" >/dev/null 2>&1
        tdengine_exec curl -s -u "root:${td_pass}" \
            "http://localhost:${td_rest_port}/rest/sql/clpm_ts" \
            -d "CREATE STABLE IF NOT EXISTS st_loop_data (ts TIMESTAMP, pv FLOAT, sp FLOAT, op FLOAT, mode TINYINT, pid_p FLOAT, pid_i FLOAT, pid_d FLOAT, pv_quality TINYINT) TAGS (loop_id BINARY(36), unit_id BINARY(36))" >/dev/null 2>&1

        # 复查超级表
        local recheck
        recheck=$(tdengine_exec curl -s -u "root:${td_pass}" \
            "http://localhost:${td_rest_port}/rest/sql/clpm_ts" -d 'SHOW STABLES' 2>/dev/null || echo "")
        if echo "$recheck" | grep -q '"st_loop_data"'; then
            echo "  [OK] clpm_ts 数据库和 st_loop_data 超级表已创建"
        else
            echo "  [FAIL] TDengine schema 初始化失败"
            return 1
        fi
    fi
}
