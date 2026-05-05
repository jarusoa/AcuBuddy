#Requires -Version 5.0
<#
.SYNOPSIS
    One-time setup: creates the venv, installs dependencies, scaffolds .env.

.DESCRIPTION
    Run this once per machine after cloning AcuBuddy. Idempotent —
    re-running skips steps that are already done. Use acubuddy.ps1 for
    daily launches.
#>

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $repoRoot

$venvDir = $null
foreach ($name in @(".venv", "venv", "env")) {
    if (Test-Path (Join-Path $repoRoot "$name\Scripts\python.exe")) {
        $venvDir = $name
        break
    }
}
if (-not $venvDir) {
    $venvDir = ".venv"
    Write-Host "Creating venv at .\$venvDir ..."
    python -m venv $venvDir
} else {
    Write-Host "Found existing venv at .\$venvDir"
}

. (Join-Path $repoRoot "$venvDir\Scripts\Activate.ps1")

Write-Host "Installing dependencies (torch + chromadb are large — allow several minutes) ..."
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

if (-not (Test-Path (Join-Path $repoRoot ".env"))) {
    if (Test-Path (Join-Path $repoRoot ".env.example")) {
        Copy-Item ".env.example" ".env"
        Write-Host ""
        Write-Host "Created .env from .env.example. EDIT IT and add your DEEPSEEK_API_KEY before launching."
    } else {
        Write-Warning "No .env.example to copy. Create .env manually with DEEPSEEK_API_KEY=..."
    }
} else {
    Write-Host ".env already exists — leaving it alone"
}

Write-Host ""
Write-Host "Setup complete. Next steps:"
Write-Host "  1. Edit .env with your DEEPSEEK_API_KEY (and optionally ACUBUDDY_PROJECT_ROOT)"
Write-Host "  2. Add Acumatica PDFs to data\, then: python build_index.py --clean"
Write-Host "  3. (Optional) python index_project.py    # builds the project catalog"
Write-Host "  4. Launch: .\acubuddy.ps1"
