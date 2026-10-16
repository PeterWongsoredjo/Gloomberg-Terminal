# Gloomberg Terminal — VPS Deployment & Operations

This is the operational runbook for running the terminal unattended on one Ubuntu
LTS VPS. It also closes, operationally, the known documentation gap flagged in
`CLAUDE.md`: no architecture document owns infrastructure. Container resource
config remains ratified where it is (`01` §3.2, `02` §3.1, `03` OR-05); this
document owns *deployment*.

## 0. Trust-boundary amendment (read first)

`docs/architecture/01_system_overview.md` §3.1(b) ratified: *no inbound listener
is exposed to the public internet*. This deployment deliberately amends that with
**exactly one** inbound listener: Caddy on 80/443, TLS-terminating, reverse-proxying
to loopback. Everything else keeps the original posture:

| Surface | Exposure |
|---|---|
| Caddy (80/443) | **public** — the single ingress |
| FastAPI backend (8000) | loopback only, reached via Caddy `/api/*` |
| Next.js terminal (3000) | loopback only, reached via Caddy `/` |
| Prefect UI (4200) | loopback only — SSH tunnel |
| MinIO S3/console (9000/9001) | loopback only — SSH tunnel |
| Postgres (5432) | loopback only |

What makes the one ingress safe: the public API surface is read-only
(`GET` endpoints + the tape WebSocket), dispatch endpoints require the
`GLOOMBERG_API_TOKEN` bearer, per-IP rate limiting (`GLOOMBERG_RATE_LIMIT`,
in-memory — which is why the backend runs exactly one uvicorn process), and the
public web bundle ships with `NEXT_PUBLIC_API_TOKEN` empty.

## 1. Provision

- One Ubuntu LTS (22.04 or 24.04) VPS, 8 GB RAM / 4 vCPU (the ratified budget), ≥40 GB disk.
- A DNS **A record** for your domain pointing at the VPS IP (Caddy needs it for
  automatic HTTPS). Pick the domain before bootstrapping — it is baked into the
  web bundle at build time.
- SSH access as root (or sudo). Harden as you prefer (key-only auth recommended);
  the bootstrap enables ufw with only 22/80/443 open.

## 2. Repo and secrets

```sh
sudo mkdir -p /opt/gloomberg
sudo git clone <your-repo-url> /opt/gloomberg/Gloomberg-Terminal
```

Create the repo-root `.env` **on your dev box** (never commit it) and copy it up:

```sh
scp .env root@<vps-ip>:/opt/gloomberg/Gloomberg-Terminal/.env
```

Required variables (plain `KEY=value` lines, no `export`, no quotes — the file is
read by systemd `EnvironmentFile`, docker compose `--env-file`, and the Python
services alike):

| Variable | VPS value |
|---|---|
| `GLOOMBERG_DOMAIN` | your domain, e.g. `terminal.example.com` (no scheme) |
| `MINIO_KEY` / `MINIO_SECRET` | MinIO root credentials |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` | Postgres credentials |
| `POSTGRES_PORT` | `5432` (the dev box uses 5433 to dodge a native PG; the VPS has none) |
| `POSTGRES_HOST` | `127.0.0.1` |
| `MINIO_ENDPOINT` | `127.0.0.1:9000` |
| `GLOOMBERG_POSTGRES_DSN` | `postgresql://<user>:<password>@127.0.0.1:5432/gloomberg` |
| `GLOOMBERG_WEB_TERMINAL_ORIGIN` | `https://<your-domain>` (CORS) |
| `GLOOMBERG_API_TOKEN` | operator bearer — generate: `openssl rand -hex 32` |
| `GLOOMBERG_ORCH_BACKEND_API_TOKEN` | **same value** as `GLOOMBERG_API_TOKEN` |
| `GROQ_API_KEY` | Groq key (fallback provider) |
| `GOOGLE_AI_STUDIO_API_KEY` | Gemini key (primary provider) |
| `GROQ_MODEL` / `GEMINI_MODEL` | optional model pins; set when a provider retires a model |
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_BASE_URL` | Langfuse tracing |
| `WEBSHARE_PROXY_HOST` / `_PORT` / `_USERNAME` / `_PASSWORD` | residential proxy for IDX fetches; leave the host empty to go direct |
| `PREFECT_API_KEY` | only when using Prefect Cloud instead of the local server (§8) |
| `GRAFANA_ADMIN_PASSWORD` | only when the opt-in monitoring stack is up |
| `BQ_PROJECT` / `BQ_CREDENTIALS` / `BQ_LOCATION` | optional BigQuery analyst mirror |

The full list of names lives in the repo-root `.env.example`.

Never put a real token in `NEXT_PUBLIC_API_TOKEN` — anything `NEXT_PUBLIC_`
compiles into the public browser bundle. The bootstrap builds the web bundle with
it explicitly empty.

## 3. Bootstrap

```sh
cd /opt/gloomberg/Gloomberg-Terminal
sudo bash deploy/bootstrap.sh
```

Idempotent — safe to re-run after fixing anything it complains about. It:
sets timezone Asia/Jakarta, installs Docker Engine / Node 22 (NodeSource) /
Caddy / uv, creates the `gloomberg` service user, syncs all four Python
environments plus `dbt deps`, builds the web bundle with
`NEXT_PUBLIC_API_BASE=https://$GLOOMBERG_DOMAIN/api/v1`, installs and starts the
five systemd units plus Caddy, registers the three Prefect deployments, installs
the backup cron, and runs a verify block.

The five units, in dependency order:

| Unit | Runs |
|---|---|
| `gloomberg-infra` | `docker compose up -d --wait` (Postgres + MinIO, loopback ports) |
| `gloomberg-prefect-server` | Prefect server on 127.0.0.1:4200 |
| `gloomberg-prefect-worker` | worker, pool `gloomberg-local` (executes all three flows) |
| `gloomberg-backend` | uvicorn on 127.0.0.1:8000 — **one process, by design** |
| `gloomberg-web` | `next start` on 127.0.0.1:3000 |

All have `Restart=on-failure` and are enabled — a reboot brings the whole stack
back with no hands.

## 4. Verify

```sh
systemctl status gloomberg-infra gloomberg-prefect-server gloomberg-prefect-worker gloomberg-backend gloomberg-web caddy
curl -s http://127.0.0.1:8000/api/v1/health
curl -sI https://<your-domain>/            # 200, TLS from Let's Encrypt
```

- Open `https://<your-domain>/` — the terminal renders; the Live Tape panel
  connects its WebSocket (`wss://<your-domain>/api/v1/tape/stream`, proxied by
  Caddy with the upgrade handled automatically).
- Prefect UI (loopback only): `ssh -L 4200:127.0.0.1:4200 root@<vps-ip>` then
  browse `http://localhost:4200` — three deployments should be visible:
  `gloomberg-daily` (cron `0 17 * * 1-5` Asia/Jakarta), `gloomberg-intraday-news`
  (900 s), `gloomberg-intraday-insight` (3600 s).
- MinIO console (loopback only): `ssh -L 9001:127.0.0.1:9001 root@<vps-ip>` then
  `http://localhost:9001` (log in with `MINIO_KEY`/`MINIO_SECRET`).

## 5. First run — seeding Gold

A fresh box has no `gold.duckdb`. That is safe: the backend logs
"gold snapshot unavailable" and boots; market endpoints serve degraded/empty
until the first EOD flow promotes. Seed it without waiting for 17:00:

```sh
sudo -u gloomberg bash -c 'cd /opt/gloomberg/Gloomberg-Terminal/services/orchestration \
  && PREFECT_API_URL=http://127.0.0.1:4200/api \
  /home/gloomberg/.local/bin/uv run prefect deployment run "gloomberg_daily_flow/gloomberg-daily" \
  --param trade_date=<last-trading-date>'
```

Watch it in the Prefect UI (SSH tunnel). It should end `SUCCESS` with
`promote` publishing `services/backend-api/warehouse/gold.duckdb`.

## 6. Day-2 operations

| Task | How |
|---|---|
| Logs | `journalctl -u gloomberg-backend -f` (same for any unit); Caddy: `journalctl -u caddy -f` |
| Restart one service | `sudo systemctl restart gloomberg-backend` |
| Restart everything | `sudo systemctl restart gloomberg-infra gloomberg-prefect-server gloomberg-prefect-worker gloomberg-backend gloomberg-web` |
| Edit the scored universe | edit `services/orchestration/config/universe.yaml` — picked up on the next flow tick, no restart |
| Re-pin a retired model | list what the key can still reach: `curl -s https://api.groq.com/openai/v1/models -H "Authorization: Bearer $GROQ_API_KEY"`, then set `GROQ_MODEL=` (or `GEMINI_MODEL=`) in `.env` and `sudo systemctl restart gloomberg-backend`. No code change, no redeploy. |
| Rotate the operator token | new value for **both** `GLOOMBERG_API_TOKEN` and `GLOOMBERG_ORCH_BACKEND_API_TOKEN` in `.env`, then `sudo systemctl restart gloomberg-backend gloomberg-prefect-worker` |
| Operator dispatch (token-gated) | `curl -X POST https://<domain>/api/v1/runs -H "Authorization: Bearer $TOKEN" ...` — the public UI stays read-only |
| TLS certificate status | `journalctl -u caddy | grep -i cert` — renewal is automatic |
| Update code | Push to `main`, approve the deploy in GitHub Actions (§10). Manual fallback: `git -C /opt/gloomberg/Gloomberg-Terminal checkout main && git pull` as `gloomberg`, then `sudo bash deploy/bootstrap.sh` |
| Check backups ran | `tail /var/log/gloomberg-backup.log`; sets live under `/var/backups/gloomberg/daily/<date>/` |
| Prune old manifests manually | `sudo -u gloomberg bash -c 'cd .../services/data-pipeline && uv run python -m pipeline.bronze.retention'` (dry run; add `--apply` to delete) |

## 7. Backups and restore

Nightly at 02:30 (server time = WIB, outside every flow window),
`deploy/backup/gloomberg-backup.sh` writes to `/var/backups/gloomberg/`:

- `daily/<date>/gloomberg-pg.sql.gz` — full `pg_dump` of the `gloomberg` database
- `daily/<date>/gold.duckdb` — the published Gold snapshot (safe to copy live:
  publish is an atomic rename, and 02:30 is outside all flow windows)
- `bronze-mirror/` — rolling `mc mirror` of the `gloomberg-bronze` bucket
  (no `--remove`, so objects pruned by retention stay archived)
- then manifest retention (`--apply`, 90-day default) and pruning of daily sets
  older than 7 days

**Restore Postgres:**

```sh
gunzip -c /var/backups/gloomberg/daily/<date>/gloomberg-pg.sql.gz | \
  docker compose --env-file /opt/gloomberg/Gloomberg-Terminal/.env \
  -f /opt/gloomberg/Gloomberg-Terminal/infra/docker-compose.yml \
  exec -T postgres psql -U <POSTGRES_USER> gloomberg
```

**Restore Gold:** copy the backed-up `gold.duckdb` over
`services/backend-api/warehouse/gold.duckdb`, then
`sudo systemctl restart gloomberg-backend`. (Or simply re-run the daily flow —
Gold is fully rebuildable from Bronze.)

**Restore Bronze:** reverse the mirror into the bucket:

```sh
docker run --rm --network gloomberg_default \
  -e MC_HOST_local="http://<MINIO_KEY>:<MINIO_SECRET>@minio:9000" \
  -v /var/backups/gloomberg/bronze-mirror:/backup \
  minio/mc:RELEASE.2025-04-08T15-39-49Z mirror --overwrite /backup local/gloomberg-bronze
```

Prefect's own state (deployments, run history) is not backed up: it is
disposable — re-running `scripts/setup_prefect.py` recreates the control plane.

## 8. Prefect Cloud (documented alternative, not the default)

The local Prefect server is the default. To use Prefect Cloud instead:
set `PREFECT_API_URL` and `PREFECT_API_KEY` (Cloud values) in `.env`, add both to
`gloomberg-prefect-worker.service` (replacing the local `PREFECT_API_URL` line),
`systemctl disable --now gloomberg-prefect-server`, re-run `setup_prefect.py`
with the Cloud env, and restart the worker.

## 9. Out of scope (deliberately)

Multi-process serving (needs Redis-backed rate limiting), HA/replicas,
Dockerfiles for the apps, Kubernetes, and monitoring beyond
Langfuse/Prefect/Data Telemetry. Revisit only with a stated need.

## 10. Continuous deployment

Every green CI run on `main` starts the `deploy` workflow, which **waits for your
approval** in the `production` environment. On approval the runner SSHes in as
`gloomberg` with a key that can only run `/usr/local/bin/gloomberg-deploy`, passes the
commit sha, and streams the `PRODUCTION_ENV` secret in as the new `.env`.

On the box, `deploy/gloomberg-deploy.sh` then:

| Step | What happens | If it fails |
|---|---|---|
| verify | full sha, already on `origin/main`, repo has no local edits | refused, nothing changed |
| env | new `.env` checked by `deploy/check-env.sh` | refused, nothing changed |
| drift | units, Caddyfile, sudoers, cron and the deploy script unchanged vs `/opt/gloomberg/.installed.sha256` | refused: re-run `bootstrap.sh` as root |
| quiet | waits up to 10 min for no running Prefect flow | refused, nothing changed |
| build web | `/opt/gloomberg/web-releases/<sha>`, `npm ci` + `next build` at low priority, live site keeps serving | refused, nothing changed |
| go live | checkout, swap `.env`, `uv sync --frozen`, `dbt deps`, compose up, `setup_prefect.py`, swap `web-current`, restart backend/web/worker | **automatic rollback** |
| health | API health, web root, three units active, within 60 s | **automatic rollback** |

Log: `/opt/gloomberg/deploy.log`, plus the GitHub job log. The last 3 web builds are
kept, so a redeploy of a recent commit skips the build.

**Redeploy or roll back by hand:** Actions → `deploy` → Run workflow → paste a full
`main` sha. Only commits that already contain the CD scripts can be deployed this way.

**System files** (anything under `deploy/systemd`, `deploy/caddy`, `deploy/sudoers`, the
deploy scripts, the backup cron) never change through CD. A commit that touches them is
refused until you merge it, `git pull` on the box, and re-run `sudo bash deploy/bootstrap.sh`.

### One-time setup

Create the `production` environment (step 3) **before** merging the CD change. The merge
itself starts a deploy, and with the reviewer set it simply waits until step 2 is done.

1. Make the deploy key on your machine (no passphrase, it only ever lives in GitHub):
   ```sh
   ssh-keygen -t ed25519 -f gloomberg-deploy -N "" -C gloomberg-deploy
   ```
2. On the VPS as root, after the CD branch is merged:
   ```sh
   cd /opt/gloomberg/Gloomberg-Terminal
   sudo -u gloomberg git checkout main && sudo -u gloomberg git pull
   # paste the contents of gloomberg-deploy.pub into this file
   nano /opt/gloomberg/deploy_key.pub
   sudo bash deploy/bootstrap.sh
   ```
   If `/etc/ssh/sshd_config` has an `AllowUsers` line, add `gloomberg` to it and
   `systemctl reload ssh`.
3. Back on your machine, from the folder with the key:
   ```sh
   ssh-keyscan -t ed25519 <vps-host> > known_hosts
   scp root@<vps-host>:/opt/gloomberg/Gloomberg-Terminal/.env .env.production
   gh api -X PUT repos/PeterWongsoredjo/Gloomberg-Terminal/environments/production \
     -F "reviewers[][type]=User" -F "reviewers[][id]=$(gh api user --jq .id)"
   # or create the environment in Settings → Environments, with yourself as reviewer
   gh secret set VPS_HOST --env production --body "<vps-host>"
   gh secret set VPS_SSH_KEY --env production < gloomberg-deploy
   gh secret set VPS_KNOWN_HOSTS --env production < known_hosts
   gh secret set PRODUCTION_ENV --env production < .env.production
   ```
4. Test the key by hand. It must refuse everything except a real `main` sha:
   ```sh
   ssh -i gloomberg-deploy gloomberg@<vps-host> "not-a-sha" < /dev/null
   ```
   Expect `ERROR: not a full commit sha`. Then approve the first real deploy in Actions.
5. Delete the local copies: `gloomberg-deploy` (private key), `known_hosts`,
   `.env.production`. Keep `gloomberg-deploy.pub` if you like, it is public.

**Rotating a secret:** edit your local `.env.production`, re-run the
`gh secret set PRODUCTION_ENV` line, then approve a deploy (or dispatch the current sha).
