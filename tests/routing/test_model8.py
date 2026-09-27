"""Capacity cooperation must beat a fair no-transfer control, not just a bad plan."""
from copy import deepcopy
import time

import pytest

from experiments.scenarios.model8_capacity import make_capacity_example, reference_routes
from models.routing.common.task_builder import build_tasks_for_model
from models.routing.variants.model_8.two_stage_v2.baseline import prepare_baseline
from models.routing.variants.model_8.two_stage_v2.state import Evaluator, baseline_state
from models.routing.variants.model_8.two_stage_v2.alns import solve_alns


def setup(volume=3):
    p = make_capacity_example(volume)
    a, b = build_tasks_for_model(p, 8)
    base = prepare_baseline(p, a, b, 2, reference_routes())
    return p, a, b, base, Evaluator(p, a, b, base)


def cooperative_state(base, quantity=1):
    state = baseline_state(base)
    state['e2_routes']['S0'] = [
        ['S0', 'Pd', 'Pp', 'Td', 'Tp', 'MEET@Hd', 'Hp', 'Qd', 'Qp', 'S0'],
        ['S0', 'Hd', 'Rd', 'Rp', 'S0']]
    state['transfers'] = [{'satellite': 'S0', 'event': 'Hd', 'sender_vehicle': 0,
                           'receiver_vehicle': 1, 'quantity': quantity}]
    return state


def test_joint_move_releases_capacity_and_saves_real_detour():
    p, a, b, base, e = setup()
    state = cooperative_state(base)
    audit = e.audit(state)
    assert base['objective'] - audit['objective'] == pytest.approx(2 ** .5 * 10)
    assert max(r['volume'] for r in audit['load_profiles']['S0:0']) == 3
    assert max(r['volume'] for r in audit['load_profiles']['S0:1']) <= 3
    t, = audit['transshipments']
    assert t['receiver_pickup_time'] >= t['handoff_ready_time']
    # The same pickup reassignment without the handoff overloads A.
    state['transfers'] = []
    state['e2_routes']['S0'][0].remove('MEET@Hd')
    with pytest.raises(ValueError, match='容量'):
        e.audit(state)


@pytest.mark.parametrize('quantity', [0, -1, 1.5, True, 3])
def test_invalid_quantities_or_unavailable_native_inventory_rejected(quantity):
    _, _, _, base, e = setup()
    with pytest.raises(ValueError):
        e.audit(cooperative_state(base, quantity))


def test_received_boxes_cannot_be_relayed_again():
    _, _, _, base, e = setup()
    state = cooperative_state(base)
    state['transfers'].append({'satellite': 'S0', 'event': 'Hd',
                               'sender_vehicle': 1, 'receiver_vehicle': 0, 'quantity': 1})
    with pytest.raises(ValueError, match='原生空箱'):
        e.audit(state)


def test_receiver_future_capacity_and_time_windows_checked():
    _, _, _, base, e = setup()
    state = cooperative_state(base, 2)
    assert e.audit(state)['objective'] < base['objective']
    # Move delivery R before the H handoff: chronology cannot satisfy H's window.
    state['e2_routes']['S0'][1] = ['S0', 'Rd', 'Hd', 'Rp', 'S0']
    with pytest.raises(ValueError, match='配送车辆或顺序'):
        e.audit(state)
    # An extra wait at Q before H causes a genuine deadline violation.
    state = cooperative_state(base)
    state['e2_routes']['S0'][0] = ['S0', 'Pd', 'Pp', 'Td', 'Tp', 'Qd', 'Qp', 'MEET@Hd', 'Hp', 'S0']
    with pytest.raises(ValueError, match='时间窗'):
        e.audit(state)


def test_duplicate_service_and_cross_satellite_handoff_rejected():
    _, _, _, base, e = setup()
    state = cooperative_state(base)
    state['e2_routes']['S0'][1].insert(1, 'Pp')
    with pytest.raises(ValueError, match='全部原始任务'):
        e.audit(state)
    state = cooperative_state(base)
    state['transfers'][0]['satellite'] = 'S1'
    with pytest.raises(ValueError):
        e.audit(state)


def test_alns_finds_joint_move_and_keeps_base_immutable():
    p, a, b, base, e = setup()
    before = deepcopy(base)
    _, _, result = solve_alns(p, a, b, 2, base_routes=reference_routes(),
                              base_info={'objective': base['objective'], 'proven_optimal': False},
                              max_time=5, max_iterations=600, seed=42,
                              verbose=False, return_info=True)
    assert result['objective'] == pytest.approx(116.78708664619074)
    assert result['transshipment_count'] > 0
    assert result['iterations'] > 0
    assert not result['proven_optimal'] and not result['conditional_proven_optimal']
    assert result['transshipment_improvement'] is None  # No false causal attribution.
    assert result['requires_gurobi'] is False
    assert base == before
    assert e.audit(result['state'])['objective'] == pytest.approx(result['objective'])


def test_exact_control_and_alns_match_certified_capacity_example():
    pytest.importorskip('gurobipy')
    from models.routing.variants.model_3.exact_solver import solve_exact as solve_m3
    from models.routing.variants.model_8.two_stage_v2.exact_solver import solve_exact
    p, a, b, base, _ = setup()
    r = solve_m3(p, a, b, 2, verbose=False, return_info=True, time_limit=10)
    assert r[2]['proven_optimal']
    assert r[2]['objective'] == pytest.approx(base['objective'])
    results = []
    for allow in (False, True):
        results.append(solve_exact(p, a, b, 2, base_routes=r[:2], base_info=r[2],
                                  allow_transfers=allow, time_limit=10, verbose=False,
                                  return_info=True, gurobi_params={'Threads': 2}))
    assert all(r[2]['conditional_proven_optimal'] for r in results)
    assert all(not r[2]['proven_optimal'] for r in results)
    assert results[0][2]['objective'] == pytest.approx(base['objective'])
    assert results[1][2]['objective'] == pytest.approx(116.78708664619074)


def test_relaxed_capacity_removes_need_for_transfer():
    pytest.importorskip('gurobipy')
    from models.routing.variants.model_8.two_stage_v2.exact_solver import solve_exact
    p, a, b, base, e = setup(4)
    costs = []
    for allow in (False, True):
        r = solve_exact(p, a, b, 2, base_routes=reference_routes(),
                        base_info={'proven_optimal': False}, allow_transfers=allow,
                        time_limit=10, verbose=False, return_info=True,
                        gurobi_params={'Threads': 2})
        assert r[2]['conditional_proven_optimal']
        assert not r[2]['stage1_proven_optimal']
        costs.append(r[2]['objective'])
    assert costs[0] == pytest.approx(costs[1])
    assert costs[0] == pytest.approx(116.78708664619074)


def test_exact_forbids_delivery_reassignment():
    gp = pytest.importorskip('gurobipy')
    from models.routing.variants.model_8.two_stage_v2.model import build_model
    p, a, b, base, _ = setup()
    model, variables, _ = build_model(p, a, b, 2, base)
    try:
        model.addConstr(variables['serve']['Pd', ('S0', 1)] == 1)
        model.Params.OutputFlag = 0
        model.Params.TimeLimit = 10
        model.optimize()
        assert model.Status == gp.GRB.INFEASIBLE
    finally:
        model.dispose()


def test_baseline_certificate_mismatch_and_expired_deadline():
    pytest.importorskip('gurobipy')
    from models.routing.variants.model_8.two_stage_v2.exact_solver import solve_exact, optimize_stage2
    p, a, b, base, _ = setup()
    with pytest.raises(ValueError, match='目标值'):
        solve_exact(p, a, b, 2, base_routes=reference_routes(), base_info={'objective': 0})
    assert optimize_stage2(p, a, b, 2, base, time.perf_counter() - 1) is None


def test_receiver_must_have_capacity_after_later_pickups():
    p = make_capacity_example()
    p.nodes['R'].container_count_pick = 2
    p.aggregate_demands_from_pre_assignment()
    a, b = build_tasks_for_model(p, 8)
    base = prepare_baseline(p, a, b, 2, reference_routes())
    e = Evaluator(p, a, b, base)
    assert e.audit(cooperative_state(base, 1))['objective'] < base['objective']
    # Two boxes fit at H, but after R pickup the receiver would carry four.
    with pytest.raises(ValueError, match='容量'):
        e.audit(cooperative_state(base, 2))
