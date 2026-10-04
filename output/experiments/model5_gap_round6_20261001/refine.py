"""Additional registered full-pool rebuilding from the successful split."""
import hashlib,json
from pathlib import Path
import sys,time
HERE=Path(__file__).resolve().parent
def read(p):return json.loads(p.read_text())
def write(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
if sys.argv[1]=='register':
    assert not (HERE/'refine_plan.json').exists()
    write(HERE/'refine_plan.json',dict(registered_before_run=True,time_limit=240,seed=43,
        max_passes=2,max_columns=96,extra_columns=128,extra_slots_per_vehicle=1,
        input_path='full_extra1.json',input_sha256=sha(HERE/'full_extra1.json'),
        source_plan_sha256=sha(HERE/'plan.json'),runner_sha256=sha(Path(__file__)),
        scope='Additional 240s warm rebuilding; monotone audited UB; original certified LB retained; all attempts retained.'))
    print('REGISTERED REFINEMENT',flush=True);sys.exit()
plan=read(HERE/'refine_plan.json');assert sha(Path(__file__))==plan['runner_sha256']
assert sha(HERE/plan['input_path'])==plan['input_sha256']
source=read(HERE/'plan.json');assert sha(HERE/'plan.json')==plan['source_plan_sha256']
for f,d in source['source_sha256'].items():assert sha(HERE/'source_snapshot'/f)==d
sys.path.insert(0,str(HERE/'source_snapshot'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.exact.multibatch_route_pool import reoptimize_route_pool
assert not (HERE/'full_refined.json').exists()
previous=read(HERE/plan['input_path']);prior=next(c for c in read(HERE/'inputs/previous.json')['cases'] if c['label']=='full')
p=load_instance(*load_routing_case(str(HERE/'inputs/full_case.json'),use_pool_config=False))
begun=time.perf_counter()
result=reoptimize_route_pool(p,previous['result'],time_limit=plan['time_limit'],seed=43,
    max_columns=plan['max_columns'],max_passes=plan['max_passes'],extra_slots_per_vehicle=plan['extra_slots_per_vehicle'],
    additional_columns={r['satellite']:r['columns'] for r in prior['reference_certificate']['sites']},
    additional_columns_per_slot=plan['extra_columns'],transfers=True,exchanges=True,recombinations=True,
    e1_backend='patterns',verbose=True,diagnostics_dir=HERE/'full_refined_models')
audit=audit_solution(p,result);assert audit['valid'] and audit['periodic_replay']['valid']
assert result['info']['best_bound'] is None and audit['objective']<=previous['upper']+1e-6
lower=prior['bound'];upper=audit['objective'];assert lower<=upper+1e-6
write(HERE/'full_refined.json',dict(result=result,audit=audit,upper=upper,lower=lower,
    gap=max(0,upper-lower)/max(1e-9,abs(upper)),wall_seconds=time.perf_counter()-begun,
    target_reached=max(0,upper-lower)/max(1e-9,abs(upper))<=.05))
print('REFINED',upper,lower,flush=True)
