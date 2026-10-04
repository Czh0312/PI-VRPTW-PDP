"""Frozen controls for optional physical trip slots, complete M5 audits."""
import hashlib,json,os
from pathlib import Path
import shutil,subprocess,sys,time
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
OLD=HERE.parent/'model5_gap_round5_20261001'
def read(p):return json.loads(p.read_text())
def write(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
if sys.argv[1]=='register':
    assert not (HERE/'plan.json').exists()
    for source in [*ROOT.glob('models/**/*.py'),*ROOT.glob('configuration/*.py')]:
        target=HERE/'source_snapshot'/source.relative_to(ROOT);target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
    (HERE/'inputs').mkdir()
    for label in ('full','tight'):shutil.copy2(OLD/'inputs'/label/'case.json',HERE/'inputs'/(label+'_case.json'))
    shutil.copy2(OLD/'retry/result.json',HERE/'inputs/previous.json')
    write(HERE/'plan.json',dict(registered_before_run=True,mode='continuous',seed=43,
        jobs=[dict(id='full_fixed',label='full',extra_slots=0),dict(id='full_extra1',label='full',extra_slots=1),dict(id='tight_extra1',label='tight',extra_slots=1)],
        time_limit=180,max_columns=64,extra_columns=96,max_passes=1,
        scope='Warm-start development controls from identical full/tight incumbents; integrated optional slot and diverse split-proposal bundle. Pool bounds never global. No cold-start or stability claim.',
        input_sha256={str(p.relative_to(HERE)):sha(p) for p in (HERE/'inputs').glob('*.json')},
        source_sha256={str(p.relative_to(HERE/'source_snapshot')):sha(p) for p in (HERE/'source_snapshot').rglob('*.py')},runner_sha256=sha(Path(__file__))))
    print('REGISTERED',flush=True);sys.exit()
plan=read(HERE/'plan.json');assert sha(Path(__file__))==plan['runner_sha256']
for f,d in plan['input_sha256'].items():assert sha(HERE/f)==d
for f,d in plan['source_sha256'].items():assert sha(HERE/'source_snapshot'/f)==d
if sys.argv[1]=='all':
    assert not (HERE/'summary.json').exists();summary=[]
    for job in plan['jobs']:
        with (HERE/(job['id']+'.log')).open('w') as log:
            try:
                run=subprocess.run([sys.executable,str(Path(__file__)),job['id']],stdout=log,stderr=subprocess.STDOUT,
                    timeout=plan['time_limit']+40,env={**os.environ,'PYTHONHASHSEED':'0'})
                failure=f'EXIT_{run.returncode}' if run.returncode else None
            except subprocess.TimeoutExpired:failure='OUTER_TIMEOUT'
        record=dict(job=job,failure=failure)
        if failure is None:
            d=read(HERE/(job['id']+'.json'));record.update(upper=d['upper'],lower=d['lower'],gap=d['gap'],wall_seconds=d['wall_seconds'])
        summary.append(record);write(HERE/'summary.json',summary);print(record,flush=True)
    sys.exit()
sys.path.insert(0,str(HERE/'source_snapshot'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.exact.multibatch_route_pool import reoptimize_route_pool
job=next(j for j in plan['jobs'] if j['id']==sys.argv[1]);assert not (HERE/(job['id']+'.json')).exists()
prior=next(c for c in read(HERE/'inputs/previous.json')['cases'] if c['label']==job['label'])
p=load_instance(*load_routing_case(str(HERE/'inputs'/(job['label']+'_case.json')),use_pool_config=False))
initial=prior['result'];before=audit_solution(p,initial);assert before['valid']
additional={r['satellite']:r['columns'] for r in prior['reference_certificate']['sites']}
begun=time.perf_counter()
result=reoptimize_route_pool(p,initial,time_limit=plan['time_limit'],seed=plan['seed'],max_columns=plan['max_columns'],
    additional_columns=additional,additional_columns_per_slot=plan['extra_columns'],
    max_passes=plan['max_passes'],extra_slots_per_vehicle=job['extra_slots'],transfers=True,exchanges=True,
    recombinations=True,e1_backend='patterns',verbose=True,diagnostics_dir=HERE/(job['id']+'_models'))
audit=audit_solution(p,result);assert audit['valid'] and audit['periodic_replay']['valid']
assert result['info']['best_bound'] is None and not result['info']['proven_optimal']
upper=audit['objective'];lower=prior['bound'];assert lower<=upper+1e-6 and upper<=before['objective']+1e-6
write(HERE/(job['id']+'.json'),dict(job=job,result=result,audit=audit,upper=upper,lower=lower,
    gap=max(0,upper-lower)/max(1e-9,abs(upper)),wall_seconds=time.perf_counter()-begun,
    target_reached=max(0,upper-lower)/max(1e-9,abs(upper))<=.05))
print('RESULT',job['id'],upper,lower,flush=True)
