from pathlib import Path
import sys,json,time,os,hashlib
H=Path(__file__).resolve().parent;sys.path.insert(0,str(H/'source_snapshot_phase14'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.solvers.exact.multibatch_physical_site import physical_site_certificate,build_physical_site
for raw,sha in json.loads((H/'manifest_phase14_input.json').read_text())['sha256'].items():assert hashlib.sha256(Path(raw).read_bytes()).hexdigest()==sha
p=load_instance(*load_routing_case(str(H/'medium25/0/inputs/case_relocated.json')));out=H/'site45';g=json.loads((out/'groups.json').read_text())['S3']
(out/'parameters.json').write_text(json.dumps(dict(satellite='S3',time_limit=60,seed=42,threads=1,trip_groups=g,partial_groups=False,original_problem=True),indent=2)+'\n')
c=json.loads((H/'checkpoint.json').read_text());c.update(status='running_site45',active_process=os.getpid());(H/'checkpoint.json').write_text(json.dumps(c,indent=2)+'\n')
b,ctx=build_physical_site(p,'S3',compress_routes=True)
try:
 b.write(str(out/'unconditional.mps.gz'));schema=[dict(label=r['label'],vehicle=r['vehicle'],slot=r['slot'],used=r['used'].VarName,nodes=[(n['order'].id,n['kind']) for n in r['nodes']],visits={j:v.VarName for j,v in r['visit'].items()},arcs=[dict(a=a,b=b,name=v.VarName) for (a,b),v in r['x'].items()]) for r in ctx['e2']];(out/'route_schema.json').write_text(json.dumps(schema,indent=2)+'\n')
 (out/'projection_proof.json').write_text(json.dumps(ctx['projection_proof'],indent=2)+'\n')
finally:b.dispose()
r=physical_site_certificate(p,'S3',time_limit=60,seed=42,trip_groups=g,compress_routes=True,diagnostics_path=out/'physical.mps.gz');(out/'certificate_pending_validation.json').write_text(json.dumps(r,indent=2)+'\n')
c=json.loads((H/'checkpoint.json').read_text());c.update(status='site45_pending_validation',active_process=None);(H/'checkpoint.json').write_text(json.dumps(c,indent=2)+'\n');print(json.dumps({k:v for k,v in r.items() if k!='projection_proof'},indent=2))
