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

# 从 .env.prod 读取数据库配置
if [ -f .env.prod ]; then
    export $(grep -v '^#' .env.prod | grep -E '^(POSTGRES_|PG|TDENGINE_)' | xargs)
fi

PG_HOST="${POSTGRES_HOST:-clpm-postgres}"
PG_PORT="${POSTGRES_PORT:-5432}"
PG_USER="${POSTGRES_USER:-clpm}"
PG_DB="${POSTGRES_DB:-clpm}"

# TDengine 配置（与 .env.prod 中 TDENGINE_DB 保持一致，避免硬编码）
TD_HOST="${TDENGINE_HOST:-clpm-tdengine}"
TD_DB="${TDENGINE_DB:-clpm_ts}"

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

TD_FILE="${BACKUP_SUBDIR}/tdengine_${TIMESTAMP}.sql.gz"

# 通过 docker exec 在 tdengine 容器内执行 taos dump
docker exec "$TD_HOST" taos -s "USE ${TD_DB}; SHOW TABLES;" >/dev/null 2>&1
if [ $? -eq 0 ]; then
    docker exec "$TD_HOST" taosdump -D "$TD_DB" 2>/dev/null | gzip > "$TD_FILE"
    TD_SIZE=$(du -h "$TD_FILE" | cut -f1)
    echo "  [OK] TDengine 备份完成: ${TD_FILE} (${TD_SIZE})"
else
    echo "  [SKIP] TDengine 数据库 ${TD_DB} 不存在或不可达，跳过"
    rm -f "$TD_FILE"
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
