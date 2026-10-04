"""Reconstruct each saved route with an uncached decoder, then audit both model scopes."""
from pathlib import Path
import sys,json
OUT=Path(__file__).resolve().parent;sys.path.insert(0,str(OUT/'after_source'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.assignment import assigned_problem,lift_result
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.alns.evaluator import evaluate
from models.routing.solvers.alns.neighborhoods import state_from_result
rows=json.loads((OUT/'quality/summary.json').read_text());assert len(rows)==32
checks=[];(OUT/'fresh_decodes').mkdir(exist_ok=True)
for row in rows:
    name='%03d_%s.json'%(row['index'],row['source']);raw=json.loads((OUT/'quality'/name).read_text())
    config,fleet=load_routing_case(raw['job']['case']);base=load_instance({**config,'model':raw['job']['model']},fleet)
    problem=assigned_problem(base,raw['result']['assignment']) if base.free_assignment else base
    _,decoded,issues=evaluate(problem,state_from_result(problem,raw['result']))
    fixed_audit=audit_solution(problem,decoded)
    if base.free_assignment:decoded=lift_result(base,decoded,raw['result']['assignment'])
    audit=audit_solution(base,decoded);original=raw['result']['info']['objective']
    equal=original is not None and abs(decoded['info']['objective']-original)<=1e-8
    valid=not issues and fixed_audit['valid'] and fixed_audit['periodic_replay']['valid'] and audit['valid'] and audit['periodic_replay']['valid']
    record=dict(file=name,model=raw['job']['model'],valid=valid,equal=equal,issues=issues,original=original,decoded=decoded['info']['objective'])
    (OUT/'fresh_decodes'/name).write_text(json.dumps(dict(check=record,result=decoded,fixed_audit=fixed_audit,audit=audit),indent=2))
    checks.append(record);(OUT/'fresh_decode.json').write_text(json.dumps(checks,indent=2));print(name,valid,equal,flush=True)
assert all(r['valid'] and r['equal'] for r in checks)
