"""Matched warm-start M3 ALNS experiment; no solver source modifications."""
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys
from zipfile import ZipFile

PROJECT = Path.cwd()
sys.path.insert(0, str(PROJECT))
from cli.route import main as run_route, load_config
from models.common.cycle import load_instance
from models.common.cycle.audit import audit_solution
from models.cost.comparison import evaluate_comparison_cost
import models.routing.model_3.alns.search as search

ROOT = Path(__file__).resolve().parent
M8 = PROJECT / 'output/inspections/m8_l05_100_newfleet_20261019_v1'
CASE = PROJECT / 'data/formal_benchmark27_interleaved_v1/model_3/large/L05/case.json'
RUN = ROOT / 'm3_alns'
if RUN.exists():
    raise ValueError('Refusing to overwrite existing M3 run')

def read(path):
    return json.loads(path.read_text(encoding='utf-8'))
def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')

fleet_override = read(M8 / 'fleet_override.json')
write(ROOT / 'fleet_override.json', fleet_override)
config, fleet = load_config(CASE, use_pool_config=False)
fleet = {**fleet_override, 'operating_policy': fleet['operating_policy']}
problem = load_instance(config, fleet)
m8_manifest = read(M8 / 'warm/alns/manifest.json')
m8_problem = load_instance(m8_manifest['config'], m8_manifest['fleet'])
def order_contract(order):
    return {key: value for key, value in asdict(order).items() if key not in ('batch', 'allowed_batch_ids')}
assert len(problem.orders) == len(m8_problem.orders) == 100
assert [order_contract(order) for order in problem.orders] == [order_contract(order) for order in m8_problem.orders]
assert vars(problem.fleet) == vars(m8_problem.fleet)
assert Path(config['data']).read_bytes() == Path(m8_manifest['config']['data']).read_bytes()
assert Path(config['economic_config']).read_bytes() == Path(m8_manifest['config']['economic_config']).read_bytes()
with ZipFile(PROJECT / 'data/formal_benchmark27_interleaved_v1/validation/witnesses.zip') as archive:
    initial = json.loads(archive.read('L05/model_3.json'))
checked = audit_solution(problem, initial)
assert checked['valid'] and checked['periodic_replay']['valid']
initial['info'].update(objective=checked['objective'], status='FEASIBLE',
                       proven_optimal=False, proven_infeasible=False)
initial['audit'] = checked
write(ROOT / 'initial_m3.json', initial)
initial_cost = evaluate_comparison_cost(problem, initial, config['economic_config'], audit=checked)
seed_path = ROOT / 'initial_m3.json'
experiment = dict(case='L05', customers=100, model=3, solver='alns', budget_seconds=120,
    seed=42, warm_start=True, gurobi_checked=False,
    reference_model8_result=str(M8 / 'warm/alns/result.json'),
    initial_result=str(seed_path), initial_sha256=hashlib.sha256(seed_path.read_bytes()).hexdigest(),
    initial_source='Dataset feasibility witness L05/model_3.json; independently audited under the same new fleet.',
    physical_orders_equal=True, fleet_equal=True, network_equal=True, cost_coefficients_equal=True,
    route_objective='unweighted sum of E1 and E2 distance',
    instrumentation='Public M3 entry forwards to its existing search; in-process wrapper supplies the search initial_result parameter. No model, objective, operator or tracked source edits.',
    initial_distance=checked['objective'], initial_comparison_cost=initial_cost['total'],
    runner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
write(ROOT / 'experiment.json', experiment)

original = search.solve_alns
def warm_search(loaded_problem, **kwargs):
    assert loaded_problem.model_number == 3
    check = audit_solution(loaded_problem, initial)
    assert check['valid'] and check['periodic_replay']['valid']
    result = original(loaded_problem, initial_result=deepcopy(initial), **kwargs)
    result['info'].update(experiment_warm_start=True, experiment_initial_distance=checked['objective'],
                          experiment_initial_sha256=experiment['initial_sha256'])
    return result

search.solve_alns = warm_search
try:
    result = run_route(['--case', str(CASE), '--fleet-config', str(ROOT / 'fleet_override.json'),
        '--solver', 'alns', '--time', '120', '--iterations', '1000000000', '--seed', '42',
        '--output', str(RUN)])
finally:
    search.solve_alns = original
audit = audit_solution(problem, result)
assert audit['valid'] and audit['periodic_replay']['valid']
cost = evaluate_comparison_cost(problem, result, config['economic_config'], audit=audit)
write(ROOT / 'm3_cost.json', cost)
run_manifest = read(RUN / 'manifest.json')
run_manifest['experiment_initial_solution'] = experiment
write(RUN / 'manifest.json', run_manifest)
summary = dict(case='L05', customers=100, model=3, status=result['info']['status'], K=1, N=100,
    objective_mode='distance', distance=result['info']['objective'], total_cost=cost['total'],
    cost_components=cost['components'], budget_seconds=120, runtime=result['info']['runtime'],
    seed=42, warm_start=True, initial_distance=checked['objective'], initial_cost=initial_cost['total'],
    alns_improved_initial=result['info']['objective'] < checked['objective'] - 1e-7,
    e1_trips=audit['e1_trip_count'], e2_trips=audit['e2_trip_count'],
    audit_valid=True, periodic_replay=True, gurobi_checked=False, proven_optimal=False)
write(ROOT / 'm3_summary.json', summary)
print(json.dumps(summary, ensure_ascii=False), flush=True)
