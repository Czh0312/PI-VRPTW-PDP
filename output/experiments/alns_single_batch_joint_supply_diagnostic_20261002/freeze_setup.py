"""Freeze current context and saved incumbents; no production changes."""
from pathlib import Path
import hashlib, json, os, shutil
from datetime import datetime, timezone

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
PREV = OUT.parent/'alns_single_batch_late_pickup_confirm_20261002'
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def write(path, value): path.write_text(json.dumps(value, indent=2), encoding='utf-8')
def hashes(base):
    return {str(p.relative_to(base)):sha(p) for p in sorted(base.rglob('*'))
            if p.is_file() and '__pycache__' not in p.parts}

assert not (OUT/'source_manifest.json').exists(), 'Do not refreeze an existing run'
owned = json.loads((PREV/'live_owned_hashes.json').read_text())
assert all(sha(ROOT/f)==h for f,h in owned.items())
write(OUT/'live_owned_hashes.json', owned)
for name in json.loads((PREV/'after_source_manifest.json').read_text()):
    dest = OUT/'frozen_source'/name
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT/name, dest)
write(OUT/'source_manifest.json', hashes(OUT/'frozen_source'))
shutil.copytree(PREV/'benchmark_inputs', OUT/'benchmark_inputs')
origins = {}
for path in sorted((OUT/'benchmark_inputs').rglob('*')):
    if not path.is_file(): continue
    origin = PREV/'benchmark_inputs'/path.relative_to(OUT/'benchmark_inputs')
    origins[str(path.relative_to(OUT))] = dict(source=str(origin), sha256=sha(origin), path_only=path.name=='case.json')
    if path.name=='case.json':
        config = json.loads(path.read_text())
        for key,value in list(config.items()):
            if isinstance(value,str):
                config[key]=value.replace(str(PREV/'benchmark_inputs'),str(OUT/'benchmark_inputs')).replace(str(PREV/'before_source'),str(OUT/'frozen_source'))
        write(path,config)
write(OUT/'input_origins.json',origins)
write(OUT/'input_hashes.json',hashes(OUT/'benchmark_inputs'))
(OUT/'incumbents').mkdir()
jobs=[]
for index,job in enumerate(json.loads((PREV/'quality_manifest.json').read_text())['jobs']):
    for phase in ('before_source','after_source'):
        name='%03d_%s.json'%(index,phase)
        source=PREV/'quality'/name
        shutil.copy2(source,OUT/'incumbents'/name)
        jobs.append(dict(name=name,case=str(OUT/'benchmark_inputs'/job['family']/'case.json'),job=job,origin=str(source),sha256=sha(source)))
write(OUT/'incumbent_manifest.json',jobs)
write(OUT/'incumbent_hashes.json',hashes(OUT/'incumbents'))
write(OUT/'context.json',dict(time=datetime.now(timezone.utc).isoformat(),load=os.getloadavg(),
    active_owned_processes=[],external_inventory='ps restricted; load observations do not establish process isolation',
    source_drift_from_confirmation=[f for f,h in json.loads((PREV/'after_source_manifest.json').read_text()).items() if sha(ROOT/f)!=h]))
print(json.dumps(dict(source_files=len(hashes(OUT/'frozen_source')),input_files=len(origins),incumbents=len(jobs))))
