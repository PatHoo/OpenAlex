#!/usr/bin/env bash
# 云主机安装 PostgreSQL 17 + PostGIS，建库并可选恢复试跑转储。
# 用法: sudo bash setup_postgres.sh '<数据库密码>' [转储文件.dump]
# 注意: 只监听 127.0.0.1；5432 不对公网开放。
set -euo pipefail

DB_PASS="${1:?用法: setup_postgres.sh <数据库密码> [dump文件]}"
DUMP="${2:-}"

. /etc/os-release
CODENAME="${VERSION_CODENAME:-}"
if [ -z "$CODENAME" ]; then
    echo "无法识别系统版本（VERSION_CODENAME 为空）" >&2
    exit 1
fi

echo "[1/6] 添加 PGDG 源并安装 PostgreSQL 17 + PostGIS（$CODENAME）"
install -d -m 0755 /usr/share/keyrings
curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
    | gpg --dearmor -o /usr/share/keyrings/postgresql.gpg
echo "deb [signed-by=/usr/share/keyrings/postgresql.gpg] http://apt.postgresql.org/pub/repos/apt ${CODENAME}-pgdg main" \
    > /etc/apt/sources.list.d/pgdg.list
# 若官方源在国内较慢，把上面的地址换成云厂商镜像站提供的 PGDG 镜像（以镜像站文档为准）
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y \
    postgresql-17 postgresql-client-17 postgresql-17-postgis-3

echo "[2/6] 限制只监听本机，并设置内存参数（16 GB 内存机器）"
CONF=/etc/postgresql/17/main/postgresql.conf
sed -ri "s/^#?listen_addresses.*/listen_addresses = '127.0.0.1'/" "$CONF"
sed -ri "s/^#?shared_buffers.*/shared_buffers = 4GB/" "$CONF"
sed -ri "s/^#?effective_cache_size.*/effective_cache_size = 12GB/" "$CONF"
sed -ri "s/^#?max_connections.*/max_connections = 100/" "$CONF"

HBA=/etc/postgresql/17/main/pg_hba.conf
if ! grep -q '^host all all 0.0.0.0/0 reject' "$HBA"; then
    cat >> "$HBA" <<'EOF'
host all all 0.0.0.0/0 reject
host all all ::/0 reject
EOF
fi

systemctl enable --now postgresql
systemctl restart postgresql

echo "[3/6] 创建角色与数据库"
if ! su - postgres -c "psql -tAc \"SELECT 1 FROM pg_roles WHERE rolname='oa_app'\"" | grep -q 1; then
    su - postgres -c "psql -v ON_ERROR_STOP=1 -c \"CREATE ROLE oa_app LOGIN PASSWORD '${DB_PASS}'\""
fi
if ! su - postgres -c "psql -tAc \"SELECT 1 FROM pg_database WHERE datname='openalex'\"" | grep -q 1; then
    su - postgres -c "createdb -O oa_app openalex"
fi

echo "[4/6] 恢复转储（如提供）"
if [ -n "$DUMP" ]; then
    [ -f "$DUMP" ] || { echo "转储文件不存在: $DUMP" >&2; exit 1; }
    PGPASSWORD="$DB_PASS" pg_restore --no-owner --no-privileges \
        -h 127.0.0.1 -U oa_app -d openalex "$DUMP" || true
    echo "pg_restore 已完成；若上方出现扩展相关报错，请核对转储来源与 PostGIS 版本"
fi

echo "[5/6] 确保 PostGIS 扩展存在、更新统计"
su - postgres -c "psql -v ON_ERROR_STOP=1 -d openalex -c 'CREATE EXTENSION IF NOT EXISTS postgis'"
PGPASSWORD="$DB_PASS" psql -h 127.0.0.1 -U oa_app -d openalex -c 'ANALYZE;' >/dev/null

echo "[6/6] 完成。pipeline 已支持原生 Linux（OA_PATH_MODE=linux），无需 /mnt/c 软链"
echo "核对监听与行数："
ss -lntp | grep 5432 || true
PGPASSWORD="$DB_PASS" psql -h 127.0.0.1 -U oa_app -d openalex \
    -c "SELECT PostGIS_Version(); SELECT count(*) AS works FROM work;"
