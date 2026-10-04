"""Review same-coefficient cross-run records after a native frozen public pipeline passes."""
from copy import deepcopy
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / 'source_snapshot'))
sys.path.insert(0, str(HERE))
import validation_core as check
import validation_conjunction as joint
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance

registration = check.read(HERE / 'best_projection_registration.json')
assert check.sha(Path(__file__)) == registration['validator_sha256']
assert check.sha(HERE / 'plan.json') == registration['pipeline_plan_sha256']
validation = HERE / 'validation.json'
current = check.read(validation)
assert current['status'] == 'VERIFIED' and len(current['case_results']) == 1
prior = Path(registration['previous_root'])
prior_manifest = check.read(prior / 'final_verification.json')
assert prior_manifest['status'] == 'VERIFIED'
assert check.sha(prior / 'final_verification.json') == registration['previous_seal_sha256']
for name, digest in prior_manifest['artifact_sha256'].items():
    assert check.sha(prior / name) == digest, name
previous = check.read(prior / 'best_certified.json')
assert check.sha(prior / 'best_certified.json') == registration['previous_record_sha256']
plan = check.read(HERE / 'plan.json')
p = load_instance(*load_routing_case(plan['case'], use_pool_config=False))
q = load_instance(*load_routing_case(check.read(prior / 'plan.json')['case'], use_pool_config=False))
assert p.orders == q.orders and p.operating_policy == q.operating_policy and vars(p.fleet) == vars(q.fleet)
assert p.depot == q.depot and p.satellites == q.satellites
assert set(p.network.nodes) == set(q.network.nodes)
for name in p.network.nodes:
    assert vars(p.network.nodes[name]) == vars(q.network.nodes[name])
    if hasattr(p.network.nodes[name], 'service_time'):
        assert p.service(name) == q.service(name)
    for other in p.network.nodes:
        assert p.distance(name, other) == q.distance(name, other)
        assert p.travel(name, other) == q.travel(name, other)
assert p.batch_demands == q.batch_demands and p.demands == q.demands and p.weights == q.weights

cold = min(current['case_results'], key=lambda row: row['upper'])
strongest = max(current['case_results'], key=lambda row: row['lower'])
upper = min(cold['upper'], previous['upper'])
lower = max(strongest['lower'], previous['lower'])
if upper >= previous['upper'] and lower <= previous['lower']:
    (HERE / 'best_record_review.json').write_text(json.dumps(dict(
        status='VERIFIED_NO_IMPROVEMENT', previous=previous,
        case_results=current['case_results'], same_coefficients_verified=True,
        current_pipeline_validation_sha256=check.sha(validation),
        interpretation='No cross-run record change; all individual pipeline results remain separate.'
    ), indent=2, ensure_ascii=False, allow_nan=False) + '\n')
    print('VERIFIED NO RECORD IMPROVEMENT', flush=True)
    sys.exit()

new_primal = cold['upper'] < previous['upper']
primal_root = HERE if new_primal else Path(previous.get('primal_root', str(prior)))
primal_id = cold['id'] if new_primal else previous['primal_source']
result = deepcopy(check.read(primal_root / (primal_id + '.json'))['experiment']['result'])
audit = check.checked_audit(p, result)
check.close(audit['objective'], upper)
old = HERE.parents[2] / 'model5_gap_round6_20261001'
manifest = check.read(old / 'final_verification.json')
assert manifest['status'] == 'VERIFIED'
for name in ['validation_final.json', 'conjunction/result.json', 'conjunction/full_joint.mps.gz',
             'inputs/previous.json', 'inputs/full_case.json']:
    assert check.sha(old / name) == manifest['artifacts_sha256'][name]
historical = next(row for row in check.read(old / 'conjunction/result.json')['cases'] if row['label'] == 'full')
reference = next(row for row in check.read(old / 'inputs/previous.json')['cases'] if row['label'] == 'full')['reference_certificate']
base = HERE.parents[2] / 'model5_gap_round4_20261001/fresh/02_zhou_Set2_100-5-1b_full_43_models/reference/e1_inventory.mps'
original = check.read(HERE.parents[2] / 'model5_gap_round4_20261001/final_verification.json')['artifact_sha256']
assert check.sha(base) == original['fresh/02_zhou_Set2_100-5-1b_full_43_models/reference/e1_inventory.mps']
projection = joint.check_joint_conjunction(p, result, reference, historical['joint_certificate'],
                                         old / 'conjunction/full_joint.mps.gz', base)
new_bound_projections = []
if strongest['lower'] > previous['lower']:
    payload = check.read(HERE / (strongest['id'] + '.json'))['experiment']
    ref = payload['reference_certificate']
    dest = HERE / (strongest['id'] + '_models')
    current_base = dest / 'reference/e1_inventory.mps'
    new_bound_projections.append(check.verify_e1(p, result, ref['e1_certificate'], current_base))
    cond = payload.get('conditional_certificate')
    if cond:
        for number, cert in enumerate(cond['masters']):
            if cert['raw_bound'] is not None:
                new_bound_projections.append(joint.check_joint_conjunction(
                    p, result, ref, cert, dest / 'conditional' / ('master_' + str(number) + '.mps.gz'), current_base))
    adaptive = payload.get('adaptive_certificate')
    if adaptive:
        for number, row in enumerate(adaptive['masters']):
            cert = row['certificate']
            if cert['raw_bound'] is None:
                continue
            frame = deepcopy(adaptive['reference_certificate'])
            for component in frame['components']:
                component['bound'] = row['site_bounds'][component['satellite']]
            new_bound_projections.append(joint.check_joint_conjunction(
                p, result, frame, cert, dest / 'adaptive' / ('master_' + str(number) + '.mps.gz'), current_base))
lower = max(lower, historical['lower'])
assert 0 <= lower <= upper
gap = max(0, upper - lower) / abs(upper)
out = dict(status='VERIFIED', scope='Cross-run best certified record on identical full M5 coefficients',
    upper=upper, lower=lower, gap_percent=100 * gap, target_reached=gap <= .05,
    previous_upper=previous['upper'], previous_lower=previous['lower'], previous_gap_percent=previous['gap_percent'],
    primal_source=primal_id, primal_root=str(primal_root),
    current_pipeline_lower=cold['lower'] if new_primal else None,
    current_pipeline_gap_percent=cold['gap_percent'] if new_primal else None,
    current_case_results=current['case_results'], strongest_current_lower_source=strongest['id'],
    physical_costs=audit['distances'], periodic_replay=audit['periodic_replay'],
    original_proof_sha256=check.sha(old / 'validation_final.json'),
    historical_joint_mps_sha256=check.sha(old / 'conjunction/full_joint.mps.gz'),
    current_pipeline_validation_sha256=check.sha(validation), same_coefficients_verified=True,
    historical_master_projection=projection, current_lower_master_projections=new_bound_projections,
    interpretation='Historical bounds and solutions were not supplied to the native public1500s pipeline with actual ALNS initialization. This is a cross-run record; native pipeline gap and total1500s wall-clock budget remain separate. No660s target or stability claim.')
(HERE / 'best_certified.json').write_text(json.dumps(out, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
print({k: out[k] for k in ['status', 'upper', 'lower', 'gap_percent', 'physical_costs']}, flush=True)
