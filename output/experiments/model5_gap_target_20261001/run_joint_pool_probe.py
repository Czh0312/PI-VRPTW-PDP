"""Frozen restricted joint route-pool UB probe; never adopt its bound globally."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
DEST = HERE/'joint_route_pool_120s'


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)+'\n')


if len(sys.argv) > 1:
    sys.path.insert(0, str(DEST/'source_snapshot'))
    from configuration.loader import load_routing_case
    from models.routing.common.cycle import load_instance
    from models.routing.common.cycle.audit import audit_solution
    from models.routing.variants.model_5.exact import reoptimize_route_pool
    job = json.loads(sys.argv[1])
    p = load_instance(*load_routing_case(job['case']))
    initial = json.loads(Path(job['initial_record']).read_text())['result']
    before = audit_solution(p, initial)
    assert before['valid'] and before['periodic_replay']['valid']
    result = reoptimize_route_pool(p, initial, time_limit=job['time_limit'], seed=42,
        verbose=True, max_columns=128, diagnostics_dir=DEST/job['id'])
    result = json.loads(json.dumps(result))
    after = audit_solution(p, result)
    assert after['valid'] and after['periodic_replay']['valid']
    assert after['objective'] <= before['objective']+1e-6
    assert result['info']['best_bound'] is None and result['info']['mip_gap'] is None
    assert not result['info']['proven_optimal']
    gap = 100*(after['objective']-job['global_lower_bound'])/abs(after['objective'])
    write(DEST/(job['id']+'.json'), dict(job=job, result=result, initial_audit=before,
        independent_audit=after, inherited_global_lower_bound=job['global_lower_bound'],
        gap_percent=gap, meets_five_percent=gap <= 5))
else:
    DEST.mkdir(exist_ok=False)
    for source in [*ROOT.glob('models/**/*.py'), *ROOT.glob('configuration/*.py')]:
        target = DEST/'source_snapshot'/source.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    plan = json.loads((HERE/'plan.json').read_text())
    selected = plan['diagnostic_cases'][1]
    job = dict(id='50_tight', case=selected['case'],
        initial_record=str(HERE/'warm_60s_plus_polish_5s/01_42_retimed-cg.json'),
        global_lower_bound=1406.2804405293473, time_limit=120, seed=42)
    write(DEST/'manifest.json', dict(job=job,
        source_sha256={str(p.relative_to(DEST/'source_snapshot')): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (DEST/'source_snapshot').rglob('*.py')},
        note='Exploratory closest-to-target observed case. Additional runtime. Restricted master bound is NOT a full M5 lower bound.'))
    with (DEST/'worker.log').open('w') as log:
        run = subprocess.run([sys.executable, str(Path(__file__)), json.dumps(job)],
            stdout=log, stderr=subprocess.STDOUT, timeout=145)
    write(DEST/'summary.json', dict(exit=run.returncode))
    if run.returncode == 0:
        saved = json.loads((DEST/'50_tight.json').read_text())
        print({k:saved[k] for k in ('gap_percent', 'meets_five_percent')})
        print(saved['result']['info'])
    else:
        print('FAILED: retained worker.log')
