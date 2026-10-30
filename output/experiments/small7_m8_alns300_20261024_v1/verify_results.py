"""Check frozen ALNS artifacts, independent audits and prior MILP bounds."""
from collections import Counter
from pathlib import Path
import csv
import json
import math
import sys

out=Path(__file__).resolve().parent
sys.path.insert(0,str(out/'source'))
from experiments.runs import run_thesis_completion as d
from experiments.runs.spacetime_windows_io_adapter import atomic_write
d.write=atomic_write
manifest=d.read(out/'manifest.json');d.verify(out,manifest)
assert len(manifest['jobs'])==9
assert {j['case'] for j in manifest['jobs']}=={f'S{i:02}' for i in range(1,10)}
evidence=d.read(out/'comparison_evidence.json')
assert evidence['physical_economic_policy_and_K_inputs_match']
assert not evidence['source_differences']
prior={r['case']:r for r in d.read(Path(evidence['prior'])/'rows.json')}
rows,comparison=[],[]
for job in manifest['jobs']:
    assert job['method']=='alns' and job['budget']==300 and job['seed']==42
    attempts=d.attempt_dirs(out,job)
    assert len(attempts)==1 and d.complete_attempt(attempts[0]),job['id']
    attempt=attempts[0]
    row=d.read(attempt/'row.json');result=d.read(attempt/'result.json')
    audit_record=d.read(attempt/'audit.json');audit=audit_record['audit']
    config=d.read(out/job['case_path'])
    assert not config.get('initial_joint_result')
    assert config['alns_time']==300 and config['alns_iter']==10**9 and config['seed']==42
    assert config['joint_batch_options']['batch_count_candidates']==list(range(1,8))
    assert audit_record['result_sha256']==d.sha(attempt/'result.json')
    assert row['valid']==bool(audit['valid'])
    assert not row['proven_optimal'] and not result['info']['proven_optimal']
    if row['valid']:
        assert row['periodic_valid'] and audit['periodic_replay']['valid']
        assert math.isfinite(row['objective'])
        assert math.isclose(row['objective'],result['info']['objective'],rel_tol=1e-7,abs_tol=1e-6)
        assert math.isclose(row['objective'],audit['objective'],rel_tol=1e-7,abs_tol=1e-6)
    bound=prior[job['case']].get('best_bound')
    if bound is not None and row['valid']:
        assert bound <= row['objective']+max(1e-5,1e-7*abs(row['objective']))
    comparison.append(dict(case=job['case'],exact_status=prior[job['case']]['status'],
        exact_lower_bound=bound,alns_status=row['status'],alns_valid=row['valid'],
        alns_cost=row.get('objective'),k=row.get('k'),pool=row.get('pool'),
        alns_wall_seconds=row.get('solve_wall_seconds'),
        bound_gap_percent=100*(row['objective']-bound)/abs(row['objective'])
            if bound is not None and row['valid'] else None,
        gap_scope='gap to earlier MILP lower bound; not a gap to a known optimum'))
    rows.append(row)
assert rows==d.read(out/'rows.json')
with (out/'exact_alns_comparison.csv').open('w',encoding='utf-8-sig',newline='') as handle:
    writer=csv.DictWriter(handle,fieldnames=list(comparison[0]))
    writer.writeheader();writer.writerows(comparison)
feasible=[r for r in rows if r['valid']]
confirmed=[r['first_observed_feasible_seconds'] for r in feasible if r.get('first_observed_feasible_seconds') is not None]
verification=dict(verified_at=d.now(),scope='S01-S09 / M8 / ALNS 300 seconds / seed 42',
    frozen_source_and_input_hashes_verified=True,completion_artifact_hashes_verified=True,
    same_inputs_and_solver_sources_as_prior_exact_experiment=True,completed=len(rows),
    statuses=dict(Counter(r['status'] for r in rows)),independently_audited_feasible=len(feasible),
    two_cycle_replay_passed=sum(bool(r.get('periodic_valid')) for r in rows),
    proven_optimal=sum(r['proven_optimal'] for r in rows),
    first_confirmed_feasible_min_seconds=min(confirmed) if confirmed else None,
    first_confirmed_feasible_max_seconds=max(confirmed) if confirmed else None,
    timing_scope='Confirmed after a returned K candidate; not exact first construction time',
    solve_wall_min_seconds=min(r['solve_wall_seconds'] for r in rows),
    solve_wall_max_seconds=max(r['solve_wall_seconds'] for r in rows),unit_test_suite_run=False)
d.write(out/'verification.json',verification)
print(json.dumps(verification,ensure_ascii=False,indent=2))
