# Version source: ../VERSION
# Lock, show or clear the active task_plan.md SHA-256 attestation.
[CmdletBinding(DefaultParameterSetName = "Attest")]
param(
    [Parameter(ParameterSetName = "Show")][switch]$Show,
    [Parameter(ParameterSetName = "Clear")][switch]$Clear,
    [string]$TaskId = ""
)

$python = Get-Command python3 -ErrorAction SilentlyContinue
if (-not $python) { $python = Get-Command python -ErrorAction SilentlyContinue }
if (-not $python) { $python = Get-Command py -ErrorAction SilentlyContinue }
if (-not $python) { Write-Error "Python is required"; exit 2 }
$args = @((Join-Path $PSScriptRoot "runtime.py"), "attest", "--cwd", (Get-Location).Path)
if ($Show) { $args += "--show" }
if ($Clear) { $args += "--clear" }
if ($TaskId -ne "") { $args += @("--task-id", $TaskId) }
& $python.Source @args
exit $LASTEXITCODE
