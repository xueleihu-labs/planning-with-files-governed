# Version source: ../VERSION
# Resolve the active planning directory through the shared Python runtime.
[CmdletBinding()]
param([string]$PlanningDir = "", [string]$TaskId = "")

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
if (-not $python) { exit 0 }

$runtime = Join-Path $PSScriptRoot "runtime.py"
$args = @($runtime, "resolve", "--cwd", (Get-Location).Path, "--quiet")
if ($PlanningDir -ne "") { $args += @("--planning-dir", $PlanningDir) }
if ($TaskId -ne "") { $args += @("--task-id", $TaskId) }
& $python.Source @args
# A resolver is a hook helper: invalid/escaped candidates fail closed to an
# empty result and must not break the host agent loop.
exit 0
