"""Explicit supplementary warm-start run after the declared cold-start attempt."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import time

PROJECT = Path.cwd()
sys.path.insert(0, str(PROJECT))
from cli.route import main as run_route
from models.common.cycle import load_instance
from models.common.cycle.audit import audit_solution
from models.common.cycle.joint import lift_result
from models.common.cycle.calendar import attach_calendar_decisions
from models.routing.model_8.alns.k_search.scheduler import _attach
import models.routing.model_8.alns.search as search

ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'warm'
if OUT.exists():
    raise ValueError('Refusing to overwrite supplementary run')
OUT.mkdir()

def read(path):
    return json.loads(path.read_text(encoding='utf-8'))
def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')

manifest = read(ROOT / 'alns/manifest.json')
problem = load_instance(manifest['config'], manifest['fleet'])
reference = read(ROOT / 'verified_reference_K3.json')
physical = audit_solution(problem, reference)
assert physical['valid'] and physical['periodic_replay']['valid']
pool = max(problem.pool_min, physical['minimum_pool_for_schedule'])
child = problem.fixed_calendar(3, reference['batch_boundaries'])
initial = lift_result(child, reference, reference['order_batch_assignment'], pool,
                      'feasibility_witness', reference['batch_cutoffs'])
attach_calendar_decisions(problem, initial, 3, reference['batch_boundaries'])
initial['audit'] = audit_solution(problem, initial)
assert initial['audit']['valid'] and initial['audit']['periodic_replay']['valid']
write(OUT / 'initial_K3.json', initial)
write(OUT / 'experiment.json', dict(budget_seconds=120, seed=42, cold_start=False,
    forced_K=False, full_domain=list(problem.batch_count_candidates), gurobi_checked=False,
    initial_source='Dataset M5 physical feasibility witness projected to M8; pool reduced to replay requirement.',
    initial_cost=initial['info']['objective'], initial_pool=pool,
    limitation='Warm-start diagnostic; report separately from cold-start algorithm success.'))

original = search.solve_variable_count
started = time.perf_counter()
rows = {}
visits = []
def record(variable, **kwargs):
    write(OUT / 'progress.json', dict(elapsed=time.perf_counter()-started,
        current=variable.count, candidates=list(rows.values()), visits=visits))
    candidate = original(variable, **kwargs)
    valid = bool(candidate.get('audit', {}).get('valid'))
    if valid:
        saved = deepcopy(candidate)
        _attach(problem, variable, saved, variable.count)
        saved['audit'] = audit_solution(problem, saved)
        assert saved['audit']['valid'] and saved['audit']['periodic_replay']['valid']
        count = variable.count
        if count not in rows or saved['info']['objective'] < rows[count]['cost']:
            write(OUT / f'best_K{count}.json', saved)
            rows[count] = dict(K=count, N=saved['container_pool_size'], cost=saved['info']['objective'], valid=True)
    visits.append(dict(K=variable.count, valid=valid, status=candidate['info']['status'],
                       budget=kwargs['max_time'], cost=candidate['info'].get('objective')))
    return candidate

search.solve_variable_count = record
try:
    result = run_route(['--case', str(PROJECT / 'data/formal_benchmark27_interleaved_v1/model_8/large/L05/case.json'),
        '--fleet-config', str(ROOT / 'fleet_override.json'), '--solver', 'alns', '--time', '120',
        '--iterations', '1000000000', '--seed', '42', '--initial-joint-result', str(OUT / 'initial_K3.json'),
        '--output', str(OUT / 'alns')])
finally:
    search.solve_variable_count = original
checked = audit_solution(problem, result)
assert checked['valid'] and checked['periodic_replay']['valid']
summary = dict(case='L05', customers=100, warm_start=True, budget_seconds=120, seed=42,
    status=result['info']['status'], K=result['cycle_batch_count'], N=result['container_pool_size'],
    cost=result['info']['objective'], initial_cost=initial['info']['objective'],
    alns_improved_initial=result['info']['objective'] < initial['info']['objective'] - 1e-7,
    valid=True, periodic_replay=True, gurobi_checked=False, proven_optimal=False,
    candidates=list(rows.values()), runtime=result['info']['runtime'])
write(OUT / 'summary.json', summary)
write(OUT / 'progress.json', dict(finished=True, elapsed=time.perf_counter()-started, visits=visits,
                                candidates=list(rows.values())))
print(json.dumps(summary, ensure_ascii=False), flush=True)
