"""Short real-case functional checks, not a comparative performance benchmark."""
import sys
import time
from bench import HERE, ROOT, read, save, sha

sys.path.insert(0, str(ROOT))
from models.common.cycle import load_instance
from models.common.cycle.audit import audit_solution
from models.routing.model_8.alns.solver import solve_alns
from tools.configuration.loader import load_routing_case

publication = read(HERE/'publication.json')
assert all(sha(ROOT/path) == digest for path, digest in publication['algorithm_files'].items())
rows = []
for case in ['S02', 'S03', 'S05']:
    p = load_instance(*load_routing_case(ROOT/f'data/formal_benchmark27_uniform_7_20_50_v1/model_8/small/{case}/case.json', use_pool_config=False))
    begun = time.perf_counter()
    result = solve_alns(p, max_time=5., max_iterations=10**9, seed=42)
    elapsed = time.perf_counter()-begun
    audit = audit_solution(p, result)
    assert audit['valid'] and audit['periodic_replay']['valid'] and not result['info']['proven_optimal']
    metrics = {}
    for key in ['timing_repair_attempts', 'timing_repair_successes']:
        assert result['info'][key] == sum(v.get(key, 0) for v in result['count_search_visits'])
        metrics[key] = result['info'][key]
    assert 0 <= metrics['timing_repair_successes'] <= metrics['timing_repair_attempts']
    row = dict(case=case, root_seed=42, seconds=5., wall_seconds=elapsed, valid=True, periodic_valid=True,
               objective=audit['objective'], **metrics)
    save(HERE/f'functional_checks/{case}_result.json', result)
    rows.append(row)
    print(row, flush=True)
assert not any(name.startswith('gurobipy') or (name.startswith('models.') and '.exact' in name) for name in sys.modules)
assert all(sha(ROOT/path) == digest for path, digest in publication['algorithm_files'].items())
save(HERE/'functional_checks.json', dict(purpose='Real-case functional checks, not a paired quality comparison.',
     root_seeds=[42], exact_reference_used=False, source_hashes=publication['algorithm_files'], rows=rows))
