"""Follow-up pricing budget reuse, registered after the first completed stage."""
from copy import deepcopy
import hashlib,json,math
from pathlib import Path
import shutil,sys,time
HERE=Path(__file__).resolve().parent
DEST=HERE/'retry'
ROOT=HERE.parents[2]
def read(p):return json.loads(p.read_text())
def write(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
if sys.argv[1]=='register':
    assert (HERE/'e2.json').exists() and (HERE/'full.json').exists() and (HERE/'tight.json').exists()
    assert not DEST.exists();DEST.mkdir()
    for source in [*ROOT.glob('models/**/*.py'),*ROOT.glob('configuration/*.py')]:
        target=DEST/'source_snapshot'/source.relative_to(ROOT)
        target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
    write(DEST/'plan.json',dict(registered_before_run=True,seed=43,
        pricing_seconds=8,certificate_seconds=180,joint_seconds_per_pool=30,
        pilots='S1 best and last positive candidate witnesses, baseline 8s then reuse 8s with fresh pricers',
        sites='unfinished sites ordered by largest diagnostic deficit, equal remaining shares',
        source_sha256={str(p.relative_to(DEST/'source_snapshot')):sha(p) for p in (DEST/'source_snapshot').rglob('*.py')},
        input_sha256={str(HERE/f):sha(HERE/f) for f in ['e2.json','full.json','tight.json']},
        runner_sha256=sha(Path(__file__)),scope='Additional warm-start experiment; all fixed-price controls and site attempts retained; original domains and objectives unchanged.'))
    print('REGISTERED RETRY',flush=True);sys.exit()
plan=read(DEST/'plan.json');assert sha(Path(__file__))==plan['runner_sha256']
for f,d in plan['source_sha256'].items():assert sha(DEST/'source_snapshot'/f)==d
for f,d in plan['input_sha256'].items():assert sha(Path(f))==d
sys.path.insert(0,str(DEST/'source_snapshot'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.exact.multibatch_route_pricing import RoutePricer,route_context
from models.routing.solvers.exact.multibatch_route_cg import site_route_cover
from models.routing.solvers.exact.multibatch_conditional_inventory import solve_conditional_inventory
p=load_instance(*load_routing_case(str(HERE/'inputs/full/case.json'),use_pool_config=False))
shared=read(HERE/'e2.json');ref=deepcopy(shared['reference'])
row=next(r for r in ref['sites'] if r['satellite']=='S1')
indices=[row['best_witness'],next(i for i in range(len(row['iterations'])-1,-1,-1)
                                  if (row['iterations'][i].get('candidate_bound') or 0)>0)]
pilots=[]
for index in dict.fromkeys(indices):
    w=row['iterations'][index]
    for reuse in (False,True):
        subset={sid:frozenset(tuple(k) for k in keys) for sid,keys,_,_ in w.get('subset_counts',[])}
        pricer=RoutePricer(p,route_context(p,'S1'),seed=43,subset_keys=subset)
        try:
            report=pricer.price({tuple(k):v for k,v in w['duals']},w['fleet_dual'],time_limit=8,
                category_duals={bid:v for bid,_,v in w.get('delivery_counts',[])},
                subset_duals={sid:v for sid,_,_,v in w.get('subset_counts',[])},reuse_budget=reuse)
            (DEST/'pilot_models').mkdir(exist_ok=True)
            pricer.write(str(DEST/'pilot_models'/f'w{index}_reuse{int(reuse)}.mps'))
            pilots.append(dict(witness_index=index,reuse_budget=reuse,report=report))
            write(DEST/'pilots.json',pilots)
            print('PILOT',index,reuse,report['lower_bound'],report['runtime'],flush=True)
        finally:pricer.dispose()
def deficit(row):
    objectives=[i['master_objective'] for i in row['iterations'] if i.get('phase')=='distance']
    return max(0,objectives[-1]-row['bound']) if objectives else 0
pending=sorted([r for r in ref['sites'] if not r['lp_converged']],key=lambda r:(-deficit(r),r['satellite']))
begun=time.perf_counter();deadline=begun+plan['certificate_seconds'];runs=[]
for number,old in enumerate(pending):
    site=old['satellite'];now=time.perf_counter()
    cuts=next((r['cuts'] for r in ref.get('subset_runs',[]) if r['satellite']==site),[])
    bag=dict(e2_trips=[dict(satellite_id=site,events=[dict(order_id=o,kind=k) for o,k in c['tasks']])
                       for c in old['columns']])
    leaf=site_route_cover(p,route_context(p,site),deadline=now+max(0,deadline-now)/(len(pending)-number),
        initial_result=bag,seed=43,batch_pricing=True,dual_repair=True,delivery_count_cuts=True,
        subset_cuts=cuts,pricing_time_limit=8,reuse_pricing_budget=True,
        pricing_export_path=DEST/'e2_models'/f'{site}_pricing.mps')
    runs.append(dict(satellite=site,cuts=cuts,certificate=leaf))
    if leaf['bound']>old['bound']:
        ref['sites'][ref['sites'].index(old)]=leaf
        component=next(c for c in ref['components'] if c['satellite']==site)
        component.update(route_cover_bound=leaf['bound'],bound=max(component['bound'],leaf['bound']))
    write(DEST/'progress.json',dict(runs=runs,wall_seconds=time.perf_counter()-begun))
    print('RETRY SITE',site,leaf['bound'],leaf['status'],leaf['runtime'],flush=True)
e2_seconds=time.perf_counter()-begun
cases=[]
for label in ('full','tight'):
    previous=read(HERE/(label+'.json'));local=deepcopy(previous['reference_certificate'])
    local['sites']=deepcopy(ref['sites']);local['components']=deepcopy(ref['components'])
    raw=local['e1_bound']+math.fsum(c['bound'] for c in local['components'])
    local.update(raw_bound=raw,bound=max(local['bound'],raw-1e-7*max(1,abs(raw))))
    p=load_instance(*load_routing_case(str(HERE/'inputs'/label/'case.json'),use_pool_config=False))
    leaves=previous['previous_conditions']+(previous['conditional_certificate'] or {}).get('conditions',[])
    unique={(r['site'],r['batch'],r['threshold']):r for r in leaves}
    master=solve_conditional_inventory(p,{c['satellite']:c['bound'] for c in local['components']},
        [{k:v for k,v in r.items() if k!='certificate'} for r in unique.values()],
        deadline=time.perf_counter()+30,initial_result=previous['result'],e1_bound=local['e1_bound'],
        fallback_bound=max(previous['bound'],local['bound']),seed=43,
        diagnostics_path=DEST/(label+'_joint.mps.gz'))
    audit=audit_solution(p,previous['result']);assert audit['valid'] and audit['periodic_replay']['valid']
    lower=max(previous['bound'],local['bound'],master['bound'])
    gap=max(0,audit['objective']-lower)/max(1e-9,abs(audit['objective']))
    cases.append(dict(label=label,reference_certificate=local,joint_certificate=master,
        conditions=list(unique.values()),upper_bound=audit['objective'],bound=lower,gap=gap,
        target_reached=gap<=.05,result=previous['result']))
    print('FINAL',label,audit['objective'],lower,gap,flush=True)
write(DEST/'result.json',dict(cases=cases,runs=runs,e2_seconds=e2_seconds,wall_seconds=time.perf_counter()-begun,
                           pilot_seconds=math.fsum(r['report']['runtime'] for r in pilots)))
