"""One deposit can be collected in portions by distinct vehicles and times."""
from copy import deepcopy
import time

import pytest

from models.routing.common.domain.instance import ProblemInstance
from models.routing.common.domain.node import Customer, Depot, Satellite
from models.routing.common.task_builder import build_tasks_for_model
from models.routing.variants.model_8.baseline import prepare_baseline
from models.routing.variants.model_8.state import Evaluator, baseline_state, event_id
from models.routing.alns.variants.model_8 import _repair_handoff, _repair_split_handoff


def split_example():
    # Synthetic audit fixture, not an empirical benchmark. A already carries
    # three boxes. After moving Q/R pickups onto A it must unload two boxes;
    # B and C each have just one spare slot after their other pickups.
    nodes = {'D0': Depot('D0', -1, 0), 'S0': Satellite('S0', 0, 0)}
    specs = [
        ('P', 10, 3, 3, (0, 30), (30, 50)),
        ('H', 20, 0, 0, (80, 120), (0, 400)),
        ('Q', 30, 1, 1, (0, 70), (230, 300)),
        ('R', 40, 1, 1, (0, 80), (260, 330)),
        ('X', 0, 2, 2, (140, 160), (170, 200)),
        ('Y', 0, 2, 2, (170, 190), (200, 230)),
    ]
    for name, x, deliveries, pickups, td, tp in specs:
        nodes[name] = Customer(name, x, 0, 1, 1, td, tp, assigned_sat='S0',
                               container_count_del=deliveries, container_count_pick=pickups)
    p = ProblemInstance(nodes, {
        'E1_vehicle_count': 1, 'E1_Qk': 1000, 'E1_Ck': 30,
        'vehicles_per_satellite': 3, 'E2_Qv': 100, 'E2_Cv': 3,
        'container_weight': 2, 'container_volume': 1, 'speed': 1})
    p.aggregate_demands_from_pre_assignment()
    a, b = build_tasks_for_model(p, 8)
    routes = ([['D0', 'S0d', 'S0p', 'D0']], {'S0': [
        ['S0', 'Pd', 'Pp', 'Hd', 'S0'],
        ['S0', 'Qd', 'Xd', 'Xp', 'Qp', 'S0'],
        ['S0', 'Rd', 'Yd', 'Yp', 'Rp', 'S0']]})
    base = prepare_baseline(p, a, b, 3, routes)
    e = Evaluator(p, a, b, base)
    trial = baseline_state(base)
    trial['e2_routes']['S0'] = [
        ['S0', 'Pd', 'Pp', 'Hd', 'Qp', 'Rp', 'S0'],
        ['S0', 'Qd', 'Xd', 'Xp', 'S0'],
        ['S0', 'Rd', 'Yd', 'Yp', 'S0']]
    return p, a, b, base, e, trial


def asynchronous_split(trial):
    result = deepcopy(trial)
    result['e2_routes']['S0'][1].insert(2, 'MEET@Hd')
    result['e2_routes']['S0'][2].insert(3, 'MEET@Hd')
    result['transfers'] = [
        {'satellite': 'S0', 'event': 'Hd', 'sender_vehicle': 0,
         'receiver_vehicle': receiver, 'quantity': 1} for receiver in (1, 2)]
    return result


def test_one_deposit_two_receivers_at_different_times():
    _, _, _, base, e, trial = split_example()
    with pytest.raises(ValueError, match='容量'):
        e.audit(trial)
    state = asynchronous_split(trial)
    audit = e.audit(state)
    left, right = audit['transshipments']
    assert left['handoff_ready_time'] == right['handoff_ready_time']
    assert left['receiver_pickup_time'] < right['receiver_pickup_time']
    assert sum(t['quantity'] for t in audit['transshipments']) == 2
    batch, = audit['handoff_batches']
    assert batch['deposited_quantity'] == 2
    assert [c['inventory_after_pickup'] for c in batch['collections']] == [1, 0]
    assert sum(n == 'Hd' for r in state['e2_routes']['S0'] for n in r) == 1
    assert sum(event_id(n) == 'Hd' for r in state['e2_routes']['S0'] for n in r) == 3
    assert audit['objective'] < base['objective']
    assert all(max(r['volume'] for r in profile) <= 3 for profile in audit['load_profiles'].values())


def test_joint_repair_succeeds_when_neither_receiver_can_take_the_total():
    _, _, _, base, e, trial = split_example()
    before = deepcopy(trial)
    for receiver in (1, 2):
        candidate, _ = _repair_handoff(e, trial, 'S0', 0, receiver,
                                       time.perf_counter() + 2, base['objective'], 32)
        assert candidate is None
    candidate, audits = _repair_split_handoff(
        e, trial, 'S0', 0, time.perf_counter() + 2, base['objective'], 32,
        max_receivers=2, receiver_candidates=2)
    assert candidate is not None and audits > 0
    state, audit = candidate
    assert len({t['receiver_vehicle'] for t in state['transfers']}) == 2
    assert len({t['event'] for t in state['transfers']}) == 1
    assert sum(t['quantity'] for t in state['transfers']) == 2
    assert audit['objective'] < base['objective']
    assert trial == before


def test_repeated_pair_records_are_one_deposit_and_collection_visit():
    from models.routing.variants.model_8.state import handoff_batches
    records = [{'satellite': 'S0', 'event': 'Hd', 'sender_vehicle': 0,
                'receiver_vehicle': receiver, 'quantity': quantity, 'physical_location': 'H',
                'handoff_ready_time': 10, 'receiver_pickup_time': pickup}
               for receiver, quantity, pickup in [(1, 1, 20), (1, 2, 20), (2, 1, 30)]]
    batch, = handoff_batches(records)
    assert batch['deposited_quantity'] == 4
    assert [(r['quantity'], r['inventory_after_pickup']) for r in batch['collections']] == [(3, 1), (1, 0)]


def test_full_alns_constructs_and_keeps_joint_split_from_valid_baseline():
    from models.routing.alns.variants.model_8 import solve_alns
    p, a, b, base, e, _ = split_example()
    _, _, info = solve_alns(p, a, b, 3,
                            base_routes=(base['e1_routes'], base['e2_routes']),
                            base_info={'objective': base['objective']},
                            max_time=5, max_iterations=1000, seed=19,
                            verbose=False, return_info=True)
    assert info['pooled_pickup_attempts'] > 0
    assert info['multi_receiver_audits'] > 0 and info['multi_receiver_feasible'] > 0
    assert info['split_handoff_deposit_count'] > 0
    audit = e.audit(info['state'])
    assert audit['objective'] < base['objective']
    assert all(batch['collections'][-1]['inventory_after_pickup'] == 0
               for batch in audit['handoff_batches'])


def test_flexible_single_receiver_ablation_cannot_fix_joint_deficit():
    _, _, _, base, e, trial = split_example()
    candidate, _ = _repair_split_handoff(e, trial, 'S0', 0,
        time.perf_counter() + 2, base['objective'], max_events=32,
        max_receivers=1, receiver_candidates=2)
    assert candidate is None


def test_split_does_not_reuse_inventory_or_ignore_receiver_future_load():
    _, _, _, _, e, trial = split_example()
    state = asynchronous_split(trial)
    state['transfers'][0]['quantity'] = 2  # B later collects X's two boxes.
    with pytest.raises(ValueError, match='容量'):
        e.audit(state)
    state['transfers'][1]['quantity'] = 2  # Total sent exceeds A's three native boxes.
    with pytest.raises(ValueError, match='容量|原生空箱'):
        e.audit(state)
    state = asynchronous_split(trial)
    state['transfers'].append({'satellite': 'S0', 'event': 'Hd',
                               'sender_vehicle': 1, 'receiver_vehicle': 2, 'quantity': 1})
    with pytest.raises(ValueError, match='原生空箱'):
        e.audit(state)


def test_exact_model_allows_multiple_receivers_at_same_customer_event():
    gp = pytest.importorskip('gurobipy')
    from models.routing.variants.model_8.model import build_model
    p, a, b, base, e, trial = split_example()
    state = asynchronous_split(trial)
    audit = e.audit(state)
    model, variables, _ = build_model(p, a, b, 3, base)
    try:
        arcs = {(event_id(i), event_id(j), ('S0', k))
                for k, route in enumerate(state['e2_routes']['S0']) for i, j in zip(route, route[1:])}
        for key, var in variables['z'].items():
            var.LB = var.UB = int(key in arcs)
        for (node, vehicle), var in variables['serve'].items():
            var.LB = var.UB = int(node in state['e2_routes'][vehicle[0]][vehicle[1]])
        transfers = {(t['event'], ('S0', 0), ('S0', t['receiver_vehicle'])): t['quantity']
                     for t in state['transfers']}
        for key, var in variables['transfer'].items():
            var.LB = var.UB = transfers.get(key, 0)
        model.Params.OutputFlag = 0
        model.Params.Threads = 1
        model.Params.TimeLimit = 5
        model.optimize()
        assert model.Status == gp.GRB.OPTIMAL
        assert model.ObjVal == pytest.approx(audit['objective'])
    finally:
        model.dispose()
