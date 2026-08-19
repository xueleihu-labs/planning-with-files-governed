# Version source: ../VERSION
# Emit plan context for PowerShell hook adapters.
[CmdletBinding()]
param(
    [ValidateSet("userprompt", "pretool", "precompact")]
    [string]$Context = "userprompt",
    [string]$TaskId = ""
)

if ($env:PLANNING_DISABLED -eq "1") { exit 0 }
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
$args = @((Join-Path $PSScriptRoot "runtime.py"), "inject", "--cwd", (Get-Location).Path, "--context", $Context)
if ($TaskId -ne "") { $args += @("--task-id", $TaskId) }
& $python.Source @args
# Hook context is advisory; invalid or escaped candidates fail closed without
# interrupting the host session.
exit 0
