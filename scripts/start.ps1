<#
.SYNOPSIS
  Start Versa on this machine and open it in the browser.

.DESCRIPTION
  Docker Desktop -> Postgres -> migrations -> (build the web app if there is no
  build yet) -> `versa serve` -> browser. Safe to run again: anything already
  running is left alone, and if Versa is already up it just opens the page.

.EXAMPLE
  .\scripts\start.ps1              # real Gemini (needs GEMINI_API_KEY in .env)
  .\scripts\start.ps1 -Stub        # no key, no cost: canned model answers
  .\scripts\start.ps1 -Rebuild     # rebuild the web app first (after changing app/lib)
  .\scripts\start.ps1 -Lan         # also reachable from a phone on the same Wi-Fi (NO login: trusted networks only)
#>
param(
    [switch]$Stub,
    [switch]$NoBrowser,
    [switch]$Rebuild,
    [switch]$Lan,
    [int]$Port = 8000
)

# Windows PowerShell 5.1 turns ANY stderr output from a native command (docker
# prints progress there) into a terminating error under 'Stop'. Every native
# call below is checked through $LASTEXITCODE instead, so keep the default.
$ErrorActionPreference = 'Continue'
Set-Location (Split-Path $PSScriptRoot -Parent) -ErrorAction Stop
function Step($msg) { Write-Host "versa: $msg" -ForegroundColor Cyan }
function Fail($msg) { Write-Host "versa: $msg" -ForegroundColor Red; exit 1 }
$url = "http://localhost:$Port"

# Already running? Just open it.
try {
    Invoke-WebRequest "$url/api/health" -UseBasicParsing -TimeoutSec 2 | Out-Null
    Step "already running at $url"
    if (-not $NoBrowser) { Start-Process $url }
    exit 0
} catch { }

# 1. configuration
if (-not (Test-Path .env)) {
    Copy-Item .env.example .env -ErrorAction Stop
    Step "created .env from .env.example"
}
if (-not $Stub) {
    $key = (Select-String -Path .env -Pattern '^GEMINI_API_KEY=(.*)$').Matches.Groups[1].Value.Trim()
    if (-not $key -or $key -eq 'your-key-here') {
        Fail "no GEMINI_API_KEY in .env. Add one, or run  .\scripts\start.ps1 -Stub  to try it without a key."
    }
}

# 2. Docker Desktop
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Step "starting Docker Desktop (this can take a minute)..."
    $desktop = "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe"
    if (-not (Test-Path $desktop)) { Fail "Docker Desktop isn't installed." }
    Start-Process $desktop
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Seconds 3
        docker info *> $null
        if ($LASTEXITCODE -eq 0) { break }
    }
    if ($LASTEXITCODE -ne 0) { Fail "Docker didn't start. Open Docker Desktop yourself, then run this again." }
}

# 3. Postgres
Step "starting the database..."
docker compose up -d *> $null
for ($i = 0; $i -lt 30; $i++) {
    docker compose exec -T postgres pg_isready -U versa -d versa *> $null
    if ($LASTEXITCODE -eq 0) { break }
    Start-Sleep -Seconds 2
}
if ($LASTEXITCODE -ne 0) { Fail "the database didn't come up (port in use? see VERSA_DB_PORT in .env.example)." }

# 4. schema
Step "applying migrations..."
uv run versa migrate | Out-Null
if ($LASTEXITCODE -ne 0) { Fail "migrations failed." }

# 5. the web app
if ($Rebuild -or -not (Test-Path app\build\web\index.html)) {
    $flutter = (Get-Command flutter -ErrorAction SilentlyContinue).Source
    if (-not $flutter -and (Test-Path C:\src\flutter\bin\flutter.bat)) { $flutter = 'C:\src\flutter\bin\flutter.bat' }
    if (-not $flutter) { Fail "no web build found and Flutter isn't installed, so it can't be built." }
    Step "building the web app (first time only, ~1 minute)..."
    Push-Location app
    & $flutter build web --release
    $code = $LASTEXITCODE
    Pop-Location
    if ($code -ne 0) { Fail "the web build failed." }
}

# 6. serve (blocks); open the browser once it answers
if (-not $NoBrowser) {
    Start-Job -ScriptBlock {
        param($u)
        for ($i = 0; $i -lt 60; $i++) {
            try { Invoke-WebRequest "$u/api/health" -UseBasicParsing -TimeoutSec 2 | Out-Null; Start-Process $u; break }
            catch { Start-Sleep -Seconds 1 }
        }
    } -ArgumentList $url | Out-Null
}
$serveArgs = @('--port', $Port)
if ($Stub) { $serveArgs += '--stub' }
if ($Lan) {
    $serveArgs += @('--host', '0.0.0.0')
    $ip = (Get-NetIPAddress -AddressFamily IPv4 |
        Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' -and $_.PrefixOrigin -ne 'WellKnown' } |
        Select-Object -First 1).IPAddress
    Write-Host "versa: on your phone (same Wi-Fi) open  http://${ip}:$Port" -ForegroundColor Yellow
    Write-Host "versa: there is NO login: anyone on this network can use it. Stop it when done." -ForegroundColor Yellow
}
Step "serving at $url  (Ctrl+C to stop)"
uv run versa serve @serveArgs
