"""Registered multi-batch conditional full-domain pricing and joint proof."""
from copy import deepcopy
import hashlib,json,math
from pathlib import Path
import shutil,sys,time
HERE=Path(__file__).resolve().parent;DEST=HERE/'conjunction';ROOT=HERE.parents[2]
def read(p):return json.loads(p.read_text())
def write(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
if sys.argv[1]=='register':
    assert not DEST.exists();DEST.mkdir()
    for source in [*ROOT.glob('models/**/*.py'),*ROOT.glob('configuration/*.py')]:
        target=DEST/'source_snapshot'/source.relative_to(ROOT);target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
    write(DEST/'plan.json',dict(registered_before_run=True,site='S3',
        supply_conditions=[dict(batch='b1',threshold=32019),dict(batch='b2',threshold=46419)],
        pricing_seconds=240,pricing_per_call=8,joint_seconds_per_pool=30,seed=43,
        source_sha256={str(p.relative_to(DEST/'source_snapshot')):sha(p) for p in (DEST/'source_snapshot').rglob('*.py')},
        input_sha256={str(HERE/f):sha(HERE/f) for f in ['full_refined.json','tight_extra1.json','inputs/previous.json']},
        prior_conditional_path=str(HERE.parent/'model5_gap_round5_20261001/full.json'),
        prior_conditional_sha256=sha(HERE.parent/'model5_gap_round5_20261001/full.json'),runner_sha256=sha(Path(__file__)),
        scope='Additional complete-domain pricing under both necessary supply thresholds, AND activation in full E1 inventory model, retained original conditional floors; not a restricted-pool lower bound.'))
    print('REGISTERED CONJUNCTION',flush=True);sys.exit()
plan=read(DEST/'plan.json');assert sha(Path(__file__))==plan['runner_sha256']
for f,d in plan['source_sha256'].items():assert sha(DEST/'source_snapshot'/f)==d
for f,d in plan['input_sha256'].items():assert sha(Path(f))==d
assert sha(Path(plan['prior_conditional_path']))==plan['prior_conditional_sha256']
sys.path.insert(0,str(DEST/'source_snapshot'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.exact.multibatch_conditional_inventory import conditional_supply_context,solve_conditional_inventory
from models.routing.solvers.exact.multibatch_route_pricing import evaluate_column
from models.routing.solvers.exact.multibatch_route_cg import site_route_cover
assert not (DEST/'result.json').exists()
p=load_instance(*load_routing_case(str(HERE/'inputs/full_case.json'),use_pool_config=False))
prior=next(c for c in read(HERE/'inputs/previous.json')['cases'] if c['label']=='full')
context=conditional_supply_context(p,plan['site'],plan['supply_conditions'])
seed=list(next(r['columns'] for r in prior['reference_certificate']['sites'] if r['satellite']==plan['site']))
old=read(Path(plan['prior_conditional_path']))['conditional_certificate']
seed += [c for row in old['conditions'] if row['site']==plan['site'] for c in row['certificate']['columns']]
current=read(HERE/'full_refined.json')['result']
seed += [dict(tasks=[(e['order_id'],e['kind']) for e in t['events']]) for t in current['e2_trips'] if t['satellite_id']==plan['site']]
valid={tuple(tuple(k) for k in c['tasks']):evaluate_column(p,context,c['tasks']) for c in seed}
columns=[c for c in valid.values() if c is not None]
bag=dict(e2_trips=[dict(satellite_id=plan['site'],events=[dict(order_id=o,kind=k) for o,k in c['tasks']]) for c in columns])
begun=time.perf_counter()
leaf=site_route_cover(p,context,deadline=begun+plan['pricing_seconds'],initial_result=bag,seed=43,
    batch_pricing=True,dual_repair=True,delivery_count_cuts=True,pricing_time_limit=8,reuse_pricing_budget=True,
    pricing_export_path=DEST/'pricing.mps')
write(DEST/'leaf.json',dict(certificate=leaf,site=plan['site'],supply_conditions=plan['supply_conditions']))
print('CONJUNCTION LEAF',leaf['bound'],leaf['status'],leaf['runtime'],flush=True)
cases=[]
for label,file in [('full','full_refined.json'),('tight','tight_extra1.json')]:
    initial=read(HERE/file);ref=next(c for c in read(HERE/'inputs/previous.json')['cases'] if c['label']==label)
    p=load_instance(*load_routing_case(str(HERE/'inputs'/(label+'_case.json')),use_pool_config=False))
    assert context==conditional_supply_context(p,plan['site'],plan['supply_conditions'])
    rows=[{k:v for k,v in c.items() if k!='certificate'} for c in ref['conditions']]
    rows.append(dict(site=plan['site'],supply_conditions=plan['supply_conditions'],bound=leaf['bound']))
    floors={c['satellite']:c['bound'] for c in ref['reference_certificate']['components']}
    master=solve_conditional_inventory(p,floors,rows,deadline=time.perf_counter()+plan['joint_seconds_per_pool'],
        initial_result=initial['result'],seed=43,e1_bound=ref['reference_certificate']['e1_bound'],
        fallback_bound=initial['lower'],diagnostics_path=DEST/(label+'_joint.mps.gz'))
    audit=audit_solution(p,initial['result']);assert audit['valid'] and audit['periodic_replay']['valid']
    lower=max(initial['lower'],master['bound']);upper=audit['objective'];assert lower<=upper+1e-6
    gap=max(0,upper-lower)/abs(upper)
    cases.append(dict(label=label,result=initial['result'],joint_certificate=master,conditions=rows,
        upper=upper,lower=lower,gap=gap,target_reached=gap<=.05))
    print('COMBINED',label,upper,lower,gap,flush=True)
write(DEST/'result.json',dict(cases=cases,leaf=leaf,wall_seconds=time.perf_counter()-begun))
