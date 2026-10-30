"""Input-only construction diagnostics; no exact plans or objective references."""
import json
import random
import sys
import time
from bench import HERE, ROOT, save, sources

sys.path[:0] = [str(HERE/'sources/candidate_v3'), str(ROOT)]
from models.common.cycle import load_instance
from tools.configuration.loader import load_routing_case
from models.routing.model_8.alns.calendar_polish import deadline_calendar_seeds
from models.routing.model_8.alns.construction import _best_construction
from models.routing.model_8.alns.evaluator import JointEvaluator
from models.routing.model_8.alns.route_beam import beam_routes, _station_plans
from models.routing.model_8.alns.small_repair import polish_small

frozen = sources('candidate_v3')
rows = []
for case, k in [('S02', 2), ('S03', 3), ('S05', 2)]:
    parent = load_instance(*load_routing_case(ROOT/f'data/formal_benchmark27_uniform_7_20_50_v1/model_8/small/{case}/case.json', use_pool_config=False))
    for index, (bounds, assignment, cuts) in enumerate(deadline_calendar_seeds(parent.for_count(k))):
        if index >= 2:
            break
        child = parent.fixed_calendar(k, bounds)
        cuts = child.proposed_cutoffs({bid: min(hi, max(lo, cuts[bid])) for bid, (lo, hi) in child.cutoff_bounds.items()})
        ev = JointEvaluator(child)
        score, state, result, issues = _best_construction(child, ev, assignment, random.Random(42), time.perf_counter()+1., cutoffs=cuts)
        row = dict(case=case, K=k, index=index, assignment=assignment, before=score if result else None, issues=list(issues))
        if result is not None:
            begun = time.perf_counter()
            rebuilt = beam_routes(child, ev, assignment, state, time.perf_counter()+2.)
            after, candidate, issues = ev(assignment, rebuilt)
            row.update(beam=after if candidate else None, beam_wall=time.perf_counter()-begun,
                       beam_N=candidate['container_pool_size'] if candidate else None)
            polished, calls, improvements = polish_small(child, ev, assignment, rebuilt, time.perf_counter()+1.)
            value, final, issues = ev(assignment, polished)
            row.update(polished=value if final else None, calls=calls, improvements=improvements)
            save(HERE/f'construction_probe/{case}_{index}.json', dict(row=row, state=polished, result=final))
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
assert sources('candidate_v3') == frozen
save(HERE/'construction_probe.json', dict(source_hashes=frozen, root_seeds=[42],
    exact_reference_used=False, full_ALNS_trials=False, rows=rows))
