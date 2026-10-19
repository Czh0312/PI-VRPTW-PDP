"""Independent raw path/partition and full integer schedule proof checks."""
from copy import deepcopy, copy
from fractions import Fraction
import itertools
import math
import time

import pytest

from models.common.cycle.audit import audit_solution
from models.routing.model_3.exact.bounds import composite_proof as composite
from models.routing.model_3.exact.bounds.route_cg.columns import problem_fingerprint
from models.routing.model_3.exact.enhanced_solver import solve_exact
from tests.routing.test_single_batch_solver_audit import tiny_problem, enumerate_tiny
from tests.routing.test_single_batch_e1_clocked import independent_routes


def independent_partition(p):
    """Every raw clocked path, then bottom-up vehicle-layer subset relaxation."""
    tasks, raw = independent_routes(p)
    paths = []
    for sequence, _ in raw:
        nodes = [p.depot]+[s for s, _ in sequence]+[p.depot]
        cost = sum((Fraction(float(p.distance(a, b))) for a,b in zip(nodes, nodes[1:])), Fraction())
        mask = sum(1 << tasks.index(t) for t in sequence)
        paths.append((mask, cost))
    states = {0: Fraction()}
    for _ in range(p.fleet.e1_count):
        layer = dict(states)
        for used, cost in states.items():
            for mask, arc in paths:
                if not used & mask:
                    key = used | mask
                    value = cost+arc
                    if key not in layer or value < layer[key]:
                        layer[key] = value
        states = layer
    return states.get((1 << len(tasks))-1)


@pytest.mark.parametrize('seed', range(12))
@pytest.mark.parametrize('vehicles', [1, 2])
def test_exact_e1_matches_independent_raw_path_mips_and_partition(seed, vehicles):
    p = copy(tiny_problem(3, seed)); p.fleet = copy(p.fleet); p.fleet.e1_count = vehicles
    expected = independent_partition(p)
    actual = composite.e1_integer_cover(p)
    assert actual['complete']
    if expected is None:
        assert actual['status'] == 'COVER_INFEASIBLE'
    else:
        assert Fraction(*actual['exact_cost']) == expected
        assert actual['bound'] <= float(expected)


@pytest.mark.parametrize('seed', [0, 1, 2, 4, 5])
def test_composite_and_fallback_match_independent_full_integer_enumeration(seed):
    p = tiny_problem(3, seed); fp = problem_fingerprint(p)
    initial = solve_exact(p, time_limit=3)
    original = deepcopy(initial)
    result = composite.solve_exact_with_composite_proof(p, time_limit=5, initial_result=initial)
    assert result['info']['proven_optimal']
    assert result['info']['objective'] == pytest.approx(enumerate_tiny(p))
    assert audit_solution(p, result)['valid'] and result['audit']['periodic_replay']['valid']
    assert fp == problem_fingerprint(p) and initial == original
    cert = result['info']['component_certificate']
    if cert['closed']:
        assert cert['e1']['complete']
        assert all(r['complete'] and r['exact_match'] for r in cert['sites'].values())
        assert set(cert['sites']) == {o.satellite for o in p.orders}


def test_zero_cost_complete_component_proof_and_cross_trip_physical_projection():
    from tests.routing.test_single_batch_primal import split_problem
    from models.routing.model_3.exact.initialization import construct_incumbent
    p = split_problem(); initial = construct_incumbent(p, time.perf_counter()+2)
    assert len(initial['e2_trips']) > 1
    result = composite.solve_exact_with_composite_proof(p, time_limit=5, initial_result=initial)
    assert audit_solution(p, result)['valid']
    for site, r in result['info']['component_certificate']['sites'].items():
        assert r['original_tasks'] == 2*sum(o.satellite == site for o in p.orders)
        assert r['projection']['trips'] == sum(t['satellite_id'] == site for t in initial['e2_trips'])


def test_complete_proof_closes_without_calling_original_kernel(monkeypatch):
    p = tiny_problem(3, 0); initial = solve_exact(p, 2)
    from models.routing.model_3.exact import enhanced_solver as solver
    monkeypatch.setattr(solver, 'solve_exact', lambda *a, **k: pytest.fail('kernel not required'))
    result = composite.solve_exact_with_composite_proof(p, 5, initial_result=initial)
    assert result['info']['component_certificate']['closed']
    assert result['info']['status'] == 'OPTIMAL_BY_COMPONENTS'


def test_one_ulp_near_equality_cannot_close(monkeypatch):
    p = tiny_problem(3, 1); initial = solve_exact(p, 2)
    real = composite.e1_integer_cover
    def almost(*a, **k):
        record = real(*a, **k)
        target = composite.schedule_costs(p, initial)['e1']
        record['exact_cost'] = composite.encode(target-Fraction(1, 2**80))
        return record
    monkeypatch.setattr(composite, 'e1_integer_cover', almost)
    result = composite.component_certificate(p, initial, 1)
    assert not result['closed'] and result['status'] == 'E1_NOT_TIGHT_AT_INITIAL'
    assert not result['sites']


def test_one_ulp_site_objective_difference_cannot_close(monkeypatch):
    p = tiny_problem(3, 0); initial = solve_exact(p, 2)
    real = composite.integer_objective
    monkeypatch.setattr(composite, 'integer_objective', lambda model: real(model)+Fraction(1, 2**80))
    result = composite.component_certificate(p, initial, 2)
    assert not result['closed'] and result['status'] == 'SITE_NOT_TIGHT_AT_INITIAL'


def test_missing_or_expired_component_cannot_inherit_initial_proof():
    p = tiny_problem(3, 0); initial = solve_exact(p, 2)
    assert initial['info']['proven_optimal']
    result = composite.solve_exact_with_composite_proof(p, 1e-8, initial_result=initial)
    assert audit_solution(p, result)['valid']
    assert not result['info']['proven_optimal'] and not result['info']['component_certificate']['closed']
    empty = composite.component_certificate(p, None, 1)
    assert not empty['closed'] and empty['bound'] == 0
    for opts in ({'deadline':time.perf_counter()-1}, {'max_sites':0}):
        cover = composite.e1_integer_cover(p, **opts)
        assert not cover['complete'] and cover['bound'] == 0


def test_fallback_receives_no_floor_hook_and_never_adds_total_bound_twice(monkeypatch):
    from models.routing.model_3.exact import enhanced_solver as solver
    p = tiny_problem(3, 1); initial = solve_exact(p, 2)
    def kernel(problem, budget, seed, verbose, start):
        result = deepcopy(start)
        result['info'].update(best_bound=4., proven_optimal=False)
        return result
    monkeypatch.setattr(solver, 'solve_exact', kernel)
    monkeypatch.setattr(composite, 'component_certificate', lambda *a, **k: dict(closed=False, bound=3.))
    result = composite.solve_exact_with_composite_proof(p, 1, initial_result=initial)
    assert result['info']['best_bound'] == 4.
    assert not result['info']['proven_optimal']


@pytest.mark.parametrize('budget', [0, -1, math.inf, math.nan, True])
def test_budget_guard(budget):
    with pytest.raises(ValueError, match='budget'):
        composite.component_certificate(tiny_problem(3, 0), None, budget)


@pytest.mark.parametrize('model', [1, 2, 4])
def test_scope_guard(model):
    with pytest.raises(ValueError, match='M3'):
        composite.component_certificate(tiny_problem(model, 0), None)


def test_invalid_original_schedule_cannot_prove():
    p = tiny_problem(3, 0); initial = solve_exact(p, 2)
    initial['e2_trips'][0]['events'].pop()
    with pytest.raises(ValueError, match='audited'):
        composite.component_certificate(p, initial)
