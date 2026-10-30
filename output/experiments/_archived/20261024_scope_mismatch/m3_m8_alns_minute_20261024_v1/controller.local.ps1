$ErrorActionPreference = 'Stop'
$experimentRoot = $PSScriptRoot
$repositoryRoot = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $experimentRoot))
Set-Location -LiteralPath $repositoryRoot
& (Join-Path $repositoryRoot 'tools/run.ps1') python -Arguments @(
    (Join-Path $experimentRoot 'driver.py'), 'run', '--output', $experimentRoot
)
exit $LASTEXITCODE
