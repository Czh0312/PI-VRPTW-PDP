"""One-seed smoke experiment, distinct from design-feasibility witnesses."""
from pathlib import Path
import json,hashlib,time,statistics
from tools.configuration.loader import load_routing_case
from models.common.cycle import load_instance
from models.common.cycle.audit import audit_solution
from models.cost.comparison import evaluate_comparison_cost
from cli.multibatch import run_case
ROOT=Path('output/l05_5sat_balanced25_design_20261020');DATA=Path('data/mechanism_l05_5sat_balanced25_v1')
assert not (ROOT/'pilot_protocol.json').exists()
def write(path,v):path.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
inputs={str(p):sha(p) for p in DATA.rglob('*') if p.is_file()}
source={str(p):sha(p) for base in ['models','cli','tools','experiments'] for p in Path(base).rglob('*.py')}
write(ROOT/'pilot_protocol.json',dict(seed=42,seconds_per_run=60,iteration_limit=1000000000,
 groups=['M3_cold','M8_cold','M8_feasible_start'],
 type='One-seed preliminary run, not a multi-seed robustness experiment',
 input_sha256=inputs,source_sha256=source,started_at=time.time(),
 initial_M8='Deterministic pre-search witness_K3_N35; no input tuning following solver outcomes'))
rows=[]
for group in ['M3_cold','M8_cold','M8_feasible_start']:
 model=3 if group=='M3_cold' else 8
 cfg,fc=load_routing_case(DATA/f'M{model}/case.json',use_pool_config=False)
 cfg.update(seed=42,alns_time=60,alns_iter=1000000000)
 if group=='M8_feasible_start':cfg['initial_joint_result']=str(ROOT/'witness_M8_K3.json')
 print('START '+group+' seed=42 seconds=60',flush=True)
 folder=ROOT/group/'seed_42';result=run_case(cfg,fc,case_path=DATA/f'M{model}/case.json',output=folder)
 problem=load_instance(cfg,fc);audit=audit_solution(problem,result) if result['info'].get('objective') is not None else {}
 write(folder/'independent_audit.json',audit)
 row=dict(group=group,seed=42,status=result['info']['status'],valid=audit.get('valid',False),wall_time=result['wall_time'])
 if row['valid']:
  priced=evaluate_comparison_cost(problem,result,str(DATA/'economic.json'),audit=audit);write(folder/'comparison_cost.json',priced)
  pool=priced['container_pool_size'];assignment=result.get('order_batch_assignment',{o.id:'b1' for o in problem.orders});dw=[];dv=[]
  for trip in result['e1_trips']:
   sites={e['site_id'] for e in trip['events'] if e['kind']=='d'}
   if not sites:continue
   orders=[o for o in problem.orders if o.satellite in sites and assignment[o.id]==trip['batch_id']]
   dw.append(sum(o.goods_weight+o.count*fc['container_weight'] for o in orders)/fc['E1_Qk'])
   dv.append(sum(o.count*fc['container_volume'] for o in orders)/fc['E1_Ck'])
  row.update(N=pool,K=result.get('cycle_batch_count',1),total_cost=priced['total'],
   D1=audit['distances']['e1'],D2=audit['distances']['e2'],trips1=audit['e1_trip_count'],trips2=audit['e2_trip_count'],
   e1_departure_weight_util=statistics.mean(dw),e1_departure_volume_util=statistics.mean(dv),
   saving_rate=(100-pool)/100,turnover=100/pool,two_cycle_valid=audit['periodic_replay']['valid'])
 rows.append(row);write(ROOT/'pilot_results.json',rows)
 print('RESULT '+json.dumps(row,ensure_ascii=False),flush=True)
changed=[p for p,h in {**inputs,**source}.items() if sha(Path(p))!=h]
write(ROOT/'pilot_integrity.json',dict(passed=not changed,changes=changed,finished_at=time.time()))
assert not changed,changed
print('COMPLETE',flush=True)
