from pathlib import Path
import json
import os
import sys
import time

SNAPSHOT = Path(__file__).resolve().parent
HERE = SNAPSHOT.parent
sys.path.insert(0, str(SNAPSHOT))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.exact.multibatch_joint_inventory import set_joint_projection
from models.routing.solvers.exact.multibatch_physical_site import physical_site_certificate
from warm_context import context_for_export
import gurobipy as gp


def write(name, data):
    (HERE/name).write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)+'\n')


def checkpoint(status, **extra):
    value = json.loads((HERE/'checkpoint.json').read_text())
    value.update(status=status, active_process=os.getpid(), **extra)
    write('checkpoint.json', value)


problem = load_instance(*load_routing_case(str(HERE/'inputs/case.json')))
initial = json.loads((HERE/'inputs/initial_result.json').read_text())
assert audit_solution(problem, initial)['valid']
begun = time.perf_counter()
checkpoint('selecting_conditions_from_frozen_master')
model = gp.read(str(HERE/'inputs/master/joint_inventory.mps.gz'))
model.Params.Threads = 1
model.Params.MIPGap = model.Params.MIPGapAbs = 0
model.Params.IntFeasTol = model.Params.FeasibilityTol = 1e-9
model.Params.Presolve = 1
model.Params.Heuristics = 0
model.Params.Seed = 42
context = context_for_export(problem, model, HERE/'inputs/master')
set_joint_projection(problem, context, initial)
model.Params.TimeLimit = 60
model.optimize()
assert model.SolCount
site = 'S1'
thresholds = {b.id:int(round(model.getVarByName(f'supply_{site}_{b.id}').X))
              for b in problem.operating_policy.batches if problem.demands[site,b.id]}
counts = {}
for b in problem.operating_policy.batches[:-1]:
    counts[b.id] = int(round(sum(v.X for (i,s),v in context['e1']['quantities'].items()
        if s == site and problem.batch_index[context['e1']['patterns'][i]['batch']] <= problem.batch_index[b.id])))
chosen = [dict(index=i, pattern=context['e1']['patterns'][i])
          for i,v in context['e1']['chosen'].items() if v.X > .5]
choice = dict(status=int(model.Status), raw_relaxation_bound=float(model.ObjBound),
    relaxation_objective=float(model.ObjVal), site=site, supply_thresholds=thresholds,
    minimum_returns=counts, chosen_e1_patterns=chosen,
    note='A diagnostic point only; this selection does not itself create a new global lower bound.')
write('master_condition_choice.json', choice)
model.dispose()

original_supplies = {(e['site_id'],t['batch_id']):e['start']+problem.service(e['site_id'])
                    for t in initial['e1_trips'] for e in t['events'] if e['kind']=='d'}
early = {b.id:sum(e['quantity'] for t in initial['e2_trips'] if t['satellite_id']==site
                 and t['return_complete']<=b.cutoff for e in t['events'] if e['kind']=='p')
         for b in problem.operating_policy.batches}
matches = all(original_supplies[site,b]>=v for b,v in thresholds.items()) and all(early[b]>=v for b,v in counts.items())
results = []
for name, conditions, minimum, warm in (
    ('unconditional',{}, {},initial),
    ('conditional',thresholds,counts,initial if matches else None)):
    checkpoint('running_physical_site_'+name, next_action='Wait for this PID; inspect '+name+'_S1.json before starting further work.')
    result = physical_site_certificate(problem,site,time_limit=240,supply_thresholds=conditions,
        minimum_returns=minimum,initial_result=warm,seed=42,verbose=True,
        diagnostics_path=HERE/'physical_models'/(name+'_S1.mps.gz'))
    result.update(original_schedule_matches_conditions=bool(warm),
        cold_probe_budget_seconds=240, source_snapshot=str(SNAPSHOT))
    write(name+'_S1.json',result)
    results.append(dict(name=name,bound=result['bound'],status=result['status'],
        subproblem_optimal=result['subproblem_optimal'],subproblem_infeasible=result['subproblem_infeasible'],
        objective=result['subproblem_objective'],runtime=result['runtime']))
    write('first_probe_summary.json',results)
    print(json.dumps(results[-1]),flush=True)
checkpoint('first_physical_site_probes_complete', active_process=None,
    first_probe_wall_seconds=time.perf_counter()-begun,
    next_action='Independently validate the conditional physical-site certificate. Then implement valid AND activation cuts in a separate joint-master copy using supply thresholds and cumulative pickup quantities; test original full projections. Existing global lower/upper remain unchanged until that integration is verified.')
