# Version source: ../VERSION
# Emit plan context for PowerShell hook adapters.
[CmdletBinding()]
param(
    [ValidateSet("userprompt", "pretool", "precompact")]
    [string]$Context = "userprompt",
    [string]$TaskId = ""
)

if ($env:PLANNING_DISABLED -eq "1") { exit 0 }
$python = Get-Command python3 -ErrorAction SilentlyContinue
if (-not $python) { $python = Get-Command python -ErrorAction SilentlyContinue }
if (-not $python) { $python = Get-Command py -ErrorAction SilentlyContinue }
if (-not $python) { exit 0 }
$args = @((Join-Path $PSScriptRoot "runtime.py"), "inject", "--cwd", (Get-Location).Path, "--context", $Context)
if ($TaskId -ne "") { $args += @("--task-id", $TaskId) }
& $python.Source @args
# Hook context is advisory; invalid or escaped candidates fail closed without
# interrupting the host session.
exit 0
