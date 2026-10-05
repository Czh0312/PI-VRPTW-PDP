"""Certificate distance rows preserve full M5 routes and count each arc once."""
from collections import defaultdict
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.exact.multibatch_bounds import distance_certificate
from tests.routing.test_multibatch_cycle import fixture, hand_solution
from tests.routing.test_multibatch_departure_loads import nonmetric_problem


@pytest.mark.parametrize('seed', [0, 1, 2, 3, 4, 5])
@pytest.mark.parametrize('compressed', [False, True])
def test_all_distance_cut_modes_preserve_nonmetric_integer_optimum(seed, compressed):
    pytest.importorskip('gurobipy')
    from models.routing.solvers.exact import solver
    from models.routing.solvers.exact.formulation import build_model
    problem = nonmetric_problem(seed)
    results = []
    for mode in ('none', 'total', 'components'):
        with patch.object(solver, 'build_model', side_effect=lambda *a, **kw: build_model(
                *a, **kw, compress_multibatch_routes=compressed)):
            result = solver.solve_exact(problem, time_limit=5, distance_cuts=mode)
        assert result['info']['proven_optimal'], result['info']
        assert audit_solution(problem, result)['valid']
        assert result['info']['distance_cut_mode'] == mode
        assert bool(result['info']['distance_cuts_added']) == (mode != 'none' and distance_certificate(problem)['bound'] > 0)
        results.append(result['info']['objective'])
    assert results == pytest.approx([results[0]]*3)


@pytest.mark.parametrize('compressed', [False, True])
@pytest.mark.parametrize('unloaded', [29, 30, 31])
def test_components_count_every_arc_once_and_preserve_cutoff_schedule(compressed, unloaded):
    gp = pytest.importorskip('gurobipy')
    from models.routing.solvers.exact.formulation import build_model
    from models.routing.solvers.exact.solver import _set_start
    problem = fixture(pool=2)
    initial = hand_solution(problem, first_unload=unloaded)
    independent = audit_solution(problem, initial)
    assert independent['valid']
    model, ctx = build_model(problem, distance_cuts='components', compress_multibatch_routes=compressed)
    try:
        combined = defaultdict(float)
        for expression in ctx['distance_component_costs'].values():
            for i in range(expression.size()):
                combined[expression.getVar(i).VarName] += expression.getCoeff(i)
        objective = model.getObjective()
        coefficients = {objective.getVar(i).VarName: objective.getCoeff(i) for i in range(objective.size())}
        assert {name: coefficient for name, coefficient in combined.items() if coefficient} == coefficients
        assert len(ctx['distance_certificate_rows']) == 3
        assert _set_start(problem, model, ctx, initial)
        model.update()
        for variable in model.getVars():
            if variable.Start != gp.GRB.UNDEFINED:
                variable.LB = variable.UB = variable.Start
        model.optimize()
        assert model.SolCount and model.ObjVal == pytest.approx(independent['objective'])
        assert sum(expr.getValue() for expr in ctx['distance_component_costs'].values()) == pytest.approx(independent['objective'])
        assert ctx['pickup_returns']['o1'].X == unloaded
    finally:
        model.dispose()


@pytest.mark.parametrize('seed', [0, 2, 5])
def test_distance_rows_strengthen_linear_relaxation_without_new_variables(seed):
    gp = pytest.importorskip('gurobipy')
    from models.routing.solvers.exact.formulation import build_model
    problem = nonmetric_problem(seed)
    values, sizes = [], []
    for mode in ('none', 'total', 'components'):
        model, ctx = build_model(problem, compress_multibatch_routes=True, distance_cuts=mode)
        relaxed = None
        try:
            sizes.append(model.NumVars)
            relaxed = model.relax()  # Drop the same unchanged E1 indicators in each arm.
            relaxed.Params.Presolve = 0
            relaxed.optimize()
            assert relaxed.Status == gp.GRB.OPTIMAL
            values.append(relaxed.ObjVal)
            if mode != 'none':
                assert relaxed.ObjVal >= distance_certificate(problem)['bound']-1e-6
        finally:
            if relaxed is not None:
                relaxed.dispose()
            model.dispose()
    assert sizes == [sizes[0]]*3
    assert values[1] >= values[0]-1e-6 and values[2] >= values[1]-1e-6


def test_zero_bounds_and_single_batch_do_not_get_certificate_rows():
    pytest.importorskip('gurobipy')
    from models.routing.solvers.exact.formulation import build_model
    for problem in (fixture(pool=2, zero=True), fixture(pool=2, one_batch=True)):
        model, ctx = build_model(problem, distance_cuts='components')
        try:
            assert not ctx.get('distance_certificate_rows')
        finally:
            model.dispose()


@pytest.mark.parametrize('zero,share_cutoff,linear', [(False, True, True), (True, True, True),
                                                    (False, False, False)])
def test_distance_rows_keep_audited_cross_batch_split_witness(zero, share_cutoff, linear):
    pytest.importorskip('gurobipy')
    from models.routing.solvers.exact import formulation
    from tests.routing.test_multibatch_route_compression import (
        test_cross_batch_split_witness_and_shared_start_are_preserved as verify_witness)
    original = formulation.build_model
    # Reuse the independently audited hand schedule, forcing the new rows in
    # its model. The verifier fixes every supplied start and checks raw cost.
    with patch.object(formulation, 'build_model', side_effect=lambda *a, **kw: original(
            *a, **kw, distance_cuts='components')):
        verify_witness(zero, share_cutoff, linear)


def test_invalid_modes_and_disabled_certificate_are_rejected():
    from models.routing.solvers.exact.solver import solve_exact
    with pytest.raises(ValueError, match='distance_cuts'):
        solve_exact(fixture(pool=2), distance_cuts='unknown')
    with pytest.raises(ValueError, match='require the distance certificate'):
        solve_exact(fixture(pool=2), distance_cuts='total', use_distance_certificate=False)


def test_numbered_m5_entry_exposes_opt_in_rows_and_keeps_default_reporting_only():
    pytest.importorskip('gurobipy')
    from models.routing.variants.model_5.exact import solve_exact
    problem = fixture(pool=2)
    baseline = solve_exact(problem, time_limit=5)
    strengthened = solve_exact(problem, time_limit=5, distance_cuts='components')
    assert baseline['info']['distance_cut_mode'] == 'none' and not baseline['info']['distance_cuts_added']
    assert strengthened['info']['distance_cut_mode'] == 'components'
    assert strengthened['info']['distance_cuts_added'] == 3
    for result in (baseline, strengthened):
        assert result['model'] == 5 and result['info']['proven_optimal']
        assert audit_solution(problem, result)['valid']
    assert strengthened['info']['objective'] == pytest.approx(baseline['info']['objective'])


def test_root_completion_measures_first_observed_optimal_root_only():
    gp = pytest.importorskip('gurobipy')
    from models.routing.solvers.exact.multibatch_cuts import CapacityCutSeparator
    state = dict(node=1, seconds=.5, status=gp.GRB.OPTIMAL)
    def get(which):
        return {gp.GRB.Callback.MIPNODE_STATUS: state['status'],
                gp.GRB.Callback.MIPNODE_NODCNT: state['node'],
                gp.GRB.Callback.RUNTIME: state['seconds']}[which]
    model = SimpleNamespace(cbGet=get, cbGetNodeRel=lambda variables: [], terminate=lambda: None)
    separator = CapacityCutSeparator([])
    separator(model, gp.GRB.Callback.MIPNODE)
    assert not separator.root_optimal_callback_observed
    state['node'] = 0
    separator(model, gp.GRB.Callback.MIPNODE)
    assert separator.root_optimal_callback_observed and separator.root_optimal_callback_seconds == .5
    state['seconds'] = 2
    separator(model, gp.GRB.Callback.MIPNODE)
    assert separator.root_optimal_callback_seconds == .5 and separator.error is None
