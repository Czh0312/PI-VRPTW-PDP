"""Registered continuation under the original cumulative solve budget."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
DEST = HERE/'adaptive'
FRESH = HERE/'fresh'
sys.path.insert(0, str(DEST/'source_snapshot'))


def write(path, value): path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n')
def read(path): return json.loads(path.read_text())
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


if len(sys.argv) > 1:
    job = read(DEST/'jobs.json')[int(sys.argv[1])]
    from configuration.loader import load_routing_case
    from models.routing.common.cycle import load_instance
    from models.routing.common.cycle.audit import audit_solution
    from models.routing.solvers.exact.multibatch_hybrid import finish_hybrid
    before = read(Path(job['initial']))
    assert sha(Path(job['initial'])) == job['initial_sha256']
    p = load_instance(*load_routing_case(before['job']['case'], use_pool_config=False))
    x = before['experiment']; start = time.perf_counter()
    result = finish_hybrid(p, x['reference_certificate'], x['conditional_certificate'], x['result'],
        deadline=start+job['time_limit'], fallback_bound=x['bound'], seed=43, target_gap=.05,
        verbose=True, diagnostics_dir=DEST/(job['id']+'_models'))
    audit = audit_solution(p, result['result'])
    assert audit['valid'] and audit['periodic_replay']['valid']
    upper, lower = audit['objective'], result['bound']
    write(DEST/(job['id']+'.json'), dict(job=job, experiment=result, independent_audit=audit,
        upper_bound=upper, bound=lower, gap=max(0, upper-lower)/max(1e-9, abs(upper)),
        wall_seconds=time.perf_counter()-start, initial_bound=x['bound'], initial_upper=x['upper_bound']))
else:
    for name, digest in read(DEST/'plan.json')['source_sha256'].items():
        assert sha(DEST/'source_snapshot'/name) == digest
    jobs = read(DEST/'jobs.json') if (DEST/'jobs.json').exists() else []
    summary = read(DEST/'summary.json') if (DEST/'summary.json').exists() else []
    known = {j['id'] for j in jobs}
    for row in read(FRESH/'summary.json'):
        ident = row['job']['id']
        if ident in known or row['failure'] or row['target_reached']: continue
        initial = FRESH/(ident+'.json')
        remaining = max(0, row['job']['options']['time_limit']-row['wall_seconds'])
        if remaining < .05: continue
        job = dict(id=ident, initial=str(initial), initial_sha256=sha(initial), time_limit=remaining,
                   initial_wall_seconds=row['wall_seconds'], cumulative_budget=660, seed=43)
        jobs.append(job); write(DEST/'jobs.json', jobs)
        with (DEST/(ident+'.log')).open('w') as log:
            try:
                run = subprocess.run([sys.executable, str(Path(__file__)), str(len(jobs)-1)],
                    stdout=log, stderr=subprocess.STDOUT, timeout=remaining+20,
                    env={**os.environ, 'PYTHONHASHSEED': '0'})
                failure = f'EXIT_{run.returncode}' if run.returncode else None
            except subprocess.TimeoutExpired:
                failure = 'OUTER_TIMEOUT'
        record = dict(id=ident, failure=failure)
        if failure is None:
            result = read(DEST/(ident+'.json'))
            record.update(upper_bound=result['upper_bound'], bound=result['bound'], gap=result['gap'],
                target_reached=result['gap'] <= .05,
                cumulative_wall_seconds=row['wall_seconds']+result['wall_seconds'])
        summary.append(record); write(DEST/'summary.json', summary); print(record, flush=True)
