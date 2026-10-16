#!/usr/bin/env bash
# puts one main commit live on this box and rolls back if it comes up unhealthy
set -euo pipefail

BASE="/opt/gloomberg"
REPO="$BASE/Gloomberg-Terminal"
RELEASES="$BASE/web-releases"
CURRENT="$BASE/web-current"
MANIFEST="$BASE/.installed.sha256"
NEW_ENV="$BASE/.env.new"
PREV_ENV="$BASE/.env.prev"
UV="/home/gloomberg/.local/bin/uv"
CHECK_ENV="/usr/local/bin/gloomberg-check-env"
PREFECT_API="http://127.0.0.1:4200/api"
KEEP_RELEASES=3
QUIET_WAIT_SECONDS=600

# files root copies into the system, a deploy never changes them on its own
INSTALLED_PATHS=(deploy/systemd deploy/caddy deploy/sudoers deploy/gloomberg-deploy.sh
    deploy/check-env.sh deploy/backup/gloomberg-backup.cron)
PY_PROJECTS=(services/data-pipeline services/orchestration services/backend-api dbt)
UNITS=(gloomberg-backend.service gloomberg-web.service gloomberg-prefect-worker.service)

SHA=""
LIVE=0
PREV_SHA=""
PREV_WEB=""
WEB_RELEASE=""

say() { printf '%s  %s\n' "$(date -u +%H:%M:%S)" "$*"; }
die() { say "ERROR: $*" >&2; exit 1; }

# prints a checksum line for every root installed file at a commit
manifest_at() {
    local rev="$1" path
    git -C "$REPO" ls-tree -r --name-only "$rev" -- "${INSTALLED_PATHS[@]}" | while read -r path; do
        printf '%s  %s\n' "$(git -C "$REPO" show "$rev:$path" | sha256sum | cut -d' ' -f1)" "$path"
    done
}

# only one deploy may run at a time
take_lock() {
    exec 9>"$BASE/.deploy.lock"
    flock -n 9 || die "another deploy is already running"
}

# the commit must be a full sha that is already on main
verify_commit() {
    [[ "$SHA" =~ ^[0-9a-f]{40}$ ]] || die "not a full commit sha: '$SHA'"
    git -C "$REPO" fetch -q origin main
    git -C "$REPO" merge-base --is-ancestor "$SHA" origin/main || die "$SHA is not on main"
    [ -z "$(git -C "$REPO" status --porcelain --untracked-files=no)" ] \
        || die "the repo on this box has local edits, clean them first"
}

# reads the new env file from stdin and checks it has every required value
receive_env() {
    (umask 077 && cat > "$NEW_ENV")
    "$CHECK_ENV" "$NEW_ENV" || die "the new env file is missing values, nothing changed"
}

# stops if the new commit changes anything only root can install
check_drift() {
    [ -f "$MANIFEST" ] || die "no install manifest, run deploy/bootstrap.sh as root once"
    diff -q <(manifest_at "$SHA") "$MANIFEST" >/dev/null \
        || die "this commit changes system files, run deploy/bootstrap.sh as root instead"
}

# counts busy flow runs, prints nothing when prefect is unreachable
busy_flow_runs() {
    curl -fsS -m 5 -X POST "$PREFECT_API/flow_runs/count" -H 'Content-Type: application/json' \
        -d '{"flow_runs":{"state":{"type":{"any_":["RUNNING","PENDING","CANCELLING"]}}}}' \
        2>/dev/null || true
}

# waits until no flow is mid run, because a restart would kill it
wait_for_quiet() {
    local waited=0 busy
    while true; do
        busy="$(busy_flow_runs)"
        if [ -z "$busy" ]; then
            say "prefect is unreachable, so no flow can be running"
            return
        fi
        [ "$busy" = "0" ] && return
        [ "$waited" -ge "$QUIET_WAIT_SECONDS" ] \
            && die "$busy flow runs still busy after ${QUIET_WAIT_SECONDS}s, nothing changed"
        say "waiting, $busy flow runs are busy"
        sleep 30
        waited=$((waited + 30))
    done
}

# builds the web app for this commit in its own folder while the live site keeps serving
build_web() {
    local domain
    WEB_RELEASE="$RELEASES/$SHA"
    if [ -f "$WEB_RELEASE/.build-ok" ]; then
        say "web build for this commit already exists"
        return
    fi
    rm -rf "$WEB_RELEASE"
    mkdir -p "$WEB_RELEASE"
    git -C "$REPO" archive "$SHA" services/web-terminal | tar -x -C "$WEB_RELEASE" --strip-components=2
    domain="$(grep -E '^GLOOMBERG_DOMAIN=' "$NEW_ENV" | head -1 | cut -d= -f2-)"
    (cd "$WEB_RELEASE" && nice -n 10 npm ci --no-audit --no-fund \
        && NEXT_PUBLIC_API_BASE="https://$domain/api/v1" NEXT_PUBLIC_API_TOKEN="" \
           NEXT_TELEMETRY_DISABLED=1 NODE_OPTIONS="--max-old-space-size=1536" nice -n 10 npm run build)
    touch "$WEB_RELEASE/.build-ok"
}

# remembers what is live now so a failure can put it back
save_point() {
    PREV_SHA="$(git -C "$REPO" rev-parse HEAD)"
    PREV_WEB="$(readlink -f "$CURRENT")"
    cp -p "$REPO/.env" "$PREV_ENV"
}

# points the web symlink at a release in one atomic step
point_web_at() {
    ln -sfn "$1" "$CURRENT.next"
    mv -T "$CURRENT.next" "$CURRENT"
}

# installs the locked python deps for every service and the dbt packages
sync_python() {
    local project
    for project in "${PY_PROJECTS[@]}"; do
        (cd "$REPO/$project" && "$UV" sync --frozen -q)
    done
    (cd "$REPO/dbt" && "$UV" run --no-sync dbt deps >/dev/null)
}

# applies infra changes, does nothing when compose is unchanged
apply_infra() {
    docker compose --env-file "$REPO/.env" -f "$REPO/infra/docker-compose.yml" up -d --wait minio postgres
}

# registers this commit's flows with the local prefect server
register_flows() {
    (cd "$REPO/services/orchestration" && PREFECT_API_URL="$PREFECT_API" \
        GLOOMBERG_ORCH_DIR="$REPO/services/orchestration" \
        "$UV" run --no-sync python scripts/setup_prefect.py >/dev/null)
}

# restarts the three app services onto the new code
restart_apps() {
    local unit
    for unit in "${UNITS[@]}"; do
        sudo -n /usr/bin/systemctl restart "$unit"
    done
}

# true once the api, the web app and all three services answer
healthy() {
    curl -fs -m 5 http://127.0.0.1:8000/api/v1/health >/dev/null \
        && curl -fs -m 5 http://127.0.0.1:3000 >/dev/null \
        && systemctl is-active --quiet "${UNITS[@]}"
}

# gives the new version a minute to come up healthy
wait_healthy() {
    local _
    for _ in $(seq 1 30); do
        healthy && return 0
        sleep 2
    done
    return 1
}

# switches code, env, deps, flows and web over to the new commit
go_live() {
    LIVE=1
    git -C "$REPO" checkout -q --detach "$SHA"
    mv "$NEW_ENV" "$REPO/.env"
    sync_python
    apply_infra
    register_flows
    point_web_at "$WEB_RELEASE"
    restart_apps
    wait_healthy || die "the new version is not healthy"
}

# puts the previous commit, env and web back after a failed go live
roll_back() {
    LIVE=0
    set +e
    say "rolling back to $PREV_SHA"
    git -C "$REPO" checkout -q --detach "$PREV_SHA"
    cp -p "$PREV_ENV" "$REPO/.env"
    sync_python
    register_flows
    point_web_at "$PREV_WEB"
    restart_apps
    if wait_healthy; then
        say "ROLLED BACK, the previous version is live again"
    else
        say "ROLLBACK UNHEALTHY, check journalctl on the box now"
    fi
}

# keeps the newest few web builds and never deletes the live or previous one
prune_releases() {
    local rel
    find "$RELEASES" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' | sort -rn \
        | tail -n +"$((KEEP_RELEASES + 1))" | cut -d' ' -f2- | while read -r rel; do
            [ "$rel" = "$WEB_RELEASE" ] || [ "$rel" = "$PREV_WEB" ] || rm -rf "$rel"
        done
}

# on any failure after going live, roll back before exiting
on_exit() {
    local code=$?
    if [ "$code" -ne 0 ] && [ "$LIVE" -eq 1 ]; then
        roll_back
    fi
    rm -f "$NEW_ENV"
    exit "$code"
}

main() {
    if [ "${1:-}" = "--manifest" ]; then
        manifest_at HEAD
        return
    fi
    SHA="${1:-${SSH_ORIGINAL_COMMAND:-}}"
    exec > >(tee -a "$BASE/deploy.log") 2>&1
    trap on_exit EXIT
    take_lock
    say "deploying $SHA"
    verify_commit
    receive_env
    check_drift
    wait_for_quiet
    build_web
    save_point
    go_live
    prune_releases
    say "DEPLOYED $SHA"
}

main "$@"
