"""Controlled cooling-only perturbation in archived sources; no production edits."""
from pathlib import Path
import hashlib
import importlib
import json
import os
import sys
import time

OUT = Path(__file__).resolve().parent
job = json.loads((OUT / 'jobs.json').read_text())[int(sys.argv[1])]
sys.path.insert(0, str(OUT / job['source']))
module = importlib.import_module('models.routing.solvers.alns.single_batch')
text = Path(module.__file__).read_text()
needle = '(time.perf_counter()-begun)/max_time))'
assert text.count(needle) == 1
text = text.replace(needle, '((iteration+1)/max(1, max_iterations))*_diagnostic_scale))')
needle = '        if accept:\n'
assert text.count(needle) == 1
trace = '''        _diagnostic_rows.append(dict(
            iteration=iterations, op=op, current_key=_diagnostic_key(current),
            proposed_key=_diagnostic_key(proposed), score=score, new_score=new_score,
            progress=progress, temperature=temperature, accepted=accept))
'''
text = text.replace(needle, trace + needle)
exec(compile(text, str(module.__file__) + ':controlled_cooling', 'exec'), module.__dict__)
module._diagnostic_scale = job['clock_scale']
module._diagnostic_rows = []
module._diagnostic_key = lambda state: hashlib.sha256(repr(module.state_key(state)).encode()).hexdigest()
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution
from models.routing.service import solve_routing

config, fleet = load_routing_case(job['case'])
problem = load_instance({**config, 'model': job['model']}, fleet)
load = list(os.getloadavg())
begun = time.perf_counter()
result = solve_routing(model=3, method='alns', problem=problem, alns_time=job['budget'],
                       alns_iter=job['iterations'], seed=job['seed'])
wall = time.perf_counter() - begun
audit = audit_solution(problem, result)
print(json.dumps(dict(job=job, wall=wall, load=load, valid=audit['valid'],
    periodic=audit['periodic_replay']['valid'], iterations=result['info']['iterations'],
    objective=result['info']['objective'], e1=result['e1_trips'], e2=result['e2_trips'],
    assignment=result.get('assignment'), trace=module._diagnostic_rows,
    independent_audit=audit, result_info=result['info'])))
