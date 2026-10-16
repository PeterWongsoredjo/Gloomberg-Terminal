#!/usr/bin/env bash
# fails if the env file at $1 is missing any value the stack cannot run without
set -euo pipefail

env_file="${1:?usage: check-env.sh <path to .env>}"
[ -f "$env_file" ] || { echo "no env file at $env_file" >&2; exit 1; }

missing=0
for key in GLOOMBERG_DOMAIN MINIO_KEY MINIO_SECRET POSTGRES_USER POSTGRES_PASSWORD \
    GROQ_API_KEY GOOGLE_AI_STUDIO_API_KEY GLOOMBERG_API_TOKEN GLOOMBERG_ORCH_BACKEND_API_TOKEN; do
    grep -qE "^${key}[[:space:]]*=[[:space:]]*." "$env_file" || { echo "missing in .env: $key" >&2; missing=1; }
done
exit "$missing"
