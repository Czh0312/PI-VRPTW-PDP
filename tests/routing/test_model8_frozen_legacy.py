"""M8 must preserve its baseline and keep conditional proofs scoped."""
from copy import deepcopy
import time

import pytest

gp = pytest.importorskip("gurobipy")
from gurobipy import GRB

from tests.routing.test_model5_exact import _problem
from models.routing.common.task_builder import build_tasks_for_model
from models.routing.variants.model_8.legacy.baseline import prepare_baseline
from models.routing.variants.model_8.legacy.model import build_model
from models.routing.variants.model_8.legacy.exact_solver import solve_exact, optimize_stage2
from models.routing.variants.model_8.legacy.alns import solve_alns


@pytest.fixture
def fixture():
    p = _problem()
    e1, e2 = build_tasks_for_model(p, 8)
    routes = ([['D0', 'S0d', 'S0p', 'D0']],
              {'S0': [['S0', 'C0d', 'C0p', 'S0'],
                      ['S0', 'C1d', 'C1p', 'S0']]})
    base = prepare_baseline(p, e1, e2, 2, routes)
    return p, e1, e2, routes, base


def optimize(model):
    model.Params.OutputFlag = 0
    model.Params.TimeLimit = 10
    model.Params.MIPGap = 0
    model.optimize()


def test_no_relay_is_exactly_the_original_routes(fixture):
    p, e1, e2, routes, base = fixture
    snapshot = deepcopy(base)
    result = optimize_stage2(
        p, e1, e2, 2, base, time.perf_counter() + 10,
        relay_fixes={'C0p': 0, 'C1p': 0},
    )
    assert result['conditional_proven_optimal']
    assert result['routes'] == routes
    assert result['objective'] == pytest.approx(base['objective'])
    assert base == snapshot


def test_forced_relay_keeps_receiver_slot_and_has_real_carriage(fixture):
    p, e1, e2, routes, base = fixture
    result = optimize_stage2(
        p, e1, e2, 2, base, time.perf_counter() + 10,
        handoff_fixes={'C0p': 'C1'}, relay_fixes={'C1p': 0},
    )
    assert result['conditional_proven_optimal']
    assert result['routes'][0] == routes[0]
    t, = result['transshipments']
    assert t['receiver_vehicle'] == 0
    assert t['carrier_vehicle'] == 1
    assert t['origin_pickup_time'] >= 20 - 1e-6
    assert t['handoff_drop_time'] >= t['origin_pickup_time'] + 1 + 2 - 1e-6
    assert t['receiver_pickup_time'] >= t['handoff_ready_time'] - 1e-6
    receiver, carrier = result['raw_e2_routes']['S0']
    assert receiver == ['S0', 'C0d', 'C0p@C1', 'S0']
    assert carrier.index('C1d') < carrier.index('C1p@C1')
    assert carrier.index('TP@C0p@C1') < carrier.index('TD@C0p@C1')


def test_cannot_reassign_a_delivery(fixture):
    p, e1, e2, _, base = fixture
    model, v, ctx = build_model(p, e1, e2, 2, base)
    try:
        incoming = gp.quicksum(x for (i, j, vehicle), x in v['z'].items()
                              if j == 'C0d' and vehicle == ('S0', 1))
        model.addConstr(incoming == 1)
        optimize(model)
        assert model.Status == GRB.INFEASIBLE
    finally:
        model.dispose()


def test_cannot_reverse_original_task_order(fixture):
    p, e1, e2, _, base = fixture
    model, v, _ = build_model(p, e1, e2, 2, base,
                             relay_fixes={'C0p': 0, 'C1p': 0})
    try:
        model.addConstr(v['z']['C0p@C0', 'C0d', ('S0', 0)] == 1)
        optimize(model)
        assert model.Status == GRB.INFEASIBLE
    finally:
        model.dispose()


def test_unproven_base_never_starts_exact_second_stage(fixture, monkeypatch):
    p, e1, e2, routes, base = fixture
    from models.routing.variants.model_8.legacy import exact_solver
    def unexpected(*args, **kwargs):
        pytest.fail('stage two started without a proven M3 optimum')
    monkeypatch.setattr(exact_solver, 'optimize_stage2', unexpected)
    a, b, info = solve_exact(p, e1, e2, 2, base_routes=routes,
                            base_info={'proven_optimal': False}, verbose=False,
                            return_info=True, return_incumbent_on_time_limit=True)
    assert (a, b) == routes
    assert info['status'] == 'BASE_OPTIMUM_NOT_PROVEN'
    assert not info['proven_optimal']


def test_exact_and_alns_share_the_same_certified_base():
    p = _problem()
    a, b = build_tasks_for_model(p, 8)
    exact = solve_exact(p, a, b, 2, time_limit=15, verbose=False,
                        return_info=True, return_incumbent_on_time_limit=True)
    info = exact[2]
    assert info['stage1_proven_optimal']
    assert info['conditional_proven_optimal']
    assert info['two_stage_proven_optimal']
    assert not info['proven_optimal']
    assert info['best_bound'] is None
    base = info['baseline']
    alns = solve_alns(p, a, b, 2,
                      base_routes=(base['e1_routes'], base['e2_routes']),
                      base_info=info['stage1'], max_time=3, max_iterations=2,
                      max_remove=1, verbose=False, return_info=True)
    assert alns[2]['baseline']['route_fingerprint'] == base['route_fingerprint']
    assert alns[2]['objective'] <= base['objective'] + 1e-6
    assert alns[2]['objective'] >= info['objective'] - 1e-6
    assert alns[2]['iterations'] > 0
    assert not alns[2]['proven_optimal']
    assert alns[2]['conditional_best_bound'] is None


def test_bad_baseline_and_mismatched_certificate_are_rejected(fixture):
    p, e1, e2, routes, base = fixture
    bad = deepcopy(routes)
    bad[1]['S0'][0].remove('C0p')
    with pytest.raises(ValueError, match='全部原始任务'):
        prepare_baseline(p, e1, e2, 2, bad)
    with pytest.raises(ValueError, match='目标值'):
        solve_exact(p, e1, e2, 2, base_routes=routes,
                    base_info={'proven_optimal': True, 'objective': 1})


def test_expired_second_stage_returns_without_building(fixture, monkeypatch):
    p, e1, e2, _, base = fixture
    from models.routing.variants.model_8.legacy import exact_solver
    monkeypatch.setattr(exact_solver, 'build_model',
                        lambda *args, **kwargs: pytest.fail('built after deadline'))
    assert optimize_stage2(p, e1, e2, 2, base, time.perf_counter() - 1) is None


def test_late_e1_pickup_wait_is_feasible_and_included_in_horizon(fixture):
    p, e1, e2, routes, _ = fixture
    base = prepare_baseline(p, e1, e2, 2, routes, TFp=7000)
    result = optimize_stage2(p, e1, e2, 2, base, time.perf_counter() + 10,
                             TFp=7000, relay_fixes={'C0p': 0, 'C1p': 0})
    assert result['conditional_proven_optimal']
    assert result['objective'] == pytest.approx(base['objective'])
