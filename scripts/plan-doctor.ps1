# Version source: ../VERSION
# Diagnostic-only runtime self-check. It does not alter plan state.
[CmdletBinding()]
param([string]$TaskId = "")

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
if (-not $python) { Write-Output "WARN  Python unavailable"; exit 0 }
$args = @((Join-Path $PSScriptRoot "runtime.py"), "doctor", "--cwd", (Get-Location).Path)
if ($TaskId -ne "") { $args += @("--task-id", $TaskId) }
& $python.Source @args
exit 0
