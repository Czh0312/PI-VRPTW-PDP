"""Delivery freedom changes route commitments, never physical feasibility."""
from copy import deepcopy

import pytest

from models.routing.alns.variants.model_8 import solve_alns
from models.routing.common.domain.instance import ProblemInstance
from models.routing.common.domain.node import Customer, Depot, Satellite
from models.routing.common.task_builder import build_tasks_for_model
from models.routing.variants.model_8.baseline import prepare_baseline
from models.routing.variants.model_8.state import Evaluator, baseline_state


def example():
    nodes = {'D0': Depot('D0', 0, 1), 'S0': Satellite('S0', 0, 0)}
    for name, x in [('A', 10), ('B', -10), ('C', 11), ('D', -11)]:
        nodes[name] = Customer(name, x, 0, 1, 0, (0, 1000), (0, 1000),
                               assigned_sat='S0', container_count_del=1, container_count_pick=0)
    p = ProblemInstance(nodes, {
        'E1_vehicle_count': 1, 'E1_Qk': 1000, 'E1_Ck': 30,
        'vehicles_per_satellite': 2, 'E2_Qv': 100, 'E2_Cv': 2,
        'container_weight': 2, 'container_volume': 1, 'speed': 1})
    p.aggregate_demands_from_pre_assignment()
    a, b = build_tasks_for_model(p, 8)
    base = prepare_baseline(p, a, b, 2, ([['D0', 'S0d', 'D0']], {
        'S0': [['S0', 'Ad', 'Bd', 'S0'], ['S0', 'Cd', 'Dd', 'S0']]}))
    return p, a, b, base


def test_delivery_reassignment_is_explicit_and_audited():
    p, a, b, base = example()
    state = baseline_state(base)
    state['e2_routes']['S0'] = [['S0', 'Ad', 'Cd', 'S0'], ['S0', 'Bd', 'Dd', 'S0']]
    with pytest.raises(ValueError, match='配送车辆或顺序'):
        Evaluator(p, a, b, base).audit(state)
    audit = Evaluator(p, a, b, base, delivery_policy='flexible').audit(state)
    assert audit['objective'] == pytest.approx(46)
    assert audit['objective'] < base['objective']


@pytest.mark.parametrize('failure', ['initial_load', 'missing_task', 'time_window'])
def test_flexible_delivery_keeps_physical_constraints(failure):
    p, a, b, base = example()
    state = baseline_state(base)
    if failure == 'initial_load':
        state['e2_routes']['S0'] = [['S0', 'Ad', 'Bd', 'Cd', 'S0'], ['S0', 'Dd', 'S0']]
    elif failure == 'missing_task':
        state['e2_routes']['S0'][0].remove('Ad')
    else:
        p.nodes['A'].tw_del = (0, 1)
        # Task time windows, rather than node attributes, are the audit input.
        task = next(t for t in b['S0'] if t.id == 'Ad')
        task.tw = (0, 1)
    with pytest.raises(ValueError):
        Evaluator(p, a, b, base, delivery_policy='flexible').audit(state)


def test_full_search_changes_delivery_routes_without_transfers():
    p, a, b, base = example()
    before = deepcopy(base)
    results = {}
    for policy in ('fixed', 'flexible'):
        _, _, info = solve_alns(p, a, b, 2, base_routes=(base['e1_routes'], base['e2_routes']),
                                base_info={'objective': base['objective']},
                                allow_transfers=False, delivery_policy=policy,
                                max_time=5, max_iterations=1000, seed=42,
                                return_info=True, verbose=False)
        results[policy] = info
        assert info['state']['transfers'] == []
        assert not info['proven_optimal']
        audit = Evaluator(p, a, b, base, delivery_policy=policy).audit(info['state'])
        assert audit['objective'] == pytest.approx(info['objective'])
    assert results['fixed']['objective'] == base['objective']
    assert results['flexible']['objective'] == pytest.approx(46)
    assert results['flexible']['delivery_move_attempts'] > 0
    assert base == before
