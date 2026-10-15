"""Guard the matched inputs and real fixed/free decision boundaries."""
import hashlib
import json
from dataclasses import replace

import pytest

from tools.data.build_formal_benchmark27 import build_suite
from tools.audit.audit_formal_benchmark27 import audit_dataset
from tools.audit.certify_formal_benchmark27 import certify
from tools.audit.check_last_recovery import last_recovery_certificate
from tools.configuration.loader import load_routing_case
from models.common.cycle import load_instance
from models.common.cycle.instance import MultiBatchInstance


def rewrite_registered_file(root, path, value):
    """Update the file hash too, so the test exercises semantic checks."""
    path.write_text(json.dumps(value))
    manifest_path = root/'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    manifest['artifact_sha256'][path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest))


def test_complete_suite_loads_with_the_declared_model_domains(tmp_path):
    root = build_suite(tmp_path/'dataset').parent
    audit = audit_dataset(root)
    assert audit['valid'] and audit['model_config_count'] == 216
    assert not audit['fixed_model_necessary_condition_failures']
    with pytest.raises(ValueError, match='overwrite'):
        build_suite(root)


@pytest.mark.parametrize('mutation', ['demand', 'pool', 'batch_binding', 'k_domain'])
def test_semantic_drift_rejected_even_with_updated_file_hash(tmp_path, mutation):
    root = build_suite(tmp_path/'dataset').parent
    if mutation == 'demand':
        path = root/'model_2/small/S01/orders.json'
        value = json.loads(path.read_text())
        value[0]['container_count'] += 1
        value[1]['container_count'] -= 1  # Preserve Q; still a different demand pattern.
        expected = 'Physical orders differ'
    elif mutation == 'pool':
        path = root/'model_6/small/S01/case.json'
        value = json.loads(path.read_text())
        value['joint_batch_options']['pool_min'] = value['joint_batch_options']['pool_max']
        expected = 'Pool accidentally fixed'
    elif mutation == 'batch_binding':
        path = root/'model_6/small/S01/orders.json'
        value = json.loads(path.read_text())
        for order in value:
            order['allowed_batch_ids'] = [order['delivery_batch_id']]
        expected = 'requires every order to allow all source batches'
    else:
        path = root/'model_6/small/S01/case.json'
        value = json.loads(path.read_text())
        value['joint_batch_options']['batch_count_candidates'] = [1,2]
        expected = 'Uncertified K truncation'
    rewrite_registered_file(root, path, value)
    with pytest.raises(ValueError, match=expected):
        audit_dataset(root)


def test_reviewed_calendar_has_audited_witnesses_for_all_models(tmp_path):
    root = build_suite(tmp_path/'dataset').parent
    result = certify(root)
    assert result['valid'] and result['audited'] == 216
    assert audit_dataset(root)['valid']
    certificate = json.loads((root/'shared/small/S01/batch_count_certificate.json').read_text())
    assert certificate['original_positive_integer_domain'] == [1,390]
    assert certificate['candidates_by_model']['6'] == [1,2,3]
    assert certificate['candidates_by_model']['8'] == list(range(1,8))


def test_incorrect_exclusion_certificate_cannot_hide_an_admissible_count(tmp_path):
    root = build_suite(tmp_path/'dataset').parent
    path = root/'shared/small/S01/batch_count_certificate.json'
    value = json.loads(path.read_text())
    value['candidates_by_model']['6'] = [1,2]
    value['upper_bound_by_model']['6'] = 2
    rewrite_registered_file(root,path,value)
    with pytest.raises(ValueError,match='Incorrect K exclusion certificate'):
        audit_dataset(root)


@pytest.mark.parametrize('base,size,lower', [('M03','medium',67.65),('L09','large',66.96666666666667)])
def test_old_75_percent_calendar_has_a_terminal_recovery_conflict(tmp_path,base,size,lower):
    root=build_suite(tmp_path/'dataset').parent
    cfg,fleet=load_routing_case(root/f'model_5/{size}/{base}/case.json')
    problem=load_instance(cfg,fleet)
    assert not last_recovery_certificate(problem)['infeasible']
    policy=problem.operating_policy
    old=replace(policy,batches=tuple(replace(b,cutoff=b.start+(b.end-b.start)*3//4) for b in policy.batches))
    original=MultiBatchInstance(problem.network,problem.orders,old,model=5)
    certificate=last_recovery_certificate(original)
    assert certificate['infeasible']
    assert certificate['available_minutes']==65
    assert certificate['lower_bound_minutes']==pytest.approx(lower)
