"""One declared 100-customer M8 run; record ordinary full-domain candidates."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import time

PROJECT = Path.cwd()
sys.path.insert(0, str(PROJECT))
from cli.route import main as run_route
from models.common.cycle import load_instance
from models.common.cycle.audit import audit_solution
from models.routing.model_8.alns.k_search.scheduler import _attach
import models.routing.model_8.alns.search as search

ROOT = Path(__file__).resolve().parent
CASE = PROJECT / 'data/formal_benchmark27_interleaved_v1/model_8/large/L05/case.json'
PROFILE = PROJECT / 'config/experiments/benchmark27_one_box_v1/fleet_by_size_draft.json'
RUN = ROOT / 'alns'
if RUN.exists():
    raise ValueError('Refusing to overwrite an existing experiment')

def read(path):
    return json.loads(path.read_text(encoding='utf-8'))

def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')

case = read(CASE)
profile = read(PROFILE)
fleet = read(PROJECT / case['routing_config'])
write(ROOT / 'previous_fleet.json', fleet)
fleet.update(profile['by_size']['large'])
write(ROOT / 'fleet_override.json', fleet)
write(ROOT / 'fleet_profile_snapshot.json', profile)
write(ROOT / 'experiment.json', dict(case='L05', customers=100, model=8, solver='alns',
    budget_seconds=300, seed=42, cold_start=True, forced_K=False,
    full_domain=list(range(1, 101)), gurobi_checked=False,
    selection='First listed 100-customer case L05; no outcome-based case selection.',
    instrumentation='In-process candidate recorder; original solver, scheduler, data and objective unchanged.',
    runner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    fleet_profile_sha256=hashlib.sha256(PROFILE.read_bytes()).hexdigest()))

original = search.solve_variable_count
started = time.perf_counter()
rows = {}
visits = []
def progress(current=None, finished=False):
    write(ROOT / 'progress.json', dict(case='L05', customers=100,
        elapsed=time.perf_counter() - started, current=current, finished=finished,
        candidates=list(rows.values()), visits=visits, gurobi_checked=False))

def record(child, **kwargs):
    k = child.count
    progress(dict(K=k, budget=kwargs['max_time'], state='running'))
    result = original(child, **kwargs)
    valid = bool(result.get('audit', {}).get('valid'))
    if valid:
        saved = deepcopy(result)
        _attach(child.parent, child, saved, k)
        checked = audit_solution(child.parent, saved)
        if not checked['valid'] or not checked['periodic_replay']['valid']:
            raise ValueError(f'K={k} failed independent replay: {checked["errors"]}')
        saved['audit'] = checked
        if k not in rows or saved['info']['objective'] < rows[k]['cost']:
            write(ROOT / f'best_K{k}.json', saved)
            rows[k] = dict(K=k, N=saved['container_pool_size'],
                cost=saved['info']['objective'], valid=True,
                elapsed=time.perf_counter() - started)
    visits.append(dict(K=k, valid=valid, status=result['info']['status'],
        cost=result['info'].get('objective'), budget=kwargs['max_time'],
        returned=time.perf_counter() - started))
    progress()
    return result

search.solve_variable_count = record
try:
    result = run_route(['--case', str(CASE), '--fleet-config', str(ROOT / 'fleet_override.json'),
        '--solver', 'alns', '--time', '300', '--iterations', '1000000000', '--seed', '42',
        '--output', str(RUN)])
finally:
    search.solve_variable_count = original

manifest = read(RUN / 'manifest.json')
problem = load_instance(manifest['config'], manifest['fleet'])
assert len(problem.orders) == 100
audit = None
if result['info'].get('objective') is not None:
    audit = audit_solution(problem, result)
    if not audit['valid'] or not audit['periodic_replay']['valid']:
        raise ValueError(audit['errors'])
summary = dict(case='L05', customers=100, budget_seconds=300, seed=42,
    status=result['info']['status'], cost=result['info'].get('objective'),
    K=result.get('cycle_batch_count'), N=result.get('container_pool_size'),
    runtime=result['info']['runtime'], valid=bool(audit and audit['valid']),
    periodic_replay=bool(audit and audit['periodic_replay']['valid']),
    candidates=list(rows.values()), proven_optimal=False, gurobi_checked=False,
    counts=[{key: row.get(key) for key in ('batch_count', 'status', 'objective', 'valid',
                                         'container_pool_size', 'runtime', 'budget')}
            for row in result.get('count_search', [])])
write(ROOT / 'summary.json', summary)
progress(finished=True)
print(json.dumps({key: value for key, value in summary.items() if key != 'counts'}, ensure_ascii=False), flush=True)
