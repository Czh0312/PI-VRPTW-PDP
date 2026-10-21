"""Local E1 merge diagnosis: no input/solver changes and no ALNS rerun."""
from pathlib import Path
from copy import deepcopy
from itertools import permutations,combinations
from collections import Counter
import json,time,hashlib
from tools.configuration.loader import load_routing_case
from models.common.cycle import load_instance
from models.common.cycle.calendar import attach_calendar_decisions
from models.common.cycle.audit import audit_solution
from models.routing.model_8.alns.state import state_from_result,clone_joint_state
from models.routing.model_8.alns.evaluator import JointEvaluator
ROOT=Path('output/l05_5sat_balanced25_design_20261020');OUT=ROOT/'merge_diagnosis'
DATA=Path('data/mechanism_l05_5sat_balanced25_v1')
def write(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
inputs={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in DATA.rglob('*') if p.is_file()}
config,fleet=load_routing_case(DATA/'M8/case.json',use_pool_config=False);parent=load_instance(config,fleet)
all_rows=[]
for group in ['M8_feasible_start','M8_cold']:
 original=json.load((ROOT/group/'seed_42/result.json').open())
 child=parent.selected_problem(original);assignment=original['order_batch_assignment'];cuts=original['batch_cutoffs']
 fixed=child.project(assignment,original['container_pool_size'],cuts)
 base=state_from_result(fixed,original);base['cutoffs']=dict(cuts)
 ev=JointEvaluator(child,capacity=2048)
 baseline_score,baseline,issues=ev(assignment,base)
 assert not issues and baseline is not None,issues
 assert abs(baseline_score-original['info']['objective'])<1e-6,(baseline_score,original['info']['objective'])
 for offset in [0,10,20]:
  for coupled in [False,True]:
   if offset and not coupled:continue
   state=clone_joint_state(base)
   state['cutoffs']={bid:max(child.cutoff_bounds[bid][0],v-child.operating_policy.ticks(offset)) for bid,v in cuts.items()}
   score,candidate,issues=ev(assignment,state)
   label=f'{group}_'+('coupled' if coupled else 'delivery_only')+f'_cutoff_earlier_{offset}'
   counters=Counter(issues);tested=valid=improved=0;history=[]
   if candidate is None:
    all_rows.append(dict(label=label,valid=False,issues=dict(counters),tested=0))
    continue
   initial_cost=score
   # Greedy best improvement; each iteration exhausts all pair-route merges
   # and both vehicle choices, delivery orders and (for coupled) pickup orders.
   for iteration in range(8):
    best=None
    for b in child.operating_policy.batches:
     rows=state['deliveries'][b.id]
     occupied=[i for i,r in enumerate(rows) if r]
     for a,c in combinations(occupied,2):
      sites=rows[a]+rows[c]
      if len(sites)>3:continue
      if sum(fixed.demands[s,b.id] for s in sites)>fixed.fleet.e1_volume_cap:continue
      if sum(fixed.weights[s,b.id] for s in sites)>fixed.fleet.e1_weight_cap:continue
      for target in (a,c):
       removed=c if target==a else a
       for delivery in permutations(sites):
        pickup_orders=list(permutations(sites)) if coupled else [None]
        for pickup in pickup_orders:
         trial=clone_joint_state(state)
         trial['deliveries'][b.id][target]=list(delivery)
         trial['deliveries'][b.id][removed]=[]
         if coupled:
          for s in sites:trial['pickup_vehicle'][b.id][s]=target
          remainder=[s for s in trial['pickup_order'][b.id] if s not in sites]
          trial['pickup_order'][b.id]=list(pickup)+remainder
         tested+=1
         value,result,violations=ev(assignment,trial)
         if result is None:
          counters.update(violations);continue
         valid+=1
         if value<score-1e-7 and (best is None or value<best[0]):
          best=(value,trial,result,dict(batch=b.id,sites=list(delivery),pickup_order=list(pickup) if pickup else None,target_vehicle=target,removed_delivery_vehicle=removed))
    if best is None:break
    score,state,candidate,change=best;improved+=1
    history.append(dict(iteration=iteration,cost=score,pool=candidate['container_pool_size'],E1_trips=len(candidate['e1_trips']),change=change))
   attach_calendar_decisions(parent,candidate,original['cycle_batch_count'],original['batch_boundaries'])
   candidate['audit']=audit_solution(parent,candidate)
   assert candidate['audit']['valid'],candidate['audit']['errors']
   candidate['diagnostic_method']='Deterministic pair-merge local enumeration, fixed order assignment, calendar boundaries and E2 task order/wait lower bounds; no ALNS, no input or solver changes.'
   write(OUT/(label+'.json'),candidate)
   sitecounts=Counter(len({e['site_id'] for e in t['events'] if e['kind']=='d'}) for t in candidate['e1_trips'])
   a=candidate['audit'];trips_with_delivery=sum(v for k,v in sitecounts.items() if k>0)
   row=dict(label=label,valid=True,earlier_cutoff_minutes=offset,coupled=coupled,
     original_cost=original['info']['objective'],original_N=original['container_pool_size'],initial_cost_for_cutoff=initial_cost,
     final_cost=score,N=candidate['container_pool_size'],E1_trips=a['e1_trip_count'],D1=a['distances']['e1'],D2=a['distances']['e2'],
     departure_volume_util=100/(25*trips_with_delivery),delivery_sites_per_trip=dict(sitecounts),
     two_cycle_valid=a['periodic_replay']['valid'],tested=tested,valid_candidates=valid,accepted_merges=improved,
     encountered_violations=dict(counters),history=history,selected_cutoffs_minutes={k:v/60 for k,v in state['cutoffs'].items()},
     source_result=str(OUT/(label+'.json')))
   all_rows.append(row);write(OUT/'summary.json',all_rows)
   print(json.dumps({k:row[k] for k in ('label','original_cost','final_cost','N','E1_trips','D1','departure_volume_util','accepted_merges')},ensure_ascii=False),flush=True)
write(OUT/'summary.json',all_rows)
changed=[p for p,h in inputs.items() if hashlib.sha256(Path(p).read_bytes()).hexdigest()!=h]
write(OUT/'integrity.json',dict(inputs_unchanged=not changed,changes=changed,input_sha256=inputs))
assert not changed,changed
