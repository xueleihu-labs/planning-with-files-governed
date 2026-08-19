# Version source: ../VERSION
[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Arguments
)

if ($env:PLANNING_DISABLED -eq '1') { exit 0 }

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
if (-not $python) {
    Write-Error "Python is required to run planning-with-files completion checks."
    exit 2
}

& $python.Source (Join-Path $PSScriptRoot "check_complete.py") @Arguments
exit $LASTEXITCODE
