"""Linear activation must preserve inactive copies and complete exact optima."""
from copy import deepcopy
from dataclasses import replace
import time
from unittest.mock import patch

import pytest

from models.common.cycle import MultiBatchInstance
from models.common.cycle.audit import audit_solution
from models.routing.model_3.exact.formulation import build_model
from tests.routing.test_single_batch_solver_audit import tiny_problem, enumerate_tiny
from tests.routing.test_single_batch_primal import split_problem


@pytest.mark.parametrize('model', [1, 2, 3, 4])
@pytest.mark.parametrize('seed', [0, 1, 4])
@pytest.mark.parametrize('compressed', [False, True])
@pytest.mark.parametrize('mode', ['identity', 'internal', 'all', 'complete'])
def test_linear_profiles_match_independent_enumeration(model, seed, compressed, mode):
    pytest.importorskip('gurobipy')
    from models.routing.model_3.exact import enhanced_solver as solver
    p = tiny_problem(model, seed)
    expected = enumerate_tiny(p)
    with patch.object(solver, 'build_model', side_effect=lambda *a, **kw: build_model(
            *a, **kw, compress_routes=compressed, linear_e2_links=mode)):
        result = solver.solve_exact(p, time_limit=5)
    assert result['info']['proven_optimal'], result['info']
    assert audit_solution(p, result)['valid']
    assert result['info']['objective'] == pytest.approx(expected)


@pytest.mark.parametrize('zero_resource', ['container_weight', 'container_volume'])
def test_linear_loads_preserve_zero_container_dimensions(zero_resource):
    pytest.importorskip('gurobipy')
    from models.routing.model_3.exact import enhanced_solver as solver
    p = tiny_problem(4, 2)
    setattr(p.fleet, zero_resource, 0)
    p = MultiBatchInstance(p.network, p.orders, p.operating_policy, model=4)
    expected = enumerate_tiny(p)
    with patch.object(solver, 'build_model', side_effect=lambda *a, **kw: build_model(
            *a, **kw, compress_routes=True, linear_e2_links=True)):
        result = solver.solve_exact(p, time_limit=5)
    assert result['info']['proven_optimal'] and audit_solution(p, result)['valid']
    assert result['info']['objective'] == pytest.approx(expected)


@pytest.mark.parametrize('mode', ['identity', 'internal', 'all', 'complete'])
@pytest.mark.parametrize('factored', [False, True])
def test_split_trip_start_extends_with_inactive_copies_and_all_profiles(mode, factored):
    gp = pytest.importorskip('gurobipy')
    from models.routing.model_3.exact.initialization import construct_incumbent
    from models.routing.model_3.exact.enhanced_solver import _set_start
    p = split_problem()
    p.fleet.e2_per_sat_count = 2
    start = construct_incumbent(p, time.perf_counter()+1)
    assert start and audit_solution(p, start)['valid']
    saved = deepcopy(start)
    m, ctx = build_model(p, compress_routes=True, linear_e2_links=mode, factor_e2_loads=factored)
    try:
        assert _set_start(p, m, ctx, start)
        m.update()
        for variable in m.getVars():
            if variable.Start != gp.GRB.UNDEFINED:
                variable.LB = variable.UB = variable.Start
        m.Params.TimeLimit = 3
        m.optimize()
        assert m.SolCount and m.ObjVal == pytest.approx(start['info']['objective'])
        assert start == saved
    finally:
        m.dispose()


def test_unused_site_windows_do_not_bound_task_times_of_another_site():
    pytest.importorskip('gurobipy')
    from models.routing.model_3.exact import enhanced_solver as solver
    from tests.routing.test_single_batch_comparison import problem
    p = problem(4, window=(0, 5), duplicate=True, pool=2)
    with patch.object(solver, 'build_model', side_effect=lambda *a, **kw: build_model(
            *a, **kw, compress_routes=True, linear_e2_links='complete')):
        result = solver.solve_exact(p, time_limit=5)
    assert result['info']['proven_optimal'] and audit_solution(p, result)['valid']
    assert result['assignment'] == {'C': 'T'}


def test_linear_links_reduce_general_constraints_without_adding_variables():
    pytest.importorskip('gurobipy')
    p = tiny_problem(4, 1)
    p.fleet.e2_per_sat_count = 2
    p.operating_policy = replace(p.operating_policy, e2_max_trips_per_vehicle=3)
    sizes = []
    for mode in (False, 'identity', 'internal', 'all', 'complete'):
        m, ctx = build_model(p, compress_routes=True, linear_e2_links=mode, factor_e2_loads=False)
        try:
            sizes.append((m.NumVars, m.NumGenConstrs))
        finally:
            m.dispose()
    assert len({size[0] for size in sizes}) == 1
    assert all(size[1] < sizes[0][1] for size in sizes[1:])
    assert sizes[-1][1] < min(size[1] for size in sizes[1:-1])


@pytest.mark.parametrize('model', [2, 4])
@pytest.mark.parametrize('site', ['S', 'T'])
def test_free_assignment_start_extends_with_all_other_site_copies_disabled(model, site):
    gp = pytest.importorskip('gurobipy')
    from models.common.cycle.assignment import assigned_problem, lift_result
    from models.routing.model_3.exact.initialization import construct_incumbent
    from models.routing.model_3.exact.enhanced_solver import _set_start
    p = tiny_problem(model, 1)
    assignment = {o.customer: site for o in p.orders}
    start = construct_incumbent(assigned_problem(p, assignment), time.perf_counter()+1)
    assert start
    start = lift_result(p, start, assignment)
    assert audit_solution(p, start)['valid']
    m, ctx = build_model(p, compress_routes=True, linear_e2_links='complete')
    try:
        assert _set_start(p, m, ctx, start)
        m.update()
        for variable in m.getVars():
            if variable.Start != gp.GRB.UNDEFINED:
                variable.LB = variable.UB = variable.Start
        m.Params.TimeLimit = 3
        m.optimize()
        assert m.SolCount and m.ObjVal == pytest.approx(start['info']['objective'])
    finally:
        m.dispose()


@pytest.mark.parametrize('model', [1, 2, 3, 4])
@pytest.mark.parametrize('compressed', [False, True])
def test_default_profile_and_explicit_disable_follow_formulation_structure(model, compressed):
    pytest.importorskip('gurobipy')
    p = tiny_problem(model, 1)
    expected = compressed or (not p.free_assignment and p.reusable)
    for enabled in (None, False):
        m, ctx = build_model(p, compress_routes=compressed, linear_e2_links=enabled)
        try:
            assert ctx['single_batch_linear_links'] == ('complete' if expected and enabled is None else False)
            assert bool(ctx['factored_e2_loads']) == (compressed and expected and enabled is None)
        finally:
            m.dispose()


def test_departure_auxiliaries_reduce_matrix_nonzeros_and_match_start_loads():
    pytest.importorskip('gurobipy')
    p = tiny_problem(4, 1)
    p.fleet.e2_per_sat_count = 2
    p.operating_policy = replace(p.operating_policy, e2_max_trips_per_vehicle=3)
    sizes = []
    for factored in (False, True):
        m, ctx = build_model(p, compress_routes=True, linear_e2_links='complete', factor_e2_loads=factored)
        try:
            sizes.append((m.NumVars, m.NumNZs, len(ctx['e2'])))
            assert sum(len(r['departure_loads']) for r in ctx['e2']) == (2*len(ctx['e2']) if factored else 0)
        finally:
            m.dispose()
    assert sizes[1][0] == sizes[0][0]+2*sizes[0][2]
    assert sizes[1][1] < sizes[0][1]
