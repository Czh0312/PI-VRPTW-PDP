from pathlib import Path
import json,sys
HERE=Path(__file__).resolve().parent;sys.path.insert(0,str(HERE/'final_source_snapshot'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.solvers.exact.multibatch_e1_routes import build_e1_routes
pfile=HERE/'global_columns_120s/100_full.json';d=json.loads(pfile.read_text());p=load_instance(*load_routing_case(d['job']['case']))
m,c=build_e1_routes(p)
try:
 m.Params.TimeLimit=20;m.optimize();assert m.SolCount
 actual={(e['site_id'],t['batch_id']):e['start']+p.service(e['site_id']) for t in d['result']['e1_trips'] for e in t['events'] if e['kind']=='d'}
 rows=[dict(site=s,batch=b,relaxed=int(round(v.X)),actual=actual[s,b],delay=int(round(v.X))-actual[s,b]) for (s,b),v in c['supplies'].items()]
 out=dict(bound=m.ObjBound,status=int(m.Status),supplies=rows)
 (HERE/'joint_supply_inspection.json').write_text(json.dumps(out,indent=2)+'\n');print(json.dumps(out,indent=2),flush=True)
finally:m.dispose()
