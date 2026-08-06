# Version source: ../VERSION
[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Arguments
)

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) { $python = Get-Command python3 -ErrorAction SilentlyContinue }
if (-not $python) {
    Write-Error "Python is required to run planning-with-files initialization."
    exit 2
}

& $python.Source (Join-Path $PSScriptRoot "project_init.py") @Arguments
exit $LASTEXITCODE
