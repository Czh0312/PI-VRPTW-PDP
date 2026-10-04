"""Independent witnesses and equivalence checks for M5 strengthening."""
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from models.routing.common.cycle import MultiBatchInstance
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.exact.multibatch_cuts import minimum_trip_count
from tests.routing.test_multibatch_cycle import fixture, hand_solution
from tests.routing.test_single_batch_solver_audit import tiny_problem


def test_trip_lower_bound_respects_delivery_batches_and_collective_pickup_capacity():
    problem = fixture(pool=4)
    assert minimum_trip_count(problem, []) == 0
    # The quantities fit in one vehicle, but its deliveries cannot mix batches.
    assert minimum_trip_count(problem, list(problem.orders)) == 2
    orders = [replace(o, batch='b1') for o in problem.orders]
    problem = MultiBatchInstance(problem.network, orders, problem.operating_policy)
    assert minimum_trip_count(problem, orders) == 1
    problem.fleet.e2_volume_cap = 1
    assert minimum_trip_count(problem, orders) == 2


def test_capacity_rounding_is_conservative_at_decimal_boundaries():
    base = fixture(pool=4)
    orders = [replace(base.orders[0], id=str(i), goods_weight=0) for i in range(3)]
    problem = MultiBatchInstance(base.network, orders, base.operating_policy)
    problem.fleet.container_weight = 0
    problem.fleet.container_volume = .1
    problem.fleet.e2_volume_cap = .3
    assert minimum_trip_count(problem, orders) == 1
    problem.fleet.e2_volume_cap = .2
    assert minimum_trip_count(problem, orders) == 2


@pytest.mark.parametrize('unloaded', [29, 30, 31])
@pytest.mark.parametrize('strengthened', [False, True])
def test_cutoff_compression_preserves_fixed_boundary_schedules(unloaded, strengthened):
    gp = pytest.importorskip('gurobipy')
    from models.routing.solvers.exact.formulation import build_model
    from models.routing.solvers.exact.solver import _set_start
    problem = fixture(pool=2)
    result = hand_solution(problem, first_unload=unloaded)
    audit = audit_solution(problem, result)
    assert audit['valid']
    model, ctx = build_model(problem, strengthen_multibatch=strengthened, share_cutoff=strengthened)
    try:
        separator = ctx.get('capacity_separator')
        if separator is not None:
            # The hand-replayed witness must also satisfy every candidate
            # cut, including ones not chosen by the callback on this solve.
            for terms, rhs in separator.candidates:
                model.addConstr(gp.quicksum(separator.variables[i] for i in terms) <= rhs)
        assert _set_start(problem, model, ctx, result)
        model.update()
        for variable in model.getVars():
            if variable.Start != gp.GRB.UNDEFINED:
                variable.LB = variable.UB = variable.Start
        model.Params.TimeLimit = 3
        model.optimize()
        assert model.SolCount and model.ObjVal == pytest.approx(audit['objective'])
        if strengthened:
            assert ctx['pickup_returns']['o1'].X == unloaded
    finally:
        model.dispose()


@pytest.mark.parametrize('seed', [0, 1, 2, 4, 5])
def test_new_formulation_matches_previous_on_directed_and_zero_travel_cases(monkeypatch, seed):
    pytest.importorskip('gurobipy')
    from models.routing.solvers.exact import solver
    from models.routing.solvers.exact.formulation import build_model
    base = tiny_problem(3, seed)
    policy = replace(fixture(pool=4).operating_policy, e2_max_trips_per_vehicle=2)
    orders = [replace(o, batch='b1' if i == 0 else 'b2',
                      delivery_window=(0, 25) if i == 0 else (60, 85),
                      pickup_window=(10, 70) if i == 0 else (65, 95)) for i, o in enumerate(base.orders)]
    problem = MultiBatchInstance(base.network, orders, policy)
    problem.fleet.e2_per_sat_count = 2
    current = solver.solve_exact(problem, time_limit=5)
    monkeypatch.setattr(solver, 'build_model', lambda *a, **kw: build_model(
        *a, **kw, strengthen_multibatch=False, share_cutoff=False))
    previous = solver.solve_exact(problem, time_limit=5)
    assert current['info']['proven_optimal'] and previous['info']['proven_optimal']
    assert audit_solution(problem, current)['valid'] and audit_solution(problem, previous)['valid']
    assert current['info']['objective'] == pytest.approx(previous['info']['objective'])
    assert current['info']['variables'] < previous['info']['variables']


def test_capacity_pruning_keeps_delivery_then_pickup_that_reuses_the_same_space():
    pytest.importorskip('gurobipy')
    from models.routing.solvers.exact.solver import solve_exact
    problem = fixture(pool=1)
    problem.fleet.e2_volume_cap = 1
    result = solve_exact(problem, time_limit=3)
    assert result['info']['proven_optimal'] and result['audit']['valid']
    assert result['info']['objective'] == 44


def test_formulation_fingerprint_does_not_depend_on_python_hash_seed():
    pytest.importorskip('gurobipy')
    script = '''
from tests.routing.test_multibatch_cycle import fixture
from models.routing.solvers.exact.formulation import build_model
model, _ = build_model(fixture())
print('FINGERPRINT', model.Fingerprint)
model.dispose()
'''
    fingerprints = []
    for seed in ('0', '1', '42'):
        run = subprocess.run([sys.executable, '-c', script], cwd=Path(__file__).resolve().parents[2],
                             env={**os.environ, 'PYTHONHASHSEED': seed}, capture_output=True, text=True, timeout=10)
        assert run.returncode == 0, run.stderr
        fingerprints.append(next(line for line in run.stdout.splitlines() if line.startswith('FINGERPRINT')))
    assert len(set(fingerprints)) == 1


def test_cut_callback_failure_is_explicit_and_keeps_the_audited_seed(monkeypatch):
    pytest.importorskip('gurobipy')
    from models.routing.solvers.exact.multibatch_cuts import CapacityCutSeparator
    from models.routing.solvers.exact.solver import solve_exact
    def fail(self, model, where):
        self.error = 'injected callback failure'
        model.terminate()
    monkeypatch.setattr(CapacityCutSeparator, '__call__', fail)
    problem = fixture()
    result = solve_exact(problem, time_limit=3)
    assert result['info']['status'] == 'CAPACITY_CUT_ERROR'
    assert result['info']['capacity_cut_error'] == 'injected callback failure'
    assert not result['info']['proven_optimal'] and not result['info']['proven_infeasible']
    assert audit_solution(problem, result)['valid']


@pytest.mark.parametrize('certificate', [False, True])
def test_feasible_time_limit_without_gurobi_bound_serializes_gap_honestly(monkeypatch, certificate):
    gp = pytest.importorskip('gurobipy')
    from models.routing.solvers.exact import solver
    original = solver.build_model
    class MissingBound:
        def __init__(self, model):
            self.model = model
        def __getattr__(self, name):
            if name == 'Status':
                return gp.GRB.TIME_LIMIT
            if name == 'ObjBound':
                return -float('inf')
            if name == 'MIPGap':
                return float('inf')
            return getattr(self.model, name)
    def build(*a, **kw):
        model, ctx = original(*a, **kw)
        return MissingBound(model), ctx
    monkeypatch.setattr(solver, 'build_model', build)
    problem = fixture()
    result = solver.solve_exact(problem, time_limit=3, use_distance_certificate=certificate)
    assert result['info']['status'] == 'TIME_LIMIT'
    assert result['info']['gurobi_best_bound'] is None
    assert result['info']['gurobi_mip_gap'] is None
    if certificate:
        from models.routing.solvers.exact.multibatch_bounds import distance_certificate
        bound = distance_certificate(problem)['bound']
        assert result['info']['best_bound'] == bound
        assert result['info']['best_bound_source'] == 'distance_certificate'
        assert result['info']['mip_gap'] == pytest.approx(1-bound/result['info']['objective'])
    else:
        assert result['info']['best_bound'] is None and result['info']['mip_gap'] is None
    assert not result['info']['proven_optimal'] and audit_solution(problem, result)['valid']
    json.dumps(result, allow_nan=False)
