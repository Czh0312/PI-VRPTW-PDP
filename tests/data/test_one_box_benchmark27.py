"""Guard the changed demand semantics without changing the frozen experiment factors."""
import json
import shutil

import pytest

from tools.data.build_one_box_benchmark27 import build_one_box, SOURCE
from tools.data.build_formal_benchmark27 import read, digest
from tools.data.one_box_contract import validate_orders, preserved_orders_hash, preserved_policy_hash
from tools.audit.audit_formal_benchmark27 import audit_dataset
from tools.audit.certify_formal_benchmark27 import certify
from tools.configuration.loader import resolve_project_path
from tests.data.test_formal_benchmark27 import rewrite_registered_file


@pytest.fixture(scope='module')
def release(tmp_path_factory):
    parent = tmp_path_factory.mktemp('one_box')
    root = build_one_box(parent/'dataset', parent/'selection.json').parent
    return root


def test_all_216_configs_preserve_original_factors_and_full_batch_domains(release):
    old = resolve_project_path(SOURCE)
    result = audit_dataset(release)
    assert result['valid'] and result['model_config_count'] == 216
    assert not result['fixed_model_necessary_condition_failures']
    manifest = read(release/'manifest.json')
    assert manifest['base_cases'][0]['customer_count'] == manifest['base_cases'][0]['total_container_uses'] == 7
    for base in manifest['base_cases']:
        for name in ('network.csv', 'fleet.json', 'economic.json', 'batch_count_certificate.json'):
            assert digest(release/'shared'/base['size']/base['id']/name) == digest(
                old/'shared'/base['size']/base['id']/name)
        for model in range(1,9):
            relative = f'model_{model}/{base["size"]}/{base["id"]}'
            orders, original = read(release/relative/'orders.json'), read(old/relative/'orders.json')
            validate_orders(orders, base['customer_count'])
            assert preserved_orders_hash(orders) == preserved_orders_hash(original)
            assert preserved_policy_hash(read(release/relative/'policy.json')) == preserved_policy_hash(
                read(old/relative/'policy.json'))
    with pytest.raises(ValueError, match='overwrite'):
        build_one_box(release)


@pytest.mark.parametrize('change', ['multiple_boxes', 'duplicate_customer', 'goods_weight'])
def test_semantic_one_box_drift_is_rejected_with_updated_artifact_hash(release, tmp_path, change):
    root = tmp_path/'changed'
    # Config paths point to the original release. Modify only the shared source
    # invariant, checked before any model path is loaded.
    shutil.copytree(release, root)
    path = root/'shared/small/S01/reference_orders.json'
    value = read(path)
    if change == 'multiple_boxes':
        value[0]['container_count'] = 2
        expected = 'container_count=1'
    elif change == 'duplicate_customer':
        value[0]['customer_id'] = value[1]['customer_id']
        expected = 'one order per customer'
    else:
        value[0]['goods_weight'] += 1
        expected = 'Non-box order fields changed'
    rewrite_registered_file(root, path, value)
    # Rebase only the manifest enumeration (the invariant fails before runtime dependencies).
    manifest = read(root/'manifest.json')
    for row in manifest['cases']:
        row['case_path'] = row['case_path'].replace(str(release), str(root))
    (root/'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match=expected):
        audit_dataset(root)


def test_all_models_have_conserved_repeating_cycle_witnesses(release):
    result = certify(release)
    assert result['valid'] and result['audited'] == 216


def test_zhou_generation_keeps_full_goods_weight_in_one_box():
    from tools.data.design_static_scenarios import design_orders
    design = read(resolve_project_path('config/datasets/static_batch_one_box_v1.json'))
    assert 'goods_per_container' not in design['zhou']
    rows = [dict(node_id='D0', node_type='d', x=0, y=0, service_time=0),
            dict(node_id='S0', node_type='s', x=1, y=0, service_time=3),
            dict(node_id='C0', node_type='c', x=2, y=0, service_time=10,
                 assigned_sat='S0', delivery_demand=80)]
    fleet = dict(vehicles_per_satellite=1, speed=2, container_weight=2,
                 container_volume=1, E2_Qv=180, E2_Cv=6)
    orders, _, _ = design_orders(rows, 'one_box_regression', 1, fleet, design)
    assert len(orders) == 1 and orders[0]['container_count'] == 1
    assert orders[0]['goods_weight'] == 80
