"""Independent full matrices and a strictly stronger overlap-repair example."""
import json,sys,time
from pathlib import Path
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE/'source_snapshot'));sys.path.insert(1,str(HERE));sys.path.insert(2,str(HERE.parents[4]))
from tests.routing.test_multibatch_hybrid import capacity_problem
from models.routing.solvers.exact.multibatch_route_pricing import route_context,RoutePricer
from models.routing.solvers.exact.multibatch_route_cg import site_route_cover,dual_certificate
from models.routing.solvers.exact.multibatch_capacity_dual_repair import repair_capacity_duals
from models.routing.solvers.exact.multibatch_count_dual_repair import repair_count_duals
import validation_capacity as capacity
import validation_conjunction as joint
p=capacity_problem();context=route_context(p,'S');path=HERE/'checker_smoke_models/column_price.mps'
row=site_route_cover(p,context,deadline=time.perf_counter()+3,batch_pricing=True,
                     delivery_count_cuts=True,dual_repair=True,capacity_dual_repair=True,
                     subset_cuts=[[('A','d'),('B','d')],[('B','d'),('C','d')]],pricing_export_path=path)
assert row['lp_converged'] and abs(row['bound']-4)<1e-5
capacity.verify_capacity_site(p,dict(site=row),context)
joint.verify_pricing_models(p,row,context,path)
subsets={i:frozenset((oid,'d') for oid in pair) for i,pair in enumerate([['A','B'],['B','C'],['A','C']])}
pi={n['key']:0.0 for n in context['nodes']};nu=0.;prices={i:1. for i in subsets}
pricer=RoutePricer(p,context,subset_keys=subsets)
try:
    priced=pricer.price(pi,nu,time_limit=3,category_duals={'b1':0.},subset_duals=prices,reuse_budget=True)
    assert priced['optimal']
    repaired=repair_capacity_duals(context,pi,nu,priced,{'b1':(2,0.)},{i:(keys,1,1.) for i,keys in subsets.items()})
    old=repair_count_duals(context,pi,nu,priced,{'b1':(2,0.)})
    assert repaired['repair_mode']=='capacity_rows_lp' and repaired['bound']>old['bound']+.4
    constant=3.;repaired['bound']+=constant
    standard=dual_certificate(pi,nu,context['maximum_trips'],priced['lower_bound'])+constant
    witness=dict(phase='distance',method='integer_pricing',master_objective=4.,
        duals=[[list(k),v] for k,v in pi.items()],fleet_dual=nu,maximum_trips=context['maximum_trips'],
        pricing={k:v for k,v in priced.items() if k not in ('column','columns')},new_column=priced['column'],
        new_columns=priced['columns'],delivery_counts=[['b1',2,0.]],
        subset_counts=[[i,[list(k) for k in sorted(keys)],1,1.] for i,keys in subsets.items()],
        standard_candidate_bound=standard,candidate_bound=max(standard,repaired['bound']),dual_repair=repaired)
    row=dict(satellite='S',bound=max(0.,witness['candidate_bound']),status='DIAGNOSTIC_FIXED_PRICE',
             lp_converged=False,best_witness=0,iterations=[witness],columns=priced['columns'])
    path=HERE/'checker_smoke_models/overlap_price.mps';pricer.model.write(str(path))
    capacity.verify_capacity_site(p,dict(site=row),context)
    joint.verify_pricing_models(p,row,context,path)
finally:pricer.dispose()
(HERE/'checker_smoke.json').write_text(json.dumps(dict(status='VERIFIED',complete_cover_bound=4.,
    fixed_price_capacity_bound=row['bound'],fixed_price_count_bound=max(0.,old['bound']+constant),
    strictly_stronger_fixed_price_example=True,repair_checks=capacity.repair_checks,model_checks=len(joint.models)),indent=2)+'\n')
print('VERIFIED INDEPENDENT CAPACITY REPAIR SMOKE')
