param([Parameter(Mandatory=$true)][string]$RepositoryRoot)
$ErrorActionPreference='Stop'
$workspaceRoot=(Resolve-Path -LiteralPath $RepositoryRoot).Path.TrimEnd([IO.Path]::DirectorySeparatorChar)
$workspacePrefix=$workspaceRoot+[IO.Path]::DirectorySeparatorChar
$archiveRoot=[IO.Path]::GetFullPath((Join-Path $workspaceRoot 'archive/datasets/retired_20261024'))
$archivePrefix=$archiveRoot+[IO.Path]::DirectorySeparatorChar
$archivePlan=Get-Content -LiteralPath (Join-Path $PSScriptRoot 'archive_plan.json') -Raw | ConvertFrom-Json
$moves=@()
# Validate every resolved absolute endpoint before any recursive directory move.
foreach ($entry in $archivePlan.targets) {
    $source=(Resolve-Path -LiteralPath (Join-Path $workspaceRoot $entry.source)).Path
    $destination=[IO.Path]::GetFullPath((Join-Path $workspaceRoot $entry.destination))
    if (-not $source.StartsWith($workspacePrefix,[StringComparison]::OrdinalIgnoreCase)) { throw "Escaping source: $source" }
    if (-not $destination.StartsWith($archivePrefix,[StringComparison]::OrdinalIgnoreCase)) { throw "Escaping archive destination: $destination" }
    if (Test-Path -LiteralPath $destination) { throw "Refusing to overwrite archive: $destination" }
    if ((Get-Item -LiteralPath $source).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Refusing to move a reparse point: $source" }
    $ancestor=Split-Path -Parent $destination
    while ($ancestor.StartsWith($workspacePrefix,[StringComparison]::OrdinalIgnoreCase)) {
        if ((Test-Path -LiteralPath $ancestor) -and ((Get-Item -LiteralPath $ancestor).Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw "Archive traverses a reparse point: $ancestor" }
        $ancestor=Split-Path -Parent $ancestor
    }
    $moves += [pscustomobject]@{source=$source;destination=$destination;relative_source=$entry.source;file_count=$entry.file_count}
}
$finished=@()
foreach ($move in $moves) {
    New-Item -ItemType Directory -Path (Split-Path -Parent $move.destination) -Force | Out-Null
    Move-Item -LiteralPath $move.source -Destination $move.destination -ErrorAction Stop
    $finished += [pscustomobject]@{source=$move.relative_source;destination=$move.destination;files=$move.file_count;status='MOVED'}
    [ordered]@{status='IN_PROGRESS';moves=$finished;deleted_files=0;solver_started=$false} | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'archive_progress.json') -Encoding utf8
}
[ordered]@{status='MOVED_PENDING_HASH_VERIFICATION';moves=$finished;deleted_files=0;solver_started=$false} | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'archive_progress.json') -Encoding utf8
[pscustomobject]@{moved_paths=$finished.Count;moved_files=($finished | Measure-Object files -Sum).Sum;deleted_files=0;solver_started=$false} | ConvertTo-Json
