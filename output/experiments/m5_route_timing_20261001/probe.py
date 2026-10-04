import json
from pathlib import Path
import sys,time
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2];sys.path.insert(0,str(ROOT))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.solvers.exact.multibatch_joint_inventory import joint_route_inventory_certificate
baseline=ROOT/'output/experiments/m5_joint_inventory_20261001/final_60s'
for index in [0,1,2]:
 p=load_instance(*load_routing_case(baseline/f'inputs/{index}/case.json'))
 initial=json.loads((baseline/f'inputs/{index}/initial_result.json').read_text()) if index<2 else json.loads((HERE.parent/'m5_joint_inventory_20261001/best_full_result.json').read_text())
 out=joint_route_inventory_certificate(p,initial_result=initial,time_limit=75,
     timing_envelopes=True,enumeration_time_limit=35,joint_time_limit=25,
     recovery_time_limit=5,verbose=True,diagnostics_dir=HERE/f'probe_{index}_models')
 (HERE/f'probe_{index}.json').write_text(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
 print('RESULT',index,out['bound'],out['upper_bound'],out['gap'],out['runtime'],out['status'],flush=True)
