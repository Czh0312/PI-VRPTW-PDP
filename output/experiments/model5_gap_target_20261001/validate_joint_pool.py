"""Extend the already verified global certificate record with new audited UBs."""
import hashlib
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
POOL = HERE/'joint_route_pool_120s'
sys.path.insert(0, str(HERE/'diagnostic_60s/source_snapshot'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution
from validation_columns import replay_column, close


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


previous = read(HERE/'validation.json')
assert previous['diagnostic_cases'] == 4 and len(previous['certificates']) == 6
for name, digest in previous['artifact_sha256'].items():
    # The registered plan is extended as experiments progress; the inputs,
    # outputs and proof artifacts of completed stages must remain unchanged.
    if name != 'plan.json':
        assert sha(HERE/name) == digest, name
manifest = read(POOL/'manifest.json')
for name, digest in manifest['source_sha256'].items():
    assert sha(POOL/'source_snapshot'/name) == digest
cases = previous['cases']
audits, column_count = 0, 0
for case in cases:
    key = f"{case['customers']}_{case['pool']}"
    saved = read(POOL/(key+'.json'))
    assert saved['job']['global_lower_bound'] == case['lower']
    candidate_config, candidate_fleet = load_routing_case(saved['job']['case'])
    cert_payload = read(case['certificate_path'])
    certified_config, certified_fleet = load_routing_case(cert_payload['job']['case'])
    assert candidate_fleet == certified_fleet
    for field in ('data', 'orders', 'distance_matrix', 'duration_matrix', 'dataset_metadata'):
        if candidate_config.get(field):
            def original_path(value):
                path = Path(value)
                return path if path.is_absolute() else ROOT/path
            assert sha(original_path(candidate_config[field])) == sha(original_path(certified_config[field]))
    p = load_instance(candidate_config, candidate_fleet)
    result = json.loads(json.dumps(saved['result']))
    audited = audit_solution(p, result)
    assert audited['valid'] and audited['periodic_replay']['valid']
    close(audited['objective'], saved['independent_audit']['objective'])
    assert result['info']['best_bound'] is None and result['info']['mip_gap'] is None
    assert not result['info']['proven_optimal'] and not result['info']['proven_infeasible']
    assert not result['info']['route_pool_complete']
    audits += 1
    for pass_dir in sorted((POOL/key).glob('pass_*')):
        pools = read(pass_dir/'columns.json')
        initial = read(pass_dir/'initial_result.json')
        assert len(pools) == len(initial['e2_trips'])
        for trip, candidates in zip(initial['e2_trips'], pools):
            original = [(e['order_id'], e['kind']) for e in trip['events']]
            assert any([tuple(k) for k in c['tasks']] == original for c in candidates)
            for column in candidates:
                replay_column(p, trip['satellite_id'], column)
                column_count += 1
    upper = audited['objective']
    assert case['lower'] <= upper+1e-6 <= case['upper']+2e-6
    if upper < case['upper']-1e-6:
        case['upper_before_joint_pool'] = case['upper']
        case['upper'] = upper
        case['result_path'] = str(POOL/(key+'.json'))
    case['gap_percent'] = 100*(case['upper']-case['lower'])/abs(case['upper'])
    close(case['gap_percent'], saved['gap_percent'])
    case['meets_five_percent'] = case['gap_percent'] <= 5
    case['route_improvement_percent'] = 100*(case['old_upper']-case['upper'])/abs(case['old_upper'])
    case['new_upper_old_lower_gap_percent'] = 100*(case['upper']-case['old_lower'])/abs(case['upper'])
    case['joint_pool_runtime'] = result['info']['runtime']
    print({k:case[k] for k in ('customers','pool','upper','lower','gap_percent','meets_five_percent')},flush=True)
core = read(ROOT/'output/experiments/model5_alns_round6_20261001/current_m5_sha256.json')['sha256']
assert all(sha(ROOT/name) == digest for name,digest in core.items())
previous.update(met_cases=sum(c['meets_five_percent'] for c in cases),
    full_target_achieved=all(c['meets_five_percent'] for c in cases),
    joint_pool_additional_audits=audits, joint_pool_replayed_columns=column_count,
    whole_cycle_audits=previous['whole_cycle_audits']+audits,
    replayed_columns=previous['replayed_columns']+column_count,
    joint_pool_scope='Same four observed configurations, extra 120s allowance per case. Restricted pool bounds never used as global bounds.')
pricing_tests = (HERE/'pytest_pricing.log').read_text().strip().splitlines()[-1]
pool_tests = (HERE/'pytest_joint_pool.log').read_text().strip().splitlines()[-1]
assert pricing_tests.startswith('38 passed') and pool_tests.startswith('27 passed')
previous.update(pricing_tests=pricing_tests, joint_pool_tests=pool_tests,
    report_sha256=sha(ROOT/'docs/model5_gap_target_20261001.md'),
    statistical_fallacy_coverage='11/11 checked; exploratory fixed-instance diagnostics')
for file in HERE.rglob('*.json'):
    if file.name not in ('validation.json', 'joint_pool_validation.json'):
        previous['artifact_sha256'][str(file.relative_to(HERE))] = sha(file)
(HERE/'joint_pool_validation.json').write_text(json.dumps(previous,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
print(dict(met=previous['met_cases'],audits=previous['whole_cycle_audits'],columns=previous['replayed_columns']))
