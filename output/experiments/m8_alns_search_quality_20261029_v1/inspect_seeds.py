"""Inspect constructive proposals on one input, with the same root seed 42."""
import random
import sys
import time
from bench import HERE, ROOT, save

sys.path[:0] = [str(HERE / 'sources' / 'temporal'), str(ROOT)]
from models.common.cycle import load_instance
from models.routing.model_8.alns.batch_initialization import temporal_assignments
from models.routing.model_8.alns.calendar_polish import deadline_calendar_seeds
from models.routing.model_8.alns.construction import _best_construction
from models.routing.model_8.alns.evaluator import JointEvaluator
from tools.configuration.loader import load_routing_case

parent = load_instance(*load_routing_case(ROOT / 'data/formal_benchmark27_uniform_7_20_50_v1/model_8/small/S02/case.json', use_pool_config=False))
variable = parent.for_count(2)
ranked = temporal_assignments(variable)
rows = []
for index, (bounds, assignment, cuts) in enumerate(deadline_calendar_seeds(variable)):
    if index >= 16:
        break
    child = parent.fixed_calendar(2, bounds)
    illegal = [oid for oid, bid in assignment.items() if bid not in child.legal_batches[oid]]
    if illegal:
        rows.append(dict(index=index, bounds=bounds, assignment=assignment, cutoffs=cuts, illegal=illegal))
        continue
    cuts = child.proposed_cutoffs({bid: min(hi, max(lo, cuts[bid])) for bid, (lo, hi) in child.cutoff_bounds.items()})
    evaluator = JointEvaluator(child)
    score, state, result, issues = _best_construction(child, evaluator, assignment,
        random.Random(42), time.perf_counter()+1., cutoffs=cuts)
    row = dict(index=index, temporal=assignment in ranked, bounds=bounds, assignment=assignment,
        cutoffs=cuts, score=score, issues=issues, feasible=result is not None,
        N=result.get('container_pool_size') if result else None,
        cost=result['info']['objective'] if result else None)
    rows.append(row)
save(HERE / 'seed_diagnosis.json', rows)
print(rows)
