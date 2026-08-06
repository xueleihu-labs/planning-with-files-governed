# Version source: ../VERSION
# Diagnostic-only runtime self-check. It does not alter plan state.
[CmdletBinding()]
param([string]$TaskId = "")

$python = Get-Command python3 -ErrorAction SilentlyContinue
if (-not $python) { $python = Get-Command python -ErrorAction SilentlyContinue }
if (-not $python) { $python = Get-Command py -ErrorAction SilentlyContinue }
if (-not $python) { Write-Output "WARN  Python unavailable"; exit 0 }
$args = @((Join-Path $PSScriptRoot "runtime.py"), "doctor", "--cwd", (Get-Location).Path)
if ($TaskId -ne "") { $args += @("--task-id", $TaskId) }
& $python.Source @args
exit 0
