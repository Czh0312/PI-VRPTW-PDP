"""Necessary-bound safety, audited MIP starts and compound ALNS moves."""
from copy import deepcopy
from dataclasses import replace
from importlib import import_module
import time

import pytest

from models.common.cycle.joint import JointBatchInstance
from models.common.cycle.pool_bound import necessary_pool_lower_bound, necessary_cost_lower_bound
from models.common.domain.instance import ProblemInstance
from tests.routing.test_model6_joint import joint_fixture, exact_available
from tests.routing.test_model78_calendar import calendar_fixture


@pytest.mark.parametrize('cleaning', [0, 5, 25])
@pytest.mark.parametrize('directed', [False, True])
def test_necessary_bounds_never_exceed_small_proven_optimum(cleaning, directed, exact_available):
    source = joint_fixture(cleaning=cleaning)
    network = source.network
    if directed:
        ids = list(network.nodes)
        distances = {a: {b: 0 if a == b else 1+(11*i+7*j)%17
                        for j, b in enumerate(ids)} for i, a in enumerate(ids)}
        # Distance need not equal travel time, nor obey the triangle inequality.
        travel = {a: {b: network.travel_time(a, b) for b in ids} for a in ids}
        f = network.fleet
        fleet = dict(E1_vehicle_count=f.e1_count, vehicles_per_satellite=f.e2_per_sat_count,
                     E1_Qk=f.e1_weight_cap, E1_Ck=f.e1_volume_cap,
                     E2_Qv=f.e2_weight_cap, E2_Cv=f.e2_volume_cap,
                     container_weight=f.container_weight, container_volume=f.container_volume)
        network = ProblemInstance(network.nodes, fleet,
                                  distance_matrix=distances, travel_time_matrix=travel)
    p = JointBatchInstance(network, source.orders, source.operating_policy, {}, source.cost_profile.config)
    from models.routing.model_6.exact import solve_exact
    result = solve_exact(p, time_limit=3)
    assert result['info']['proven_optimal'] and result['audit']['valid']
    assert necessary_pool_lower_bound(p) <= result['container_pool_size']
    assert necessary_cost_lower_bound(p) <= result['info']['objective']+1e-7


def test_mandatory_overlap_strengthens_average_without_reusing_unreturned_boxes():
    source = joint_fixture()
    orders = [replace(o, pickup_window=(75, 90)) for o in source.orders]
    p = JointBatchInstance(source.network, orders, source.operating_policy, {}, source.cost_profile.config)
    assert (p.total+1)//2 == 1
    assert necessary_pool_lower_bound(p) == 2


@pytest.mark.parametrize('pickup, expected', [(48, 1), (49, 1), (50, 2)])
def test_mandatory_intervals_allow_release_at_exact_loading_time(pickup, expected):
    source = joint_fixture()
    orders = [replace(source.orders[0], allowed_batch_ids=('b1',), pickup_window=(pickup, 90)),
              replace(source.orders[1], allowed_batch_ids=('b2',), delivery_window=(60, 90),
                      pickup_window=(65, 90))]
    p = JointBatchInstance(source.network, orders, source.operating_policy, {}, source.cost_profile.config)
    assert necessary_pool_lower_bound(p) == expected


@pytest.mark.parametrize('number', [6, 7, 8])
def test_fast_all_k_bounds_relax_every_legal_calendar(number):
    if number == 6:
        from tests.routing.test_model6_uniform import uniform_fixture
        p = uniform_fixture()
    else:
        p = calendar_fixture(number)
    for count in p.batch_count_candidates:
        cheap = necessary_cost_lower_bound(p, batch_count=count, relaxed_loading=True)
        assert cheap <= necessary_cost_lower_bound(p.for_count(count))+1e-7


@pytest.mark.parametrize('number', [6, 7, 8])
def test_mip_start_replayed_retained_after_build_timeout_without_proof(number, monkeypatch, exact_available):
    p = joint_fixture() if number == 6 else calendar_fixture(number, counts=(2,)).for_count(2)
    module = import_module(f'models.routing.model_{number}.exact.'+'enhanced_solver')
    incumbent = module.solve_exact(p, time_limit=3)
    assert incumbent['audit']['valid']
    saved = deepcopy(incumbent)
    def timeout(*args, **kwargs):
        raise TimeoutError()
    monkeypatch.setattr(module, 'build_model', timeout)
    result = module.solve_exact(p, time_limit=.1, initial_result=incumbent)
    assert result['audit']['valid']
    assert result['info']['objective'] == incumbent['info']['objective']
    assert result['info']['incumbent_retained']
    assert result['info']['last_solver_status'] == 'TIME_LIMIT_BUILD'
    assert not result['info']['proven_optimal'] and not result['info']['proven_infeasible']
    assert result['info']['best_bound'] <= result['info']['objective']
    assert incumbent == saved
    tampered = deepcopy(incumbent)
    tampered['e2_trips'][0]['events'][0]['quantity'] += 1
    with pytest.raises(ValueError, match='physical replay'):
        module.solve_exact(p, time_limit=.1, initial_result=tampered)


@pytest.mark.parametrize('number', [6, 7, 8])
def test_mip_start_encoded_and_exact_solution_audited(number, exact_available):
    p = joint_fixture() if number == 6 else calendar_fixture(number, counts=(2,)).for_count(2)
    module = import_module(f'models.routing.model_{number}.exact.'+'enhanced_solver')
    first = module.solve_exact(p, time_limit=3)
    result = module.solve_exact(p, time_limit=3, initial_result=first)
    assert result['audit']['valid'] and result['info']['proven_optimal']
    assert result['info']['objective'] == pytest.approx(first['info']['objective'])
    assert result['info']['mip_start_variables'] > 0


@pytest.mark.parametrize('number', [6, 7, 8])
def test_compound_neighbors_preserve_tasks_and_do_not_mutate_incumbent(number):
    from collections import Counter
    module = import_module(f'models.routing.model_{number}.alns.polish')
    source = joint_fixture()
    p = source.project({'o1': 'b1', 'o2': 'b2'}, 2)
    state = dict(deliveries={'b1': [['S']], 'b2': [['S']]},
                 pickup_order={'b1': ['S'], 'b2': ['S']},
                 pickup_vehicle={'b1': {'S': 0}, 'b2': {'S': 0}},
                 e2=[dict(satellite='S', vehicle=0, tasks=[('o1', 'd'), ('o1', 'p')]),
                     dict(satellite='S', vehicle=0, tasks=[('o2', 'd'), ('o2', 'p')])],
                 cutoffs={b.id: b.cutoff for b in p.operating_policy.batches})
    saved = deepcopy(state)
    tasks = Counter(t for r in state['e2'] for t in r['tasks'])
    proposals = list(module._neighbors(p, state, source.cost_profile.rates, time.perf_counter()+2))
    assert proposals
    for trial in proposals:
        assert Counter(t for r in trial['e2'] for t in r['tasks']) == tasks
        assert trial['cutoffs'] == state['cutoffs']
        assert all(r['satellite'] == 'S' for r in trial['e2'])
    assert state == saved
