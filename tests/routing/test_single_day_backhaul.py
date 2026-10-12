"""Business regressions for the opt-in single-day, all-returned-once policy."""
import json

import pytest

from tools.configuration.loader import load_routing_case
from models.common.domain.instance import ProblemInstance
from models.common.domain.node import Customer, Depot, Satellite
from models.common.operating_policy import OperatingPolicy
from models.common.task_builder import build_tasks_for_model
from models.common.solution_audit import audit_exact_solution
from archive.routing_baselines.alns.common.types import Route, Solution
from archive.routing_baselines.alns.model_3 import Evaluator, solve_alns


def problem(policy=True, **overrides):
    nodes = {'D': Depot('D', 0, 0), 'A': Satellite('A', 10, 0, 2),
             'B': Satellite('B', 20, 0, 2)}
    nodes['a'] = Customer('a', 11, 0, 2, 1, (0, 80), (60, 130), 'A')
    nodes['b'] = Customer('b', 21, 0, 2, 1, (0, 80), (90, 130), 'B')
    config = dict(E1_vehicle_count=2, E1_Qk=20, E1_Ck=10,
                  vehicles_per_satellite=2, E2_Qv=10, E2_Cv=5,
                  container_weight=1, container_volume=1, speed=1)
    if policy:
        config['operating_policy'] = dict(policy_version='single_day_backhaul_v1',
            dispatch_time=5, day_end=200, e2_dispatch_prep_time=3,
            e2_return_unload_time=4, depot_return_unload_time=6,
            container_pool_size=2, **overrides)
    p = ProblemInstance(nodes, config)
    p.aggregate_demands_from_pre_assignment()
    return p


def paths():
    return [['D', 'Ad', 'Bd', 'Ap', 'Bp', 'D']], {
        'A': [['A', 'ad', 'ap', 'A']], 'B': [['B', 'bd', 'bp', 'B']]}


def solution(r1, r2):
    s = Solution()
    s.e1_routes = [Route(r[0], r[1:-1]) for r in r1]
    s.e2_routes = {sat: [Route(r[0], r[1:-1]) for r in routes] for sat, routes in r2.items()}
    return s


def test_same_day_timing_waits_locally_and_preserves_counts():
    p = problem()
    audit = audit_exact_solution(p, 3, *paths())
    assert audit['valid'], audit['errors']
    assert audit['container_balance'] == dict(delivered=2, collected=2, returned_to_depot=2)
    a, b = audit['satellite_inventory']['A'], audit['satellite_inventory']['B']
    assert a['ready_time'] == pytest.approx(66)
    assert b['ready_time'] == pytest.approx(96)
    assert a['pickup_start'] < b['ready_time']  # no global all-satellite barrier
    assert a['initial_empty'] == a['final_empty'] == 0
    assert a['peak_empty'] == 1
    timeline = audit['timeline']['e1'][0]
    assert timeline['departure'] == 5
    assert timeline['return_complete'] == pytest.approx(124)
    assert timeline['events'][2]['arrival'] < timeline['events'][2]['service_start']
    assert timeline['waiting'] > 0


def test_interleaved_first_echelon_rejected_by_audit_and_alns():
    p = problem()
    r1, r2 = paths()
    r1[0] = ['D', 'Ad', 'Ap', 'Bd', 'Bp', 'D']
    assert any('after pickup' in e for e in audit_exact_solution(p, 3, r1, r2)['errors'])
    tasks = build_tasks_for_model(p, 3)
    evaluator = Evaluator(p, *tasks, vehicles_per_satellite=2)
    assert not evaluator.check_e1_route_feasible(Route('D', r1[0][1:-1]))
    assert not evaluator.is_feasible(solution(r1, r2))
    assert evaluator.is_feasible(solution(*paths()))


def test_final_return_leg_and_unloading_count_toward_deadline():
    p = problem()
    cfg = p.operating_policy.as_dict()
    cfg = {k: v for k, v in cfg.items() if k in OperatingPolicy.__dataclass_fields__}
    cfg['day_end'] = 123
    p.operating_policy = OperatingPolicy.from_config(cfg)
    audit = audit_exact_solution(p, 3, *paths())
    assert not audit['valid']
    assert any('E1 route 0: return completion' in e for e in audit['errors'])
    evaluator = Evaluator(p, *build_tasks_for_model(p, 3), vehicles_per_satellite=2)
    assert not evaluator.is_feasible(solution(*paths()))


def test_legacy_route_order_and_deadline_are_unchanged():
    p = problem(policy=False)
    r1, r2 = paths()
    # A's return pushes B's delivery past its window unless relaxed explicitly.
    p.nodes['b'].tw_del = (0, 120)
    p.aggregate_demands_from_pre_assignment()
    r1[0] = ['D', 'Ad', 'Ap', 'Bd', 'Bp', 'D']
    assert audit_exact_solution(p, 3, r1, r2)['valid']
    assert p.operating_policy.policy_version == 'legacy'


def test_different_delivery_pickup_vehicles_are_allowed():
    p = problem()
    _, r2 = paths()
    r1 = [['D', 'Ad', 'Bd', 'D'], ['D', 'Ap', 'Bp', 'D']]
    audit = audit_exact_solution(p, 3, r1, r2)
    assert audit['valid'], audit['errors']
    assert audit['e1_delivery_only_routes'] == audit['e1_pickup_only_routes'] == 1


@pytest.mark.parametrize('bad', [
    {}, {'policy_version': 'single_day_backhaul_v1'},
    {'policy_version': 'single_day_backhaul_v1', 'day_end': float('nan')},
    {'policy_version': 'single_day_backhaul_v1', 'day_end': -1},
    {'policy_version': 'single_day_backhaul_v1', 'day_end': 5, 'dispatch_time': 5},
    {'policy_version': 'single_day_backhaul_v1', 'day_end': 100, 'allow_transfers': True},
])
def test_invalid_policy_never_silently_falls_back(bad):
    with pytest.raises(ValueError):
        OperatingPolicy.from_config(bad)


def test_data_balance_pool_and_unsupported_models():
    p = problem()
    p.nodes['a'].container_count_pick = 0
    with pytest.raises(ValueError, match='must match'):
        build_tasks_for_model(p, 3)
    p.nodes['a'].container_count_pick = 1
    p.nodes['a'].container_count_del = p.nodes['a'].container_count_pick = 2
    with pytest.raises(ValueError, match='below daily demand'):
        build_tasks_for_model(p, 3)
    p = problem()
    with pytest.raises(ValueError, match='only Model 3'):
        build_tasks_for_model(p, 4)


def test_stale_aggregate_tasks_rejected():
    p = problem()
    r1, r2 = build_tasks_for_model(p, 3)
    r1[0].v += 1
    with pytest.raises(ValueError, match='tasks must match'):
        Evaluator(p, r1, r2)


def test_volume_only_containers_still_require_e1_delivery():
    p = problem()
    p.fleet.container_weight = 0
    for c in p.customers:
        c.weight_del = 0
    e1, e2 = build_tasks_for_model(p, 3)
    assert {t.id for t in e1} == {'Ad', 'Ap', 'Bd', 'Bp'}
    assert audit_exact_solution(p, 3, *paths())['valid']
    assert Evaluator(p, e1, e2, vehicles_per_satellite=2).is_feasible(solution(*paths()))


@pytest.mark.parametrize('bad', [-1, float('nan'), float('inf')])
def test_invalid_container_dimensions_rejected(bad):
    p = problem()
    p.fleet.container_weight = bad
    with pytest.raises(ValueError, match='finite and nonnegative'):
        build_tasks_for_model(p, 3)


def test_case_load_and_serialized_routes_reaudit():
    cfg, fleet = load_routing_case('config/experiments/baselines/zhou_single_day.json')
    assert cfg['model'] == 3
    assert fleet['operating_policy']['day_end'] == 1440
    p = problem()
    r1, r2 = json.loads(json.dumps(paths()))
    audit = audit_exact_solution(p, 3, r1, r2)
    assert audit['valid']
    json.dumps(audit, allow_nan=False)


def test_alns_output_is_audited_and_not_claimed_optimal():
    p = problem()
    result = solve_alns(p, *build_tasks_for_model(p, 3), 2,
                        max_time=0.2, max_iterations=50, verbose=False, return_info=True)
    assert result[-1]['feasible'], result[-1]
    assert result[-1]['proven_optimal'] is False
    assert audit_exact_solution(p, 3, *result[:2], objective=result[-1]['objective'])['valid']


def test_second_echelon_can_mix_customers_and_wait_for_other_vehicle():
    p = problem()
    p.nodes['b'].assigned_sat = 'A'
    p.nodes['a'].tw_del = (50, 100)
    for c in p.customers:
        c.tw_pick = (0, 160)
    tasks = build_tasks_for_model(p, 3)
    evaluator = Evaluator(p, *tasks, vehicles_per_satellite=2)
    r1 = [['D', 'Ad', 'Ap', 'D']]
    cross = {'A': [['A', 'ad', 'bp', 'A'], ['A', 'bd', 'ap', 'A']], 'B': []}
    mixed = {'A': [['A', 'ad', 'ap', 'bd', 'bp', 'A']], 'B': []}
    for routes in (cross, mixed):
        audit = audit_exact_solution(p, 3, r1, routes)
        assert audit['valid'], audit['errors']
        assert evaluator.is_feasible(solution(r1, routes))
    audit = audit_exact_solution(p, 3, r1, cross)
    ap = next(e for r in audit['timeline']['e2']['A'] for e in r['events'] if e['node'] == 'ap')
    assert ap['service_start'] >= 51


def test_zero_time_cyclic_cross_vehicle_dependencies_rejected():
    p = problem()
    for n in p.nodes.values():
        n.x = n.y = n.service_time = 0
    for c in p.customers:
        c.assigned_sat = 'A'
        c.tw_del = c.tw_pick = (0, 100)
    p._build_matrices()
    tasks = build_tasks_for_model(p, 3)
    r1 = [['D', 'Ad', 'Ap', 'D']]
    r2 = {'A': [['A', 'ap', 'bd', 'A'], ['A', 'bp', 'ad', 'A']], 'B': []}
    assert not audit_exact_solution(p, 3, r1, r2)['valid']
    assert not Evaluator(p, *tasks, vehicles_per_satellite=2).is_feasible(solution(r1, r2))


@pytest.fixture
def exact_solver():
    gp = pytest.importorskip('gurobipy')
    try:
        env = gp.Env(empty=True)
        env.setParam('OutputFlag', 0)
        env.start()
        env.dispose()
    except gp.GurobiError as exc:
        pytest.skip(f'Gurobi license unavailable: {exc}')
    from archive.routing_baselines.variants.model_3.exact_solver import solve_exact
    return solve_exact


def test_exact_backends_agree_and_warm_start_obeys_new_policy(exact_solver):
    p = problem()
    objectives = []
    for backend in ('commodity_flow', 'indexed'):
        result = exact_solver(p, *build_tasks_for_model(p, 3), 2,
                              backend=backend, time_limit=15, verbose=False,
                              return_info=True, warm_start_routes=paths())
        assert result[-1]['proven_optimal'], result[-1]
        audit = audit_exact_solution(p, 3, *result[:2], objective=result[-1]['objective'])
        assert audit['valid'], audit['errors']
        assert result[-1]['operating_policy']['policy_version'] == 'single_day_backhaul_v1'
        objectives.append(result[-1]['objective'])
    assert objectives[0] == pytest.approx(objectives[1])


@pytest.mark.parametrize('backend', ['commodity_flow', 'indexed'])
def test_zero_time_exact_routes_remain_connected_and_ordered(exact_solver, backend):
    p = problem()
    from dataclasses import replace
    p.operating_policy = replace(p.operating_policy, dispatch_time=0,
        e2_dispatch_prep_time=0, e2_return_unload_time=0, depot_return_unload_time=0)
    for n in p.nodes.values():
        n.x = n.y = n.service_time = 0
    for c in p.customers:
        c.assigned_sat = 'A'
        c.tw_del = c.tw_pick = (0, 100)
    p._build_matrices()
    result = exact_solver(p, *build_tasks_for_model(p, 3), 2, backend=backend,
                          time_limit=10, verbose=False, return_info=True)
    assert result[-1]['proven_optimal']
    audit = audit_exact_solution(p, 3, *result[:2])
    assert audit['valid'], audit['errors']


@pytest.mark.parametrize('backend', ['commodity_flow', 'indexed'])
def test_exact_infeasible_closing_time(exact_solver, backend):
    p = problem()
    from dataclasses import replace
    p.operating_policy = replace(p.operating_policy, day_end=59)
    result = exact_solver(p, *build_tasks_for_model(p, 3), 2,
                          backend=backend, time_limit=10, verbose=False, return_info=True)
    assert result[-1]['status'] == 'INFEASIBLE'
    assert result[0] is None and result[-1]['proven_optimal'] is False
