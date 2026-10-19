"""Independent optima, symmetry starts and global certificates after B=1 cuts."""
from collections import Counter
from copy import deepcopy
from dataclasses import replace
import math
from types import SimpleNamespace

import pytest

from models.common.cycle import MultiBatchInstance
from models.common.cycle.assignment import assigned_problem, input_assignment
from models.common.cycle.audit import audit_solution
from models.common.cycle.precheck import travel_lower_bounds
from models.routing.model_3.exact.preprocessing.domains import assignment_domains
from models.routing.model_3.exact.preprocessing.strengthening import joint_arc_estimate, single_batch_bounds
from tests.routing.test_single_batch_solver_audit import tiny_problem, enumerate_tiny


@pytest.mark.parametrize('model', [2, 4])
@pytest.mark.parametrize('seed', range(6))
def test_domain_tree_matches_independent_enumeration_with_forced_splits(monkeypatch, model, seed):
    pytest.importorskip('gurobipy')
    from models.routing.model_4.exact.assignment_search import dispatcher as assignment
    monkeypatch.setattr(assignment, 'JOINT_ARC_LIMIT', 1)
    p = tiny_problem(model, seed)
    optimum = enumerate_tiny(p)
    result = assignment.solve_exact(p, time_limit=5, strategy='domain_tree')
    info = result['info']
    if math.isinf(optimum):
        assert info['proven_infeasible']
    else:
        assert info['proven_optimal'], info
        assert audit_solution(p, result)['valid']
        assert info['objective'] == pytest.approx(optimum)
        assert info['best_bound'] <= optimum+1e-8
    assert info['assignments_closed'] == info['assignment_count']


@pytest.mark.parametrize('model', [1, 2, 3, 4])
@pytest.mark.parametrize('zero_resource', ['container_weight', 'container_volume'])
def test_load_bounds_preserve_optimum_with_a_zero_container_dimension(model, zero_resource):
    pytest.importorskip('gurobipy')
    from models.routing.service import solve_routing
    p = tiny_problem(model, 2)
    setattr(p.fleet, zero_resource, 0)
    p = MultiBatchInstance(p.network, p.orders, p.operating_policy, model=model)
    expected = enumerate_tiny(p)
    result = solve_routing(model=model, method='exact', problem=p, exact_time=5)
    assert result['info']['proven_optimal'] and result['audit']['valid']
    assert result['info']['objective'] == pytest.approx(expected)


def test_trip_symmetry_relabels_a_valid_start_with_unequal_vehicle_workloads():
    pytest.importorskip('gurobipy')
    from models.routing.model_3.exact.enhanced_solver import solve_exact, _set_start
    from models.routing.model_3.exact.model import _build_complete_model as build_model
    base = tiny_problem(1, 1)
    p = assigned_problem(base, {o.customer: 'S' for o in base.orders})
    p.operating_policy = replace(p.operating_policy, e2_max_trips_per_vehicle=2)
    p.fleet.e2_per_sat_count = 2
    p.fleet.e2_weight_cap = 2
    p.fleet.e2_volume_cap = 1
    result = solve_exact(p, time_limit=5)
    assert result['audit']['valid'] and result['info']['proven_optimal']
    assert sorted(Counter(t['vehicle_id'] for t in result['e2_trips']).values()) == [1, 2]
    for trip in result['e2_trips']:
        trip['vehicle_id'] = 1-trip['vehicle_id']
    assert audit_solution(p, result)['valid']
    saved = deepcopy(result)
    m, ctx = build_model(p)
    try:
        assert _set_start(p, m, ctx, result)
        m.update()
        counts = [sum(r['used'].Start for r in ctx['e2'] if r['vehicle'] == v) for v in range(2)]
        assert counts == [2, 1]
        m.Params.TimeLimit = 3
        m.optimize()
        assert m.SolCount and m.ObjVal == pytest.approx(result['info']['objective'])
        assert result == saved
    finally:
        m.dispose()


def test_arc_estimate_matches_materialized_model_after_slot_reduction():
    pytest.importorskip('gurobipy')
    from models.routing.model_3.exact.model import _build_complete_model as build_model
    p = tiny_problem(4, 1)
    p.fleet.e2_per_sat_count = 3
    p.operating_policy = replace(p.operating_policy, e2_max_trips_per_vehicle=12)
    check = assignment_domains(p)
    m, ctx = build_model(p, domain_check=check)
    try:
        assert joint_arc_estimate(p, check) == len({v.index for r in ctx['e2'] for v in r['x'].values()})
        # 6 tasks: only 6+3+2 trip slots per site, instead of 6*3.
        assert len(ctx['e2']) == 22
    finally:
        m.dispose()


def test_inactive_satellite_does_not_apply_its_customer_deadline_to_another_assignment():
    pytest.importorskip('gurobipy')
    from models.routing.model_4.exact.assignment_search.dispatcher import solve_exact
    from tests.routing.test_single_batch_comparison import problem
    p = problem(4, window=(0, 5), duplicate=True, pool=2)
    result = solve_exact(p, time_limit=3, strategy='joint')
    assert result['assignment'] == {'C': 'T'}
    assert result['info']['proven_optimal'] and result['audit']['valid']


def test_tree_timeout_keeps_every_unresolved_assignment_in_global_bound(monkeypatch):
    from models.routing.model_4.exact.assignment_search import domain_tree as assignment_tree
    from models.routing.model_4.exact import enhanced_solver as solver
    from models.routing.model_4.exact.bounds.distance import free_assignment_lower_bound
    from models.routing.model_4.exact.initialization import construct_incumbent
    from models.common.cycle.state import empty_result
    from tests.routing.test_single_batch_comparison import problem
    import time
    p = problem(4)
    travel = travel_lower_bounds(p)
    check = assignment_domains(p, travel)
    bound = free_assignment_lower_bound(p, check['domains'])
    fixed = assigned_problem(p, input_assignment(p))
    incumbent = construct_incumbent(fixed, time.perf_counter()+1)
    assert incumbent and incumbent['audit']['valid']
    assert incumbent['info']['objective'] > enumerate_tiny(p)
    # Keep the same epoch as the geometry helper's real monotonic clock.
    # Starting at zero accidentally makes its deadline expired in a long suite.
    now = [time.perf_counter()]
    expires = now[0]+10
    calls = []
    def limited(node, **kwargs):
        calls.append(node.free_assignment)
        if not node.free_assignment:
            now[0] += kwargs['time_limit']
            copied = deepcopy(incumbent)
            copied['info'].update(proven_optimal=True, best_bound=copied['info']['objective'])
            return copied
        now[0] = expires
        assert kwargs['initial_result']['assignment'] == input_assignment(p)
        result = empty_result(node, 'exact')
        result['info'].update(status='TIME_LIMIT', best_bound=bound)
        return result
    monkeypatch.setattr(solver, 'solve_exact', limited)
    monkeypatch.setattr(assignment_tree, 'time', SimpleNamespace(perf_counter=lambda: now[0]))
    result = assignment_tree.solve_domain_tree(p, 10, 42, False, travel, check, bound, 100000)
    assert calls == [False, True]
    assert result['audit']['valid'] and not result['info']['proven_optimal']
    info = result['info']
    assert info['assignments_closed'] == 0
    assert info['domain_frontier_assignments'] == 2
    assert info['best_bound'] <= enumerate_tiny(p)


def test_forced_capacity_pruning_is_a_necessary_condition_only():
    from models.routing.model_4.exact.assignment_search.domain_tree import _forced_capacity_conflict
    p = tiny_problem(4, 2)
    p.fleet.e1_volume_cap = 1
    domains = {o.customer: ['S', 'T'] for o in p.orders}
    assert not _forced_capacity_conflict(p, domains)
    assert _forced_capacity_conflict(p, {c: ['S'] for c in domains})


def test_bounds_use_relaxed_paths_on_nonmetric_data():
    from tests.routing.test_single_batch_solver_audit import nonmetric_problem
    p = nonmetric_problem()
    travel = travel_lower_bounds(p)
    bounds = single_batch_bounds(p, travel)
    # S->D is 100, but S->T->D is 2; a direct-arc bound would falsely reject S.
    assert bounds['visits']['S', 'p'][1] >= p.operating_policy.batches[0].cutoff


def test_assignment_seed_is_only_an_audited_upper_bound_and_survives_build_timeout(monkeypatch):
    from models.routing.model_4.exact.assignment_search import dispatcher as assignment
    from models.routing.model_4.exact import enhanced_solver as solver
    p = tiny_problem(4, 1)
    p.fleet.e2_per_sat_count = 3
    p.operating_policy = replace(p.operating_policy, e2_max_trips_per_vehicle=12)
    # Exercise seed probes even on an oracle-sized input.
    monkeypatch.setattr(assignment, 'joint_arc_estimate', lambda *a, **kw: 6000)
    def timeout(*args, **kwargs):
        raise TimeoutError('construction budget exhausted')
    monkeypatch.setattr(solver, 'build_model', timeout)
    result = assignment.solve_exact(p, time_limit=1, strategy='joint')
    assert result['audit']['valid'] and audit_solution(p, result)['valid']
    assert result['info']['assignment_seed_probes'] > 0
    assert not result['info']['proven_optimal'] and not result['info']['proven_infeasible']
    assert result['info']['assignments_closed'] == result['info']['domain_assignments_eliminated']
    assert result['info']['best_bound'] <= enumerate_tiny(p)
