"""Actual small-case execution of both live-CG budget profiles."""
from copy import deepcopy
import json,sys
from pathlib import Path
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE/'candidate_source'))
sys.path.insert(1,str(HERE.parents[4]))
import models.routing.service as service
from models.routing.solvers.exact.multibatch_hybrid import solve_hybrid
from models.routing.common.cycle.audit import audit_solution
from tests.routing.test_multibatch_cycle import fixture,hand_solution
p=fixture(pool=2);initial=hand_solution(p)
service.solve_routing=lambda **kw:deepcopy(initial)
rows=[]
for name,options in [('subsets25',dict(cover_time_limit=1.25,conditional_time_limit=.3,count_dual_repair=True,capacity_dual_repair=True)),
                     ('subsets0',dict(cover_time_limit=1.25,conditional_time_limit=.3,count_dual_repair=True,capacity_dual_repair=True,subset_time_fraction=0))]:
    out=solve_hybrid(p,time_limit=2,primal_time_limit=0,adaptive_pricing_mode='resume',**options)
    audit=audit_solution(p,out['result'])
    assert audit['valid'] and audit['periodic_replay']['valid']
    assert out['bound']<=out['upper_bound']+1e-6 and out['target_reached']
    rows.append(dict(profile=name,upper=out['upper_bound'],lower=out['bound'],gap=out['gap'],physical_and_periodic_audit=True))
(HERE/'smoke_profiles.json').write_text(json.dumps(dict(status='PASSED',cases=rows),indent=2)+'\n')
print(rows)
