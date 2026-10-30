"""Check feasibility of each saved M8 diagnostic schedule in native M9."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from models.common.cycle.assignment import input_assignment
from models.common.cycle.audit import audit_solution
from models.common.cycle.calendar import attach_calendar_decisions
from models.common.cycle.instance import load_instance
from tools.configuration.loader import load_routing_case

root = Path(__file__).resolve().parent
for case in ('S03', 'S04'):
    folder = root/case
    source_path = folder/'M8_fixedK2_seed42_300s/result.json'
    source = json.loads(source_path.read_text(encoding='utf-8'))
    if source['info']['objective'] is None:
        continue
    config, fleet = load_routing_case(folder/'M9_case.json')
    problem = load_instance(config, fleet)
    witness = deepcopy(source)
    witness['assignment'] = input_assignment(problem)
    witness['info'] = dict(status='FEASIBILITY_PROJECTION_ONLY',
        objective=source['info']['objective'], best_bound=None, mip_gap=None,
        proven_optimal=False, proven_infeasible=False,
        proof_scope='feasibility_only_no_M9_optimization')
    attach_calendar_decisions(problem, witness, source['cycle_batch_count'], source['batch_boundaries'])
    audit = audit_solution(problem, witness)
    assert audit['valid'] and audit['periodic_replay']['valid'], audit['errors']
    check = dict(case=case, source_result=str(source_path),
        source_sha256=hashlib.sha256(source_path.read_bytes()).hexdigest(), target_model=9,
        native_count_domain=list(problem.batch_count_candidates), objective=audit['objective'],
        audit_valid=audit['valid'], periodic_replay_valid=audit['periodic_replay']['valid'],
        assignment=witness['assignment'],
        claim='Feasibility of the saved M8 schedule in M9 only; no M9 optimization or optimality claim.')
    (folder/'M8_schedule_in_M9_check.json').write_text(
        json.dumps(check, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    print(json.dumps(check, ensure_ascii=False), flush=True)
