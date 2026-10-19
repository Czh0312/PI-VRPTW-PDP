"""Route identities use only route IDs that can visit the particular task."""
from dataclasses import replace
from unittest.mock import patch

import pytest

from models.common.cycle.audit import audit_solution
from models.routing.model_3.exact import enhanced_solver as solver
from models.routing.model_3.exact.formulation import build_model
from tests.routing.test_single_batch_solver_audit import tiny_problem, enumerate_tiny


@pytest.mark.parametrize('model', [1, 2, 3, 4])
@pytest.mark.parametrize('seed', [0, 1, 4])
def test_tight_route_bounds_preserve_independent_optima(model, seed):
    pytest.importorskip('gurobipy')
    p = tiny_problem(model, seed)
    with patch.object(solver, 'build_model', side_effect=lambda *a, **kw: build_model(
            *a, **kw, compress_routes=True, tight_route_bounds=True)):
        result = solver.solve_exact(p, time_limit=5)
    assert result['info']['proven_optimal'] and audit_solution(p, result)['valid']
    assert result['info']['objective'] == pytest.approx(enumerate_tiny(p))
    assert result['info']['e2_tight_route_bounds']


@pytest.mark.parametrize('model', [1, 2, 3, 4])
def test_task_identity_range_matches_available_physical_route_ids(model):
    pytest.importorskip('gurobipy')
    p = tiny_problem(model, 1)
    p.fleet.e2_per_sat_count = 3
    p.operating_policy = replace(p.operating_policy, e2_max_trips_per_vehicle=3)
    sizes = []
    for enabled in (False, True):
        m, ctx = build_model(p, compress_routes=True, tight_route_bounds=enabled)
        try:
            sizes.append((m.NumVars, m.NumConstrs, m.NumGenConstrs))
            for key, var in ctx['e2_route_identities'].items():
                ids = [rid for rid, r in enumerate(ctx['e2'], 1) if any(
                    (n['order'].id, n['kind']) == key for n in r['nodes'])]
                assert (var.LB, var.UB) == ((min(ids), max(ids)) if enabled else (1, len(ctx['e2'])))
            if enabled and not p.free_assignment:
                assert all(var.UB-var.LB < len(ctx['e2'])-1
                           for var in ctx['e2_route_identities'].values())
        finally:
            m.dispose()
    assert sizes[0] == sizes[1]


def test_forbidden_edge_between_different_warehouses_remains_absent():
    pytest.importorskip('gurobipy')
    p = tiny_problem(3, 1)
    m, ctx = build_model(p, compress_routes=True, tight_route_bounds=True)
    try:
        assert all(p.order_map[left[0]].satellite == p.order_map[right[0]].satellite
                   for left, right in ctx['global_e2_arcs'])
    finally:
        m.dispose()


def test_linear_identity_activation_uses_endpoint_ranges_in_both_directions():
    pytest.importorskip('gurobipy')
    p = tiny_problem(3, 1)
    p.fleet.e2_per_sat_count = 2
    p.operating_policy = replace(p.operating_policy, e2_max_trips_per_vehicle=3)
    m, ctx = build_model(p, compress_routes=True, tight_route_bounds=True)
    try:
        left, right = next(iter(ctx['global_e2_arcs']))
        a, b = ctx['e2_route_identities'][left], ctx['e2_route_identities'][right]
        arc = ctx['global_e2_arcs'][left, right]
        expected = {(1.0, -1.0): max(0, a.UB-b.LB),
                    (-1.0, 1.0): max(0, b.UB-a.LB)}
        seen = set()
        for c in m.getConstrs():
            row = m.getRow(c)
            if row.size() == 3:
                coefficients = {row.getVar(i).index: row.getCoeff(i) for i in range(row.size())}
                direction = coefficients.get(a.index), coefficients.get(b.index)
                if arc.index in coefficients and direction in expected:
                    assert coefficients[arc.index] == expected[direction]
                    assert c.RHS == expected[direction]
                    assert c.RHS < len(ctx['e2'])-1
                    seen.add(direction)
        assert seen == set(expected)
    finally:
        m.dispose()
