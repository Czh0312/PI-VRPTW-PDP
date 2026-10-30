$ErrorActionPreference = 'Stop'
$studyRoot = $PSScriptRoot
$studyProjectRoot = (Get-Item -LiteralPath $studyRoot).Parent.Parent.Parent.FullName
Set-Location -LiteralPath $studyProjectRoot
& .\tools\run.ps1 python -Arguments @(
    (Join-Path $studyRoot 'driver.py'),
    'run', '--output', $studyRoot
)
exit $LASTEXITCODE
