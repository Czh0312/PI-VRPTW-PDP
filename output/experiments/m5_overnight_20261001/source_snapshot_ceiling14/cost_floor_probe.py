"""Sequential equal-budget physical site probes with a certified cover floor."""
from pathlib import Path
import hashlib
import json
import os
import sys
import time

SNAPSHOT = Path(__file__).resolve().parent
HERE = SNAPSHOT.parent
OUT = HERE/'cost_floor2'
OUT.mkdir(exist_ok=True)
sys.path.insert(0, str(SNAPSHOT))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.exact.multibatch_physical_site import (
    build_physical_site, project_physical_site, physical_site_certificate)
import gurobipy as gp


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)+'\n')


def checkpoint(status, **extra):
    value = json.loads((HERE/'checkpoint.json').read_text())
    value.update({'status':status, 'active_process':os.getpid(), **extra})
    write(HERE/'checkpoint.json', value)


begun = time.perf_counter()
checkpoint('independent_cover_floor_replay', source_snapshot=str(SNAPSHOT),
    next_action='Wait for this worker and read cost_floor2/summary.json; do not start another heavy experiment concurrently.')
source = json.loads((HERE/'inputs/S1_cover_floor_source.json').read_text())
master = HERE/'inputs/S1_integer_cover.mps.gz'
assert hashlib.sha256(master.read_bytes()).hexdigest() == source['integer_master_sha256']
model = gp.read(str(master))
model.Params.Threads = 1
model.Params.Seed = 42
model.Params.MIPGap = model.Params.MIPGapAbs = 0
model.Params.IntFeasTol = model.Params.FeasibilityTol = 1e-9
model.Params.TimeLimit = 45
model.optimize()
assert model.Status == gp.GRB.OPTIMAL
assert model.ObjBound+1e-5 >= source['bound']
floor = source['bound']
write(OUT/'floor_replay.json', dict(source=source, status=int(model.Status),
    replay_raw_bound=float(model.ObjBound), replay_objective=float(model.ObjVal),
    externally_certified_floor=floor, replay_supported=True))
model.dispose()

problem = load_instance(*load_routing_case(str(HERE/'inputs/case.json')))
initial = json.loads((HERE/'inputs/initial_result.json').read_text())
assert audit_solution(problem, initial)['valid']
choice = json.loads((HERE/'master_condition_choice.json').read_text())
site = 'S1'
results = []
for name, thresholds, counts in (
    ('unconditional', {}, {}),
    ('conditional', choice['supply_thresholds'], choice['minimum_returns'])):
    checkpoint('validating_large_physical_projection_'+name)
    model, context = build_physical_site(problem, site,
        supply_thresholds=thresholds, minimum_returns=counts, cost_floor=floor)
    try:
        projection = project_physical_site(problem, context, initial, fix=True)
        model.Params.TimeLimit = 30
        model.Params.IntFeasTol = model.Params.FeasibilityTol = 1e-9
        model.optimize()
        assert model.Status == gp.GRB.OPTIMAL
        assert abs(model.ObjVal-projection['original_site_cost']) < 1e-6
        projection.update(status=int(model.Status), projection_objective=float(model.ObjVal),
                          full_schedule_projection=True, external_cover_floor=floor)
        write(OUT/(name+'_fixed_projection.json'), projection)
    finally:
        model.dispose()
    checkpoint('running_cost_floor_physical_'+name)
    result = physical_site_certificate(problem, site, time_limit=240,
        supply_thresholds=thresholds, minimum_returns=counts, initial_result=initial,
        fallback_bound=floor, seed=42, verbose=True,
        diagnostics_path=OUT/(name+'_S1.mps.gz'))
    result.update(source_snapshot=str(SNAPSHOT), floor_source=str(HERE/'inputs/S1_cover_floor_source.json'),
                  full_global_lower_unchanged=True, full_schedule_projection=projection)
    write(OUT/(name+'_S1.json'), result)
    results.append(dict(name=name, bound=result['bound'], raw_bound=result['raw_bound'],
        status=result['status'], objective=result['subproblem_objective'],
        subproblem_optimal=result['subproblem_optimal'], subproblem_infeasible=result['subproblem_infeasible'],
        runtime=result['runtime'], additional_conditional_floor=max(0,result['bound']-floor)))
    write(OUT/'summary.json', results)
    print(json.dumps(results[-1]), flush=True)
checkpoint('cost_floor_physical_probes_complete', active_process=None,
    cost_floor_probe_wall_seconds=time.perf_counter()-begun,
    next_action='Read cost_floor2/summary.json. If a conditional site floor exceeds the unconditional cover floor, independently replay the exported subproblem before adding exact AND activation rows to a separate joint-master copy. If neither improves, change the physical formulation or pursue an audited full incumbent; avoid repeating this weak relaxation. Global lower 2235.4231373718553 and upper 2247.646367634466 remain unchanged.')
