"""Replay paired smoke evidence and embed unchanged fixed schedules in M6."""
from collections import Counter
from copy import deepcopy
import json
import math
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution
from models.routing.common.cycle.joint import lift_result
from models.routing.common.cycle.uniform import attach_uniform_decisions
from tools.data.build_model5_model6_shared_dataset import load_shared_suite
from tools.data.build_model6_uniform_dataset import digest, write_json


def main():
    out=Path(__file__).resolve().parent
    manifest=load_shared_suite(ROOT/'data/model5_model6/shared_v1_20261005/manifest.json')
    records={r['name']:r for r in manifest['cases']}
    studies=('core_smoke','stress_smoke','exact_control')
    rows=[]
    lifted=[]
    fixed_replays=[]
    for study in studies:
        directory=out/study
        batch=json.loads((directory/'rows.json').read_text())
        plan=json.loads((directory/'experiment.json').read_text())
        jobs={j['run']:j for j in plan['jobs']}
        assert len(batch)==plan['job_count']
        for file,expected in plan['source_hashes'].items():
            assert digest(directory/'source_snapshot'/file)==expected
        for row in batch:
            rows.append(dict(study=study,**row))
            if not row['valid']:
                continue
            raw=json.loads((directory/row['run']/'result.json').read_text())
            problem=load_instance(*load_routing_case(jobs[row['run']]['case_path'],use_pool_config=False))
            audited=audit_solution(problem,raw)
            assert audited['valid'],(study,row['run'],audited['errors'])
            assert audited['periodic_replay']['valid'] and audited['periodic_replay']['cycles']==2
            record=records[row['case']]
            if row['treatment']=='M5_distance':
                uniform=load_instance(*load_routing_case(record['m6_case'],use_pool_config=False))
                fixed=load_instance(*load_routing_case(record['m6_fixed_policy_case'],use_pool_config=False))
                assignment={o.id:o.batch for o in problem.orders}
                cuts={b.id:b.cutoff for b in problem.operating_policy.batches}
                checks=[]
                for target in (fixed,uniform.for_count(record['reference_batch_count'])):
                    physical=deepcopy(raw)
                    physical['info'].update(proven_optimal=False,best_bound=None,mip_gap=None)
                    result=lift_result(target,physical,assignment,problem.total,'fixed_schedule_replay',cuts)
                    assert result['audit']['valid'],result['audit']['errors']
                    assert math.isclose(result['cost']['total'],row['cost'],rel_tol=1e-9,abs_tol=1e-6)
                    if target is not fixed:
                        attach_uniform_decisions(uniform,result,record['reference_batch_count'])
                        checked=audit_solution(uniform,result)
                        assert checked['valid'],checked['errors']
                    checks.append(True)
                lifted.append(dict(study=study,run=row['run'],case=row['case'],
                    same_routes_and_times=True,cost_identical=True,
                    fixed_control_valid=checks[0],uniform_m6_valid=checks[1]))
            if row['treatment']=='M6_fixed_policy_cost':
                baseline=load_instance(*load_routing_case(record['m5_case'],use_pool_config=False))
                physical=deepcopy(raw)
                physical.update(model=5,objective_mode='distance')
                physical['info'].update(objective=row['distance'],proven_optimal=False,best_bound=None,mip_gap=None)
                checked=audit_solution(baseline,physical)
                assert checked['valid'],checked['errors']
                fixed_replays.append(dict(study=study,run=row['run'],case=row['case'],m5_valid=True))
    exact=json.loads((out/'exact_control/rows.json').read_text())
    by_arm={r['treatment']:r for r in exact}
    assert all(r['valid'] and r['proven_optimal'] for r in exact)
    assert by_arm['M6_uniform']['cost']<=by_arm['M6_fixed_policy_cost']['cost']+1e-6
    assert by_arm['M6_fixed_policy_cost']['cost']<=by_arm['M5_distance']['cost']+1e-6
    tests=ET.parse(out/'pytest.xml').getroot()
    test_counts={k:sum(int(s.attrib.get(k,0)) for s in tests.iter('testsuite'))
                 for k in ('tests','failures','errors','skipped')}
    assert not any(test_counts[k] for k in ('failures','errors','skipped'))
    evidence=dict(dataset_version=manifest['dataset_version'],
        semantic_dataset_sha256=manifest['semantic_dataset_sha256'],
        tests=test_counts,reproduction=json.loads((out/'reproduction.json').read_text()),
        input_audit={k:v for k,v in json.loads((out/'audit/audit.json').read_text()).items() if k!='rows'},
        planned_runs=len(rows),valid_runs=sum(r['valid'] for r in rows),
        statuses=dict(Counter(r['status'] for r in rows)),
        proven_infeasible_runs=sum(r['proven_infeasible'] for r in rows),
        unresolved_runs=[{k:r[k] for k in ('study','case','treatment','solver','seed','status')}
                         for r in rows if not r['valid'] and not r['proven_infeasible']],
        all_valid_schedules_independently_replayed_two_cycles=True,
        m5_schedule_embeddings=lifted,fixed_cost_schedule_m5_replays=fixed_replays,
        exact_control_proof_scopes={r['treatment']:r['proof_objective'] for r in exact},
        exact_control_cost_order_verified=True,
        python_version=sys.version,verifier_sha256=digest(Path(__file__)),
        limitations=['Engineering validation only; no convergence, causal, population or business-effect claim',
                     'Original failed solver attempts are preserved even if a different arm yields a feasible schedule',
                     'M5 reference failures are retained; report feasibility denominators with cost comparisons'])
    write_json(out/'verification.json',evidence)
    print(json.dumps({k:evidence[k] for k in ('tests','planned_runs','valid_runs','statuses','proven_infeasible_runs','unresolved_runs')},indent=2))
    print(f'Unchanged M5 schedules embedded in both cost models: {len(lifted)}; fixed-cost schedules valid in M5: {len(fixed_replays)}')


if __name__=='__main__':
    main()
