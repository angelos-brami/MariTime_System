param(
    [string]$PnpmExe = ""
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$BundledRoot = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies"
$BundledNodeBin = Join-Path $BundledRoot "node\bin"
if (Test-Path $BundledNodeBin) {
    $env:Path = "$BundledNodeBin;$env:Path"
}
if (-not $PnpmExe) {
    $BundledPnpm = Join-Path $BundledRoot "bin\fallback\pnpm.cmd"
    $PnpmExe = if (Test-Path $BundledPnpm) { $BundledPnpm } else { "pnpm" }
}

Set-Location $RepoRoot
& ".venv\Scripts\python.exe" -m ruff check .
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& ".venv\Scripts\python.exe" -m mypy apps/api packages
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& ".venv\Scripts\python.exe" -m pytest
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $PnpmExe lint:web
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $PnpmExe typecheck:web
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $PnpmExe build:web
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
