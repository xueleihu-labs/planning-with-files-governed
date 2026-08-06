# Version source: ../VERSION
# Resolve the active planning directory through the shared Python runtime.
[CmdletBinding()]
param([string]$PlanningDir = "", [string]$TaskId = "")

$python = Get-Command python3 -ErrorAction SilentlyContinue
if (-not $python) { $python = Get-Command python -ErrorAction SilentlyContinue }
if (-not $python) { $python = Get-Command py -ErrorAction SilentlyContinue }
if (-not $python) { exit 0 }

$runtime = Join-Path $PSScriptRoot "runtime.py"
$args = @($runtime, "resolve", "--cwd", (Get-Location).Path, "--quiet")
if ($PlanningDir -ne "") { $args += @("--planning-dir", $PlanningDir) }
if ($TaskId -ne "") { $args += @("--task-id", $TaskId) }
& $python.Source @args
# A resolver is a hook helper: invalid/escaped candidates fail closed to an
# empty result and must not break the host agent loop.
exit 0
