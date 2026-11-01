<#
.SYNOPSIS
    Package this project, upload it to a server, and rebuild the containers.
    Run it on the Windows machine.

.EXAMPLE
    pwsh -File scripts\deploy.ps1 -Server deploy@1.2.3.4

.NOTES
    Why not git push + git clone on the server: this network cannot reach
    github.com. Shipping a tarball also means the server needs no deploy key.

    Keep this file ASCII-only. It is parsed by Windows PowerShell 5.1 by
    default, which reads a BOM-less UTF-8 script as ANSI and mangles non-ASCII
    strings badly enough to break parsing.
#>
param(
    [Parameter(Mandatory = $true)]
    [string]$Server,

    [string]$KeyPath = "$env:USERPROFILE\.ssh\id_ed25519",

    [string]$RemoteDir = '~/ai-customer-support',

    # Upload only, do not rebuild containers.
    [switch]$UploadOnly,

    [switch]$SkipHealthCheck,

    # Published dashboard port on the server. 8080 may already be taken by a
    # preinstalled service, so the server side can override it.
    [int]$DashboardPort = 8081
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$archive = Join-Path $env:TEMP 'ai-customer-support.tar.gz'
$sshArgs = @('-i', $KeyPath, '-o', 'StrictHostKeyChecking=accept-new', '-o', 'BatchMode=yes')

Write-Host '==> 1/5 pack' -ForegroundColor Cyan
Push-Location $projectRoot
try {
    # Exclude local build artifacts; the containers build them again.
    # .env travels separately: the server copy may carry edits such as
    # DASHBOARD_PORT, and extracting over it would silently discard them.
    tar -czf $archive '--exclude=./.venv' '--exclude=./.git' '--exclude=./dashboard/node_modules' '--exclude=./dashboard/dist' '--exclude=./supabase/.temp' '--exclude=./.env' '--exclude=__pycache__' '--exclude=*.pyc' .
}
finally {
    Pop-Location
}
$sizeMb = [math]::Round((Get-Item $archive).Length / 1MB, 2)
Write-Host "    $archive ($sizeMb MB)" -ForegroundColor Green

Write-Host '==> 2/5 upload' -ForegroundColor Cyan
& scp @sshArgs $archive "${Server}:/tmp/ai-customer-support.tar.gz"
if ($LASTEXITCODE -ne 0) { throw "scp archive failed (exit $LASTEXITCODE)" }
& scp @sshArgs (Join-Path $projectRoot '.env') "${Server}:/tmp/env.incoming"
if ($LASTEXITCODE -ne 0) { throw "scp .env failed (exit $LASTEXITCODE)" }

Write-Host '==> 3/5 extract' -ForegroundColor Cyan
$unpack = "mkdir -p $RemoteDir && tar -xzf /tmp/ai-customer-support.tar.gz -C $RemoteDir && " +
          "if [ -f $RemoteDir/.env ]; then rm -f /tmp/env.incoming; echo '.env kept (server copy wins)'; " +
          "else mv /tmp/env.incoming $RemoteDir/.env; echo '.env written (first deploy)'; fi && " +
          "grep -q '^DASHBOARD_PORT=' $RemoteDir/.env || echo 'DASHBOARD_PORT=$DashboardPort' >> $RemoteDir/.env"
& ssh @sshArgs $Server $unpack
if ($LASTEXITCODE -ne 0) { throw "extract failed (exit $LASTEXITCODE)" }

if ($UploadOnly) {
    Write-Host '==> stopped after upload (-UploadOnly)' -ForegroundColor Yellow
    exit 0
}

Write-Host '==> 4/5 rebuild' -ForegroundColor Cyan
$deploy = "cd $RemoteDir && docker compose up -d --build && docker compose ps"
& ssh @sshArgs $Server $deploy
if ($LASTEXITCODE -ne 0) { throw "compose failed (exit $LASTEXITCODE)" }

if (-not $SkipHealthCheck) {
    Write-Host '==> 5/5 health check' -ForegroundColor Cyan
    Start-Sleep -Seconds 8
    $health = "curl -fsS --max-time 10 http://127.0.0.1:$DashboardPort/health; echo; " +
              "cd $RemoteDir && docker compose logs --tail 5 poller"
    & ssh @sshArgs $Server $health
}

Write-Host ''
Write-Host 'Deployed.' -ForegroundColor Green
Write-Host 'The dashboard listens on loopback only. Open a tunnel:' -ForegroundColor Yellow
Write-Host "  ssh -i `"$KeyPath`" -L ${DashboardPort}:localhost:${DashboardPort} $Server" -ForegroundColor Yellow
Write-Host "Then browse http://127.0.0.1:$DashboardPort/" -ForegroundColor Yellow
