"""Independent oracle, domain and certificate checks for joint M2/M4 MILPs."""
from dataclasses import replace
import itertools
import math
import time

import pytest

from models.routing.common.cycle import MultiBatchInstance
from models.routing.common.cycle.assignment import assigned_problem, input_assignment
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.exact.assignment import solve_exact
from models.routing.solvers.exact.bounds import free_assignment_lower_bound
from models.routing.solvers.exact.domains import assignment_domains
from tests.routing.test_single_batch_solver_audit import tiny_problem, enumerate_tiny
from tests.routing.test_single_batch_comparison import problem as one_customer


@pytest.mark.parametrize('model', [2, 4])
@pytest.mark.parametrize('seed', range(6))
def test_joint_optimum_and_global_bound_match_independent_full_enumeration(model, seed):
    pytest.importorskip('gurobipy')
    p = tiny_problem(model, seed)
    optimum = enumerate_tiny(p)
    check = assignment_domains(p)
    if math.isfinite(optimum):
        assert check['passed']
        assert free_assignment_lower_bound(p, check['domains']) <= optimum+1e-8
    result = solve_exact(p, time_limit=5, strategy='joint')
    if math.isinf(optimum):
        assert result['info']['proven_infeasible']
    else:
        assert result['audit']['valid'] and audit_solution(p, result)['valid']
        assert result['info']['proven_optimal'], result['info']
        assert result['info']['objective'] == pytest.approx(optimum)
        assert result['info']['best_bound'] <= optimum+1e-8
        assert result['info']['assignments_visited'] == 0


@pytest.mark.parametrize('model', [2, 4])
def test_joint_rescues_original_assignment_and_keeps_repeated_customer_orders_together(model):
    pytest.importorskip('gurobipy')
    p = one_customer(model, window=(0, 5), duplicate=True, pool=2)
    check = assignment_domains(p)
    assert check['domains'] == {'C': ['T']}
    result = solve_exact(p, time_limit=3, strategy='joint')
    assert result['assignment'] == {'C': 'T'}
    assert result['info']['proven_optimal'] and result['audit']['valid']
    assert result['info']['objective'] == pytest.approx(4)
    assert result['info']['domain_assignments_eliminated'] == 1
    assert result['info']['assignments_closed'] == 2
    assert {r['satellite_id'] for r in result['e2_trips']} == {'T'}
    assert sum(e['quantity'] for r in result['e1_trips'] for e in r['events'] if e['kind'] == 'd') == 2


@pytest.mark.parametrize('model', [2, 4])
def test_joint_and_enumeration_agree_with_multiple_vehicles_trip_slots_and_capacity(model):
    pytest.importorskip('gurobipy')
    base = tiny_problem(model, 4)
    p = MultiBatchInstance(base.network, base.orders,
                           replace(base.operating_policy, e2_max_trips_per_vehicle=2), model=model)
    p.fleet.e2_per_sat_count = 2
    joint = solve_exact(p, time_limit=5, strategy='joint')
    enumerated = solve_exact(p, time_limit=5, strategy='enumeration')
    assert joint['info']['proven_optimal'] and enumerated['info']['proven_optimal']
    assert joint['audit']['valid'] and enumerated['audit']['valid']
    assert joint['info']['objective'] == pytest.approx(enumerated['info']['objective'])


@pytest.mark.parametrize('model', [2, 4])
def test_empty_orders_joint_zero_objective(model):
    pytest.importorskip('gurobipy')
    base = one_customer(model)
    p = MultiBatchInstance(base.network, [], base.operating_policy, model=model)
    result = solve_exact(p, time_limit=2, strategy='joint')
    assert result['info']['proven_optimal'] and result['audit']['valid']
    assert result['assignment'] == {} and result['info']['objective'] == 0


def test_joint_contains_no_quadratic_quantity_times_visit_products():
    pytest.importorskip('gurobipy')
    from models.routing.solvers.exact.formulation import build_model
    p = tiny_problem(4, 1)
    model, ctx = build_model(p)
    try:
        assert len(ctx['assignments']) == 6
        assert model.NumQConstrs == 0 and model.NumQNZs == 0
    finally:
        model.dispose()


def test_joint_build_timeout_returns_audited_seed_without_proof(monkeypatch):
    pytest.importorskip('gurobipy')
    from models.routing.solvers.exact import solver
    def timeout(*args, **kwargs):
        raise TimeoutError('test interrupted construction')
    monkeypatch.setattr(solver, 'build_model', timeout)
    result = solve_exact(one_customer(4), time_limit=1, strategy='joint')
    assert result['info']['status'] == 'TIME_LIMIT_BUILD'
    assert result['audit']['valid']
    assert not result['info']['proven_optimal'] and not result['info']['proven_infeasible']
    assert result['info']['assignments_closed'] == 0
    assert 0 < result['info']['best_bound'] <= result['info']['objective']


def test_global_bound_covers_unvisited_assignments_without_claiming_closure(monkeypatch):
    from models.routing.solvers.exact import assignment, solver
    from models.routing.solvers.exact.primal import construct_incumbent
    p = one_customer(2)
    def unclosed(fixed, **kwargs):
        result = construct_incumbent(fixed, time.perf_counter()+1)
        result['info'].update(status='TIME_LIMIT', proven_optimal=False, best_bound=0)
        return result
    monkeypatch.setattr(solver, 'solve_exact', unclosed)
    monkeypatch.setattr(assignment, '_assignment_stream', lambda problem, domains: iter([input_assignment(problem)]))
    result = solve_exact(p, time_limit=1, strategy='enumeration')
    assert result['info']['assignments_visited'] == 1
    assert result['info']['status'] == 'TIME_LIMIT' and not result['info']['proven_optimal']
    assert 0 < result['info']['best_bound'] <= 4  # Unvisited T has exact optimum 4.


def test_domain_and_lower_bound_use_directed_shortest_distances_not_direct_round_trips():
    p = one_customer(4)
    # Cheap D->S->T->D paths can beat the direct D<->T round trip.
    for a, row in p.network.node_index.items():
        for b, col in p.network.node_index.items():
            p.network.dist[row][col] = 0 if a == b else 50
    for a, b in [('D', 'S'), ('S', 'T'), ('T', 'D'), ('T', 'C'), ('C', 'T')]:
        p.network.dist[p.network.node_index[a]][p.network.node_index[b]] = 1
    check = assignment_domains(p)
    optimum = enumerate_tiny(p)
    assert math.isfinite(optimum)
    assert free_assignment_lower_bound(p, check['domains']) <= optimum


def test_preferred_assignments_are_complete_unique_and_respect_domains():
    from models.routing.solvers.exact.assignment import _assignment_stream
    p = tiny_problem(4, 1)
    domains = {c: list(p.satellites) for c in input_assignment(p)}
    domains[next(iter(domains))] = [p.satellites[0]]
    stream = list(_assignment_stream(p, domains))
    expected = {tuple(zip(sorted(domains), values)) for values in itertools.product(*(domains[c] for c in sorted(domains)))}
    assert len(stream) == len(expected)
    assert {tuple(sorted(a.items())) for a in stream} == expected


def test_domain_filter_does_not_reject_any_feasible_independent_tiny_partition():
    p = one_customer(4, window=(0, 5), duplicate=True, pool=2)
    check = assignment_domains(p)
    customers = sorted(check['domains'])
    feasible = 0
    for sites in itertools.product(p.satellites, repeat=len(customers)):
        assignment = dict(zip(customers, sites))
        fixed = assigned_problem(p, assignment)
        if math.isfinite(enumerate_tiny(fixed)):
            feasible += 1
            assert all(s in check['domains'][c] for c, s in assignment.items())
    assert feasible > 0


def test_fixed_partition_warm_start_never_transfers_its_local_certificate(monkeypatch):
    from models.routing.solvers.exact import solver
    from models.routing.solvers.exact.primal import construct_incumbent
    base = one_customer(4, pool=12)
    p = MultiBatchInstance(base.network, [replace(base.orders[0], id=f'o{i}') for i in range(12)],
                           replace(base.operating_policy, e2_max_trips_per_vehicle=10), model=4)
    p.fleet.e2_per_sat_count = 2
    calls = []
    def finish_without_joint_search(problem, **kwargs):
        calls.append(problem.free_assignment)
        if not problem.free_assignment:
            result = construct_incumbent(problem, time.perf_counter()+1)
            assert result and result['audit']['valid']
            result['info'].update(status='OPTIMAL', proven_optimal=True,
                                   best_bound=result['info']['objective'], mip_gap=0)
            return result
        result = kwargs['initial_result']
        assert result['audit']['valid']
        assert not result['info']['proven_optimal']
        assert result['info']['best_bound'] <= 4  # The other satellite attains 4.
        return result
    monkeypatch.setattr(solver, 'solve_exact', finish_without_joint_search)
    result = solve_exact(p, time_limit=2, strategy='joint')
    assert calls == [False, True]
    assert result['info']['status'] == 'TIME_LIMIT'
    assert not result['info']['proven_optimal']
    assert result['info']['assignments_closed'] == 0
    assert result['info']['initial_partition_solves'] == 1
