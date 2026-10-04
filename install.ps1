# stm32-probe MCP - one-shot setup on a new PC (Windows PowerShell 5.1+)
#
#   1. git clone the repository (or copy this folder without .venv) to the new PC.
#   2. powershell -ExecutionPolicy Bypass -File .\install.ps1
#   3. Restart Claude Code.
#
# Prerequisites: Python 3.11+ (py launcher), STM32CubeIDE (OpenOCD + arm-none-eabi-gdb),
# STM32CubeCLT (STM32_Programmer_CLI + SVD files).
param(
    [string]$BuildDir = "",     # folder searched for the newest .elf (optional)
    [switch]$NoRegister         # do not touch ~/.claude.json
)
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here
Write-Host "== stm32-probe MCP setup in $here"

$py = Get-Command py -ErrorAction SilentlyContinue
if (-not $py) { $py = Get-Command python -ErrorAction SilentlyContinue }
if (-not $py) { throw "Python 3.11+ not found. Install Python (with the py launcher) first." }

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "Creating .venv ..."
    if ($py.Name -eq "py.exe") { & $py.Source -3 -m venv .venv } else { & $py.Source -m venv .venv }
}
& .\.venv\Scripts\python.exe -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
if ($LASTEXITCODE -ne 0) { throw ".venv Python is older than 3.11 - delete .venv and install a newer Python." }
& .\.venv\Scripts\python.exe -m pip install --disable-pip-version-check -q -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "dependency installation failed" }
& .\.venv\Scripts\python.exe -c "import fastmcp, pygdbmi; print('deps ok: fastmcp', fastmcp.__version__)"

if (-not $NoRegister) {
    $args_ = @()
    if ($BuildDir) { $args_ += @("--build-dir", $BuildDir) }
    & .\.venv\Scripts\python.exe register_mcp.py @args_
}
Write-Host "== done. Restart Claude Code, then ask: 'stm32-probe check_setup'"
