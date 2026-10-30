# Readers permit atomic replacement of the active progress file on Windows.
$experimentPath = $PSScriptRoot
$caseRows = @(foreach ($case in @('S03','S08')) {
    $progressPath = Join-Path (Join-Path $experimentPath $case) 'progress.json'
    $stream = $null
    $reader = $null
    try {
        $shareMode = [IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete
        $stream = [IO.File]::Open($progressPath,[IO.FileMode]::Open,[IO.FileAccess]::Read,$shareMode)
        $reader = [IO.StreamReader]::new($stream)
        $value = $reader.ReadToEnd() | ConvertFrom-Json
        [PSCustomObject]@{
            Case=$case
            Stage=$value.stage
            Minutes=[math]::Round($(if ($value.stage -eq 'complete') { $value.algorithm_seconds } else { $value.elapsed })/60,2)
            Iteration=$(if ($value.stage -eq 'complete') { $value.iterations } else { $value.iteration })
            Upper=$value.audited_upper_bound
            Lower=$value.global_lower_bound
            GapPercent=[math]::Round(100*($value.audited_upper_bound-$value.global_lower_bound)/$value.audited_upper_bound,6)
            RSS_MB=$value.rss_mb
        }
    } catch {
        [PSCustomObject]@{Case=$case;Stage='read-retry';Message=$_.Exception.Message}
    } finally {
        if ($null -ne $reader) { $reader.Dispose() }
        elseif ($null -ne $stream) { $stream.Dispose() }
    }
})
$caseRows | ConvertTo-Json -Depth 4
