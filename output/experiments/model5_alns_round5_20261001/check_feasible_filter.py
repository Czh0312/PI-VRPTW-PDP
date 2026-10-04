"""Check proposed screens against independently audited full-cycle candidates."""
import hashlib
import json
from pathlib import Path
import random
import sys

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
sys.path.insert(0, str(ROOT))
from configuration.loader import load_routing_case, apply_container_pool_size
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.alns.evaluator import evaluate, initial_state
from models.routing.solvers.alns.intensification import CycleDescent
from models.routing.solvers.alns.neighborhoods import clone_state, state_key, state_from_result


def main():
    path = ROOT/'models/routing/solvers/alns/intensification.py'
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    rows = [r for r in json.loads((OUT/'before/summary.json').read_text())
            if r['split'] == 'development' and r['seed'] == 42 and r['budget'] == 5]
    assert len(rows) == 8
    records = []
    for row in rows:
        config, fleet = load_routing_case(row['case'], use_pool_config=False)
        config, fleet = apply_container_pool_size(config, fleet, row['pool'])
        problem = load_instance(config, fleet)
        saved = json.loads((OUT/'before'/row['result_file']).read_text())['result']
        warm = state_from_result(problem, saved)
        for route in warm['e2']:
            route.pop('depart_after', None)
            route.pop('return_after', None)
        for label, state in [('initial', initial_state(problem, random.Random(42))), ('free_warm', warm)]:
            _, decoded, issues = evaluate(problem, state)
            if issues:
                records.append(dict(case=row['case_id'], pool=row['pool'], state=label,
                                    original_valid=False, issues=issues))
                continue
            assert audit_solution(problem, decoded)['periodic_replay']['valid']
            descent = CycleDescent(problem)
            options = descent._e2_candidates(state, float('inf'))
            feasible = screened = 0
            for kind, detail in options:
                candidate = clone_state(state)
                for index, tasks in detail:
                    candidate['e2'][index]['tasks'] = list(tasks)
                candidate['e2'] = [r for r in candidate['e2'] if r['tasks']]
                _, result, issues = evaluate(problem, candidate)
                descent.key, descent.queue = state_key(state), [(kind, detail)]
                proposed = descent.propose(state, decoded, float('inf'))
                screened += proposed is None
                if not issues:
                    audit = audit_solution(problem, result)
                    assert audit['valid'] and audit['periodic_replay']['valid']
                    assert proposed == candidate, 'Audited feasible candidate was falsely screened'
                    feasible += 1
            records.append(dict(case=row['case_id'], pool=row['pool'], state=label,
                original_valid=True, candidates=len(options), fully_audited_feasible=feasible,
                screened=screened, feasible_false_rejections=0))
            print(row['case_id'], row['pool'], label, len(options), feasible, flush=True)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
    record = dict(source_sha256=digest, records=records,
        candidate_count=sum(r.get('candidates', 0) for r in records),
        audited_feasible_count=sum(r.get('fully_audited_feasible', 0) for r in records),
        false_rejections=0, claim='Empirical coverage of registered development states, not a universal feasibility certificate')
    (OUT/'feasible_filter_verification.json').write_text(json.dumps(record, ensure_ascii=False, indent=2)+'\n')


if __name__ == '__main__':
    main()
