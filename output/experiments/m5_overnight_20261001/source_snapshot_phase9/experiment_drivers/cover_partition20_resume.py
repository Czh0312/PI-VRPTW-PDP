from pathlib import Path
import sys,json
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE/'source_snapshot_subset19'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.solvers.exact.multibatch_physical_site import physical_site_certificate
problem=load_instance(*load_routing_case(str(HERE/'inputs/case.json')))
groups=json.loads((HERE/'cover_partition20/cover_point.json').read_text())['trip_groups']
cert=physical_site_certificate(problem,'S1',time_limit=30,seed=42,trip_groups=groups,
    fallback_bound=451.71429479447573,diagnostics_path=HERE/'cover_partition20/physical.mps.gz')
(HERE/'cover_partition20/physical.json').write_text(json.dumps(cert,indent=2)+'\n')
print(json.dumps({k:v for k,v in cert.items() if k!='projection_proof'},indent=2))
