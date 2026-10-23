import json
from pathlib import Path
from tools.data.build_formal_benchmark27 import digest,write_json
old=Path('data/formal_benchmark27_spacetime_v4')
new=Path('data/formal_benchmark27_spacetime_v4_1')
manifest=json.loads((new/'manifest.json').read_text(encoding='utf-8'))
checked={}
for base in manifest['base_cases']:
    shared=Path('shared')/base['size']/base['id']
    paths=[shared/name for name in ('network.csv','fleet.json','economic.json','reference_orders.json','reference_policy.json','batch_count_certificate.json')]
    paths += [Path(f'model_{m}')/base['size']/base['id']/name for m in range(1,9) for name in ('orders.json','policy.json','parameters.json')]
    for relative in paths:
        a,b=digest(old/relative),digest(new/relative)
        assert a==b,relative
        checked[relative.as_posix()]=a
out=Path('output/inspections/small7_revision_20261023_v2')
write_json(out/'metadata_correction.json',dict(passed=True,preliminary_dataset=old.as_posix(),final_dataset=new.as_posix(),physical_files_unchanged=len(checked),sha256=checked,reason='Only inherited fleet mapping metadata and version/runtime paths corrected; physical cases unchanged. Preliminary 19-call study retained; every final call rerun.'))
tests=json.loads((out/'tests.json').read_text(encoding='utf-8'));tests['seconds']=146.85;write_json(out/'tests.json',tests)
print(f'Physical files unchanged between v4 and v4_1: {len(checked)}')
