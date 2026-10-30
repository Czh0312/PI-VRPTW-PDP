$ErrorActionPreference = 'Stop'
$studyRoot = $PSScriptRoot
$repositoryRoot = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $studyRoot))
Set-Location -LiteralPath $repositoryRoot
& (Join-Path $repositoryRoot 'tools/run.ps1') python -Arguments @((Join-Path $studyRoot 'run_unlimited.py'))
exit $LASTEXITCODE
