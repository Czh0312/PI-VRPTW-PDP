"""Registered sequential paired cold runs, no inherited solutions or bounds."""
import hashlib,json,os
from pathlib import Path
import shutil,subprocess,sys,time

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
OLD=HERE.parent/'model5_gap_round6_20261001'
CHANGED=['models/routing/solvers/exact/multibatch_conditional_inventory.py',
         'models/routing/solvers/exact/multibatch_hybrid.py']
def read(p):return json.loads(p.read_text())
def write(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

if sys.argv[1]=='register':
    assert not (HERE/'plan.json').exists()
    original=read(HERE/'baseline_manifest.json')['source_sha256']
    for name,digest in original.items():assert sha(HERE/'baseline_source'/name)==digest
    shutil.copytree(HERE/'baseline_source',HERE/'candidate_source')
    for name in CHANGED:shutil.copy2(ROOT/name,HERE/'candidate_source'/name)
    frozen=HERE/'inputs/full';frozen.mkdir(parents=True)
    cfg=read(OLD/'inputs/full_case.json');bindings=[]
    old_manifest_path=HERE.parent/'model5_gap_round4_20261001/final_verification.json'
    old_manifest=read(old_manifest_path)['artifact_sha256']
    for field in ['data','orders','routing_config','dataset_metadata']:
        source=Path(cfg[field]);name=str(source.relative_to(old_manifest_path.parent))
        assert sha(source)==old_manifest[name]
        target=frozen/source.name;shutil.copy2(source,target);cfg[field]=str(target)
        bindings.append(dict(field=field,source=str(source),source_sha256=sha(source),frozen=str(target)))
    write(frozen/'case.json',cfg)
    common=dict(time_limit=660,alns_time_limit=15,primal_time_limit=180,cover_time_limit=360,
                conditional_time_limit=105,target_gap=.05,seed=43,max_subset_cuts_per_batch=6,max_conditions=3)
    jobs=[dict(id='full_baseline_43',source='baseline_source',options=common),
          dict(id='full_candidate_43',source='candidate_source',options={**common,
                   'extra_slots_per_vehicle':1,'route_pool_max_columns':64,'max_supply_conjunctions':1})]
    write(HERE/'plan.json',dict(registered_before_run=True,mode='continuous',jobs=jobs,
        case=str(frozen/'case.json'),declared_source_changes=CHANGED,
        source_sha256={directory:{str(p.relative_to(HERE/directory)):sha(p)
                       for p in (HERE/directory).rglob('*.py')}
                       for directory in ['baseline_source','candidate_source']},
        input_sha256={str(p.relative_to(HERE)):sha(p) for p in frozen.iterdir()},
        input_bindings=bindings,prior_input_manifest=str(old_manifest_path),prior_input_manifest_sha256=sha(old_manifest_path),
        runner_sha256=sha(Path(__file__)),python=sys.version,hash_seed=0,
        scope='Two sequential 660s cold development runs on the difficult full-pool case. Same coefficients, seed, stage ceilings and ALNS code. Candidate bundles spare slots, 64 local candidates and one automatic supply conjunction. No warm starts, imported certificates, equal-incumbent or population stability claim.',
        fallback='Retain all runs. Keep new behavior opt-in; preserve the previous audited 4.72%/1.85% warm records separately.'))
    print('REGISTERED TWO COLD RUNS',flush=True);sys.exit()

plan=read(HERE/'plan.json');assert sha(Path(__file__))==plan['runner_sha256']
for directory,hashes in plan['source_sha256'].items():
    for name,digest in hashes.items():assert sha(HERE/directory/name)==digest
for name,digest in plan['input_sha256'].items():assert sha(HERE/name)==digest
if sys.argv[1]=='all':
    assert not (HERE/'summary.json').exists();summary=[]
    for job in plan['jobs']:
        begun=time.perf_counter()
        with (HERE/(job['id']+'.log')).open('w') as log:
            try:
                run=subprocess.run([sys.executable,str(Path(__file__)),job['id']],stdout=log,stderr=subprocess.STDOUT,
                    timeout=job['options']['time_limit']+20,env={**os.environ,'PYTHONHASHSEED':'0'},cwd=ROOT)
                failure=f'EXIT_{run.returncode}' if run.returncode else None
            except subprocess.TimeoutExpired:failure='OUTER_TIMEOUT'
        row=dict(job=job,failure=failure,outer_seconds=time.perf_counter()-begun)
        if (HERE/(job['id']+'.json')).exists():
            payload=read(HERE/(job['id']+'.json'));out=payload['experiment']
            row.update(upper=out['upper_bound'],lower=out['bound'],gap=out['gap'],target_reached=out['target_reached'],
                       wall_seconds=payload['wall_seconds'],valid=payload['independent_audit']['valid'],stages=out['stages'])
        summary.append(row);write(HERE/'summary.json',summary);print(row,flush=True)
    sys.exit()

job=next(job for job in plan['jobs'] if job['id']==sys.argv[1])
assert not (HERE/(job['id']+'.json')).exists()
sys.path.insert(0,str(HERE/job['source']))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution
from models.routing.variants.model_5.exact import solve_hybrid
p=load_instance(*load_routing_case(plan['case'],use_pool_config=False))
start=time.perf_counter();loads=os.getloadavg()
out=solve_hybrid(p,**job['options'],verbose=True,diagnostics_dir=HERE/(job['id']+'_models'))
audit=audit_solution(p,out['result'])
assert audit['valid'] and audit['periodic_replay']['valid']
assert not out['proven_optimal'] and not out['proven_infeasible']
write(HERE/(job['id']+'.json'),dict(job=job,experiment=out,independent_audit=audit,
    wall_seconds=time.perf_counter()-start,system_load_at_start=loads,system_load_at_end=os.getloadavg()))
print('COLD RESULT',job['id'],out['upper_bound'],out['bound'],out['gap'],flush=True)
