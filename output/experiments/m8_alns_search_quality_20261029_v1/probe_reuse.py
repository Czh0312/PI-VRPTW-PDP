"""One bounded route repair of an input-generated chronological seed (seed 42)."""
import random
import sys
import time
from bench import HERE, ROOT, save

sys.path[:0] = [str(HERE / 'sources' / 'reuse'), str(ROOT)]
from models.common.cycle import load_instance
from models.routing.model_8.alns.calendar_polish import deadline_calendar_seeds
from models.routing.model_8.alns.construction import _best_construction
from models.routing.model_8.alns.evaluator import JointEvaluator
from models.routing.model_8.alns.polish import polish_routes
from tools.configuration.loader import load_routing_case

p = load_instance(*load_routing_case(ROOT / 'data/formal_benchmark27_uniform_7_20_50_v1/model_8/small/S02/case.json', use_pool_config=False))
bounds, assignment, cuts = next(deadline_calendar_seeds(p.for_count(2)))
child = p.fixed_calendar(2, bounds)
cuts = child.proposed_cutoffs({bid: min(hi, max(lo, cuts[bid])) for bid, (lo, hi) in child.cutoff_bounds.items()})
ev = JointEvaluator(child)
score, state, result, issues = _best_construction(child, ev, assignment, random.Random(42), time.perf_counter()+1., cutoffs=cuts)
assert result is not None
begun = time.perf_counter()
polished, calls, improved = polish_routes(ev.fixed(assignment, state['cutoffs']), ev, assignment, state, begun+.8)
value, after, errors = ev(assignment, polished)
assert after is not None
row = dict(seed=42, before=score, after=value, elapsed=time.perf_counter()-begun, calls=calls,
    improvements=improved, before_N=result['container_pool_size'], after_N=after['container_pool_size'],
    before_parts=result['cost_components'], after_parts=after['cost_components'])
save(HERE/'reuse_probe.json', row)
save(HERE/'reuse_probe_result.json', after)
print(row)
