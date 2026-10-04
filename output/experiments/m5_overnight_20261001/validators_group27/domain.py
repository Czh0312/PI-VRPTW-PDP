"""Independent all visiting orders with forward clocks and Floyd-Warshall."""
from pathlib import Path
from itertools import permutations
import sys,json,math,time
HERE=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(HERE/'source_snapshot_group27'))
sys.path.insert(0,str(HERE.parent/'m5_timed_integrality_20261001'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from validation_enumeration import independent_context
started=time.perf_counter();problem=load_instance(*load_routing_case(str(HERE/'medium25/0/inputs/case_relocated.json')))
p,f=problem.operating_policy,problem.fleet
raw=json.loads((HERE/'group27/groups.json').read_text());expected={};checked=0
for site,groups in sorted(raw.items()):
 context=independent_context(problem,site);lookup={n['key']:n for n in context['nodes']};rows=[]
 groups=sorted(tuple(sorted(tuple(k) for k in g)) for g in groups)
 flattened=[k for g in groups for k in g];assert len(set(flattened))==len(flattened)==len(lookup) and set(flattened)==set(lookup)
 def replay(sequence):
  nodes=[lookup[k] for k in sequence];batches={n['batch'] for n in nodes if n['key'][1]=='d'}
  if len(batches)>1:return None
  departure=max(p.cycle_start,max((context['release'][b] for b in batches),default=p.cycle_start))
  w=math.fsum(n['w'] for n in nodes if n['key'][1]=='d');v=math.fsum(n['v'] for n in nodes if n['key'][1]=='d')
  if w>f.e2_weight_cap+1e-6 or v>f.e2_volume_cap+1e-6:return None
  now,previous,delivered,picked,starts,cost=departure,site,{},set(),[],0.0
  for n in nodes:
   oid,kind=n['key'];order=problem.order_map[oid]
   if n['low']>n['high'] or kind=='d' and oid in picked:return None
   now=max(now+problem.travel(previous,n['location']),n['low'])
   if kind=='p' and (oid,'d') in sequence:
    if oid not in delivered:return None
    now=max(now,delivered[oid]+n['service']+n['use'])
   if now>n['high']:return None
   starts.append(now)
   if kind=='d':delivered[oid]=now
   else:picked.add(oid)
   sign=-1 if kind=='d' else 1;w+=sign*n['w'];v+=sign*n['v']
   if not (-1e-6<=w<=f.e2_weight_cap+1e-6 and -1e-6<=v<=f.e2_volume_cap+1e-6):return None
   cost+=problem.distance(previous,n['location']);now+=n['service'];previous=n['location']
  returned=now+problem.travel(previous,site)+p.e2_return_unload_time
  limit=min(p.cycle_end-p.e2_turnaround_time,p.batches[-1].cutoff) if picked else p.cycle_end-p.e2_turnaround_time
  if returned>limit:return None
  return dict(tasks=sequence,cost=cost+problem.distance(previous,site),departure=departure,starts=starts,return_complete=returned)
 for group in groups:
  for sequence in permutations(group):
   checked+=1;r=replay(sequence)
   if r is not None:rows.append(r)
 expected[site]=rows
observed=json.loads((HERE/'group27/models/columns.json').read_text());slots=json.loads((HERE/'group27/models/slots.json').read_text())
for slot,rows in zip(slots,observed):
 independent=expected[slot['satellite_id']];assert len(rows)==len(independent)
 for a,b in zip(rows,independent):
  assert a['tasks']==[list(k) for k in b['tasks']]
  for k in ['cost','departure','return_complete']:assert abs(a[k]-b[k])<1e-6
  assert a['starts']==b['starts']
(HERE/'group27/independent_paths.json').write_text(json.dumps(expected,indent=2)+'\n')
proof=dict(independent_forward_clocks_and_Floyd_bounds=True,permutations_checked=checked,
 distinct_paths=sum(map(len,expected.values())),all_paths_including_cost_dominated_retained=True,
 entire_candidate_domain_independently_equal=True,runtime=time.perf_counter()-started)
(HERE/'group27/domain_validation.json').write_text(json.dumps(proof,indent=2)+'\n');print(json.dumps(proof,indent=2))
