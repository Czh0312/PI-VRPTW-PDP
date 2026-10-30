"""Same-instance, same physical initial route M3/M8 ALNS cost comparison."""
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys
import time

PROJECT=Path.cwd()
sys.path.insert(0,str(PROJECT))
from cli.route import main as run_route, load_config
from models.common.cycle import load_instance
from models.common.cycle.audit import audit_solution
from models.common.cycle.joint import lift_result
from models.common.cycle.calendar import attach_calendar_decisions
from models.cost.comparison import evaluate_comparison_cost
from models.routing.model_8.alns.k_search.scheduler import _attach
import models.routing.model_3.alns.search as search3
import models.routing.model_8.alns.search as search8

ROOT=Path(__file__).resolve().parent
DATA=PROJECT/'data/scenarios/continuous_flow_100_v1'
if (ROOT/'m3').exists() or (ROOT/'m8').exists():
    raise ValueError('Refusing to overwrite existing solver runs')
def read(path):
    return json.loads(path.read_text(encoding='utf-8'))
def write(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')

cases={model:DATA/f'model_{model}/case.json' for model in (3,8)}
loaded={model:load_config(path,use_pool_config=False) for model,path in cases.items()}
problems={model:load_instance(*loaded[model]) for model in (3,8)}
def physical(order):
    return {key:value for key,value in asdict(order).items() if key not in ('batch','allowed_batch_ids')}
assert [physical(o) for o in problems[3].orders]==[physical(o) for o in problems[8].orders]
assert all(o.release_time==480*60 for o in problems[3].orders)
assert vars(problems[3].fleet)==vars(problems[8].fleet)
initial3=read(DATA/'validation/model3_witness.json')
checked3=audit_solution(problems[3],initial3)
assert checked3['valid'] and checked3['periodic_replay']['valid']
initial3['info'].update(status='FEASIBLE',objective=checked3['objective'],proven_optimal=False,
    proven_infeasible=False,best_bound=None)
initial3['audit']=checked3
write(ROOT/'initial_m3.json',initial3)
child=problems[8].fixed_calendar(1)
initial8=lift_result(child,initial3,{o.id:'b1' for o in problems[8].orders},100,
    'feasibility_witness',{'b1':problems[3].operating_policy.batches[0].cutoff})
assert initial8['audit']['valid'],initial8['audit']['errors']
attach_calendar_decisions(problems[8],initial8,1,problems[8].balanced_boundaries(1))
initial8['audit']=audit_solution(problems[8],initial8)
assert initial8['audit']['valid'] and initial8['audit']['periodic_replay']['valid']
assert initial3['e1_trips']==initial8['e1_trips'] and initial3['e2_trips']==initial8['e2_trips']
write(ROOT/'initial_m8.json',initial8)
seed_cost=evaluate_comparison_cost(problems[3],initial3,loaded[3][0]['economic_config'])['total']
assert abs(seed_cost-initial8['info']['objective'])<1e-6
experiment=dict(scenario='continuous_flow_100_v1',customers=100,budget_per_model=120,seed=42,
    warm_start=True,identical_physical_initial_routes=True,initial_K=1,initial_N=100,
    initial_comparison_cost=seed_cost,initial_total_distance=checked3['objective'],
    model8_K_domain=list(range(1,101)),model8_pool_domain=[1,100],
    same_physical_orders=True,same_fleet=True,all_goods_ready='08:00',
    initial_source='Published model3 feasibility witness, independently replayed as M3 and M8 K=1.',
    m3_initial_mechanism='In-process wrapper supplies the existing search initial_result argument; no solver source edits.',
    model3_objective='sum of E1 and E2 distance',model8_objective='acquisition plus one operating cycle cost',
    gurobi_checked=False,runner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    initial_sha256={f'M{model}':hashlib.sha256((ROOT/f'initial_m{model}.json').read_bytes()).hexdigest() for model in (3,8)})
write(ROOT/'experiment.json',experiment)
started=time.perf_counter()
write(ROOT/'progress.json',dict(current='M3',finished=False,initial_cost=seed_cost))

original3=search3.solve_alns
def warm3(problem,**kwargs):
    result=original3(problem,initial_result=deepcopy(initial3),**kwargs)
    result['info'].update(experiment_warm_start=True,experiment_initial_distance=checked3['objective'])
    return result
search3.solve_alns=warm3
try:
    m3=run_route(['--case',str(cases[3]),'--solver','alns','--time','120','--iterations','1000000000',
                  '--seed','42','--output',str(ROOT/'m3')])
finally:
    search3.solve_alns=original3
audit3=audit_solution(problems[3],m3)
assert audit3['valid'] and audit3['periodic_replay']['valid']
cost3=evaluate_comparison_cost(problems[3],m3,loaded[3][0]['economic_config'],audit=audit3)
write(ROOT/'m3_cost.json',cost3)
manifest3=read(ROOT/'m3/manifest.json')
manifest3['experiment_initial_solution']=dict(path=str(ROOT/'initial_m3.json'),
    sha256=experiment['initial_sha256']['M3'],same_physical_seed_as_model8=True)
write(ROOT/'m3/manifest.json',manifest3)
print(json.dumps(dict(model=3,valid=True,cost=cost3['total'],distance=sum(audit3['distances'].values()),
                     e1_trips=audit3['e1_trip_count'],e2_trips=audit3['e2_trip_count']),ensure_ascii=False),flush=True)

rows={}
visits=[]
def progress(current=None,finished=False):
    write(ROOT/'progress.json',dict(current=current,finished=finished,elapsed=time.perf_counter()-started,
        m3_cost=cost3['total'],model8_initial_cost=seed_cost,candidates=list(rows.values()),visits=visits))
original8=search8.solve_variable_count
def record(child,**kwargs):
    progress(dict(model=8,K=child.count,budget=kwargs['max_time']))
    candidate=original8(child,**kwargs)
    valid=bool(candidate.get('audit',{}).get('valid'))
    if valid:
        saved=deepcopy(candidate)
        _attach(problems[8],child,saved,child.count)
        saved['audit']=audit_solution(problems[8],saved)
        assert saved['audit']['valid'] and saved['audit']['periodic_replay']['valid']
        k=child.count
        if k not in rows or saved['info']['objective']<rows[k]['cost']:
            write(ROOT/f'm8_best_K{k}.json',saved)
            rows[k]=dict(K=k,N=saved['container_pool_size'],cost=saved['info']['objective'],valid=True)
    visits.append(dict(K=child.count,valid=valid,status=candidate['info']['status'],
                       cost=candidate['info'].get('objective'),budget=kwargs['max_time']))
    progress()
    return candidate
search8.solve_variable_count=record
try:
    m8=run_route(['--case',str(cases[8]),'--solver','alns','--time','120','--iterations','1000000000',
        '--seed','42','--initial-joint-result',str(ROOT/'initial_m8.json'),'--output',str(ROOT/'m8')])
finally:
    search8.solve_variable_count=original8
audit8=audit_solution(problems[8],m8)
assert audit8['valid'] and audit8['periodic_replay']['valid']
cost8=evaluate_comparison_cost(problems[8],m8,loaded[8][0]['economic_config'],audit=audit8)
write(ROOT/'m8_cost.json',cost8)
summaries=[]
for model,result,audit,cost in ((3,m3,audit3,cost3),(8,m8,audit8,cost8)):
    comp=cost['components']
    summaries.append(dict(model=model,status=result['info']['status'],
        K=1 if model==3 else result['cycle_batch_count'],N=cost['container_pool_size'],
        D1=audit['distances']['e1'],D2=audit['distances']['e2'],distance=sum(audit['distances'].values()),
        acquisition=comp['container_pool'],transport=comp['e1_distance']+comp['e2_distance'],
        other_operating=sum(v for k,v in comp.items() if k not in ('container_pool','e1_distance','e2_distance')),
        recurring_cost=cost['total']-comp['container_pool'],total_cost=cost['total'],
        e1_trips=audit['e1_trip_count'],e2_trips=audit['e2_trip_count'],
        e1_vehicles=audit['e1_vehicle_count'],e2_vehicles=audit['e2_vehicle_count'],
        runtime=result['info']['runtime'],budget=120,warm_start=True,seed=42,
        valid=True,periodic_replay=True,proven_optimal=False,gurobi_checked=False))
summary=dict(experiment=experiment,results=summaries,m8_candidates=list(rows.values()),
    cost_difference_m8_minus_m3=cost8['total']-cost3['total'],
    winner='M3' if cost3['total']<cost8['total']-1e-6 else 'M8' if cost8['total']<cost3['total']-1e-6 else 'tie')
write(ROOT/'summary.json',summary)
progress(finished=True)
print(json.dumps(summary,ensure_ascii=False),flush=True)
