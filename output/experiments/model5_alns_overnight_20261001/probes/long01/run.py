"""Fresh common ALNS incumbent, charged to each sequential 1500s pipeline."""
from copy import deepcopy
import datetime, hashlib, json, os
from pathlib import Path
import shutil, subprocess, sys, time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[4]
NIGHT = HERE.parents[1]
BUDGET = HERE.parent/'budget01'
def read(p): return json.loads(p.read_text())
def write(p,v): p.write_text(json.dumps(v,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def checkpoint(**values):
    obj=read(NIGHT/'checkpoint.json')
    obj.update(values,updated_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
    write(NIGHT/'checkpoint.json',obj)

if sys.argv[1]=='register':
    assert not (HERE/'plan.json').exists()
    previous=read(BUDGET/'plan.json');assert read(BUDGET/'validation.json')['status']=='VERIFIED'
    assert read(HERE/'smoke_profiles.json')['status']=='PASSED'
    assert read(HERE/'checker_smoke.json')['status']=='VERIFIED'
    original=read(HERE/'baseline_manifest.json')['source_sha256']
    for name,digest in original.items():assert sha(HERE/'baseline_source'/name)==digest
    snapshot_hashes={directory:{str(p.relative_to(HERE/directory)):sha(p) for p in (HERE/directory).rglob('*.py')} for directory in ['baseline_source','candidate_source']}
    frozen=HERE/'inputs';frozen.mkdir()
    cfg=read(Path(previous['case']));bindings=[]
    for field in ['data','orders','routing_config','dataset_metadata']:
        origin=Path(cfg[field]);target=frozen/origin.name
        assert sha(origin)==previous['input_sha256'][str(origin.relative_to(BUDGET))]
        shutil.copy2(origin,target);cfg[field]=str(target)
        bindings.append(dict(field=field,origin=str(origin),sha256=sha(origin),frozen=str(target)))
    write(frozen/'case.json',cfg)
    common=dict(time_limit=1500,alns_time_limit=15,primal_time_limit=180,cover_time_limit=1160,
        conditional_time_limit=105,target_gap=.05,seed=48,max_subset_cuts_per_batch=6,max_conditions=3,
        extra_slots_per_vehicle=1,route_pool_max_columns=64,max_supply_conjunctions=1,adaptive_pricing_mode='resume',count_dual_repair=True,capacity_dual_repair=True)
    jobs=[dict(id='baseline_subsets25_long',source='baseline_source',options=common),
          dict(id='candidate_subsets0_long',source='candidate_source',options={**common,'subset_time_fraction':0})]
    write(HERE/'plan.json',dict(registered_before_run=True,mode='continuous',jobs=jobs,
        case=str(frozen/'case.json'),input_bindings=bindings,
        input_sha256={str(p.relative_to(HERE)):sha(p) for p in frozen.iterdir()},
        source_sha256=snapshot_hashes,declared_source_changes=[],
        initializer=dict(source='baseline_source',seed=48,alns_time_limit=15,alns_iter=100000,
            origin='Fresh call on registered frozen input; no historical result imported'),
        initializer_budget_rule='Each job hybrid deadline is 1500 minus measured common ALNS+audit wall time. The shared initializer is charged to both jobs. Loading and independent post-run validation are outside solver budget.',
        checker_sha256={p.name:sha(p) for p in HERE.glob('validation_*.py')},
        runner_sha256=sha(Path(__file__)),validator_sha256=sha(HERE/'validate.py'),
        smoke_sha256={name:sha(HERE/name) for name in ['smoke_profiles.py','smoke_profiles.json','smoke_profiles.log','checker_smoke.py','checker_smoke.json','checker_smoke.log','capacity_repair_regression.log','research_note.json']},
        predecessor_plan_sha256=sha(BUDGET/'plan.json'),hash_seed=0,python=sys.version,
        scope='One freshly generated seed-48 common ALNS incumbent, two sequential matched-input matched-incumbent 1500s subset-reserve profiles; not two independently randomized cold starts or stability evidence.',
        hypothesis='At a larger1500s budget with identical live-CG capacity-repair source, compare the25percent subset reserve against continuous original-cover pricing. Both use the same fresh seed48 initial and budget. This does not isolate an algorithm speedup against prior660s experiments.',
        fallback='Defaults unchanged. Retain the profile even if it worsens; one matched shared-initial seed cannot show population stability.'))
    print('REGISTERED FRESH SHARED INITIAL PAIR',flush=True);sys.exit()

plan=read(HERE/'plan.json');assert sha(Path(__file__))==plan['runner_sha256']
for directory,hashes in plan['source_sha256'].items():
    for name,digest in hashes.items():assert sha(HERE/directory/name)==digest
for name,digest in plan['input_sha256'].items():assert sha(HERE/name)==digest
if sys.argv[1]=='all':
    assert read(HERE.parent/'capacity01'/'validation.json')['status']=='VERIFIED'
    assert not (HERE/'summary.json').exists() and not (HERE/'initial.json').exists()
    summary=[]
    for name in ['initial']+[j['id'] for j in plan['jobs']]:
        if datetime.datetime.now(datetime.timezone.utc)>=datetime.datetime(2026,10,1,23,0,tzinfo=datetime.timezone.utc):
            checkpoint(status='STOPPED_AT_NO_NEW_RUN_CUTOFF',running=None,next_step='Local07:00 cutoff reached; verify existing outputs and finalize report only.');sys.exit()
        limit=plan['initializer']['alns_time_limit'] if name=='initial' else 1500
        begun=time.perf_counter()
        with (HERE/(name+'.log')).open('w') as log:
            proc=subprocess.Popen([sys.executable,str(Path(__file__)),name],stdout=log,
                stderr=subprocess.STDOUT,env={**os.environ,'PYTHONHASHSEED':'0'},cwd=ROOT)
            checkpoint(status='RUNNING_FRESH_SHARED_INITIAL_PAIR',current_probe=str(HERE),
                running=dict(pid=proc.pid,conductor_pid=os.getpid(),job=name,
                             log=str(HERE/(name+'.log')),started_utc=datetime.datetime.now(datetime.timezone.utc).isoformat()))
            try:rc=proc.wait(timeout=limit+20);failure=f'EXIT_{rc}' if rc else None
            except subprocess.TimeoutExpired:proc.kill();proc.wait();failure='OUTER_TIMEOUT'
        row=dict(id=name,failure=failure,outer_seconds=time.perf_counter()-begun)
        if failure:
            write(HERE/'failure.json',row);checkpoint(status='FRESH_PAIR_FAILED',running=None,next_step='Inspect retained failure.json and logs; do not overwrite failed records.');sys.exit(1)
        if name=='initial':
            write(HERE/'initial_manifest.json',dict(sha256=sha(HERE/'initial.json'),origin='Fresh registered initializer',
                initializer=plan['initializer'],frozen_before_paired_jobs=True))
            continue
        job=next(j for j in plan['jobs'] if j['id']==name);payload=read(HERE/(name+'.json'));out=payload['experiment']
        row.update(job=job,upper=out['upper_bound'],lower=out['bound'],gap=out['gap'],
            target_reached=out['target_reached'],wall_seconds=payload['wall_seconds'],
            pipeline_wall_seconds=payload['pipeline_wall_seconds'],valid=payload['independent_audit']['valid'],verified=False)
        summary.append(row);write(HERE/'summary.json',summary);print('UNVERIFIED FRESH PAIR RESULT',row,flush=True)
    checkpoint(status='FRESH_PAIR_NEEDS_VERIFICATION',running=None,next_step='Run long01/validate.py; compare matched1500s reserve profiles and independently check complete domain proof models. Preserve historical best separately.')
    sys.exit()

initializing=sys.argv[1]=='initial'
job=None if initializing else next(j for j in plan['jobs'] if j['id']==sys.argv[1])
source=plan['initializer']['source'] if initializing else job['source']
sys.path.insert(0,str(HERE/source))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution
import models.routing.service as service
p=load_instance(*load_routing_case(plan['case'],use_pool_config=False))
if initializing:
    assert not (HERE/'initial.json').exists()
    begun=time.perf_counter();loads=os.getloadavg()
    result=service.solve_routing(model=5,method='alns',problem=p,seed=48,alns_time=15,alns_iter=100000)
    audit=audit_solution(p,result)
    assert audit['valid'] and audit['periodic_replay']['valid']
    elapsed=time.perf_counter()-begun
    assert elapsed<=15.5
    write(HERE/'initial.json',dict(result=result,independent_audit=audit,wall_seconds=elapsed,
        system_load_at_start=loads,system_load_at_end=os.getloadavg(),initializer=plan['initializer']))
    print('FRESH ALNS',audit['objective'],elapsed,flush=True);sys.exit()

assert not (HERE/(job['id']+'.json')).exists()
manifest=read(HERE/'initial_manifest.json');assert sha(HERE/'initial.json')==manifest['sha256']
fresh=read(HERE/'initial.json');initial=fresh['result'];audit=audit_solution(p,initial)
assert audit['valid'] and audit['periodic_replay']['valid']
service.solve_routing=lambda **kw:deepcopy(initial)
from models.routing.variants.model_5.exact import solve_hybrid
options={**job['options'],'time_limit':1500-fresh['wall_seconds']}
begun=time.perf_counter();loads=os.getloadavg()
out=solve_hybrid(p,**options,verbose=True,diagnostics_dir=HERE/(job['id']+'_models'))
assert out['initial_result']==initial
audit=audit_solution(p,out['result'])
assert audit['valid'] and audit['periodic_replay']['valid']
wall=time.perf_counter()-begun;assert wall+fresh['wall_seconds']<=1500.5
write(HERE/(job['id']+'.json'),dict(job=job,experiment=out,independent_audit=audit,
    wall_seconds=wall,pipeline_wall_seconds=wall+fresh['wall_seconds'],
    actual_hybrid_time_limit=options['time_limit'],charged_initial_wall_seconds=fresh['wall_seconds'],
    system_load_at_start=loads,system_load_at_end=os.getloadavg()))
print('FRESH SHARED INITIAL PIPELINE',job['id'],out['upper_bound'],out['bound'],out['gap'],flush=True)
