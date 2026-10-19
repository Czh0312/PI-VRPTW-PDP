"""Physical boundary cases for covers that are only local proposals."""
import pytest
from models.routing.model_5.exact.primal.cover_pool import preserve_cover_routes, reoptimize_cover_pool
from models.routing.model_5.exact.bounds.inventory.e1_routes import build_e1_routes
from models.routing.model_5.exact.primal.route_pool import add_route_pool
from tests.routing.test_multibatch_cycle import fixture, hand_solution
from tests.routing.test_multibatch_route_pool import three_slot_case


def test_cover_retention_keeps_indexed_E1_vehicles_when_turnaround_is_positive():
    pytest.importorskip('gurobipy')
    p = fixture(pool=2, e1_turnaround_time=2, e2_dispatch_prep_time=2)
    initial = hand_solution(p)
    for trip in initial['e2_trips']:
        trip['departure'] += 2
        trip['events'][0]['start'] += 2
    cover = {'S': [[(e['order_id'], e['kind']) for e in t['events']] for t in initial['e2_trips']]}
    result = reoptimize_cover_pool(p, initial, cover, time_limit=2)
    assert result['audit']['valid'] and result['info']['route_pool_backend'] == 'indexed'
    assert not result['info']['proven_optimal'] and not result['info']['proven_infeasible']


def test_locally_valid_complete_cover_can_have_a_global_zero_time_causal_cycle():
    gp = pytest.importorskip('gurobipy')
    p, initial = three_slot_case(zero=True)
    cyclic = {'S': [
        [('extra', 'p'), ('o1', 'd')],
        [('o1', 'p'), ('extra', 'd')],
        [('o2', 'd'), ('o2', 'p')],
    ]}
    pools, proof = preserve_cover_routes(p, initial, [[], [], []], cyclic)
    assert not proof['full_feasible']
    # Use only these proposed sequences: task coverage alone must not make
    # their cross-trip pickup-before-delivery cycle physically feasible.
    model, context = build_e1_routes(p)
    try:
        add_route_pool(p, model, context, initial, pools)
        model.optimize()
        assert model.Status == gp.GRB.INFEASIBLE
    finally:
        model.dispose()
