"""Exercise the IO adapter on an isolated, already-completed pilot study."""
from pathlib import Path
import json
import sys

out = Path('output/spacetime_rental1_quality_20261021_pilot_v3').resolve()
sys.path.insert(0,str(out))
from controller_io_adapter import load_frozen
module,d = load_frozen(out)
manifest = d.read(out/'manifest.json')
d.verify(out,manifest)
job = next(j for j in manifest['jobs'] if j['id']=='S01_m1_exact_8s_s42')
with d.study_lock(out):
    row = module.execute(out,manifest,job)
assert row['valid'] and row['proven_optimal'],row
print(json.dumps(dict(status=row['status'],valid=row['valid'],attempt=row['attempt'],
    objective=row['objective'],worker_bootstrap='controller_io_adapter.py')))
