"""Equivalence and preservation tests for the second solver optimization."""
from copy import deepcopy
from dataclasses import replace
import random
import time

import pytest

from models.common.cycle import MultiBatchInstance
from models.common.cycle.assignment import assigned_problem, input_assignment
from models.common.cycle.audit import audit_solution
from models.common.cycle.precheck import check_necessary_conditions, travel_lower_bounds
from models.routing.model_3.alns.construction import reassigned_state
from models.routing.model_3.alns.evaluator import initial_state, evaluate
from models.routing.model_3.alns.neighborhoods import repair_orders, state_key
from tests.routing.test_single_batch_solver_audit import tiny_problem


@pytest.mark.parametrize('seed', range(6))
@pytest.mark.parametrize('model', [1, 3])
def test_distance_screen_matches_unscreened_repair_on_directed_nonmetric_arcs(seed, model):
    p = tiny_problem(model, seed)
    state = initial_state(p, random.Random(seed), randomized=True)
    before = deepcopy(state)
    removed = list(p.orders)[:2]
    screened = repair_orders(p, state, removed, time.perf_counter()+5)
    reference = repair_orders(p, state, removed, time.perf_counter()+5, screen=False)
    assert screened is not None and reference is not None
    assert state_key(screened) == state_key(reference)
    assert state == before
    assert repair_orders(p, state, removed, 0) is None


def test_assignment_projection_moves_every_order_of_customer_and_preserves_others():
    base = tiny_problem(4, 2)
    p = MultiBatchInstance(base.network, list(base.orders)+[replace(base.orders[0], id='repeat')],
                           base.operating_policy, model=4)
    assignment = input_assignment(p)
    fixed = assigned_problem(p, assignment)
    original = initial_state(fixed, random.Random(1))
    _, decoded, issues = evaluate(fixed, original)
    assert not issues and audit_solution(fixed, decoded)['valid']
    customer = p.orders[0].customer
    assignment[customer] = next(s for s in p.satellites if s != assignment[customer])
    changed = assigned_problem(p, assignment)
    saved = deepcopy(decoded)
    projected = reassigned_state(changed, decoded, time.perf_counter()+2)
    assert projected is not None and decoded == saved
    assert sorted(t for r in projected['e2'] for t in r['tasks']) == sorted(
        (o.id, k) for o in changed.orders for k in changed.task_kinds)
    assert all(changed.order_map[oid].satellite == r['satellite']
               for r in projected['e2'] for oid, _ in r['tasks'])
    for old in original['e2']:
        kept = [t for t in old['tasks'] if p.order_map[t[0]].customer != customer]
        if kept:
            assert any([t for t in r['tasks'] if p.order_map[t[0]].customer != customer] == kept
                       for r in projected['e2'])
    _, result, issues = evaluate(changed, projected)
    assert not issues and audit_solution(changed, result)['valid']


def test_travel_bounds_are_scoped_to_solve_and_service_cache_observes_edits():
    p = tiny_problem(3, 2)
    context = travel_lower_bounds(p)
    assert check_necessary_conditions(p, context) == check_necessary_conditions(p)
    assert p.service('S') == 0
    p.network.nodes['S'].service_time = 2
    assert p.service('S') == 2
    for a, b in p._travel:
        if a != b:
            p._travel[a, b] = 1000
    assert not check_necessary_conditions(p)['passed']


@pytest.mark.parametrize('model', [1, 3])
@pytest.mark.parametrize('seed', [0, 1, 2, 4])
def test_shared_event_milp_matches_indexed_reference_with_multiple_vehicles_and_trips(monkeypatch, model, seed):
    pytest.importorskip('gurobipy')
    from models.routing.model_3.exact import enhanced_solver as solver
    from models.routing.model_3.exact.model import _build_complete_model as build_model
    base = tiny_problem(model, seed)
    p = MultiBatchInstance(base.network, base.orders,
                           replace(base.operating_policy, e2_max_trips_per_vehicle=2), model=model)
    p.fleet.e2_per_sat_count = 2
    compact = solver.solve_exact(p, time_limit=5)
    def indexed(*args, **kwargs):
        return build_model(*args, **kwargs, share_e2=False)
    monkeypatch.setattr(solver, 'build_model', indexed)
    reference = solver.solve_exact(p, time_limit=5)
    assert compact['info']['proven_optimal'] and reference['info']['proven_optimal']
    assert compact['audit']['valid'] and reference['audit']['valid']
    assert compact['info']['objective'] == pytest.approx(reference['info']['objective'])
    assert compact['info']['constraints'] < reference['info']['constraints']


def test_primal_seed_is_audited_and_failure_is_not_an_infeasibility_proof():
    from models.routing.model_3.exact.initialization import construct_incumbent
    base = tiny_problem(3, 1)
    # An exhausted construction budget conveys no infeasibility proof.
    assert construct_incumbent(base, 0) is None
    p = MultiBatchInstance(base.network, base.orders,
                           replace(base.operating_policy, e2_max_trips_per_vehicle=3), model=3)
    result = construct_incumbent(p, time.perf_counter()+1)
    assert result is not None and audit_solution(p, result)['valid']
    assert not result['info']['proven_optimal'] and not result['info']['proven_infeasible']


def test_mip_start_relabels_sparse_vehicle_ids_and_keeps_audited_incumbent_on_build_timeout(monkeypatch):
    pytest.importorskip('gurobipy')
    from models.routing.model_3.exact import enhanced_solver as solver
    from models.routing.model_3.exact.model import _build_complete_model as build_model
    from models.routing.model_3.exact.initialization import construct_incumbent
    p = tiny_problem(3, 2)
    p.fleet.e1_count = p.fleet.e2_per_sat_count = 3
    start = construct_incumbent(p, time.perf_counter()+1)
    assert start is not None
    # The single E2 trip at each satellite is valid on vehicle 2, but the
    # symmetry constraints and task-count cap require a relabeled start.
    for trip in start['e2_trips']:
        trip['vehicle_id'] = 2
    for trip in start['e1_trips']:
        trip['vehicle_id'] += 1
    assert audit_solution(p, start)['valid']
    saved = deepcopy(start)
    model, ctx = build_model(p)
    try:
        assert solver._set_start(p, model, ctx, start)
        model.update()
        assert all(r['used'].Start == int(r['vehicle'] == 0) for r in ctx['e2'])
        model.Params.TimeLimit = 3
        model.optimize()
        assert model.SolCount > 0
    finally:
        model.dispose()
    def timeout(*args, **kwargs):
        raise TimeoutError('test construction budget')
    monkeypatch.setattr(solver, 'build_model', timeout)
    result = solver.solve_exact(p, time_limit=.001, initial_result=start)
    assert result['info']['status'] == 'TIME_LIMIT_BUILD'
    assert result['audit']['valid'] and audit_solution(p, result)['valid']
    assert result['info']['objective'] <= start['info']['objective']
    assert not result['info']['proven_optimal']
    assert start == saved
