#!/usr/bin/env bash
# 每日数据库转储：本地保留 7 天，并上传独立 COS 桶（保留期由桶生命周期控制）。
# 依赖 /etc/openalex/db.env，至少包含：
#   DB_PASSWORD=...
#   COS_SECRET_ID=...
#   COS_SECRET_KEY=...
#   BACKUP_BUCKET=...
#   COS_REGION=ap-guangzhou
# crontab: 0 3 * * * /opt/openalex/backup_postgres.sh >> /var/log/openalex-backup.log 2>&1
set -euo pipefail

ENV_FILE=/etc/openalex/db.env
[ -r "$ENV_FILE" ] || { echo "无法读取 $ENV_FILE" >&2; exit 1; }
set -a
. "$ENV_FILE"
set +a

: "${DB_PASSWORD:?DB_PASSWORD 未设置}"
: "${BACKUP_BUCKET:?BACKUP_BUCKET 未设置}"

BACKUP_DIR=/var/backups/openalex
STAMP="$(date +%F_%H%M)"
FILE="$BACKUP_DIR/openalex_${STAMP}.dump"
mkdir -p "$BACKUP_DIR"

echo "[$(date -Is)] 开始转储"
PGPASSWORD="$DB_PASSWORD" pg_dump -h 127.0.0.1 -U oa_app -Fc openalex -f "$FILE"
[ -s "$FILE" ] || { echo "转储文件为空，终止" >&2; exit 1; }
echo "转储完成: $(du -h "$FILE" | cut -f1)"

if command -v coscmd >/dev/null 2>&1 && [ -n "${COS_SECRET_ID:-}" ] && [ -n "${COS_SECRET_KEY:-}" ]; then
    coscmd config -a "$COS_SECRET_ID" -s "$COS_SECRET_KEY" \
        -b "$BACKUP_BUCKET" -r "${COS_REGION:-ap-guangzhou}"
    coscmd upload "$FILE" "db/$(basename "$FILE")"
    echo "已上传 COS: db/$(basename "$FILE")"
else
    echo "coscmd 或 COS 凭据缺失，本次仅保留本地副本"
fi

find "$BACKUP_DIR" -name 'openalex_*.dump' -mtime +7 -delete
echo "[$(date -Is)] 完成"
