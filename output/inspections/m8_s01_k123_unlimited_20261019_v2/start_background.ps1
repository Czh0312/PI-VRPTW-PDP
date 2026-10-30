$ErrorActionPreference = 'Stop'
if (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'launch.json')) {
    throw 'This run has already been launched; refusing a duplicate.'
}
$powerShellPath = (Get-Command pwsh).Source
$launcherPath = Join-Path $PSScriptRoot 'launch.ps1'
$studyProcess = Start-Process -FilePath $powerShellPath -WindowStyle Hidden -PassThru `
    -ArgumentList @('-NoLogo', '-NoProfile', '-File', ('"' + $launcherPath + '"')) `
    -RedirectStandardOutput (Join-Path $PSScriptRoot 'stdout.log') `
    -RedirectStandardError (Join-Path $PSScriptRoot 'stderr.log')
$registration = [pscustomobject]@{
    launcher_pid = $studyProcess.Id
    launcher_path = $launcherPath
    launch_time = [DateTimeOffset]::Now.ToString('o')
    study_directory = $PSScriptRoot
    time_limit = $null
}
$registration | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'launch.json') -Encoding utf8
$registration | ConvertTo-Json
