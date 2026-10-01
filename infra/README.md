# infra — local containers for the medallion

MinIO (Bronze object store) and Postgres (serving state). Sized to the 8 GB / 4 vCPU
box budget: dbt and DuckDB run **host-side**, so the containers stay small and reach
each other by service DNS while the host reaches MinIO at `localhost:9000`.

## Bring up

Credentials come from the repo-root `.env` (copy `infra/.env.example` if you have none):

```sh
docker compose --env-file ../.env -f docker-compose.yml up -d
```

This starts MinIO (`:9000` API, `:9001` console), creates the `gloomberg-bronze`
bucket via a one-shot `mc` init, and starts Postgres (database `gloomberg`).
All ports bind to `127.0.0.1` only — no public ingress.

## Postgres host port (local vs VPS)

The Postgres container publishes to `POSTGRES_PORT` from `.env`. Set it to `5433`
locally when a native Postgres already owns `5432`; on the VPS (no native PG) set it
to `5432` or drop the line. The dbt `serving` profile reads the same variable, so the
one setting moves the whole stack between machines with no code change.

## Monitoring (optional)

Prometheus, Pushgateway and Grafana sit behind the `monitoring` profile, so the
plain bring-up above never starts them. Set `GRAFANA_ADMIN_PASSWORD` in `.env`, then:

```sh
docker compose --env-file ../.env -f docker-compose.yml --profile monitoring up -d
```

Grafana is on `127.0.0.1:3001`, Prometheus on `:9090`, Pushgateway on `:9091`.
They use host networking, so on Docker Desktop turn on *Settings > Resources >
Network > Enable host networking*. The full guide is [`docs/monitoring.md`](../docs/monitoring.md).

## Tear down

```sh
docker compose --env-file ../.env -f docker-compose.yml down        # keep data
docker compose --env-file ../.env -f docker-compose.yml down -v     # wipe volumes
docker compose --env-file ../.env -f docker-compose.yml --profile monitoring down
```
