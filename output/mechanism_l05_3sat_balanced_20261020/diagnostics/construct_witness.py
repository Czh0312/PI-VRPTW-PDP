"""Deterministic feasibility witness; not a solver and not used by cold runs."""
from pathlib import Path
import json,math,itertools,collections
from tools.configuration.loader import load_routing_case
from models.common.cycle import load_instance
from models.common.cycle.calendar import attach_calendar_decisions
from models.common.cycle.audit import audit_solution
from models.routing.model_8.alns.evaluator import JointEvaluator

ROOT=Path('output/mechanism_l05_3sat_balanced_20261020')
config,fleet=load_routing_case('data/mechanism_l05_3sat_balanced_v1/M8/case.json',use_pool_config=False)
parent=load_instance(config,fleet);child=parent.fixed_calendar(3);p=child.operating_policy
orders=json.load(open('data/mechanism_l05_3sat_balanced_v1/reference_orders.json'))
assignment={o['order_id']:o['delivery_batch_id'] for o in orders}
cutoffs={b.id:b.start+p.ticks(200) for b in p.batches}
fixed=child.project(assignment,100,cutoffs)
state=dict(deliveries={b.id:[[s] for s in fixed.satellites]+[[],[]] for b in p.batches},
 pickup_order={b.id:list(fixed.satellites) for b in p.batches},
 pickup_vehicle={b.id:{s:i for i,s in enumerate(fixed.satellites)} for b in p.batches},e2=[],cutoffs=cutoffs)
for b in p.batches:
 for s in fixed.satellites:
  remaining={o.id:o for o in fixed.orders if o.batch==b.id and o.satellite==s}
  groups=[]
  # Exactly ceil(n/3) short routes, then use extra vehicles for pairs where possible.
  ngroups=min(8,len(remaining)); sizes=[len(remaining)//ngroups+(i<len(remaining)%ngroups) for i in range(ngroups)]
  for size in sorted(sizes,reverse=True):
   first=min(remaining,key=lambda oid:(fixed.order_map[oid].delivery_window[0],oid))
   group=[remaining.pop(first)]
   while len(group)<size:
    oid=min(remaining,key=lambda oid:(min(fixed.distance(remaining[oid].customer,o.customer) for o in group),oid))
    group.append(remaining.pop(oid))
   best=None
   for sequence in itertools.permutations(group):
    t=max(b.start+p.e1_load_time+fixed.travel(fixed.depot,s)+fixed.service(s)+p.e2_dispatch_prep_time,
          min(o.delivery_window[0]-fixed.travel(s,o.customer) for o in sequence))
    last=s;delivered={};good=True
    for o in sequence:
     t=max(t+fixed.travel(last,o.customer),o.delivery_window[0]);good &= t<=o.delivery_window[1]
     delivered[o.id]=t;t+=o.service_time;last=o.customer
    for pickup in itertools.permutations(group):
     tt=t;ll=last;gg=good
     for o in pickup:
      tt=max(tt+fixed.travel(ll,o.customer),o.pickup_window[0],delivered[o.id]+o.service_time+o.min_use_time)
      gg &= tt<=o.pickup_window[1];tt+=o.service_time;ll=o.customer
     end=tt+fixed.travel(ll,s)+p.e2_return_unload_time
     if gg and (best is None or end<best[0]):best=(end,sequence,pickup)
   assert best is not None
   _,ds,ps=best
   groups.append([(o.id,'d') for o in ds]+[(o.id,'p') for o in ps])
  for v,tasks in enumerate(groups):state['e2'].append(dict(satellite=s,vehicle=v,tasks=tasks))
evaluator=JointEvaluator(child)
score,result,issues=evaluator(assignment,state)
(ROOT/'diagnostics/witness_diagnostics.json').write_text(json.dumps(dict(score=score,issues=issues),indent=2))
print('construction issues:',collections.Counter(issues))
if result is not None:
 attach_calendar_decisions(parent,result,3,parent.balanced_boundaries(3))
 result['audit']=audit_solution(parent,result)
 assert result['audit']['valid'],result['audit']['errors']
 result['witness_origin']='Deterministic 3-wave grouping, max 3 orders per route, no ALNS, no input modification; constructed after cold-start M8 failure for diagnosis.'
 (ROOT/'diagnostics/witness_M8_K3.json').write_text(json.dumps(result,indent=2))
 print('AUDITED WITNESS',result['container_pool_size'],result['info']['objective'])
