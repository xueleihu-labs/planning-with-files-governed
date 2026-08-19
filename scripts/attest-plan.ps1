# Version source: ../VERSION
# Lock, show or clear the active task_plan.md SHA-256 attestation.
[CmdletBinding(DefaultParameterSetName = "Attest")]
param(
    [Parameter(ParameterSetName = "Show")][switch]$Show,
    [Parameter(ParameterSetName = "Clear")][switch]$Clear,
    [string]$TaskId = ""
)

function Find-PythonCommand {
    $names = if ($env:OS -eq "Windows_NT") { @("python", "py", "python3") } else { @("python3", "python", "py") }
    foreach ($name in $names) {
        $candidate = Get-Command $name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if (-not $candidate) { continue }
        & $candidate.Source -c "import sys" *> $null
        if ($LASTEXITCODE -eq 0) { return $candidate }
    }
    return $null
}

$python = Find-PythonCommand
if (-not $python) { Write-Error "Python is required"; exit 2 }
$args = @((Join-Path $PSScriptRoot "runtime.py"), "attest", "--cwd", (Get-Location).Path)
if ($Show) { $args += "--show" }
if ($Clear) { $args += "--clear" }
if ($TaskId -ne "") { $args += @("--task-id", $TaskId) }
& $python.Source @args
exit $LASTEXITCODE
