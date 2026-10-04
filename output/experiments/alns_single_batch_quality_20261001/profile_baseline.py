import cProfile,pstats,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];OUT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.service import solve_routing
case=ROOT/'output/experiments/alns_single_batch_descent_20260930/benchmark_inputs/zhou_Set2_100-10-1/case.json'
config,fleet=load_routing_case(case)
for model in (2,4):
 p=load_instance({**config,'model':model},fleet);pr=cProfile.Profile();pr.enable()
 result=solve_routing(model=model,method='alns',problem=p,alns_time=60,alns_iter=300,seed=42)
 pr.disable();pr.dump_stats(str(OUT/f'M{model}_before.pstats'))
 print('MODEL',model,'objective',result['info']['objective'],'valid',result['audit']['valid'])
 pstats.Stats(pr).strip_dirs().sort_stats('cumulative').print_stats(18)
