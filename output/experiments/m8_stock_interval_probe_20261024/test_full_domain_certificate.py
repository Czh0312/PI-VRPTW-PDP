"""Cross-clock physical implications and full-domain certificate integration."""
from copy import copy
from dataclasses import replace
import importlib.util
from pathlib import Path

from models.routing.model_8.exact.parameters import necessary_time_bounds

spec = importlib.util.spec_from_file_location('full_domain_proof', Path(__file__).with_name('certify_full_domain.py'))
proof = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proof)


def test_necessary_time_implications_cover_every_refined_clock_and_react_to_input():
    case = proof.ROOT/'output/experiments/m8_physical_time_frozen_inputs_20261024/case.json'
    parent = proof.trial.load_instance(*proof.trial.load_routing_case(case, use_pool_config=False))
    first = necessary_time_bounds(parent.for_count(1))
    expected = {'C2_order', 'C4_order', 'C5_order', 'C7_order'}
    for k in parent.batch_count_candidates:
        child = parent.for_count(k)
        factor = child.operating_policy.time_tick_subdivision
        bounds = necessary_time_bounds(child)
        assert {row['order'] for row in bounds['forced_first_batch']} == expected
        assert bounds['pool_lower_bound'] == 4
        for base, refined in zip(first['intervals'], bounds['intervals']):
            assert refined['latest_loading'] == base['latest_loading'] * factor
            assert refined['earliest_clean'] == base['earliest_clean'] * factor
    relaxed = copy(parent.for_count(1))
    relaxed.orders = [replace(o, delivery_window=(o.delivery_window[0], relaxed.operating_policy.cycle_end))
                      for o in relaxed.orders]
    assert not necessary_time_bounds(relaxed)['forced_first_batch']


def test_full_domain_certificate_closes_cost_and_preserves_native_status():
    certificate, witness = proof.verify()
    assert certificate['historical_physical_inputs_independently_compared']
    assert certificate['complete_batch_count_domain'] == list(range(1, 8))
    assert len(certificate['K_rows']) == 7
    assert certificate['global_lower_bound'] == certificate['objective']
    assert certificate['selected_K'] == 2 and certificate['pool'] == 4
    assert witness['info']['status'] == 'TIME_LIMIT'
    assert witness['audit']['valid'] and witness['audit']['periodic_replay']['valid']
    assert certificate['relaxed_route_partition']['status'] == 'OPTIMAL'
