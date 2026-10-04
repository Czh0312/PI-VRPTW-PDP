"""Freeze confirmation with unchanged business inputs and exact queue256 control."""
from pathlib import Path
from datetime import datetime,timezone
import difflib,hashlib,json,os,shutil
OUT=Path(__file__).resolve().parent;ROOT=OUT.parents[2]
PILOT=OUT.parent/'alns_single_batch_descent_queue_20261002'
ORIGIN=OUT.parent/'alns_single_batch_late_pickup_confirm_20261002'
OWN='models/routing/solvers/alns/single_batch.py'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,v):p.write_text(json.dumps(v,indent=2),encoding='utf-8')
def hashes(base):return {str(p.relative_to(base)):sha(p) for p in sorted(base.rglob('*')) if p.is_file() and '__pycache__' not in p.parts}
assert not (OUT/'after_source_manifest.json').exists(),'Never refreeze or duplicate a confirmation'
current=json.loads((PILOT/'current_owned_hashes.json').read_text())
assert all(sha(ROOT/f)==h for f,h in current.items())
write(OUT/'current_owned_hashes.json',current)
write(OUT/'owned_hashes.json',{f:h for f,h in current.items() if f!= 'tests/routing/test_single_batch_late_pickup.py'})
for directory in ('models','configuration','config'):
    for path in sorted((ROOT/directory).rglob('*')):
        if path.is_file() and '__pycache__' not in path.parts and path.suffix in ('.py','.json','.md'):
            dest=OUT/'after_source'/path.relative_to(ROOT);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,dest)
path=ROOT/'tools/audit/benchmark_single_batch_solvers.py';dest=OUT/'after_source'/path.relative_to(ROOT);dest.parent.mkdir(parents=True);shutil.copy2(path,dest)
shutil.copytree(OUT/'after_source',OUT/'before_source')
shutil.copy2(PILOT/'before_source'/OWN,OUT/'before_source'/OWN)
write(OUT/'accepted_owned_hashes.json',{OWN:sha(OUT/'before_source'/OWN),'tests/routing/test_single_batch_late_pickup.py':current['tests/routing/test_single_batch_late_pickup.py']})
shutil.copytree(OUT/'after_source',OUT/'disabled_source')
disabled=OUT/'disabled_source'/OWN;text=disabled.read_text();assert text.count('_descent_capacity=1024')==1;disabled.write_text(text.replace('_descent_capacity=1024','_descent_capacity=256'))
for source in ('before_source','after_source','disabled_source'):write(OUT/(source+'_manifest.json'),hashes(OUT/source))
before=hashes(OUT/'before_source');after=hashes(OUT/'after_source');diff=[f for f in before if before[f]!=after[f]];assert diff==[OWN]
write(OUT/'source_scope.json',dict(source_differences=diff))
(OUT/'candidate.diff').write_text(''.join(difflib.unified_diff((OUT/'before_source'/OWN).read_text().splitlines(True),(OUT/'after_source'/OWN).read_text().splitlines(True),fromfile='before/'+OWN,tofile='after/'+OWN)))
assert (OUT/'before_source/config/routing/container_pools.json').read_bytes()==(ORIGIN/'before_source/config/routing/container_pools.json').read_bytes(),'Business pool config must stay exact'
families=['zhou_Set2_100-10-1_subset50','zhou_Set2_100-10-1','zhou_Set2_100-5-2']
origins={}
for family in families:
    source=ORIGIN/'benchmark_inputs'/family;target=OUT/'benchmark_inputs'/family;shutil.copytree(source,target)
    for path in sorted(target.iterdir()):
        if path.is_file():origins[str(path.relative_to(OUT))]=dict(source=str(source/path.name),sha256=sha(source/path.name),path_only=path.name=='case.json')
    config=json.loads((target/'case.json').read_text())
    for key,value in list(config.items()):
        if isinstance(value,str):config[key]=value.replace(str(ORIGIN/'benchmark_inputs'),str(OUT/'benchmark_inputs')).replace(str(ORIGIN/'before_source'),str(OUT/'before_source'))
    write(target/'case.json',config)
write(OUT/'input_origins.json',origins);write(OUT/'input_hashes.json',hashes(OUT/'benchmark_inputs'))
jobs=[dict(family=family,case=str(OUT/'benchmark_inputs'/family/'case.json'),model=3,solver='alns',seed=seed,budget=60,iterations=1000000,size=50 if 'subset50' in family else 100) for family in families[:2] for seed in (282,283,284)]
jobs += [dict(family=families[2],case=str(OUT/'benchmark_inputs'/families[2]/'case.json'),model=3,solver='alns',seed=seed,budget=120,iterations=1000000,size=100) for seed in (282,283)]
write(OUT/'quality_manifest.json',dict(jobs=jobs))
write(OUT/'verification_manifest.json',dict(jobs=[dict(family=family,case=str(OUT/'benchmark_inputs'/family/'case.json'),model=model,seed=281,budget=60,iterations=500) for family in families[1:] for model in range(1,5)]))
for name in ('fixed_work.py','check_disabled.py','run_pairs.py','input_preflight.py','summarize.py','check_final.py','verify_adopted.py'):
    text=(PILOT/name).read_text().replace('seed=271','seed=281')
    if name=='summarize.py':text=text.replace("p['budget']==60","p['budget']==120").replace("p['budget']==30","p['budget']==60")
    (OUT/name).write_text(text)
write(OUT/'context.json',dict(frozen=datetime.now(timezone.utc).isoformat(),load=os.getloadavg(),active_owned_process=None,
    external_inventory='ps restricted; load observations do not establish isolation',
    source_drift_from_pilot=[f for f,h in json.loads((PILOT/'after_source_manifest.json').read_text()).items() if sha(ROOT/f)!=h],
    prior_full_regression='Unchanged owned module/tests: pilot2884 passed14warnings86.65s. No fresh full regression before confirmation, source drift recorded.'))
print(json.dumps(dict(source_files=len(after),input_files=len(origins),quality_pairs=len(jobs),source_differences=diff)))
