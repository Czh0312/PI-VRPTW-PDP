import json
from pathlib import Path
import sys
import time

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
sys.path.insert(0,str(ROOT))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.solvers.exact.multibatch_joint_inventory import joint_route_inventory_certificate
old=HERE.parent/'m5_route_timing_20261001'
p=json.loads((old/'verified_120s/02_42_timed.json').read_text())
problem=load_instance(*load_routing_case(p['job']['case']))
initial=json.loads((old/'best_full_result.json').read_text())
out=joint_route_inventory_certificate(problem,time_limit=300,initial_result=initial,seed=42,
    timing_envelopes=True,service_envelopes=True,enumeration_time_limit=90,joint_time_limit=150,
    recovery_time_limit=3,e1_time_limit=10,integer_cover_time_limit=15,verbose=True,
    diagnostics_dir=HERE/'probe_profile_models')
(HERE/'probe_profile.json').write_text(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
j=out['joint_certificate'] or {}
print(json.dumps(dict(bound=out['bound'],upper=out['upper_bound'],gap=out['gap'],status=out['status'],
    joint_status=j.get('status'),raw_bound=j.get('raw_bound'),choices=j.get('route_choices'),runtime=out['runtime']),ensure_ascii=False),flush=True)
