"""Compare count-cut pricing/LP to independently enumerated elementary trips."""
from pathlib import Path
import json,math,random,sys,time
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(HERE/'subset_source_snapshot'))
import gurobipy as gp
from tests.routing.test_multibatch_route_cg import enumerate_trips
from tests.routing.test_multibatch_cycle import policy_config
from models.routing.common.cycle import MultiBatchInstance,Order
from models.routing.common.domain.instance import ProblemInstance
from models.routing.common.domain.node import Depot,Satellite,Customer
from models.routing.solvers.exact.multibatch_route_pricing import RoutePricer,route_context
from models.routing.solvers.exact.multibatch_route_cg import route_cover_certificate
nodes={'D':Depot('D',0,0),'S':Satellite('S',0,0)};orders=[]
for oid in ('A','B','C'):
 nodes[oid]=Customer(oid,1,0,0,0,(0,125),(0,125),'S')
 orders.append(Order(oid,oid,'S','b1',1,0,(0,25),(0,29),0,0))
fleet=dict(E1_vehicle_count=1,vehicles_per_satellite=2,E1_Qk=10,E1_Ck=10,E2_Qv=2,E2_Cv=2,container_weight=1,container_volume=1)
cfg=policy_config();cfg.update(container_pool_size=3,e2_max_trips_per_vehicle=1)
p=MultiBatchInstance(ProblemInstance(nodes,fleet),orders,cfg);c=route_context(p,'S');oracle=enumerate_trips(p,'S')
start=time.perf_counter();rng=random.Random(441);checks=0
subsets={0:frozenset([('A','d'),('B','d')]),1:frozenset([('B','d'),('C','d')])}
for phase in (False,True):
 for _ in range(10):
  pi={n['key']:rng.uniform(-10,20) for n in c['nodes']};nu=-rng.uniform(0,5);prizes={'b1':rng.uniform(0,12)}
  subset_duals={sid:rng.uniform(0,10) for sid in subsets}
  expected=min((0 if phase else cost)-sum(pi[k] for k in seq)-nu-(prizes['b1'] if any(k=='d' for _,k in seq) else 0)-sum(subset_duals[sid] for sid,keys in subsets.items() if keys.intersection(seq)) for seq,cost in oracle)
  pricer=RoutePricer(p,c,subset_keys=subsets)
  try:
   r=pricer.price(pi,nu,time_limit=5,phase_one=phase,category_duals=prizes,subset_duals=subset_duals)
   assert r['optimal'] and abs(r['reduced_cost']-expected)<1e-6
   assert r['lower_bound']<=expected+1e-7;checks+=1
  finally:pricer.dispose()
values=[]
for cuts in (False,True):
 m=gp.Model();m.Params.OutputFlag=0
 try:
  x=m.addVars(len(oracle),obj={i:cost for i,(_,cost) in enumerate(oracle)})
  for n in c['nodes']:m.addConstr(gp.quicksum(x[i] for i,(seq,_) in enumerate(oracle) if n['key'] in seq)==1)
  m.addConstr(x.sum()<=2)
  if cuts:m.addConstr(gp.quicksum(x[i] for i,(seq,_) in enumerate(oracle) if any(k=='d' for _,k in seq))>=2)
  m.optimize();assert m.Status==gp.GRB.OPTIMAL;values.append(m.ObjVal)
 finally:m.dispose()
 cert=route_cover_certificate(p,time_limit=5,batch_pricing=True,dual_repair=True,delivery_count_cuts=cuts,heuristic_pricing=False)
 assert cert['status']=='LP_CONVERGED' and abs(cert['sites'][0]['bound']-values[-1])<1e-3
 assert not cert['proven_optimal']
assert values==[3,4],values
from models.routing.solvers.exact.solver import solve_exact
full=solve_exact(p,time_limit=5);assert full['info']['proven_optimal'] and abs(full['info']['objective']-4)<1e-6
(HERE/'subset_oracle.json').write_text(json.dumps(dict(pricing_checks=checks,lp_without=values[0],lp_with=values[1],integer_optimum=4,passed=True,wall_seconds=time.perf_counter()-start))+'\n')
print(dict(pricing_checks=checks,lp_without=values[0],lp_with=values[1],passed=True),flush=True)
