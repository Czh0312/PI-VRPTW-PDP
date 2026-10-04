"""Sequential E1-only ablations, using the formal frozen source and input."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

here = Path(__file__).resolve().parent
frozen = here/'final_60s'
sys.path.insert(0, str(frozen/'source_snapshot'))


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n')


if len(sys.argv) > 1:
    job = json.loads(sys.argv[1])
    from configuration.loader import load_routing_case
    from models.routing.common.cycle import load_instance
    from models.routing.common.cycle.audit import audit_solution
    from models.routing.solvers.exact.multibatch_bounds import distance_certificate
    from models.routing.solvers.exact.multibatch_e1_inventory import e1_inventory_certificate
    p = load_instance(*load_routing_case(job['case']))
    initial = json.loads(Path(job['initial_result']).read_text())
    audited = audit_solution(p, initial)
    assert audited['valid']
    started = time.perf_counter()
    cert = e1_inventory_certificate(p, time_limit=job['time_limit'], backend=job['backend'],
        inventory=job['inventory'], seed=42, initial_result=initial, verbose=True,
        distance_bounds=distance_certificate(p), diagnostics_path=here/'e1_ablation'/(job['id']+'.mps'))
    save(here/'e1_ablation'/(job['id']+'.json'), dict(job=job, certificate=cert, independent_audit=audited,
                                                  wall_seconds=time.perf_counter()-started))
else:
    out = here/'e1_ablation'
    out.mkdir(exist_ok=False)
    jobs = [dict(id=f'{i:02d}_{backend}_{int(stock)}', backend=backend, inventory=stock,
        time_limit=10, seed=42, case=str(frozen/'inputs/2/case.json'),
        initial_result=str(frozen/'inputs/2/initial_result.json'))
        for i, (backend, stock) in enumerate([('indexed', True), ('patterns', True), ('patterns', False)])]
    save(out/'manifest.json', dict(jobs=jobs, source_manifest=str(frozen/'manifest.json'),
        source_manifest_sha256=hashlib.sha256((frozen/'manifest.json').read_bytes()).hexdigest(),
        script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()))
    rows = []
    for job in jobs:
        with (out/(job['id']+'.log')).open('w') as log:
            run = subprocess.run([sys.executable, str(Path(__file__)), json.dumps(job)],
                stdout=log, stderr=subprocess.STDOUT, timeout=30,
                env={**os.environ, 'PYTHONHASHSEED': '0'})
        assert run.returncode == 0
        rows.append(json.loads((out/(job['id']+'.json')).read_text()))
        save(out/'summary.json', rows)
        print(job['id']+' completed', flush=True)
