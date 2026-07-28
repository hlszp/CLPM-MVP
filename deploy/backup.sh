#!/bin/bash
# ============================================================
# CLPM 数据自动备份脚本 (S2-B4)
# 功能：PostgreSQL pg_dump + TDengine 数据导出
# 用法：./deploy/backup.sh [backup_dir]
# 定时执行（crontab）：
#   0 2 * * * /path/to/CLPM/deploy/backup.sh >> /var/log/clpm-backup.log 2>&1
# 建议策略：每日 02:00 全量备份，保留最近 30 天
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# 备份目录
BACKUP_DIR="${1:-/data/backups/clpm}"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_SUBDIR="${BACKUP_DIR}/${TIMESTAMP}"

mkdir -p "$BACKUP_SUBDIR"
echo "=== CLPM 数据备份开始 ${TIMESTAMP} ==="
echo "备份目录: ${BACKUP_SUBDIR}"

# ------------------------------------------------------------
# 1. PostgreSQL 备份（pg_dump）
# ------------------------------------------------------------
echo ""
echo "[1/3] PostgreSQL 备份..."

# 安全读取 .env.prod（不 source/执行配置内容）
read_env_value() {
    local key="$1"
    if [ -f .env.prod ]; then
        sed -n "s/^${key}=//p" .env.prod | tail -1
    fi
}

PG_USER="$(read_env_value POSTGRES_USER)"
PG_USER="${PG_USER:-clpm}"
PG_DB="$(read_env_value POSTGRES_DB)"
PG_DB="${PG_DB:-clpm}"

# TDENGINE_HOST 是 Compose 网络内 DNS 名，不是 docker exec 所需的容器名。
TD_CONTAINER="clpm-tdengine"
TD_DB="$(read_env_value TDENGINE_DB)"
TD_DB="${TD_DB:-clpm_ts}"

PG_FILE="${BACKUP_SUBDIR}/postgres_${TIMESTAMP}.sql.gz"

# 通过 docker exec 在 postgres 容器内执行 pg_dump
docker exec clpm-postgres pg_dump \
    -U "$PG_USER" \
    -d "$PG_DB" \
    --no-owner \
    --no-privileges \
    --clean \
    --if-exists \
    2>/dev/null | gzip > "$PG_FILE"

PG_SIZE=$(du -h "$PG_FILE" | cut -f1)
echo "  [OK] PostgreSQL 备份完成: ${PG_FILE} (${PG_SIZE})"

# ------------------------------------------------------------
# 2. TDengine 备份（taos dump）
# ------------------------------------------------------------
echo ""
echo "[2/3] TDengine 备份..."

TD_FILE="${BACKUP_SUBDIR}/tdengine_${TIMESTAMP}.tar.gz"
TD_DUMP_NAME="tdengine_dump_${TIMESTAMP}"
TD_CONTAINER_DUMP_DIR="/tmp/${TD_DUMP_NAME}"
TD_LOCAL_DUMP_DIR="${BACKUP_SUBDIR}/${TD_DUMP_NAME}"

# 生产 TDengine 已通过 TAOS_ROOT_PASSWORD 改密，备份必须带 root 凭据，
# 否则认证失败会静默跳过（原 [SKIP] 分支），造成"有备份流程、无备份数据"。
TD_PASSWORD="$(read_env_value TDENGINE_PASSWORD)"
if [ -z "$TD_PASSWORD" ]; then
    echo "  [FAIL] .env.prod 未设置 TDENGINE_PASSWORD，无法备份 TDengine"
    exit 1
fi

# 通过 docker exec 在 tdengine 容器内执行 taos dump（带 root 凭据）。
# 认证失败/库不可达/导出失败均为硬失败：备份是部署与容灾的前置保障，
# 静默跳过会让运维误以为数据可回滚。
if docker exec "$TD_CONTAINER" taos -u root -p"$TD_PASSWORD" -s "USE ${TD_DB}; SHOW TABLES;" >/dev/null 2>&1; then
    docker exec "$TD_CONTAINER" taosdump -u root -p"$TD_PASSWORD" -D "$TD_DB" -o "$TD_CONTAINER_DUMP_DIR" >/dev/null
    docker cp "${TD_CONTAINER}:${TD_CONTAINER_DUMP_DIR}" "$TD_LOCAL_DUMP_DIR" >/dev/null
    tar -C "$BACKUP_SUBDIR" -czf "$TD_FILE" "$TD_DUMP_NAME"
    rm -rf "$TD_LOCAL_DUMP_DIR"
    docker exec "$TD_CONTAINER" rm -rf "$TD_CONTAINER_DUMP_DIR"
    TD_SIZE=$(du -h "$TD_FILE" | cut -f1)
    echo "  [OK] TDengine 备份完成: ${TD_FILE} (${TD_SIZE})"
else
    echo "  [FAIL] TDengine 数据库 ${TD_DB} 不可达或认证失败（容器 ${TD_CONTAINER}）"
    echo "  排查：docker ps | grep tdengine；确认 .env.prod 的 TDENGINE_PASSWORD 与容器 TAOS_ROOT_PASSWORD 一致"
    exit 1
fi

# ------------------------------------------------------------
# 3. 清理过期备份（保留最近 30 天）
# ------------------------------------------------------------
echo ""
echo "[3/3] 清理过期备份（保留最近 30 天）..."
DELETED_COUNT=$(find "$BACKUP_DIR" -maxdepth 1 -type d -name "20*" -mtime +30 -exec rm -rf {} + 2>/dev/null | wc -l || echo 0)
REMAINING=$(find "$BACKUP_DIR" -maxdepth 1 -type d -name "20*" | wc -l)
echo "  已清理过期备份，剩余 ${REMAINING} 份"

# ------------------------------------------------------------
# 备份摘要
# ------------------------------------------------------------
echo ""
echo "=== 备份完成 ==="
echo "  PostgreSQL: ${PG_FILE}"
if [ -f "$TD_FILE" ]; then
    echo "  TDengine:   ${TD_FILE}"
fi
echo "  总大小: $(du -sh "$BACKUP_SUBDIR" | cut -f1)"
echo "  时间戳: ${TIMESTAMP}"
