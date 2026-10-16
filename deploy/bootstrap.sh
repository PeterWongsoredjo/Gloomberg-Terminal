#!/usr/bin/env bash
# Takes a fresh Ubuntu LTS VPS to a running Gloomberg Terminal. Safe to re-run.
set -euo pipefail

REPO="/opt/gloomberg/Gloomberg-Terminal"
APP_USER="gloomberg"
APP_HOME="/home/$APP_USER"
UV="$APP_HOME/.local/bin/uv"
NODE_MAJOR=22

say() { printf '\n=== %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run as root: sudo bash deploy/bootstrap.sh"
[ -d "$REPO" ] || die "clone the repo to $REPO first"
[ -f "$REPO/.env" ] || die "copy the operator .env to $REPO/.env first, see docs/DEPLOYMENT.md"

say "checking required .env variables"
bash "$REPO/deploy/check-env.sh" "$REPO/.env" || die "fill the variables above, then re-run"
GLOOMBERG_DOMAIN="$(grep -E '^GLOOMBERG_DOMAIN=' "$REPO/.env" | head -1 | cut -d= -f2-)"

say "timezone Asia/Jakarta"
timedatectl set-timezone Asia/Jakarta

say "base packages"
apt-get update -y
apt-get install -y ca-certificates curl gnupg git ufw cron

say "firewall: allow 22, 80, 443 only"
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

say "docker engine"
if ! command -v docker >/dev/null 2>&1; then
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
        > /etc/apt/sources.list.d/docker.list
    apt-get update -y
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
fi

say "node $NODE_MAJOR"
if ! command -v node >/dev/null 2>&1 || [ "$(node -v | sed 's/^v//' | cut -d. -f1)" != "$NODE_MAJOR" ]; then
    curl -fsSL "https://deb.nodesource.com/setup_${NODE_MAJOR}.x" | bash -
    apt-get install -y nodejs
fi

say "caddy"
if ! command -v caddy >/dev/null 2>&1; then
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
        | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
        > /etc/apt/sources.list.d/caddy-stable.list
    apt-get update -y
    apt-get install -y caddy
fi

say "app user and permissions"
id "$APP_USER" >/dev/null 2>&1 || useradd --create-home --shell /bin/bash "$APP_USER"
usermod -aG docker "$APP_USER"
chown -R "$APP_USER:$APP_USER" /opt/gloomberg
chmod 600 "$REPO/.env"

say "uv"
[ -x "$UV" ] || runuser -u "$APP_USER" -- bash -c 'curl -LsSf https://astral.sh/uv/install.sh | sh'

say "python environments"
for svc in services/data-pipeline services/orchestration services/backend-api dbt; do
    runuser -u "$APP_USER" -- bash -c "cd '$REPO/$svc' && '$UV' sync"
done
runuser -u "$APP_USER" -- bash -c "cd '$REPO/dbt' && '$UV' run dbt deps"

say "web terminal build, NEXT_PUBLIC values are baked in here"
runuser -u "$APP_USER" -- bash -c "cd '$REPO/services/web-terminal' && npm ci \
    && NEXT_PUBLIC_API_BASE=\"https://${GLOOMBERG_DOMAIN}/api/v1\" NEXT_PUBLIC_API_TOKEN='' npm run build"

say "web release folder, the web unit runs whatever web-current points at"
runuser -u "$APP_USER" -- mkdir -p /opt/gloomberg/web-releases
[ -e /opt/gloomberg/web-current ] \
    || runuser -u "$APP_USER" -- ln -s "$REPO/services/web-terminal" /opt/gloomberg/web-current

say "systemd units and caddy config"
cp "$REPO"/deploy/systemd/*.service /etc/systemd/system/
cp "$REPO/deploy/caddy/Caddyfile" /etc/caddy/Caddyfile
mkdir -p /etc/systemd/system/caddy.service.d
printf '[Service]\nEnvironment=GLOOMBERG_DOMAIN=%s\n' "$GLOOMBERG_DOMAIN" \
    > /etc/systemd/system/caddy.service.d/gloomberg.conf
systemctl daemon-reload

say "starting services"
systemctl enable --now gloomberg-infra.service
systemctl enable --now gloomberg-prefect-server.service
systemctl enable --now gloomberg-backend.service
systemctl enable --now gloomberg-web.service
systemctl restart caddy

say "prefect registration"
for _ in $(seq 1 60); do
    curl -fs http://127.0.0.1:4200/api/health >/dev/null 2>&1 && break
    sleep 2
done
curl -fs http://127.0.0.1:4200/api/health >/dev/null 2>&1 || die "prefect server did not come up"
runuser -u "$APP_USER" -- bash -c "cd '$REPO/services/orchestration' \
    && PREFECT_API_URL=http://127.0.0.1:4200/api GLOOMBERG_ORCH_DIR='$REPO/services/orchestration' \
    '$UV' run python scripts/setup_prefect.py"
systemctl enable --now gloomberg-prefect-worker.service

say "backup cron"
install -m 644 "$REPO/deploy/backup/gloomberg-backup.cron" /etc/cron.d/gloomberg-backup

say "deploy access, the ci key may only run the deploy script"
install -m 755 "$REPO/deploy/gloomberg-deploy.sh" /usr/local/bin/gloomberg-deploy
install -m 755 "$REPO/deploy/check-env.sh" /usr/local/bin/gloomberg-check-env
visudo -cf "$REPO/deploy/sudoers/gloomberg-deploy" >/dev/null || die "deploy/sudoers/gloomberg-deploy is invalid"
install -m 440 "$REPO/deploy/sudoers/gloomberg-deploy" /etc/sudoers.d/gloomberg-deploy
if [ -f /opt/gloomberg/deploy_key.pub ]; then
    install -d -m 700 -o "$APP_USER" -g "$APP_USER" "$APP_HOME/.ssh"
    install -m 600 -o "$APP_USER" -g "$APP_USER" /dev/null "$APP_HOME/.ssh/authorized_keys.new"
    [ -f "$APP_HOME/.ssh/authorized_keys" ] && cat "$APP_HOME/.ssh/authorized_keys" > "$APP_HOME/.ssh/authorized_keys.new"
    deploy_key="$(cat /opt/gloomberg/deploy_key.pub)"
    grep -qF "$deploy_key" "$APP_HOME/.ssh/authorized_keys.new" \
        || printf 'restrict,command="/usr/local/bin/gloomberg-deploy" %s\n' "$deploy_key" >> "$APP_HOME/.ssh/authorized_keys.new"
    mv "$APP_HOME/.ssh/authorized_keys.new" "$APP_HOME/.ssh/authorized_keys"
    echo "deploy key installed for $APP_USER"
else
    echo "no /opt/gloomberg/deploy_key.pub yet, skipping the deploy key"
fi
runuser -u "$APP_USER" -- /usr/local/bin/gloomberg-deploy --manifest > /opt/gloomberg/.installed.sha256

say "verifying"
sleep 3
for unit in gloomberg-infra gloomberg-prefect-server gloomberg-prefect-worker gloomberg-backend gloomberg-web caddy; do
    systemctl is-active --quiet "$unit" || die "$unit is not active, check: journalctl -u $unit"
    echo "active: $unit"
done
curl -fs http://127.0.0.1:8000/api/v1/health >/dev/null || die "backend health check failed"
echo "backend health OK"
curl -fs http://127.0.0.1:3000 >/dev/null || die "web terminal check failed"
echo "web terminal OK"

say "bootstrap complete"
echo "verify from outside:  https://${GLOOMBERG_DOMAIN}/"
echo "seed the first gold snapshot by running the daily flow, see docs/DEPLOYMENT.md"
