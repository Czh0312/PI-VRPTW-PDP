from pathlib import Path
import json,random,sys,time
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(HERE/'source_snapshot'))
from tests.routing.test_multibatch_route_cg import enumerate_trips,detour_problem
from tests.routing.test_multibatch_departure_loads import nonmetric_problem
from tests.routing.test_multibatch_joint_exchanges import five_site_fixture,full_slot_fixture
from models.routing.solvers.exact.multibatch_route_pricing import RoutePricer,route_context
count=0;start=time.perf_counter()
for p in [nonmetric_problem(i) for i in range(6)]+[detour_problem(),five_site_fixture()[0],full_slot_fixture()[0]]:
    for site in p.satellites:
        c=route_context(p,site)
        if not c['nodes']:continue
        oracle=enumerate_trips(p,site)
        for phase in (False,True):
            for linear in (False,True):
                rng=random.Random(100+count);duals={n['key']:rng.uniform(-5,20) for n in c['nodes']};nu=-2.5
                expected=min((0 if phase else cost)-sum(duals[k] for k in seq)-nu for seq,cost in oracle)
                r=RoutePricer(p,c,connectivity=True,linear_links=linear)
                try:
                    saved=r.price(duals,nu,time_limit=5,phase_one=phase)
                    assert saved['optimal'] and abs(saved['reduced_cost']-expected)<1e-6
                    assert saved['lower_bound']<=expected+1e-7
                    assert saved['column']['tasks'] in {seq for seq,_ in oracle}
                    count+=1
                finally:r.dispose()
(HERE/'flow_oracle.json').write_text(json.dumps(dict(pricing_checks=count,passed=True,wall_seconds=time.perf_counter()-start))+'\n')
print(dict(pricing_checks=count,passed=True),flush=True)
