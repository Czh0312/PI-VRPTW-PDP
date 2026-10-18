"""Physical edge cases for the shared-task MILP formulation of M6--M8."""
from importlib import import_module
from dataclasses import replace

import pytest

from tests.routing.test_model6_joint import joint_fixture, exact_available


def build(number, problem):
    name = 'model' if number == 6 else 'formulations.fixed_count'
    return import_module(f'models.routing.model_{number}.exact.{name}').build_model(problem, compact_e2=True)


def fix_route(model, route, tasks):
    lookup = {(n['order'].id, n['kind']): i for i, n in enumerate(route['nodes'])}
    order = [-1] + [lookup[task] for task in tasks] + [-2]
    selected = set(zip(order, order[1:])) if tasks else set()
    assert selected.issubset(route['x'])
    for arc, variable in route['x'].items():
        model.addConstr(variable == int(arc in selected))


@pytest.mark.parametrize('number', [6, 7, 8])
@pytest.mark.parametrize('split_pickups', [False, True])
def test_shared_loads_allow_distinct_trips_and_pickup_only_routes(number, split_pickups, exact_available):
    # Zero travel/service allows simultaneous events. Causal order still has to
    # exist, and a pickup-only trip must not acquire a spurious delivery batch.
    problem = joint_fixture(zero=True, one_batch=True)
    problem.fleet.e2_weight_cap = 4 if split_pickups else 2
    problem.fleet.e2_volume_cap = 2 if split_pickups else 1
    model, ctx = build(number, problem)
    try:
        plans = ([('o1', 'd'), ('o2', 'd')], [('o1', 'p'), ('o2', 'p')]) if split_pickups else (
            [('o1', 'd'), ('o1', 'p')], [('o2', 'd'), ('o2', 'p')])
        for i, route in enumerate(ctx['e2']):
            fix_route(model, route, plans[i] if i < len(plans) else [])
        model.Params.TimeLimit = 3
        model.optimize()
        assert model.Status == 2
        assert ctx['e2'][1]['loaded'].getValue() == (0 if split_pickups else 1)
        # End-of-delivery loads are different across the two distinct routes;
        # the shared state must never propagate across the satellite boundary.
        loads = ctx['variables'].task_load_weight['S']
        assert all(v.X <= problem.fleet.e2_weight_cap for v in loads.values())
    finally:
        model.dispose()


@pytest.mark.parametrize('number', [6, 7, 8])
def test_shared_loads_still_reject_an_overcapacity_delivery_trip(number, exact_available):
    problem = joint_fixture(zero=True, one_batch=True)
    problem.fleet.e2_weight_cap = 3  # Two deliveries initially weigh four.
    model, ctx = build(number, problem)
    try:
        fix_route(model, ctx['e2'][0], [('o1', 'd'), ('o2', 'd'), ('o1', 'p'), ('o2', 'p')])
        model.Params.TimeLimit = 3
        model.optimize()
        assert model.Status == 3
    finally:
        model.dispose()


@pytest.mark.parametrize('number', [6, 7, 8])
def test_delivery_trip_cannot_mix_two_productive_batches(number, exact_available):
    problem = joint_fixture(zero=True)
    model, ctx = build(number, problem)
    try:
        # Exactly two orders and two productive batches force distinct batches.
        route = ctx['e2'][0]
        for i, node in enumerate(route['nodes']):
            if node['kind'] == 'd':
                model.addConstr(route['visit'][i] == 1)
        model.Params.TimeLimit = 3
        model.optimize()
        assert model.Status == 3
    finally:
        model.dispose()


@pytest.mark.parametrize('number', [6, 7, 8])
def test_zero_duration_subtour_is_excluded_by_shared_causality(number, exact_available):
    problem = joint_fixture(zero=True, one_batch=True)
    model, ctx = build(number, problem)
    try:
        route = ctx['e2'][0]
        # Disconnected delivery cycle has zero travel and service, so time
        # alone cannot rule it out. The task-order variables must do so.
        model.addConstr(route['x'][0, 2] == 1)
        model.addConstr(route['x'][2, 0] == 1)
        model.Params.TimeLimit = 3
        model.optimize()
        assert model.Status == 3
    finally:
        model.dispose()


@pytest.mark.parametrize('number', [6, 7, 8])
def test_assignment_guesses_are_soft_hints_not_partial_starts_or_fixes(number, exact_available):
    problem = joint_fixture()
    model, ctx = build(number, problem)
    try:
        before = [(v.LB, v.UB) for v in ctx['assignments'].values()]
        hints = import_module(f'models.routing.model_{number}.exact.initialization')
        count = hints.install_assignment_hints(problem, model, ctx)
        model.update()
        assert count == len(ctx['assignments'])
        assert model.NumStart == 0
        assert [(v.LB, v.UB) for v in ctx['assignments'].values()] == before
        assert all(v.VarHintVal in (0, 1) for v in ctx['assignments'].values())
        model.Params.TimeLimit = 3
        model.optimize()
        assert model.Status == 2
        assert model.ObjVal == pytest.approx(61)
    finally:
        model.dispose()


@pytest.mark.parametrize('number', [6, 7, 8])
@pytest.mark.parametrize('variant', [0, 3, 6])
def test_compact_formulation_matches_independent_oracle(number, variant, monkeypatch, exact_available):
    from tests.routing.test_model78_independent_oracle import test_full_milp_matches_independent_one_order_oracle
    module = import_module(f'models.routing.model_{number}.exact.' + ('solver' if number == 6 else 'search.fixed_count'))
    original = module.build_model
    monkeypatch.setattr(module, 'build_model', lambda p, **kw: original(p, compact_e2=True, **kw))
    test_full_milp_matches_independent_one_order_oracle(number, variant, exact_available)


@pytest.mark.parametrize('number', [6, 7, 8])
@pytest.mark.parametrize('zero_weight', [False, True])
def test_compact_and_expanded_optima_match_with_nonmetric_distance(number, zero_weight, monkeypatch, exact_available):
    from models.common.cycle.joint import JointBatchInstance
    from models.common.cycle.calendar import CalendarCycleInstance
    from models.common.domain.instance import ProblemInstance
    source = joint_fixture()
    ids = list(source.network.nodes)
    distance = {a: {b: 0 if a == b else 1+(11*i+7*j) % 17 for j, b in enumerate(ids)}
                for i, a in enumerate(ids)}
    travel = {a: {b: source.network.travel_time(a, b) for b in ids} for a in ids}
    network = ProblemInstance(source.network.nodes, dict(E1_vehicle_count=1, vehicles_per_satellite=2,
        E1_Qk=100, E1_Ck=100, E2_Qv=100, E2_Cv=100,
        container_weight=0 if zero_weight else 1, container_volume=1),
        distance_matrix=distance, travel_time_matrix=travel)
    if number == 6:
        problem = JointBatchInstance(network, source.orders, source.operating_policy, {}, source.cost_profile.config)
    else:
        orders = [source.orders[0], replace(source.orders[1], release_time=70, delivery_window=(75, 90))]
        options = dict(batch_count_candidates=[2])
        if number == 7:
            options['loading_mode'] = 'optimized'
        problem = CalendarCycleInstance(network, orders, source.operating_policy, options,
                                        source.cost_profile.config, model=number).for_count(2)
    module = import_module(f'models.routing.model_{number}.exact.' + ('solver' if number == 6 else 'search.fixed_count'))
    original = module.build_model
    results = []
    for compact in (False, True):
        monkeypatch.setattr(module, 'build_model', lambda p, **kw: original(p, compact_e2=compact, **kw))
        result = module.solve_exact(problem, time_limit=5)
        assert result['info']['proven_optimal'] and result['audit']['valid'], result['info']
        results.append(result['info']['objective'])
    assert results[0] == pytest.approx(results[1])
