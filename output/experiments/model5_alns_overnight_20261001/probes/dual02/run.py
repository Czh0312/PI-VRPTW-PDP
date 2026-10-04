"""Equal-budget continuation from identical verified reference and incumbent."""
import datetime,hashlib,json,os
from pathlib import Path
import shutil,subprocess,sys,time
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[4];NIGHT=HERE.parents[1];OLD=HERE.parent/'fresh01'
CHANGED=['models/routing/solvers/exact/multibatch_hybrid.py',
         'models/routing/solvers/exact/multibatch_pricing_polish.py']
def read(p):return json.loads(p.read_text())
def write(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def checkpoint(**kw):
    obj=read(NIGHT/'checkpoint.json');obj.update(kw,updated_at=datetime.datetime.now(datetime.timezone.utc).isoformat());write(NIGHT/'checkpoint.json',obj)
if sys.argv[1]=='register':
    assert not (HERE/'plan.json').exists()
    origin=read(HERE/'baseline_manifest.json')['source_sha256']
    for name,digest in origin.items():assert sha(HERE/'baseline_source'/name)==digest
    shutil.copytree(HERE/'baseline_source',HERE/'candidate_source')
    for name in CHANGED:shutil.copy2(ROOT/name,HERE/'candidate_source'/name)
    old_manifest=read(OLD/'final_verification.json');assert old_manifest['status']=='VERIFIED'
    frozen=HERE/'inputs';frozen.mkdir();cfg=read(OLD/'inputs/case.json');bindings=[]
    for field in ['data','orders','routing_config','dataset_metadata']:
        origin=Path(cfg[field]);target=frozen/origin.name
        assert sha(origin)==old_manifest['artifact_sha256'][str(origin.relative_to(OLD))]
        shutil.copy2(origin,target);cfg[field]=str(target)
        bindings.append(dict(field=field,origin=str(origin),sha256=sha(origin),frozen=str(target)))
    write(frozen/'case.json',cfg)
    payload=OLD/'baseline_subset25.json'
    assert sha(payload)==old_manifest['artifact_sha256'][payload.name]
    o=read(payload)['experiment']
    write(frozen/'state.json',dict(initial_result=o['result'],reference=o['reference_certificate'],
        conditional=o['conditional_certificate'],fallback_bound=o['conditional_certificate']['bound']))
    jobs=[dict(id='baseline_cg',source='baseline_source',options={}),
          dict(id='candidate_fixed',source='candidate_source',options={'adaptive_pricing_mode':'fixed_duals'})]
    write(HERE/'plan.json',dict(registered_before_run=True,mode='continuous',jobs=jobs,time_limit=180,
        case=str(frozen/'case.json'),state=str(frozen/'state.json'),input_bindings=bindings,
        origin=str(payload),origin_sha256=sha(payload),origin_final=str(OLD/'final_verification.json'),origin_final_sha256=sha(OLD/'final_verification.json'),
        source_sha256={directory:{str(p.relative_to(HERE/directory)):sha(p)
            for p in (HERE/directory).rglob('*.py')} for directory in ['baseline_source','candidate_source']},
        input_sha256={str(p.relative_to(HERE)):sha(p) for p in frozen.iterdir()},
        checker_sha256={p.name:sha(p) for p in HERE.glob('validation_*.py')},
        runner_sha256=sha(Path(__file__)),validator_sha256=sha(HERE/'validate.py'),
        declared_source_changes=CHANGED,seed=43,extra_slots_per_vehicle=1,route_pool_max_columns=64,
        scope='Matched 180s warm adaptive continuation on one verified full pipeline state. This is not a cold start, single-660s achievement or stability test.',
        hypothesis='Direct repricing at the existing best witness avoids cover master regeneration and may make short adaptive budget useful.',
        fallback='All defaults retain column generation; preserve failures and stronger original floors.'))
    print('REGISTERED WARM DUAL REFINEMENT PAIR',flush=True);sys.exit()
plan=read(HERE/'plan.json');assert sha(Path(__file__))==plan['runner_sha256']
for directory,hashes in plan['source_sha256'].items():
    for name,digest in hashes.items():assert sha(HERE/directory/name)==digest
for name,digest in plan['input_sha256'].items():assert sha(HERE/name)==digest
if sys.argv[1]=='all':
    assert not (HERE/'summary.json').exists();summary=[]
    for job in plan['jobs']:
        begun=time.perf_counter()
        with (HERE/(job['id']+'.log')).open('w') as log:
            proc=subprocess.Popen([sys.executable,str(Path(__file__)),job['id']],stdout=log,stderr=subprocess.STDOUT,
                env={**os.environ,'PYTHONHASHSEED':'0'},cwd=ROOT)
            checkpoint(status='RUNNING_FIXED_DUAL_PROBE',current_probe=str(HERE),running=dict(pid=proc.pid,
                conductor_pid=os.getpid(),job=job['id'],log=str(HERE/(job['id']+'.log'))))
            try:rc=proc.wait(timeout=plan['time_limit']+20);failure=f'EXIT_{rc}' if rc else None
            except subprocess.TimeoutExpired:proc.kill();proc.wait();failure='OUTER_TIMEOUT'
        row=dict(job=job,failure=failure,outer_seconds=time.perf_counter()-begun)
        if (HERE/(job['id']+'.json')).exists():
            payload=read(HERE/(job['id']+'.json'));out=payload['experiment'];row.update(upper=out['upper_bound'],
                lower=out['bound'],gap=out['gap'],wall_seconds=payload['wall_seconds'],verified=False)
        summary.append(row);write(HERE/'summary.json',summary);print('UNVERIFIED DUAL PROBE',row,flush=True)
    checkpoint(status='FIXED_DUAL_PROBE_NEEDS_VERIFICATION',running=None,next_step='Run dual01/validate.py. Compare fixed prices with restarted CG under the same warm state and 180s budget. Do not call this a cold result.')
    sys.exit()
job=next(j for j in plan['jobs'] if j['id']==sys.argv[1]);assert not (HERE/(job['id']+'.json')).exists()
sys.path.insert(0,str(HERE/job['source']))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.exact.multibatch_hybrid import finish_hybrid
p=load_instance(*load_routing_case(plan['case'],use_pool_config=False));state=read(Path(plan['state']))
initial=audit_solution(p,state['initial_result']);assert initial['valid'] and initial['periodic_replay']['valid']
begun=time.perf_counter();loads=os.getloadavg()
out=finish_hybrid(p,state['reference'],state['conditional'],state['initial_result'],
    deadline=begun+plan['time_limit'],fallback_bound=state['fallback_bound'],seed=43,target_gap=.05,
    verbose=True,diagnostics_dir=HERE/(job['id']+'_models'),extra_slots_per_vehicle=1,route_pool_max_columns=64,**job['options'])
audit=audit_solution(p,out['result']);assert audit['valid'] and audit['periodic_replay']['valid']
upper=audit['objective'];gap=max(0,upper-out['bound'])/abs(upper)
out.update(upper_bound=upper,gap=gap,proven_optimal=False,proven_infeasible=False)
out['result']['info'].update(objective=upper,best_bound=out['bound'],mip_gap=gap,proven_optimal=False,proven_infeasible=False)
write(HERE/(job['id']+'.json'),dict(job=job,experiment=out,independent_audit=audit,
    wall_seconds=time.perf_counter()-begun,system_load_at_start=loads,system_load_at_end=os.getloadavg()))
print('WARM DUAL PROBE',job['id'],upper,out['bound'],gap,flush=True)
