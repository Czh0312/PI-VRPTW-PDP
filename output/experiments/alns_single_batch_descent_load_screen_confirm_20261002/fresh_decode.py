"""Replay every quality result with an uncached decoder and full audit."""
from pathlib import Path
import sys, json
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(OUT/'after_source'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.alns.evaluator import evaluate
from models.routing.solvers.alns.neighborhoods import state_from_result
rows = json.loads((OUT/'quality/summary.json').read_text())
assert len(rows) == 16
checks = []
for row in rows:
    name = '%03d_%s.json' % (row['index'], row['source'])
    raw = json.loads((OUT/'quality'/name).read_text())
    config, fleet = load_routing_case(raw['job']['case'])
    problem = load_instance({**config, 'model': 3}, fleet)
    _, decoded, issues = evaluate(problem, state_from_result(problem, raw['result']))
    audit = audit_solution(problem, decoded)
    original = raw['result']['info']['objective']
    equal = original is not None and abs(decoded['info']['objective']-original) <= 1e-8
    valid = not issues and audit['valid'] and audit['periodic_replay']['valid']
    checks.append(dict(file=name, valid=valid, equal=equal, issues=issues,
                       original=original, decoded=decoded['info']['objective']))
    (OUT/'fresh_decode.json').write_text(json.dumps(checks, indent=2))
    print(name, valid, equal, flush=True)
assert all(r['valid'] and r['equal'] for r in checks)
