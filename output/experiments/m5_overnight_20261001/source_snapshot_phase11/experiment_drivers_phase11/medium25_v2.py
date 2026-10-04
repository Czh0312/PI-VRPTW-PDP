from pathlib import Path
from copy import deepcopy
import sys,json,time,hashlib,os
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE/'source_snapshot_medium25'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.exact.multibatch_cover_pool import reoptimize_cover_pool,preserve_cover_routes
from models.routing.solvers.exact.multibatch_route_pool import expanded_trip_slots,candidate_pools

def write(path,value):path.write_text(json.dumps(value,indent=2,ensure_ascii=False,allow_nan=False)+'\n')
def checkpoint(**kw):
 c=json.loads((HERE/'checkpoint.json').read_text());c.update(kw);write(HERE/'checkpoint.json',c)
manifest=json.loads((HERE/'manifest_medium25_input.json').read_text())['sha256']
for path,digest in manifest.items():assert hashlib.sha256(Path(path).read_bytes()).hexdigest()==digest
params=dict(time_limit=90,seed=42,max_columns=64,transfers=True,exchanges=True,recombinations=False,additional_columns_per_slot=32,extra_slots_per_vehicle=1)
checkpoint(status='medium25_preflight',active_process=os.getpid())
for i in [0,1]:
 out=HERE/'medium25'/str(i)
 problem=load_instance(*load_routing_case(str(out/'inputs/case_relocated.json')))
 original=load_instance(*load_routing_case(str(out/'inputs/case.json')))
 assert problem.orders==original.orders and vars(problem.fleet)==vars(original.fleet) and problem.operating_policy==original.operating_policy
 assert problem.satellites==original.satellites and problem.depot==original.depot
 for a in [problem.depot]+list(problem.satellites)+[o.customer for o in problem.orders]:
  for b in [problem.depot]+list(problem.satellites)+[o.customer for o in problem.orders]:
   assert problem.distance(a,b)==original.distance(a,b) and problem.travel(a,b)==original.travel(a,b)
 initial=json.loads((out/'inputs/initial_result.json').read_text());audit=audit_solution(problem,initial);assert audit['valid']
 covers=json.loads((out/'covers.json').read_text());proposals=json.loads((out/'proposals.json').read_text())
 layout=expanded_trip_slots(problem,initial,1);allproposals=deepcopy(proposals)
 for trip in initial['e2_trips']:
  for kind in ('d','p'):
   keys=[(e['order_id'],kind) for e in trip['events'] if e['kind']==kind]
   for a in range(len(keys)):
    for b in range(a+1,len(keys)+1):allproposals[trip['satellite_id']].append(dict(tasks=keys[a:b]))
 pools=candidate_pools(problem,layout,max_columns=64,transfers=True,exchanges=True,recombinations=False,
  additional_columns=allproposals,additional_columns_per_slot=32,balanced_extra=True)
 pinned,novelty=preserve_cover_routes(problem,layout,pools,covers)
 write(out/'preflight_slots.json',layout['e2_trips']);write(out/'baseline_columns.json',pools);write(out/'preserved_columns.json',pinned)
 write(out/'preflight.json',dict(**novelty,relocated_business_identical=True,initial_audit_valid=True,upper_before=audit['objective']))
 write(out/'parameters.json',dict(**params,same_input=True,same_initial=True,same_total_budget=True,sequential=True,scope='restricted_candidate_full_upper_only',complete_universe_used_only_as_proposals=True))
 variants=[('control',{})]+([('preserved',covers)] if novelty['added_columns'] else [])
 for name,protection in variants:
  target=out/name;target.mkdir();checkpoint(status=f'medium25_{i}_{name}',active_process=os.getpid())
  begun=time.perf_counter();result=reoptimize_cover_pool(problem,initial,protection,**params,additional_columns=proposals,diagnostics_dir=target/'models')
  actual=json.loads((target/'models/columns.json').read_text());expected=pools if name=='control' else pinned
  assert actual==json.loads(json.dumps(expected)), 'Generation deadline altered comparison candidate set'
  assert json.loads((target/'models/slots.json').read_text())==layout['e2_trips']
  checked=audit_solution(problem,result);assert checked['valid']
  write(target/'result.json',result);write(target/'audit.json',checked)
  write(target/'summary.json',dict(objective=checked['objective'],runtime=time.perf_counter()-begun,info=result['info'],candidate_matrix_matches_preflight=True,full_proven_optimal=False,full_proven_infeasible=False))
  print(i,name,checked['objective'],result['info']['runtime'],flush=True)
 comparison=dict(preflight=novelty,parameters=params,initial_upper=audit['objective'],full_proven_optimal=False,
  variants={name:json.loads((out/name/'summary.json').read_text()) for name,_ in variants},prior_global_lower=json.loads((out/'prior_bound.json').read_text())['bound'])
 if not novelty['added_columns']:comparison['preservation_skipped']='No new candidate columns; duplicate solve avoided'
 write(out/'comparison.json',comparison)
checkpoint(status='medium25_trials_complete',active_process=None)
