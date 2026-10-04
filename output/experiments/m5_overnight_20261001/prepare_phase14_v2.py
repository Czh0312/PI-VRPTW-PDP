from pathlib import Path
from collections import Counter,defaultdict
import json,shutil,hashlib,sys
H=Path(__file__).resolve().parent
assert (H/'source_snapshot_phase14').exists()
sys.path.insert(0,str(H/'source_snapshot_phase14'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
p=load_instance(*load_routing_case(str(H/'medium25/0/inputs/case_relocated.json')))
assert json.loads((H/'master44/independent_validation.json').read_text())['promote_supported']
rows=json.loads((H/'master44/selected_columns.json').read_text());groups=defaultdict(list);count=Counter()
for r in rows:
 if r['satellite']=='S1':continue
 assert abs(r['value']-1)<1e-7
 tasks=[tuple(t) for t in r['tasks']];groups[r['satellite']].append(tasks);count.update(tasks)
assert count==Counter((o.id,k) for o in p.orders if o.satellite!='S1' for k in ('d','p'))
(H/'site45').mkdir();(H/'site45/groups.json').write_text(json.dumps(groups,indent=2)+'\n')
(H/'site45/coverage_validation.json').write_text(json.dumps(dict(tasks_checked=len(count),each_task_once=True,declared_sites_integer=True,S1_fractional_not_grouped=True,full_feasible=False),indent=2)+'\n')
files=[x for x in (H/'source_snapshot_phase14').rglob('*') if x.is_file()]+[H/'site45/groups.json',H/'master44/master.mps.gz',H/'master44/independent_validation.json',H/'medium25/0/inputs/case_relocated.json',H/'medium25/0/inputs/initial_result.json',Path(__file__)]
(H/'manifest_phase14_input.json').write_text(json.dumps({'sha256':{str(f):hashlib.sha256(f.read_bytes()).hexdigest() for f in files}},indent=2)+'\n')
print({s:len(gs) for s,gs in groups.items()})
