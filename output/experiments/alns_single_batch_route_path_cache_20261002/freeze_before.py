"""Freeze accepted source, unchanged scenario02 inputs, and planned jobs."""
from pathlib import Path
import hashlib,json,os,shutil
from datetime import datetime,timezone
OUT=Path(__file__).resolve().parent;ROOT=OUT.parents[2]
PILOT=OUT.parent/'alns_single_batch_descent_load_screen_20261002'
CONFIRM=OUT.parent/'alns_single_batch_late_pickup_confirm_20261002'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,v):p.write_text(json.dumps(v,indent=2),encoding='utf-8')
def hashes(base):return {str(p.relative_to(base)):sha(p) for p in sorted(base.rglob('*')) if p.is_file() and '__pycache__' not in p.parts}
assert not (OUT/'before_source_manifest.json').exists(), 'Never refreeze an existing experiment'
accepted=json.loads((CONFIRM/'live_owned_hashes.json').read_text())
current=json.loads((OUT.parent/'alns_single_batch_overnight_20261001/checkpoint.json').read_text())['current_owned_hashes']
assert all(sha(ROOT/f)==h for f,h in current.items())
write(OUT/'current_baseline_owned_hashes.json',current)
assert all(sha(ROOT/f)==h for f,h in accepted.items())
write(OUT/'accepted_owned_hashes.json',accepted)
for dirname in ('models','configuration','config'):
    for src in sorted((ROOT/dirname).rglob('*')):
        if src.is_file() and '__pycache__' not in src.parts and src.suffix in ('.py','.json','.md'):
            dest=OUT/'before_source'/src.relative_to(ROOT);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dest)
src=ROOT/'tools/audit/benchmark_single_batch_solvers.py';dest=OUT/'before_source'/src.relative_to(ROOT);dest.parent.mkdir(parents=True);shutil.copy2(src,dest)
write(OUT/'before_source_manifest.json',hashes(OUT/'before_source'))
origins={}
families=['zhou_Set2_100-5-3_subset50_scenario02','zhou_Set2_100-5-3_scenario02','zhou_Set2_100-10-2_scenario02']
for family in families:
    src=PILOT/'benchmark_inputs'/family;dest=OUT/'benchmark_inputs'/family;shutil.copytree(src,dest)
    for f in sorted(dest.iterdir()):
        if f.is_file():origins[str(f.relative_to(OUT))]=dict(source=str(src/f.name),sha256=sha(src/f.name),path_only=f.name=='case.json')
    config=json.loads((dest/'case.json').read_text())
    for key,value in list(config.items()):
        if isinstance(value,str):
            if (src/Path(value).name).is_file(): config[key]=str(dest/Path(value).name)
            elif key in ('pool_config','container_pool_config'):config[key]=str(OUT/'before_source/config/routing/container_pools.json')
    write(dest/'case.json',config)
write(OUT/'input_origins.json',origins);write(OUT/'input_hashes.json',hashes(OUT/'benchmark_inputs'))
jobs=[dict(family=family,case=str(OUT/'benchmark_inputs'/family/'case.json'),model=3,solver='alns',seed=seed,budget=30,iterations=1000000,size=50 if 'subset50' in family else 100)
      for family in families[:2] for seed in (322,323,324)]
jobs += [dict(family=families[2],case=str(OUT/'benchmark_inputs'/families[2]/'case.json'),model=3,solver='alns',seed=seed,budget=60,iterations=1000000,size=100) for seed in (322,323)]
write(OUT/'quality_manifest.json',dict(jobs=jobs))
verify=[dict(family=family,case=str(OUT/'benchmark_inputs'/family/'case.json'),model=model,seed=321,iterations=500,budget=60) for family in families[1:] for model in range(1,5)]
write(OUT/'verification_manifest.json',dict(jobs=verify))
write(OUT/'context.json',dict(frozen=datetime.now(timezone.utc).isoformat(),load=os.getloadavg(),active_owned_process=None,external_inventory='ps restricted; load observation only',
    drift_from_confirmation=[f for f,h in json.loads((CONFIRM/'after_source_manifest.json').read_text()).items() if sha(ROOT/f)!=h]))
print(json.dumps(dict(source_files=len(hashes(OUT/'before_source')),inputs=len(origins),quality_pairs=len(jobs))))
