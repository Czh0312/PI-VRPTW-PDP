"""Replay new count-dual certificates with the independent frozen checker."""
import json,sys,time
from pathlib import Path
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE/'candidate_source'));sys.path.insert(1,str(HERE));sys.path.insert(2,str(HERE.parents[4]))
from tests.routing.test_multibatch_hybrid import capacity_problem
from models.routing.solvers.exact.multibatch_route_pricing import route_context
from models.routing.solvers.exact.multibatch_route_cg import site_route_cover
import validation_capacity as capacity
import validation_conjunction as joint
p=capacity_problem();context=route_context(p,'S');path=HERE/'checker_smoke_models/price.mps'
row=site_route_cover(p,context,deadline=time.perf_counter()+3,batch_pricing=True,
                     delivery_count_cuts=True,dual_repair=True,count_dual_repair=True,pricing_export_path=path)
assert row['lp_converged'] and abs(row['bound']-4)<1e-5
capacity.verify_capacity_site(p,dict(site=row),context)
joint.verify_pricing_models(p,row,context,path)
(HERE/'checker_smoke.json').write_text(json.dumps(dict(status='VERIFIED',bound=row['bound'],repair_checks=capacity.repair_checks,model_checks=len(joint.models)),indent=2)+'\n')
print('VERIFIED INDEPENDENT COUNT REPAIR SMOKE')
