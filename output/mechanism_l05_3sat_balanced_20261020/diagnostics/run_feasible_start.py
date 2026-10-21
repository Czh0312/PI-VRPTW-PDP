"""Postdeclared diagnostic: audited constructive start, unchanged inputs/ALNS."""
from pathlib import Path
from copy import deepcopy
import json,time,sys,hashlib,csv
from tools.configuration.loader import load_routing_case
from models.common.cycle import load_instance
from models.common.cycle.audit import audit_solution
from models.common.cycle.calendar import attach_calendar_decisions
from models.cost.comparison import evaluate_comparison_cost
from cli.multibatch import run_case
ROOT=Path('output/mechanism_l05_3sat_balanced_20261020')
SEEDS=[17,42,2026];SECONDS=60
protocol=dict(mode='postdeclared diagnostic after cold-start failure',seeds=SEEDS,seconds=SECONDS,
 groups=['M8_feasible_start','M8_K3_feasible_start'],
 physical_inputs='Identical to cold-start experiment; no regeneration or tuning',
 initial_solution='Deterministic audited witness with 35 containers and 3 batches; same schedule for every seed/group',
 intent='Separate existence/economics from cold-start construction success; not a replacement for cold-start results',
 start_time=time.time())
(ROOT/'diagnostics/feasible_start_protocol.json').write_text(json.dumps(protocol,indent=2))
rows=[]
original=json.loads((ROOT/'diagnostics/witness_M8_K3.json').read_text())
for seed in SEEDS:
 for label in ['M8','M8_K3']:
  group=label+'_feasible_start';path=ROOT/group/f'seed_{seed}'
  assert not path.exists(),path
  cfg,fleet=load_routing_case(f'data/mechanism_l05_3sat_balanced_v1/{label}/case.json',use_pool_config=False)
  instance=load_instance(cfg,fleet);witness=deepcopy(original)
  attach_calendar_decisions(instance,witness,3,witness['batch_boundaries'])
  witness['audit']=audit_solution(instance,witness)
  assert witness['audit']['valid'],witness['audit']['errors']
  start_path=ROOT/'diagnostics'/f'witness_{label}_initial.json'
  start_path.write_text(json.dumps(witness,indent=2))
  cfg.update(seed=seed,alns_time=SECONDS,initial_joint_result=str(start_path))
  print(f'START {group} seed={seed} budget={SECONDS}s',flush=True)
  result=run_case(cfg,fleet,case_path=f'data/mechanism_l05_3sat_balanced_v1/{label}/case.json',output=path)
  audit=audit_solution(instance,result)
  assert audit['valid'],audit['errors']
  (path/'independent_audit.json').write_text(json.dumps(audit,indent=2))
  priced=evaluate_comparison_cost(instance,result,'data/mechanism_l05_3sat_balanced_v1/economic.json',audit=audit)
  (path/'comparison_cost.json').write_text(json.dumps(priced,indent=2))
  pool=priced['container_pool_size']
  row=dict(group=group,seed=seed,valid=True,status=result['info']['status'],N=pool,Q=100,K=result['cycle_batch_count'],
   saving_rate=(100-pool)/100,turnover=100/pool,D1=audit['distances']['e1'],D2=audit['distances']['e2'],
   trips1=audit['e1_trip_count'],trips2=audit['e2_trip_count'],vehicles1=audit['e1_vehicle_count'],vehicles2=audit['e2_vehicle_count'],
   total_cost=priced['total'],two_cycle_valid=audit['periodic_replay']['valid'],runtime=result['wall_time'],
   cost_components=priced['components'],source=str(path),initial_cost=original['info']['objective'])
  rows.append(row);(ROOT/'diagnostics/feasible_start_summary.json').write_text(json.dumps(rows,indent=2))
  print('LATEST '+json.dumps(row),flush=True)
meta=json.loads((ROOT/'execution.json').read_text())
changes=[p for key in ['source_sha256','input_sha256'] for p,h in meta[key].items() if hashlib.sha256(Path(p).read_bytes()).hexdigest()!=h]
(ROOT/'diagnostics/feasible_start_integrity.json').write_text(json.dumps(dict(passed=not changes,changes=changes,finished_at=time.time()),indent=2))
assert not changes,changes
