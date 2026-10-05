"""Compare online selection with the frozen offline diagnostic for all controls."""
from pathlib import Path
import json
import sys
import time
OUT = Path(__file__).resolve().parent
DIAG = OUT.parent / 'alns_single_batch_candidate_diversity_20261005'
sys.path.insert(0, str(OUT / 'after_source'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.solvers.alns.neighborhoods import state_from_result
from models.routing.solvers.alns.single_batch_descent import SingleBatchDescent

checks = []
for item in json.loads((DIAG / 'manifest.json').read_text()):
    config, fleet = load_routing_case(item['case'])
    problem = load_instance({**config, 'model': 3}, fleet)
    raw = json.loads((DIAG / 'incumbents' / item['name']).read_text())
    state = state_from_result(problem, raw['result'])
    rows = json.loads((DIAG / 'candidates' / item['name']).read_text())
    by_rank = {r['rank']: r for r in rows}
    selection = json.loads((DIAG / 'results' / Path(item['name']).stem / 'selection.json').read_text())
    normalize = lambda changes: tuple((i, tuple(tuple(t) for t in tasks)) for i, tasks in changes)
    for enabled, label in [(False, 'baseline'), (True, 'diversity')]:
        actual = SingleBatchDescent(problem, cache_sites=False, cache_distances=True,
                                   diversify=enabled)._enumerate_routes(state['e2'], time.perf_counter()+60)
        expected = [normalize(by_rank[rank]['changes']) for rank in selection[label]]
        exact = [normalize(r[2]) for r in actual] == expected
        checks.append(dict(name=item['name'], enabled=enabled, candidates=len(actual), exact=exact))
(OUT / 'historical_selection_equivalence.json').write_text(json.dumps(checks, indent=2)+'\n')
print('Historical candidate sequences exact:', sum(r['exact'] for r in checks), '/', len(checks))
assert len(checks) == 32 and all(r['exact'] for r in checks)
