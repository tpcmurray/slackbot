# ============================================================
# Kibitz — Idempotent Startup Script
# Run from the project root: .\start.ps1
# Re-run safely at any time; only starts what isn't running.
# ============================================================

$ErrorActionPreference = "Stop"
$ProjectRoot = $PSScriptRoot

# ── Configuration ──────────────────────────────────────────
$LlamaServerExe  = "C:\llama.cpp\llama-server.exe"
$ModelPath        = "C:\models\qwopus-9b-v2\Qwen3.5-9B.Q8_0.gguf"
$MmprojPath       = "C:\models\qwopus-9b-v2\mmproj-BF16.gguf"
$LlamaPort        = 8080
$LlamaContextSize = 8192
$LlamaGpuLayers   = 99
$SearxngPort      = 8888

# ── Helpers ────────────────────────────────────────────────
function Write-Status($msg)  { Write-Host "[*] $msg" -ForegroundColor Cyan }
function Write-Ok($msg)      { Write-Host "[+] $msg" -ForegroundColor Green }
function Write-Warn($msg)    { Write-Host "[!] $msg" -ForegroundColor Yellow }
function Write-Fail($msg)    { Write-Host "[-] $msg" -ForegroundColor Red }

function Test-TcpPort([int]$Port) {
    try {
        $tcp = New-Object System.Net.Sockets.TcpClient
        $tcp.Connect("127.0.0.1", $Port)
        $tcp.Close()
        return $true
    } catch {
        return $false
    }
}

# ── 1. Check .env ─────────────────────────────────────────
Write-Status "Checking .env file..."
$envFile = Join-Path $ProjectRoot ".env"
$envExample = Join-Path $ProjectRoot ".env.example"

if (-not (Test-Path $envFile)) {
    if (Test-Path $envExample) {
        Copy-Item $envExample $envFile
        Write-Warn ".env created from .env.example — edit it with your Slack tokens before the bot can connect."
    } else {
        Write-Fail ".env.example not found. Cannot create .env."
        exit 1
    }
}

# Validate that tokens are filled in (not still placeholders)
$envContent = Get-Content $envFile -Raw
$missingTokens = @()
if ($envContent -match "SLACK_BOT_TOKEN=xoxb-\.\.\.") { $missingTokens += "SLACK_BOT_TOKEN" }
if ($envContent -match "SLACK_APP_TOKEN=xapp-\.\.\.") { $missingTokens += "SLACK_APP_TOKEN" }
if ($missingTokens.Count -gt 0) {
    Write-Warn "Placeholder tokens detected in .env: $($missingTokens -join ', ')"
    Write-Warn "The bot won't connect to Slack until you fill these in."
}

Write-Ok ".env file exists."

# ── 2. Python dependencies ────────────────────────────────
Write-Status "Checking Python dependencies..."
$reqFile = Join-Path $ProjectRoot "requirements.txt"
if (-not (Test-Path $reqFile)) {
    Write-Fail "requirements.txt not found."
    exit 1
}

# Quick check: try importing the heaviest dependency
$pipCheck = & python -c "import slack_bolt" 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Status "Installing Python dependencies..."
    & pip install -r $reqFile
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "pip install failed."
        exit 1
    }
    Write-Ok "Dependencies installed."
} else {
    Write-Ok "Python dependencies already installed."
}

# ── 3. Docker Desktop ────────────────────────────────────
Write-Status "Checking Docker..."
$dockerRunning = & docker info 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Status "Starting Docker Desktop..."
    Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"
    Write-Status "Waiting for Docker daemon..."
    $timeout = 60
    $elapsed = 0
    while ($elapsed -lt $timeout) {
        Start-Sleep -Seconds 3
        $elapsed += 3
        $check = & docker info 2>&1
        if ($LASTEXITCODE -eq 0) { break }
    }
    if ($elapsed -ge $timeout) {
        Write-Fail "Docker did not start within ${timeout}s."
        exit 1
    }
    Write-Ok "Docker is running."
} else {
    Write-Ok "Docker already running."
}

# ── 4. SearXNG ────────────────────────────────────────────
Write-Status "Checking SearXNG on port $SearxngPort..."
if (Test-TcpPort $SearxngPort) {
    Write-Ok "SearXNG already running on port $SearxngPort."
} else {
    Write-Status "Starting SearXNG via docker compose..."
    Push-Location $ProjectRoot
    & docker compose up -d 2>&1
    Pop-Location

    # Wait for it to be ready
    $timeout = 30
    $elapsed = 0
    while ($elapsed -lt $timeout) {
        Start-Sleep -Seconds 2
        $elapsed += 2
        if (Test-TcpPort $SearxngPort) { break }
    }
    if ($elapsed -ge $timeout) {
        Write-Fail "SearXNG did not start within ${timeout}s."
        exit 1
    }
    Write-Ok "SearXNG started on port $SearxngPort."
}

# ── 5. llama.cpp server ──────────────────────────────────
Write-Status "Checking llama.cpp on port $LlamaPort..."
if (Test-TcpPort $LlamaPort) {
    Write-Ok "llama.cpp already running on port $LlamaPort."
} else {
    if (-not (Test-Path $LlamaServerExe)) {
        Write-Fail "llama-server not found at $LlamaServerExe"
        exit 1
    }
    if (-not (Test-Path $ModelPath)) {
        Write-Fail "Model file not found at $ModelPath"
        exit 1
    }

    Write-Status "Starting llama.cpp server..."
    $llamaArgs = @(
        "-m", $ModelPath,
        "--port", $LlamaPort,
        "--host", "127.0.0.1",
        "-c", $LlamaContextSize,
        "-ngl", $LlamaGpuLayers
    )
    if ($MmprojPath -and (Test-Path $MmprojPath)) {
        $llamaArgs += @("--mmproj", $MmprojPath)
        Write-Status "Vision projector enabled."
    }

    Start-Process -FilePath $LlamaServerExe -ArgumentList $llamaArgs -WindowStyle Normal

    # Wait for it to be ready
    $timeout = 120
    $elapsed = 0
    while ($elapsed -lt $timeout) {
        Start-Sleep -Seconds 3
        $elapsed += 3
        if (Test-TcpPort $LlamaPort) { break }
    }
    if ($elapsed -ge $timeout) {
        Write-Fail "llama.cpp did not start within ${timeout}s (model loading can be slow)."
        exit 1
    }
    Write-Ok "llama.cpp started on port $LlamaPort."
}

# ── 6. Start the bot ─────────────────────────────────────
Write-Status "Starting Kibitz..."
Push-Location $ProjectRoot
& python bot.py
Pop-Location
