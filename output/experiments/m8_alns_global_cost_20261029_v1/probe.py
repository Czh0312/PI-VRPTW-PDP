"""Deterministic complete-cost refinements of recorded ALNS states; no exact input."""
import json
import sys
import time
from bench import HERE, ROOT, read, save, sources

sys.path[:0] = [str(HERE/'sources/candidate'), str(ROOT)]
from models.common.cycle import load_instance
from tools.configuration.loader import load_routing_case
from models.routing.model_8.alns.construction import _seed_from_result
from models.routing.model_8.alns.evaluator import JointEvaluator
from models.routing.model_8.alns.chain_repair import complete_request_repair, recovery_frontier, _distance

hashes = sources('candidate')
rows = []
for case in [f'S{i:02}' for i in range(1, 10)]:
    p = load_instance(*load_routing_case(ROOT/f'data/formal_benchmark27_uniform_7_20_50_v1/model_8/small/{case}/case.json', use_pool_config=False))
    recorded = read(ROOT/f'output/experiments/m8_alns_small_5s_20261029_v1/runs/{case}/result.json')
    child = p.fixed_calendar(recorded['cycle_batch_count'], recorded['batch_boundaries'])
    _, assignment, state = _seed_from_result(child, recorded)
    ev = JointEvaluator(child)
    original, valid, _ = ev(assignment, state)
    assert valid is not None
    fixed = ev.fixed(assignment, state['cutoffs'])
    ranked = []
    for oid in assignment:
        without = [{**route, 'tasks': [task for task in route['tasks'] if task[0] != oid]} for route in state['e2']]
        ranked.append((_distance(fixed, without), oid))
    orders = [oid for _, oid in sorted(ranked)[:2]]
    begun = time.perf_counter()
    proposed, calls, improvements = complete_request_repair(child, ev, assignment, state, orders, begun+.65, max_evaluations=96)
    direct, _, _ = ev(assignment, proposed)
    polished, profile_calls, profile_improvements = recovery_frontier(child, ev, assignment, proposed, begun+1.)
    score, candidate, issues = ev(assignment, polished)
    assert candidate is not None and candidate['audit']['periodic_replay']['valid'] and score <= original+1e-8
    row = dict(case=case, K=recorded['cycle_batch_count'], before=original, after_direct=direct, after=score,
        wall_seconds=time.perf_counter()-begun, complete_evaluations=calls, complete_improvements=improvements,
        profile_evaluations=profile_calls, profile_improvements=profile_improvements)
    rows.append(row)
    save(HERE/f'probe/{case}.json', dict(summary=row, state=polished, result=candidate))
    print(json.dumps(row, ensure_ascii=False), flush=True)
assert sources('candidate') == hashes
save(HERE/'probe.json', dict(source_hashes=hashes, exact_reference_used=False, cold_ALNS_trial=False,
    extra_root_seeds=0, rows=rows))
