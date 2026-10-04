"""Post-report diagnostic: same small M4 cases and source, 60-second budget."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
BASE = ROOT/'output/experiments/free_assignment_joint_20260930'
sys.path.insert(0, str(ROOT))


def worker(job):
    from configuration.loader import load_routing_case
    from models.routing.common.cycle import load_instance
    from models.routing.common.cycle.audit import audit_solution
    from models.routing.variants.model_4.exact.solver import solve_exact
    config, fleet = load_routing_case(job['case'])
    problem = load_instance({**config, 'model': 4}, fleet)
    start = time.perf_counter()
    result = solve_exact(problem, time_limit=job['budget'], seed=job['seed'], verbose=True)
    wall = time.perf_counter()-start
    audit = audit_solution(problem, result)
    (OUT/(job['family']+'.json')).write_text(json.dumps(dict(job=job, result=result,
        independent_audit=audit, wall=wall), ensure_ascii=False, indent=2))


def main():
    if len(sys.argv) > 1:
        worker(json.loads(sys.argv[1]))
        return
    source_hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                     for base in ('models', 'configuration') for p in (ROOT/base).rglob('*.py')}
    expected = json.loads((BASE/'after/hashes.json').read_text())
    assert source_hashes == expected['sources']
    assert all(hashlib.sha256((BASE/p).read_bytes()).hexdigest() == h for p,h in expected['inputs'].items())
    jobs = [dict(family=family, model=4, budget=60, seed=42,
                 case=str(BASE/'benchmark_inputs'/family/'case.json'))
            for family in ('lade_0823_10aoi', 'lade_0825_10aoi')]
    with (OUT/'manifest.json').open('x') as f:
        json.dump(dict(purpose='diagnose_short_budget_vs_small_instance_proof_difficulty',
                       jobs=jobs, source_hashes=source_hashes, input_hashes=expected['inputs'],
                       outer_timeout_seconds=80, created=time.strftime('%Y-%m-%dT%H:%M:%S')),
                  f, ensure_ascii=False, indent=2)
    summary = []
    for job in jobs:
        with (OUT/(job['family']+'.log')).open('w') as log:
            try:
                process = subprocess.run([sys.executable, str(Path(__file__).resolve()), json.dumps(job)],
                    cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, timeout=80,
                    env={**os.environ, 'PYTHONHASHSEED': '0'})
                if process.returncode:
                    row = dict(job=job, status='ERROR', returncode=process.returncode)
                else:
                    saved = json.loads((OUT/(job['family']+'.json')).read_text())
                    row = dict(job=job, wall=saved['wall'], valid=saved['independent_audit']['valid'],
                               periodic=saved['independent_audit'].get('periodic_replay',{}).get('valid'),
                               info=saved['result']['info'])
            except subprocess.TimeoutExpired:
                row = dict(job=job, status='OUTER_TIMEOUT')
        summary.append(row)
        (OUT/'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
        print(job['family'], row.get('info',{}).get('status',row.get('status')),
              row.get('info',{}).get('objective'), row.get('info',{}).get('mip_gap'), flush=True)


if __name__ == '__main__':
    main()
