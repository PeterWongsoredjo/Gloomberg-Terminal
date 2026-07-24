#!/usr/bin/env bash
# Nightly backup: Postgres dump, gold snapshot, bronze mirror, keep seven days.
set -euo pipefail

REPO="${GLOOMBERG_REPO:-/opt/gloomberg/Gloomberg-Terminal}"
BACKUP_ROOT="${GLOOMBERG_BACKUP_ROOT:-/var/backups/gloomberg}"
APP_USER="gloomberg"
UV="/home/$APP_USER/.local/bin/uv"
MC_IMAGE="minio/mc:RELEASE.2025-04-08T15-39-49Z"
KEEP_DAYS=7
DEST="$BACKUP_ROOT/daily/$(date +%F)"
GOLD="$REPO/services/backend-api/warehouse/gold.duckdb"

# shellcheck disable=SC1091
set -a && . "$REPO/.env" && set +a

mkdir -p "$DEST" "$BACKUP_ROOT/bronze-mirror"

echo "[backup] postgres dump"
docker compose --env-file "$REPO/.env" -f "$REPO/infra/docker-compose.yml" \
    exec -T postgres pg_dump -U "$POSTGRES_USER" gloomberg | gzip > "$DEST/gloomberg-pg.sql.gz"

echo "[backup] gold snapshot"
if [ -f "$GOLD" ]; then
    cp "$GOLD" "$DEST/gold.duckdb"
else
    echo "[backup] gold snapshot absent, first daily flow has not promoted yet"
fi

echo "[backup] bronze mirror"
docker run --rm --network gloomberg_default \
    -e MC_HOST_local="http://$MINIO_KEY:$MINIO_SECRET@minio:9000" \
    -v "$BACKUP_ROOT/bronze-mirror:/backup" \
    "$MC_IMAGE" mirror --overwrite local/gloomberg-bronze /backup

echo "[backup] manifest retention"
runuser -u "$APP_USER" -- bash -c "cd '$REPO/services/data-pipeline' && '$UV' run python -m pipeline.bronze.retention --apply"

echo "[backup] prune daily sets older than $KEEP_DAYS days"
find "$BACKUP_ROOT/daily" -mindepth 1 -maxdepth 1 -type d -mtime +"$KEEP_DAYS" -exec rm -rf {} +

echo "[backup] done: $DEST"
