from pathlib import Path
import json,sys,time,os
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE/'source_snapshot_subset19'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.solvers.exact.multibatch_physical_site import physical_site_certificate
problem=load_instance(*load_routing_case(str(HERE/'inputs/case.json')))
groups=json.loads((HERE/'cover_partition20/cover_point.json').read_text())['trip_groups']
out=HERE/'cover_returns21';summaries=[]
for name,counts in [('b1zero',{'b1':0}),('b2ten',{'b2':10}),('b1zero_b2ten',{'b1':0,'b2':10})]:
 c=json.loads((HERE/'checkpoint.json').read_text());c.update(status='running_cover21_'+name,active_process=os.getpid());(HERE/'checkpoint.json').write_text(json.dumps(c,indent=2)+'\n')
 cert=physical_site_certificate(problem,'S1',time_limit=30,seed=42,trip_groups=groups,
    maximum_returns=counts,fallback_bound=451.71429479447573,diagnostics_path=out/(name+'.mps.gz'))
 (out/(name+'.json')).write_text(json.dumps(cert,indent=2)+'\n')
 summaries.append(dict(name=name,**{k:v for k,v in cert.items() if k not in ('projection_proof','trip_groups')}))
 (out/'summary.json').write_text(json.dumps(summaries,indent=2)+'\n')
c=json.loads((HERE/'checkpoint.json').read_text());c.update(status='cover21_complete',active_process=None);(HERE/'checkpoint.json').write_text(json.dumps(c,indent=2)+'\n')
print(json.dumps(summaries,indent=2))
