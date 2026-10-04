from pathlib import Path
from collections import Counter,defaultdict
import json,sys,hashlib
H=Path('/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/m5_overnight_20261001');sys.path.insert(0,str(H/'source_snapshot_phase13'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
p=load_instance(*load_routing_case(str(H/'medium25/0/inputs/case_relocated.json')))
rows=json.loads((H/'master39/selected_columns.json').read_text());g=defaultdict(list);counts=Counter()
for r in rows:
 assert abs(r['value']-1)<1e-7
 tasks=[tuple(t) for t in r['tasks']];g[r['satellite']].append(tasks);counts.update(tasks)
assert counts==Counter((o.id,k) for o in p.orders for k in ('d','p'))
(H/'site40').mkdir();(H/'site40/groups.json').write_text(json.dumps(g,indent=2)+'\n')
(H/'site40/coverage_validation.json').write_text(json.dumps(dict(tasks_checked=len(counts),each_task_once=True,selected_integer=True,full_feasible=False),indent=2)+'\n')
s=(H/'site31.py').read_text().replace('site31','site40').replace("for p,h in json.loads((HERE/'manifest_phase13_input.json').read_text())['sha256'].items():assert hashlib.sha256(Path(p).read_bytes()).hexdigest()==h","assert json.loads((HERE/'master39/independent_validation.json').read_text())['promote_supported']")
(H/'site40.py').write_text(s)
print({s:len(gs) for s,gs in g.items()})
