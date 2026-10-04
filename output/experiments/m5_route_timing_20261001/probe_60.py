import json
from pathlib import Path
import sys
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2];sys.path.insert(0,str(ROOT))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.solvers.exact.multibatch_joint_inventory import joint_route_inventory_certificate
baseline=ROOT/'output/experiments/m5_joint_inventory_20261001'
p=load_instance(*load_routing_case(baseline/'final_60s/inputs/2/case.json'))
initial=json.loads((baseline/'best_full_result.json').read_text())
out=joint_route_inventory_certificate(p,initial_result=initial,time_limit=60,
    timing_envelopes=True,enumeration_time_limit=25,joint_time_limit=25,
    recovery_time_limit=3,verbose=True,diagnostics_dir=HERE/'probe_60_models')
(HERE/'probe_60.json').write_text(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
print('RESULT',out['bound'],out['upper_bound'],out['gap'],out['runtime'],out['status'],flush=True)
