$ErrorActionPreference = "Stop"

$Python = $env:PYTHON_BIN
if (-not $Python) {
    $Python = "python"
}

& $Python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 'Python 3.11+ is required.')"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
& $Python -m pip install --upgrade pip
& $Python -m pip install -e $ScriptDir
if ($env:FUSION_MEMORY_SKIP_WIZARD -eq "1") {
    & $Python -m fusion_memory.cli init
} else {
    & $Python -m fusion_memory.cli init --wizard
}
& $Python -m fusion_memory.cli doctor

Write-Host ""
Write-Host "Fusion Memory is installed."
Write-Host "Start it with: fusion-memory start"
Write-Host "Check it with: fusion-memory status"
