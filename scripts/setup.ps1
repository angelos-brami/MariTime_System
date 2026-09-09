param(
    [string]$PythonExe = "",
    [string]$PnpmExe = ""
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$BundledRoot = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies"
$BundledNodeBin = Join-Path $BundledRoot "node\bin"
if (Test-Path $BundledNodeBin) {
    $env:Path = "$BundledNodeBin;$env:Path"
}
if (-not $PythonExe) {
    $BundledPython = Join-Path $BundledRoot "python\python.exe"
    $PythonExe = if (Test-Path $BundledPython) { $BundledPython } else { "python" }
}
if (-not $PnpmExe) {
    $BundledPnpm = Join-Path $BundledRoot "bin\fallback\pnpm.cmd"
    $PnpmExe = if (Test-Path $BundledPnpm) { $BundledPnpm } else { "pnpm" }
}

Set-Location $RepoRoot
if (-not (Test-Path ".venv")) {
    & $PythonExe -m venv .venv
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
& ".venv\Scripts\python.exe" -m pip install -e ".[dev]"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $PnpmExe install
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "Dependencies installed. Copy .env.example to .env before starting services."
