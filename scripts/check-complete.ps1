# Version source: ../VERSION
[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Arguments
)

if ($env:PLANNING_DISABLED -eq '1') { exit 0 }

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) { $python = Get-Command python3 -ErrorAction SilentlyContinue }
if (-not $python) {
    Write-Error "Python is required to run planning-with-files completion checks."
    exit 2
}

& $python.Source (Join-Path $PSScriptRoot "check_complete.py") @Arguments
exit $LASTEXITCODE
