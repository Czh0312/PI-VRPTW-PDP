param([Parameter(Mandatory=$true)][string]$RepositoryRoot)
$ErrorActionPreference = 'Stop'
$workspacePath = (Resolve-Path -LiteralPath $RepositoryRoot).Path.TrimEnd([IO.Path]::DirectorySeparatorChar)
$pathPrefix = $workspacePath + [IO.Path]::DirectorySeparatorChar
function Assert-WorkspacePath([string]$Candidate) {
    $absolute = [IO.Path]::GetFullPath($Candidate)
    if (-not $absolute.StartsWith($pathPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Path leaves the intended workspace: $absolute"
    }
    return $absolute
}
function Save-Json([string]$Destination, $Value) {
    $Value | ConvertTo-Json -Depth 35 | Set-Content -LiteralPath $Destination -Encoding utf8
}
function Digest([string]$FilePath) {
    return (Get-FileHash -LiteralPath $FilePath -Algorithm SHA256).Hash.ToLowerInvariant()
}
$oldPath = Assert-WorkspacePath (Join-Path $workspacePath 'output/experiments/m3_m8_alns_minute_20261024_v1')
$archiveParent = Assert-WorkspacePath (Join-Path $workspacePath 'output/experiments/_archived/20261024_scope_mismatch')
$archivePath = Assert-WorkspacePath (Join-Path $archiveParent 'm3_m8_alns_minute_20261024_v1')
$planPath = Assert-WorkspacePath (Join-Path $workspacePath 'config/experiments/m3_m8_alns_7_20_50_3cases_v1')
$auditPath = Assert-WorkspacePath $PSScriptRoot
if (-not (Test-Path -LiteralPath $oldPath -PathType Container)) { throw 'Old experiment is absent; refusing an ambiguous cleanup.' }
if (Test-Path -LiteralPath $archivePath) { throw 'Archive target already exists; never overwrite historical evidence.' }
if (Test-Path -LiteralPath (Join-Path $planPath 'plan.json')) { throw 'Corrected plan already exists; never overwrite a registered selection.' }
if ((Get-Item -LiteralPath $oldPath).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Refusing to move a reparse point.' }
$stopState = Get-Content -LiteralPath (Join-Path $oldPath 'controller_state.json') -Raw | ConvertFrom-Json
if ($stopState.status -ne 'STOPPED_BY_USER' -or -not (Test-Path -LiteralPath (Join-Path $oldPath 'STOP'))) { throw 'Experiment must be stopped before archival.' }
foreach ($ownedId in @(25376,23996,2000)) {
    if (Get-Process -Id $ownedId -ErrorAction SilentlyContinue) { throw "Recorded process still present: $ownedId" }
}
$frozenPath = Join-Path $workspacePath 'config/experiments/benchmark27_spacetime_v4_1/frozen.json'
$frozenBefore = Digest $frozenPath
$frozen = Get-Content -LiteralPath $frozenPath -Raw | ConvertFrom-Json
$datasetPath = Join-Path $workspacePath $frozen.dataset
$datasetFiles = @{
    'manifest.json'=$frozen.manifest_sha256
    'selection.json'=$frozen.selection_sha256
    'case_index.csv'=$frozen.case_index_sha256
}
foreach ($entry in $datasetFiles.GetEnumerator()) {
    if ((Digest (Join-Path $datasetPath $entry.Key)) -ne $entry.Value) { throw "Frozen dataset differs: $($entry.Key)" }
}
$allRecords = @(Import-Csv -LiteralPath (Join-Path $datasetPath 'case_index.csv') | Where-Object model -eq '3')
$specifications = @(
    @{size='small'; customers=7; seconds=300},
    @{size='medium'; customers=20; seconds=600},
    @{size='large'; customers=50; seconds=1200}
)
$selected = @()
$inputChecks = @()
$physicalFields = @('order_id','customer_id','satellite_id','container_count','goods_weight','delivery_ready','delivery_due','pickup_ready','pickup_due','service_time','min_use_time','release_time')
foreach ($specification in $specifications) {
    $eligible = @($allRecords | Where-Object { $_.size -eq $specification.size -and [int]$_.customer_count -eq $specification.customers } | Sort-Object base_case_id)
    if ($eligible.Count -lt 3) { throw "Fewer than three cases of exactly $($specification.customers) customers." }
    foreach ($caseRecord in @($eligible | Select-Object -First 3)) {
        $caseId = $caseRecord.base_case_id
        $pairedHashes = @{}
        $pairedPhysical = @{}
        $casePaths = @{}
        foreach ($modelNumber in @(3,8)) {
            $relativeCase = "data/formal_benchmark27_spacetime_v4_1/model_$modelNumber/$($specification.size)/$caseId/case.json"
            $absoluteCase = Join-Path $workspacePath $relativeCase
            $caseConfiguration = Get-Content -LiteralPath $absoluteCase -Raw | ConvertFrom-Json
            $orders = @(Get-Content -LiteralPath (Join-Path $workspacePath $caseConfiguration.orders) -Raw | ConvertFrom-Json)
            $customerCount = @($orders.customer_id | Sort-Object -Unique).Count
            $boxes = ($orders | Measure-Object -Property container_count -Sum).Sum
            if ($orders.Count -ne $specification.customers -or $customerCount -ne $specification.customers -or $boxes -ne $specification.customers) {
                throw "Actual orders/customers/boxes do not equal required count: $caseId M$modelNumber"
            }
            $hashes = [ordered]@{case=(Digest $absoluteCase)}
            foreach ($field in @('data','orders','routing_config','operating_policy','economic_config')) {
                $hashes[$field] = Digest (Join-Path $workspacePath $caseConfiguration.$field)
            }
            $pairedHashes[$modelNumber] = $hashes
            $pairedPhysical[$modelNumber] = $orders | Sort-Object order_id | Select-Object -Property $physicalFields | ConvertTo-Json -Depth 10 -Compress
            $casePaths["model_$modelNumber"] = $relativeCase
            $inputChecks += [ordered]@{case=$caseId; model=$modelNumber; size=$specification.size; required_customers=$specification.customers; actual_customers=$customerCount; actual_orders=$orders.Count; actual_boxes=$boxes; valid=$true; source_case=$relativeCase; input_sha256=$hashes}
        }
        foreach ($field in @('data','routing_config','economic_config')) {
            if ($pairedHashes[3][$field] -ne $pairedHashes[8][$field]) { throw "Paired physical/economic input differs: $caseId $field" }
        }
        if ($pairedPhysical[3] -ne $pairedPhysical[8]) { throw "Paired customer demand/windows differ: $caseId" }
        $selected += [ordered]@{case=$caseId; size=$specification.size; customer_count=$specification.customers; budget_seconds_per_model_per_seed=$specification.seconds; family_id=$caseRecord.family_id; source_block=$caseRecord.source_block; source_cases=$casePaths}
    }
}
if ($selected.Count -ne 9 -or $inputChecks.Count -ne 18) { throw 'Unexpected scope count.' }
$evidenceBefore = @{}
foreach ($name in @('manifest.json','manifest.sha256.json','user_stop.json','progress.jsonl')) {
    $evidenceBefore[$name] = Digest (Join-Path $oldPath $name)
}
foreach ($runName in @('S01_m3_alns_300s_s42','S01_m8_alns_300s_s42')) {
    foreach ($name in @('result.json','audit.json','row.json','completion.json')) {
        $relativeEvidence = "runs/$runName/attempt_001/$name"
        $evidenceBefore[$relativeEvidence] = Digest (Join-Path $oldPath $relativeEvidence)
    }
}
$validationTime = (Get-Date).ToUniversalTime().ToString('o')
$jobs = @()
foreach ($caseSelection in $selected) {
    foreach ($seedNumber in @(42,43,44)) {
        foreach ($modelNumber in @(3,8)) {
            $jobs += [ordered]@{id="$($caseSelection.case)_m${modelNumber}_alns_$($caseSelection.budget_seconds_per_model_per_seed)s_s$seedNumber"; case=$caseSelection.case; size=$caseSelection.size; customer_count=$caseSelection.customer_count; model=$modelNumber; seed=$seedNumber; budget_seconds=$caseSelection.budget_seconds_per_model_per_seed; method='alns'; objective='cost'; case_path=$caseSelection.source_cases["model_$modelNumber"]}
        }
    }
}
$plannedSeconds = ($jobs | ForEach-Object { $_.budget_seconds } | Measure-Object -Sum).Sum
if ($jobs.Count -ne 54 -or $plannedSeconds -ne 37800) { throw 'Incorrect run count or total budget.' }
New-Item -ItemType Directory -Path $planPath -Force | Out-Null
$plan = [ordered]@{
    schema_version=1; plan_id='m3_m8_alns_7_20_50_3cases_v1'; status='PREPARED_NOT_STARTED'; created_at=$validationTime;
    user_scope='Small exactly 7, medium exactly 20, large exactly 50 customers; three cases per size.';
    source_dataset=$frozen.dataset; source_dataset_manifest_sha256=$frozen.manifest_sha256;
    selection_rule='Filter exact customer counts first, then select first three base_case_id values in each size. No filtering by solver outcome.';
    customer_count_by_size=@{small=7;medium=20;large=50}; cases_per_size=3; models=@(3,8); seeds=@(42,43,44);
    objective_by_model=@{'3'='cost';'8'='cost'}; budgets_seconds=@{small=300;medium=600;large=1200};
    m8_batch_count_candidates=@(1,2,3,4); m8_calendar_decision_grid_seconds=60; preserve_original_physical_time_units=$true;
    m3_preserve_fixed_calendar=$true; external_initial_solutions=$false;
    required_pre_dispatch_checks=@('actual_unique_customers_equals_required_count','actual_orders_equals_required_count','three_cases_per_size','paired_physical_and_economic_inputs_match','source_hashes_match_registered_selection');
    automatic_start=$false; user_stop_remains_effective=$true;
    selected_cases=$selected; jobs=$jobs; planned_case_count=9; planned_runs=54; planned_solver_seconds=$plannedSeconds; planned_solver_hours=10.5;
    previous_experiment='output/experiments/_archived/20261024_scope_mismatch/m3_m8_alns_minute_20261024_v1';
    prior_result_policy='Archived S01 remains valid as an audited 7-customer exploratory pair; do not automatically merge it into a new formal study. M05 was interrupted and excluded; no large case was run.'
}
Save-Json (Join-Path $planPath 'plan.json') $plan
Save-Json (Join-Path $planPath 'input_validation.json') ([ordered]@{status='PASS'; checked_at=$validationTime; checked_model_cases=18; all_exact_customer_counts_match=$true; paired_physical_inputs_match=$true; selected_cases=$inputChecks; solver_started=$false; validation_type='Input and selection inspection only; no optimization or unit-test run.'})
$selected | ForEach-Object { [pscustomobject]@{case=$_.case;size=$_.size;customer_count=$_.customer_count;model3_case=$_.source_cases['model_3'];model8_case=$_.source_cases['model_8'];seconds_per_model_per_seed=$_.budget_seconds_per_model_per_seed} } | Export-Csv -LiteralPath (Join-Path $planPath 'selection.csv') -NoTypeInformation -Encoding utf8
Save-Json (Join-Path $planPath 'registered_hashes.json') ([ordered]@{plan_sha256=(Digest (Join-Path $planPath 'plan.json')); selection_sha256=(Digest (Join-Path $planPath 'selection.csv')); input_validation_sha256=(Digest (Join-Path $planPath 'input_validation.json'))})
New-Item -ItemType Directory -Path $archiveParent -Force | Out-Null
if ((Get-Item -LiteralPath $archiveParent).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Archive parent is a reparse point.' }
# Both resolved absolute endpoints were checked against the workspace above.
# Move only this explicitly named, stopped experiment, with no overwrite/delete.
Move-Item -LiteralPath $oldPath -Destination $archivePath -ErrorAction Stop
foreach ($entry in $evidenceBefore.GetEnumerator()) {
    if ((Digest (Join-Path $archivePath $entry.Key)) -ne $entry.Value) { throw "Archived evidence hash differs: $($entry.Key)" }
}
foreach ($entry in $datasetFiles.GetEnumerator()) {
    if ((Digest (Join-Path $datasetPath $entry.Key)) -ne $entry.Value) { throw 'Original dataset changed during cleanup.' }
}
if ((Digest $frozenPath) -ne $frozenBefore) { throw 'Original frozen dataset registration changed.' }
$archiveStatus = [ordered]@{status='ARCHIVED_SCOPE_MISMATCH'; archived_at=(Get-Date).ToUniversalTime().ToString('o'); original_path=$oldPath; archive_path=$archivePath; reason='Old runner selected 30/100-customer pilots and a mixed-size 27-case queue instead of exactly 7/20/50 customers and three cases per size.'; excluded_from_current_formal_comparison=$true; completed_S01_pair_preserved=$true; interrupted_M05_30_customer_run_excluded=$true; large_case_runs=0; preserved_evidence_hashes=$evidenceBefore; evidence_hashes_unchanged=$true; originals_unchanged=$true; no_deletion=$true; replay_note='Frozen cases retain their original absolute paths; restore the original directory path before replaying. Archive is historical evidence, not an active execution folder.'}
Save-Json (Join-Path $archivePath 'archive_status.json') $archiveStatus
$archivedProgress = Get-Content -LiteralPath (Join-Path $archivePath 'progress.json') -Raw | ConvertFrom-Json
$archivedProgress.status='ARCHIVED_SCOPE_MISMATCH'
Save-Json (Join-Path $archivePath 'progress.json') $archivedProgress
$readme = @'
# 模型3与模型8：已纠正的实验清单

当前状态：仅准备清单，保持停止，未启动新求解。

| 规模 | 每例客户数 | 算例 | 每模型、每种子预算 |
|---|---:|---|---:|
| 小 | 7 | S01、S02、S03 | 5分钟 |
| 中 | 20 | M01、M02、M03 | 10分钟 |
| 大 | 50 | L01、L02、L03 | 20分钟 |

每档3例，共9例；模型3与模型8；配对种子42、43、44；共54次求解，总算法预算10.5小时。

先按客户数严格过滤，再按算例编号选前三例，未根据求解成本选择算例。已逐一读取两个模型的18份订单文件，核对实际顾客数、订单数、一客一箱及配对物理与费用输入。

两个模型均采用总成本目标；模型8的K候选为1～4，边界与截点整分钟；物理行驶、服务及清洗时间保留原精度。模型3保留原单批次固定日历。

旧数据集本身包含中20/30、大50/100；之前的错误是按混合规模标签选例，没有严格筛选客户数。原始数据集未改写。本文件夹的plan.json及selection.csv才是本轮纠正后的选例清单，未来启动前须再次执行计划中的硬性检查。

旧实验已移入output/experiments/_archived/20261024_scope_mismatch/，不作为当前正式对比汇总。S01的7客户配对结果及审计保留；M05的30客户运行中断，无有效结果；100客户算例未运行。
'@
Set-Content -LiteralPath (Join-Path $planPath 'README.md') -Value $readme -Encoding utf8
$archiveNotice = @'
# 已归档：选例规模与当前要求不符

本目录属于被停止的旧实验，不能作为小7、中20、大50、每档3例的正式汇总。旧清单中中规模混入30客户，大规模混入100客户。

保留原配置、源码快照、日志及完整结果用于追溯，没有删除证据。S01的两个7客户结果有效，但仅是旧方案中的一组探索结果；M05的30客户运行中断，不构成实验结果；没有运行大规模算例。

当前正确选例见config/experiments/m3_m8_alns_7_20_50_3cases_v1/README.md。所有求解保持停止。

注意：冻结配置保留原绝对路径。若将来审计重放，应先恢复原目录路径或另建明确记录路径映射的新实验，不直接修改本归档的冻结配置。
'@
Set-Content -LiteralPath (Join-Path $archivePath 'ARCHIVE_NOTICE.md') -Value $archiveNotice -Encoding utf8
$cleanupSummary = [ordered]@{status='PASS'; completed_at=(Get-Date).ToUniversalTime().ToString('o'); archived_only='output/experiments/m3_m8_alns_minute_20261024_v1'; archive='output/experiments/_archived/20261024_scope_mismatch/m3_m8_alns_minute_20261024_v1'; corrected_plan='config/experiments/m3_m8_alns_7_20_50_3cases_v1/plan.json'; cases_by_size=@{small=@('S01','S02','S03');medium=@('M01','M02','M03');large=@('L01','L02','L03')}; customer_counts=@{small=7;medium=20;large=50}; model_case_inputs_checked=18; preserved_completed_runs=2; interrupted_excluded_runs=1; large_executed_runs=0; evidence_hashes_unchanged=$true; frozen_dataset_and_registration_unchanged=$true; other_experiments_untouched=$true; deleted_files=0; solver_started=$false; planned_runs=54; planned_solver_hours=10.5}
Save-Json (Join-Path $auditPath 'cleanup_summary.json') $cleanupSummary
$cleanupSummary | ConvertTo-Json -Depth 8
