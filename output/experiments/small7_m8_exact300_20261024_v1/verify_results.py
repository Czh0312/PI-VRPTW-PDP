"""Verify saved experiment evidence, without rerunning optimization."""
from collections import Counter
from pathlib import Path
import json
import shutil
import sys

out=Path(__file__).resolve().parent
sys.path.insert(0,str(out/'source'))
from experiments.runs import run_thesis_completion as d
from experiments.runs.spacetime_windows_io_adapter import atomic_write
d.write=atomic_write
manifest=d.read(out/'manifest.json')
d.verify(out,manifest)
assert len(manifest['jobs'])==9
assert {j['case'] for j in manifest['jobs']}=={f'S{i:02}' for i in range(1,10)}
rows=[]
interrupted=[]
for job in manifest['jobs']:
    complete=[]
    for attempt in d.attempt_dirs(out,job):
        assert d.complete_attempt(attempt), str(attempt)
        row=d.read(attempt/'row.json')
        if row['status']=='INTERRUPTED_IO':
            interrupted.append(dict(case=job['case'],attempt=row['attempt'],status=row['status']))
        else:
            complete.append((attempt,row))
    assert len(complete)==1, job['id']
    attempt,row=complete[0]
    result=d.read(attempt/'result.json')
    info=result['info']
    options=info['solver_config']
    assert options['time_limit']==300 and options['threads']==1 and options['seed']==42
    assert options['formulation']=='expanded'
    assert options['mip_gap']==0 and options['mip_gap_abs']==0
    assert not options['mip_start'] and not options['custom_callback'] and not options['external_certificate']
    assert info['complete_count_domain'] and info['batch_count_candidates']==list(range(1,8))
    assert info['proof_scope']=='full_model_8_all_K_joint_cost_integer_time_MILP'
    assert info['status']==row['status'] and info.get('objective')==row.get('objective')
    audit=d.read(attempt/'audit.json')['audit']
    assert bool(audit['valid'])==row['valid']
    assert not row.get('proven_optimal') or (row['valid'] and row.get('periodic_valid'))
    rows.append(row)
assert rows==d.read(out/'recovered_rows.json')
summary=dict(verified_at=d.now(),scope='S01-S09 / M8 / frozen spacetime_v4_1 / 300 seconds each',
    frozen_source_and_input_hashes_verified=True,completion_artifact_hashes_verified=True,
    complete_full_K_runs=len(rows),statuses=dict(Counter(r['status'] for r in rows)),
    audited_feasible=sum(r['valid'] for r in rows),proven_optimal=sum(r['proven_optimal'] for r in rows),
    interrupted_attempts=interrupted,
    solve_wall_min_seconds=min(r['solve_wall_seconds'] for r in rows),
    solve_wall_max_seconds=max(r['solve_wall_seconds'] for r in rows),
    unit_test_suite_run=False)
d.write(out/'verification.json',summary)
for source,target in [('recovered_report.md','report.md'),('recovered_rows.json','rows.json'),
                      ('recovered_runs.csv','runs.csv'),('recovered_progress.json','progress.json')]:
    shutil.copyfile(out/source,out/target)
print(json.dumps(summary,ensure_ascii=False,indent=2))
