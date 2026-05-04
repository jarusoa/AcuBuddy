#Requires -Version 5.0
<#
.SYNOPSIS
    Launches OpenCode with the AcuBuddy environment fully set up.

.DESCRIPTION
    Activates .venv, loads .env into the process environment, and launches
    OpenCode from the repo root so opencode.json's relative paths resolve.
    Any extra arguments are forwarded to opencode.

.EXAMPLE
    .\acubuddy.ps1
#>

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $repoRoot

$venvActivate = Join-Path $repoRoot ".venv\Scripts\Activate.ps1"
if (-not (Test-Path $venvActivate)) {
    Write-Error "No venv found at $venvActivate. Create one with: python -m venv .venv"
    exit 1
}
. $venvActivate

$envFile = Join-Path $repoRoot ".env"
if (-not (Test-Path $envFile)) {
    Write-Warning "No .env file at $envFile. Copy .env.example to .env and add DEEPSEEK_API_KEY."
} else {
    Get-Content $envFile | ForEach-Object {
        $line = $_.Trim()
        if (-not $line) { return }
        if ($line.StartsWith("#")) { return }
        if ($line -notmatch '^([^=]+)=(.*)$') { return }
        $name = $matches[1].Trim()
        $value = $matches[2].Trim().Trim("'", '"')
        [Environment]::SetEnvironmentVariable($name, $value, 'Process')
    }
}

if (-not $env:DEEPSEEK_API_KEY) {
    Write-Warning "DEEPSEEK_API_KEY not set. OpenCode will fail to call DeepSeek."
}

& opencode @args
