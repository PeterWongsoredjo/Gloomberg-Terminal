#!/usr/bin/env bash
# runs the real deploy script against a fake repo and fake system tools
set -uo pipefail

SRC="${1:-$PWD}"
T="$(mktemp -d)"
BASE="$T/opt"; REPO="$BASE/Gloomberg-Terminal"; ORIGIN="$T/origin.git"; BIN="$T/bin"
mkdir -p "$BASE" "$BIN"
pass=0; fail=0
check() { if eval "$2"; then echo "  PASS  $1"; pass=$((pass+1)); else echo "  FAIL  $1"; fail=$((fail+1)); [ "$fail" = 1 ] && sed "s/^/      | /" "$T/out.log" | tail -15; fi; }

# fake system tools that log every call
cat > "$BIN/sudo" <<'EOF'
#!/usr/bin/env bash
[ "$1" = "-n" ] && shift
exec "$@"
EOF
cat > "$BIN/systemctl" <<EOF
#!/usr/bin/env bash
echo "systemctl \$*" >> "$T/calls.log"
exit 0
EOF
cat > "$BIN/docker" <<EOF
#!/usr/bin/env bash
echo "docker \$*" >> "$T/calls.log"
EOF
cat > "$BIN/uv" <<EOF
#!/usr/bin/env bash
echo "uv \$* in \$PWD" >> "$T/calls.log"
EOF
cat > "$BIN/npm" <<EOF
#!/usr/bin/env bash
echo "npm \$* in \$PWD api=\${NEXT_PUBLIC_API_BASE:-}" >> "$T/calls.log"
[ "\$1 \$2" = "run build" ] && mkdir -p .next && echo built > .next/BUILD
exit 0
EOF
cat > "$BIN/curl" <<EOF
#!/usr/bin/env bash
for a in "\$@"; do case "\$a" in
  *flow_runs/count) [ -n "\${FAKE_PREFECT_DOWN:-}" ] && exit 7; echo "\${FAKE_BUSY:-0}"; exit 0 ;;
  *8000*|*3000*) head=\$(git -C "$REPO" rev-parse HEAD); [ "\$head" = "\${FAKE_BAD_SHA:-none}" ] && exit 22; exit 0 ;;
esac; done
exit 0
EOF
chmod +x "$BIN"/*
export PATH="$BIN:$PATH"

# a tiny repo shaped like the real one
mkdir -p "$T/work" && cd "$T/work" && git init -q -b main && git config user.email t@t && git config user.name t
mkdir -p deploy/systemd deploy/caddy deploy/sudoers deploy/backup services/web-terminal \
    services/data-pipeline services/orchestration/scripts services/backend-api dbt infra
cp "$SRC/deploy/gloomberg-deploy.sh" "$SRC/deploy/check-env.sh" deploy/
cp "$SRC/deploy/sudoers/gloomberg-deploy" deploy/sudoers/
cp "$SRC"/deploy/systemd/*.service deploy/systemd/
cp "$SRC/deploy/caddy/Caddyfile" deploy/caddy/
cp "$SRC/deploy/backup/gloomberg-backup.cron" deploy/backup/
echo '{"name":"web"}' > services/web-terminal/package.json
echo v1 > services/backend-api/app.txt
touch infra/docker-compose.yml services/orchestration/scripts/setup_prefect.py services/data-pipeline/keep dbt/keep
git add -A && git commit -qm base
C1=$(git rev-parse HEAD)
echo v2 > services/backend-api/app.txt && git commit -qam good && C2=$(git rev-parse HEAD)
echo v3 > services/backend-api/app.txt && git commit -qam broken && C3=$(git rev-parse HEAD)
echo "# changed" >> deploy/systemd/gloomberg-web.service && git commit -qam drift && C4=$(git rev-parse HEAD)
git checkout -q -b side "$C2" && echo side > side.txt && git add side.txt && git commit -qm side && CSIDE=$(git rev-parse HEAD)
git checkout -q main && git reset -q --hard "$C3"
git clone -q --bare "$T/work" "$ORIGIN"
git -C "$ORIGIN" update-ref refs/heads/main "$C4"

# the box: clone at the base commit, like bootstrap leaves it
git clone -q "$ORIGIN" "$REPO" && git -C "$REPO" checkout -q --detach "$C1"
mkdir -p "$BASE/web-releases"
ln -s "$REPO/services/web-terminal" "$BASE/web-current"
GOOD_ENV="GLOOMBERG_DOMAIN=example.test
MINIO_KEY=k
MINIO_SECRET=s
POSTGRES_USER=u
POSTGRES_PASSWORD=p
GROQ_API_KEY=g
GOOGLE_AI_STUDIO_API_KEY=a
GLOOMBERG_API_TOKEN=t
GLOOMBERG_ORCH_BACKEND_API_TOKEN=t"
printf '%s\nOLD=1\n' "$GOOD_ENV" > "$REPO/.env"

# the script under test, pointed at the sandbox
sed -e "s#^BASE=.*#BASE=\"$BASE\"#" -e "s#^UV=.*#UV=\"$BIN/uv\"#" \
    -e "s#^CHECK_ENV=.*#CHECK_ENV=\"$T/check-env\"#" -e "s#^QUIET_WAIT_SECONDS=.*#QUIET_WAIT_SECONDS=\${QUIET:-600}#" \
    -e 's#/usr/bin/systemctl#systemctl#' -e 's#sleep 30#sleep 0#' -e 's#sleep 2$#sleep 0#' \
    "$SRC/deploy/gloomberg-deploy.sh" > "$T/deploy" && chmod +x "$T/deploy"
cp "$SRC/deploy/check-env.sh" "$T/check-env" && chmod +x "$T/check-env"
"$T/deploy" --manifest > "$BASE/.installed.sha256"

run() { local sha="$1"; shift; printf '%s\n' "${ENV_IN:-$GOOD_ENV}" | env "$@" "$T/deploy" "$sha" > "$T/out.log" 2>&1; echo $? > "$T/rc"; }
head_is() { [ "$(git -C "$REPO" rev-parse HEAD)" = "$1" ]; }
web_is() { [ "$(readlink -f "$BASE/web-current")" = "$(readlink -f "$1")" ]; }

echo "1. manifest"
check "manifest lists the installed files" "grep -q 'deploy/systemd/gloomberg-web.service' '$BASE/.installed.sha256'"

echo "2. happy deploy"
run "$C2"
check "exit 0" "[ \$(cat $T/rc) = 0 ] && grep -q DEPLOYED $T/out.log"
check "repo on the new commit" "head_is $C2"
check "web-current points at the new release" "web_is $BASE/web-releases/$C2"
check "web built with the domain" "grep -q 'api=https://example.test/api/v1' $T/calls.log"
check "env replaced from stdin" "! grep -q OLD=1 $REPO/.env && grep -q GLOOMBERG_DOMAIN $REPO/.env"
check "previous env kept" "grep -q OLD=1 $BASE/.env.prev"
check "three services restarted" "[ \$(grep -c 'systemctl restart' $T/calls.log) = 3 ]"
check "no leftover env.new" "[ ! -f $BASE/.env.new ]"

echo "3. guards, nothing may change"
: > "$T/calls.log"
run "not-a-sha"
check "bad sha refused" "[ \$(cat $T/rc) != 0 ] && grep -q 'not a full commit sha' $T/out.log && head_is $C2"
run "$CSIDE"
check "commit off main refused" "[ \$(cat $T/rc) != 0 ] && grep -q 'is not on main' $T/out.log && head_is $C2"
ENV_IN="GLOOMBERG_DOMAIN=x" run "$C3"
check "incomplete env refused" "[ \$(cat $T/rc) != 0 ] && grep -q 'missing values' $T/out.log && head_is $C2 && grep -q OLD=1 $BASE/.env.prev"
run "$C4"
check "system file drift refused" "[ \$(cat $T/rc) != 0 ] && grep -q 'changes system files' $T/out.log && head_is $C2"
run "$C3" FAKE_BUSY=2 QUIET=0
check "busy flows abort" "[ \$(cat $T/rc) != 0 ] && grep -q 'flow runs still busy' $T/out.log && head_is $C2"
check "guards restarted nothing" "! grep -q 'systemctl restart' $T/calls.log"

echo "4. unhealthy release rolls back"
run "$C3" FAKE_BAD_SHA="$C3"
check "exit non zero" "[ \$(cat $T/rc) != 0 ]"
check "says rolled back" "grep -q 'ROLLED BACK' $T/out.log"
check "repo back on the good commit" "head_is $C2"
check "web back on the good release" "web_is $BASE/web-releases/$C2"

echo "5. prefect down still deploys"
: > "$T/calls.log"
run "$C3" FAKE_PREFECT_DOWN=1
check "deploys with a warning" "[ \$(cat $T/rc) = 0 ] && grep -q 'prefect is unreachable' $T/out.log && head_is $C3"

echo "6. redeploy reuses the build"
: > "$T/calls.log"
run "$C2"
check "no rebuild for a built commit" "[ \$(cat $T/rc) = 0 ] && ! grep -q 'run build' $T/calls.log && web_is $BASE/web-releases/$C2"

echo
echo "result: $pass passed, $fail failed"
rm -rf "$T"
[ "$fail" -eq 0 ]
