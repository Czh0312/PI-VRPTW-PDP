"""Current M8 is one joint problem; historical two-stage tests live separately."""
from copy import deepcopy
from unittest.mock import patch

import pytest

from models.routing.common.domain.instance import ProblemInstance
from models.routing.common.domain.node import Customer, Depot, Satellite
from models.routing.common.task_builder import build_tasks_for_model
from models.routing.variants.model_8.state import Evaluator, state_from_routes
from models.routing.alns.variants.model_8 import solve_alns


def e1_example():
    nodes = {'D0': Depot('D0', 0, 0), 'S0': Satellite('S0', 10, 0), 'S1': Satellite('S1', -10, 0)}
    for name, x, sat in [('A', 11, 'S0'), ('B', -11, 'S1')]:
        nodes[name] = Customer(name, x, 0, 1, 1, (0, 1000), (0, 1000), assigned_sat=sat,
                               container_count_del=1, container_count_pick=1)
    p = ProblemInstance(nodes, {'E1_vehicle_count': 2, 'E1_Qk': 1000, 'E1_Ck': 30,
        'vehicles_per_satellite': 2, 'E2_Qv': 100, 'E2_Cv': 4,
        'container_weight': 2, 'container_volume': 1, 'speed': 1})
    p.aggregate_demands_from_pre_assignment()
    a, b = build_tasks_for_model(p, 8)
    routes = ([['D0', 'S0d', 'S1d', 'S0p', 'S1p', 'D0']],
              {'S0': [['S0', 'Ad', 'Ap', 'S0']], 'S1': [['S1', 'Bd', 'Bp', 'S1']]})
    return p, a, b, routes


def activation_example():
    nodes = {'D0': Depot('D0', 0, 1), 'S0': Satellite('S0', 0, 0)}
    for name, x, tw in [('A', 10, (10, 20)), ('B', -10, (35, 45)),
                         ('C', 11, (65, 75)), ('D', -11, (100, 110))]:
        nodes[name] = Customer(name, x, 0, 1, 0, tw, (0, 1000), assigned_sat='S0',
                               container_count_del=1, container_count_pick=0)
    p = ProblemInstance(nodes, {'E1_vehicle_count': 1, 'E1_Qk': 1000, 'E1_Ck': 30,
        'vehicles_per_satellite': 2, 'E2_Qv': 100, 'E2_Cv': 4,
        'container_weight': 2, 'container_volume': 1, 'speed': 1})
    p.aggregate_demands_from_pre_assignment()
    a, b = build_tasks_for_model(p, 8)
    routes = ([['D0', 'S0d', 'D0']], {'S0': [['S0', 'Ad', 'Bd', 'Cd', 'Dd', 'S0']]})
    return p, a, b, routes


def test_alns_changes_e1_routes_in_the_joint_loop():
    p, a, b, routes = e1_example()
    original = deepcopy(routes)
    _, _, info = solve_alns(p, a, b, 2, warm_start_routes=routes, allow_transfers=False,
                            max_time=5, max_iterations=1000, seed=42, verbose=False, return_info=True)
    assert info['initial_objective'] == pytest.approx(84)
    assert info['objective'] == pytest.approx(44)
    assert info['e1_routes_changed'] and info['e1_move_attempts'] > 0
    assert info['state']['transfers'] == []
    assert any(h['operator'].startswith('e1_') for h in info['search_history'])
    assert routes == original


def test_alns_can_activate_unused_vehicle_and_change_delivery_ownership():
    p, a, b, routes = activation_example()
    _, _, info = solve_alns(p, a, b, 2, warm_start_routes=routes, allow_transfers=False,
                            max_time=5, max_iterations=2000, seed=42, verbose=False, return_info=True)
    assert len(info['initial_state']['e2_routes']['S0'][1]) == 2
    assert all(len(r) > 2 for r in info['state']['e2_routes']['S0'])
    assert info['objective'] == pytest.approx(46)
    assert info['objective'] < info['initial_objective']
    assert info['delivery_move_attempts'] > 0
    Evaluator(p, a, b, 2).audit(info['state'])


def test_default_alns_never_solves_m3_and_finds_capacity_cooperation():
    from experiments.scenarios.model8_capacity import make_capacity_example
    p = make_capacity_example()
    a, b = build_tasks_for_model(p, 8)
    with patch('models.routing.alns.variants.model_3.solve_alns', side_effect=AssertionError('M3 solve called')):
        _, _, info = solve_alns(p, a, b, 2, max_time=5, max_iterations=1500,
                                verbose=False, return_info=True)
    assert info['objective'] == pytest.approx(116.78708664619074)
    assert info['transshipment_count'] > 0
    assert info['initial_solution_source'] == 'constructive_insertion_without_m3_solve'
    assert not any('stage1' in k or 'stage2' in k or 'baseline' in k or 'conditional' in k for k in info)


def test_joint_exact_does_not_freeze_warm_routes_or_solve_m3():
    pytest.importorskip('gurobipy')
    from models.routing.variants.model_8.exact_solver import solve_exact
    p, a, b, routes = e1_example()
    with patch('models.routing.variants.model_3.exact_solver.solve_exact', side_effect=AssertionError('M3 solve called')):
        _, _, info = solve_exact(p, a, b, 2, warm_start_routes=routes,
                                 time_limit=10, allow_transfers=False,
                                 gurobi_params={'Threads': 1}, verbose=False, return_info=True)
    assert info['proven_optimal']
    assert info['objective'] == pytest.approx(44)
    assert info['proof_scope'] == 'full_joint_fixed_satellite_problem'
    assert not any('stage1' in k or 'stage2' in k or 'baseline' in k or 'conditional' in k for k in info)


def test_joint_no_transfer_exact_matches_m3_and_relay_improves_mechanism():
    pytest.importorskip('gurobipy')
    from experiments.scenarios.model8_capacity import make_capacity_example
    from models.routing.variants.model_3.exact_solver import solve_exact as solve_m3
    from models.routing.variants.model_8.exact_solver import solve_exact
    p = make_capacity_example()
    a, b = build_tasks_for_model(p, 8)
    m3 = solve_m3(p, a, b, 2, time_limit=10, verbose=False, return_info=True)[2]
    results = [solve_exact(p, a, b, 2, time_limit=10, allow_transfers=allow,
                           gurobi_params={'Threads': 1}, verbose=False, return_info=True)[2]
               for allow in (False, True)]
    assert m3['proven_optimal'] and all(r['proven_optimal'] for r in results)
    assert results[0]['objective'] == pytest.approx(m3['objective'])
    assert results[1]['objective'] < results[0]['objective']


def test_joint_audit_and_repair_keep_unequal_multi_receiver_split():
    import time
    from tests.routing.test_model8_split import split_example, asynchronous_split
    from models.routing.variants.model_8.neighborhoods import _repair_split_handoff
    p, _, _, base, _, trial = split_example()
    p.nodes['R'].container_count_pick = 2
    p.nodes['X'].container_count_pick = 1
    p.nodes['Y'].container_count_pick = 1
    p.aggregate_demands_from_pre_assignment()
    a, b = build_tasks_for_model(p, 8)
    e = Evaluator(p, a, b, 3)
    trial = state_from_routes(p, 3, (base['e1_routes'], trial['e2_routes']))
    candidate, _ = _repair_split_handoff(e, trial, 'S0', 0, time.perf_counter()+3,
                                         1000, 32, max_receivers=2, receiver_candidates=2)
    assert candidate is not None
    state, audit = candidate
    assert sorted(t['quantity'] for t in state['transfers']) == [1, 2]
    assert audit['handoff_batches'][0]['deposited_quantity'] == 3
    assert audit['handoff_batches'][0]['collections'][-1]['inventory_after_pickup'] == 0


@pytest.mark.parametrize('error', ['missing_e1', 'overload_e1', 'extra_e2', 'missing_task', 'receive_twice'])
def test_joint_audit_preserves_real_constraints(error):
    p, a, b, routes = e1_example()
    state = state_from_routes(p, 2, routes)
    if error == 'missing_e1':
        state['e1_routes'][0].remove('S0d')
    elif error == 'overload_e1':
        a[0].v = 100
    elif error == 'extra_e2':
        state['e2_routes']['S0'].append(['S0', 'S0'])
    elif error == 'missing_task':
        state['e2_routes']['S0'][0].remove('Ap')
    else:
        state['transfers'] = [{'satellite': 'S0', 'event': 'Ad', 'sender_vehicle': 0,
                               'receiver_vehicle': 1, 'quantity': 2}]
    with pytest.raises(ValueError):
        Evaluator(p, a, b, 2).audit(state)


def test_old_stage_parameters_are_not_silently_accepted():
    p, a, b, routes = e1_example()
    with pytest.raises(TypeError):
        solve_alns(p, a, b, 2, base_routes=routes)


@pytest.mark.parametrize('method', ['exact', 'alns'])
def test_application_dispatch_uses_joint_entry_without_stage_options(method):
    from models.routing.service import solve_routing
    p, a, b, _ = e1_example()
    with patch('models.routing.service._load_solver') as load:
        load.return_value.return_value = ('routes', 'routes', {'version': 'joint_capacity_cooperation_v3'})
        solve_routing(model=8, method=method, problem=p, e1_tasks=a, e2_tasks_by_sat=b)
        kwargs = load.return_value.call_args.kwargs
        assert 'stage1_time_ratio' not in kwargs and 'base_routes' not in kwargs
        assert kwargs['vehicles_per_satellite'] == 2


def test_application_rejects_old_stage_budget():
    from models.routing.service import solve_routing
    p, a, b, _ = e1_example()
    with pytest.raises(ValueError, match='联合优化'):
        solve_routing(model=8, method='alns', problem=p, e1_tasks=a,
                      e2_tasks_by_sat=b, stage1_time_ratio=.6)
