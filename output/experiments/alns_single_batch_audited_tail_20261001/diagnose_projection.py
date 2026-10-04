"""Compare preserved suffix seeds and original projection on saved incumbents.

This is exploratory diagnosis of old results, not equal-time quality evidence.
"""
import json,sys,time
from pathlib import Path

out = Path(__file__).resolve().parent
sys.path.insert(0,str(out.parents[2]))
from diagnostic_helpers import _tail_options,_tail_state
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.alns.neighborhoods import state_from_result
from models.routing.solvers.alns.construction import reassigned_state
from models.routing.solvers.alns.single_batch_cache import SingleBatchCache

old = out.parent/'alns_single_batch_route_recombine_20261001'
jobs = json.loads((old/'quality_manifest.json').read_text())['jobs']
rows = []
for index,job in enumerate(jobs):
    phase = 'quality_missing_inputs' if index < 3 else 'quality'
    raw = json.loads((old/phase/('%03d_before_source.json'%index)).read_text())
    config,fleet = load_routing_case(job['case'])
    p = load_instance({**config,'model':2},fleet)
    incumbent = raw['result'];current = incumbent['assignment']
    state = state_from_result(p,incumbent)
    options = _tail_options(p,current,state,time.perf_counter()+30)
    observations = []
    for option in options[:3]:
        assignment,fixed,seed = _tail_state(p,current,state,option)
        cache = SingleBatchCache(fixed)
        score,direct,issues = cache(seed)
        audit = audit_solution(fixed,direct)
        projection = reassigned_state(fixed,incumbent,time.perf_counter()+10,scorer=cache.route_scorer)
        ps,pr,pi = cache(projection)
        pa = audit_solution(fixed,pr)
        observations.append(dict(geometry_delta=option[0],direct_objective=score,direct_valid=audit['valid'],
                                 direct_issues=issues,projected_objective=ps,projected_valid=pa['valid'],
                                 projected_issues=pi,same_tasks=projection['e2']==seed['e2']))
    row = dict(index=index,family=job['family'],seed=job['seed'],old_objective=incumbent['info']['objective'],
               geometric_options=len(options),observations=observations)
    rows.append(row)
    print(index,job['family'],len(options),[(r['direct_valid'],round(r['direct_objective']-row['old_objective'],3),r['projected_valid'],round(r['projected_objective']-row['old_objective'],3)) for r in observations],flush=True)
(out/'projection_diagnosis.json').write_text(json.dumps(rows,indent=2))
