"""Business-level regression checks for the separate interleaved release."""
from copy import deepcopy
from pathlib import Path
import zipfile

from tools.configuration.loader import resolve_project_path
from tools.data.build_formal_benchmark27 import read, digest
from tools.data.build_interleaved_benchmark27 import design_case, WINDOW_FIELDS
from tools.audit.audit_formal_benchmark27 import physical_orders


ROOT = resolve_project_path('data/formal_benchmark27_interleaved_v1')
SOURCE = resolve_project_path('data/formal_benchmark27_one_box_v1')


def test_only_four_window_endpoints_change_across_all_bases():
    manifest = read(ROOT/'manifest.json')
    assert len(manifest['base_cases']) == 27
    assert len(manifest['cases']) == 216
    for base in manifest['base_cases']:
        common = Path('shared')/base['size']/base['id']
        old = read(SOURCE/common/'reference_orders.json')
        new = read(ROOT/common/'reference_orders.json')
        without_windows = lambda rows: [{k: v for k, v in o.items() if k not in WINDOW_FIELDS} for o in rows]
        assert without_windows(old) == without_windows(new)
        assert old != new
        for name in ('network.csv', 'fleet.json', 'economic.json', 'reference_policy.json'):
            assert digest(SOURCE/common/name) == digest(ROOT/common/name)
        for model in range(1, 9):
            cfg = read(resolve_project_path(base['models'][str(model)]))
            assert physical_orders(read(resolve_project_path(cfg['orders']))) == physical_orders(new)


def test_each_customer_can_finish_delivery_and_use_before_pickup_window():
    widths = set()
    customers = 0
    for base in read(ROOT/'manifest.json')['base_cases']:
        orders = read(ROOT/'shared'/base['size']/base['id']/'reference_orders.json')
        assert len({o['customer_id'] for o in orders}) == len(orders) == base['customer_count']
        for order in orders:
            assert order['container_count'] == 1
            assert order['delivery_ready'] < order['delivery_due']
            assert order['pickup_ready'] < order['pickup_due']
            assert order['pickup_ready'] >= order['delivery_due'] + order['service_time'] + order['min_use_time']
            widths.add((order['delivery_due'] - order['delivery_ready'],
                        order['pickup_due'] - order['pickup_ready']))
            customers += 1
    assert customers == 1017
    assert len(widths) > 100


def test_windows_allow_other_deliveries_between_own_delivery_and_pickup():
    for base in read(ROOT/'manifest.json')['base_cases']:
        orders = read(ROOT/'shared'/base['size']/base['id']/'reference_orders.json')
        assert any(a['order_id'] != b['order_id'] and a['satellite_id'] == b['satellite_id']
            and a['delivery_due'] < b['delivery_ready'] and b['delivery_due'] < a['pickup_ready']
            for a in orders for b in orders), base['id']
        # Pickup before another customer's later delivery: exclude a global
        # "deliver everyone first, collect everyone afterwards" appointment block.
        assert any(a['order_id'] != b['order_id'] and a['satellite_id'] == b['satellite_id']
            and a['pickup_due'] < b['delivery_ready']
            for a in orders for b in orders), base['id']


def test_seed_reproduces_published_s01_without_using_objective():
    rules = read(ROOT/'window_rules.json')
    base = next(b for b in read(SOURCE/'manifest.json')['base_cases'] if b['id'] == 'S01')
    with zipfile.ZipFile(SOURCE/'validation/witnesses.zip') as archive:
        rows, witness, _ = design_case(SOURCE, base, archive, rules)
        changed = deepcopy(rules)
        changed['seed'] += 1
        other_rows, _, _ = design_case(SOURCE, base, archive, changed)
    assert rows == read(ROOT/'shared/small/S01/reference_orders.json')
    assert rows != other_rows
    assert witness['info']['proven_optimal'] is False


def test_low_k_domain_and_full_replay_evidence_are_reported_honestly():
    for base in read(ROOT/'manifest.json')['base_cases']:
        certificate = read(ROOT/'shared'/base['size']/base['id']/'batch_count_certificate.json')
        for model in (6, 7, 8):
            cfg = read(resolve_project_path(base['models'][str(model)]))
            assert cfg['joint_batch_options']['batch_count_candidates'] == certificate['candidates_by_model'][str(model)]
    report = read(ROOT/'validation/feasibility.json')
    assert report['solver_optimization_calls'] == 0
    assert report['not_automatically_used_as_solver_start'] is True
    assert len(report['cases']) == 216
    assert all(r['valid'] for r in report['cases'])
    reusable = [r for r in report['cases'] if r['model'] >= 3]
    assert len(reusable) == 162
    assert all(r['periodic_replay'] is True for r in reusable)
