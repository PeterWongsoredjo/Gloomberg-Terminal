# Brings up the whole local stack: infra containers, backend API, Prefect
# server + worker, and the frontend. Each long-running process gets its own
# visible window so you can watch logs or Ctrl+C just that one piece.
#
# Usage: powershell -ExecutionPolicy Bypass -File scripts\dev-up.ps1

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot

function Wait-Http($Url, $Label, $TimeoutSec = 60) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        try {
            $resp = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3
            if ($resp.StatusCode -eq 200) {
                Write-Host "  ok: $Label is up" -ForegroundColor Green
                return
            }
        } catch {}
        Start-Sleep -Seconds 2
    }
    throw "$Label never came up at $Url within ${TimeoutSec}s"
}

function Start-Window($Title, $WorkingDir, $Command) {
    Start-Process powershell -ArgumentList @(
        "-NoExit", "-Command",
        "`$host.ui.RawUI.WindowTitle = '$Title'; Set-Location '$WorkingDir'; $Command"
    )
}

Write-Host "1/5 docker: postgres + minio" -ForegroundColor Cyan
docker info > $null 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "  starting Docker Desktop, this can take a minute..."
    Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"
    $deadline = (Get-Date).AddSeconds(120)
    while ((Get-Date) -lt $deadline) {
        docker info > $null 2>&1
        if ($LASTEXITCODE -eq 0) { break }
        Start-Sleep -Seconds 5
    }
}
Push-Location "$RepoRoot\infra"
docker compose --env-file ..\.env up -d
Pop-Location
Wait-Http "http://127.0.0.1:9000/minio/health/live" "MinIO"

Write-Host "2/5 backend API (uvicorn)" -ForegroundColor Cyan
Start-Window "gloomberg: backend-api" "$RepoRoot\services\backend-api" `
    "uv run uvicorn app.main:app --host 127.0.0.1 --port 8000"
Wait-Http "http://127.0.0.1:8000/api/v1/health" "backend-api" 60

Write-Host "3/5 prefect server" -ForegroundColor Cyan
Start-Window "gloomberg: prefect server" "$RepoRoot\services\orchestration" `
    "uv run prefect server start"
Wait-Http "http://127.0.0.1:4200/api/health" "Prefect server" 60

Write-Host "4/5 prefect worker (applies deployments, then polls)" -ForegroundColor Cyan
Start-Window "gloomberg: prefect worker" "$RepoRoot\services\orchestration" (
    "`$env:PREFECT_API_URL = 'http://127.0.0.1:4200/api'; " +
    "`$env:GLOOMBERG_ORCH_DIR = '$RepoRoot\services\orchestration'; " +
    "`$env:PYTHONUTF8 = '1'; " +
    "uv run python scripts\setup_prefect.py; " +
    "uv run prefect worker start --pool gloomberg-local"
)

Write-Host "5/5 frontend (next dev)" -ForegroundColor Cyan
Start-Window "gloomberg: web-terminal" "$RepoRoot\services\web-terminal" "npm run dev"
Wait-Http "http://127.0.0.1:3000" "web-terminal" 60

Write-Host ""
Write-Host "All up. Five windows are running: backend, prefect server, prefect worker, frontend (+ docker in background)." -ForegroundColor Green
Write-Host "Terminal:  http://localhost:3000"
Write-Host "Prefect:   http://localhost:4200"
Write-Host "Close a window (or Ctrl+C inside it) to stop just that piece."
