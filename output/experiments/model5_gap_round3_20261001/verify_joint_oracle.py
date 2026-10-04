"""Check the conditional-context and joint-bound construction against tiny full M5 optima."""
from pathlib import Path
import ast,json,math,sys,time
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(HERE/'subset_source_snapshot'))
from tests.routing.test_multibatch_departure_loads import nonmetric_problem
from models.routing.solvers.exact.solver import solve_exact
from models.routing.solvers.exact.multibatch_e1_routes import build_e1_routes,set_route_projection
from models.routing.solvers.exact.multibatch_route_pricing import route_context
from models.routing.solvers.exact.multibatch_route_cg import site_route_cover
from models.routing.common.cycle.precheck import travel_lower_bounds
from models.routing.common.cycle.audit import audit_solution
import gurobipy as gp

def read(p):return json.loads(Path(p).read_text())
def write(p,v):p.write_text(json.dumps(v,indent=2,ensure_ascii=False,allow_nan=False)+'\n')
source=HERE/'run_joint_conditional.py';tree=ast.parse(source.read_text())
functions=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in ('context_at','joint')]
exec(compile(ast.Module(body=functions,type_ignores=[]),str(source),'exec'))
dest=HERE/'joint_oracle';dest.mkdir(exist_ok=False);rows=[]
for seed in (0,2,5):
 p=nonmetric_problem(seed);full=solve_exact(p,time_limit=5)
 assert full['info']['proven_optimal'] and audit_solution(p,full)['valid']
 baselines={}
 for site in p.satellites:
  row=site_route_cover(p,route_context(p,site),deadline=time.perf_counter()+5,batch_pricing=True,dual_repair=True,delivery_count_cuts=True)
  baselines[site]=row['bound']
 site=next(s for s in p.satellites if any(o.satellite==s for o in p.orders));bid=next(o.batch for o in p.orders if o.satellite==site)
 travel=travel_lower_bounds(p);threshold=p.batch_map[bid].start+p.operating_policy.e1_load_time+travel['outward'][p.depot][site]+p.service(site)+3
 c=context_at(p,site,bid,threshold)
 conditional=site_route_cover(p,c,deadline=time.perf_counter()+5,batch_pricing=True,dual_repair=True,delivery_count_cuts=True)
 job=dict(site=site,batch=bid,threshold=threshold)
 folder=dest/str(seed);folder.mkdir();proof=joint(p,[dict(job=job,site=conditional)],full,baselines,folder)
 assert proof['bound']<=full['info']['objective']+1e-6,(seed,proof,full['info'])
 rows.append(dict(seed=seed,full_optimum=full['info']['objective'],joint_bound=proof['bound'],conditional_site_bound=conditional['bound']))
write(HERE/'joint_oracle.json',dict(passed=True,cases=rows))
print(dict(passed=True,cases=rows),flush=True)
