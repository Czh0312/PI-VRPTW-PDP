# Dot-source before observing JSON files written by a running experiment.
# Minimize the open-handle interval and request delete sharing. Windows may
# still deny replacement: do not poll live.json contents while workers run.
function Open-ThesisSharedRead {
    param([Parameter(Mandatory=$true)][string]$LiteralPath)
    $resolvedPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($LiteralPath)
    return [System.IO.FileStream]::new(
        $resolvedPath,
        [System.IO.FileMode]::Open,
        [System.IO.FileAccess]::Read,
        ([System.IO.FileShare]::ReadWrite -bor [System.IO.FileShare]::Delete)
    )
}

function Read-ThesisJsonShared {
    param([Parameter(Mandatory=$true)][string]$LiteralPath)
    $stream = Open-ThesisSharedRead -LiteralPath $LiteralPath
    $reader = $null
    try {
        $reader = [System.IO.StreamReader]::new($stream, [System.Text.Encoding]::UTF8, $true)
        $jsonText = $reader.ReadToEnd()
    } finally {
        if ($null -ne $reader) { $reader.Dispose() }
        else { $stream.Dispose() }
    }
    return ($jsonText | ConvertFrom-Json)
}
