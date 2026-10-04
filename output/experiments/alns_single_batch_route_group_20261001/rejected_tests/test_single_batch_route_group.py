"""Whole-route customer reassignment remains a fully audited M2 proposal."""
from copy import deepcopy
from dataclasses import replace
import random
import time

import pytest

from models.routing.common.cycle import MultiBatchInstance
from models.routing.common.cycle.assignment import assigned_problem, input_assignment
from models.routing.common.cycle.audit import audit_solution
from models.routing.common.domain.instance import ProblemInstance
from models.routing.common.domain.node import Satellite
from models.routing.service import solve_routing
from models.routing.solvers.alns.construction import reassigned_state
from models.routing.solvers.alns.evaluator import evaluate
from models.routing.solvers.alns.partitioned_assignment import _route_group_assignment, solve_alns
from tests.routing.test_single_batch_solver_audit import tiny_problem


class FirstOption:
    def sample(self, rows, count):
        return rows[:count]

    def choice(self, rows):
        return rows[0]


def directed_group_problem():
    base = tiny_problem(2, 1)
    nodes = dict(base.network.nodes)
    nodes['U'] = Satellite('U', 0, 0)
    distances = {a: {b: 0 for b in nodes} for a in nodes}
    distances['S']['C0'], distances['C1']['S'] = 10, 10
    distances['T']['C0'], distances['C1']['T'] = 1, 40
    distances['U']['C0'], distances['C1']['U'] = 12, 1
    travel = {a: {b: 0 for b in nodes} for a in nodes}
    fleet = dict(E1_vehicle_count=1, vehicles_per_satellite=2, E1_Qk=100, E1_Ck=100,
                 E2_Qv=100, E2_Cv=100, container_weight=1, container_volume=1)
    orders = [replace(o, satellite='S') for o in base.orders]
    orders.append(replace(orders[0], id='same_customer_second_order'))
    p = MultiBatchInstance(ProblemInstance(nodes, fleet, distance_matrix=distances,
                           travel_time_matrix=travel), orders, base.operating_policy, model=2)
    incumbent = dict(e2_trips=[dict(satellite_id='S', events=[
        dict(order_id='o0', kind='d'), dict(order_id='o1', kind='d')])])
    return p, incumbent


def test_directed_return_arc_ranking_and_all_orders_for_a_customer_move_together():
    p, incumbent = directed_group_problem()
    current = input_assignment(p)
    saved = deepcopy(incumbent), dict(current), p.orders
    proposed = _route_group_assignment(p, current, incumbent, FirstOption())
    assert proposed == dict(C0='U', C1='U', C2='S')
    fixed = assigned_problem(p, proposed)
    assert all(o.satellite == 'U' for o in fixed.orders if o.customer in ('C0', 'C1'))
    assert incumbent == saved[0] and current == saved[1] and p.orders == saved[2]
    assert not fixed.free_assignment


@pytest.mark.parametrize('variant', ['no_trips', 'empty_events', 'singleton', 'same_customer_only',
                                     'pickup_event', 'wrong_owner', 'single_satellite'])
def test_ineligible_groups_return_none_without_consuming_rng(variant):
    p, incumbent = directed_group_problem()
    current = input_assignment(p)
    if variant == 'no_trips':
        incumbent['e2_trips'] = []
    elif variant == 'empty_events':
        incumbent['e2_trips'][0]['events'] = []
    elif variant == 'singleton':
        incumbent['e2_trips'][0]['events'].pop()
    elif variant == 'same_customer_only':
        incumbent['e2_trips'][0]['events'][1]['order_id'] = 'same_customer_second_order'
    elif variant == 'pickup_event':
        incumbent['e2_trips'][0]['events'][1]['kind'] = 'p'
    elif variant == 'wrong_owner':
        current['C0'] = 'T'
    else:
        p.satellites = ('S',)
    rng = random.Random(23)
    before = rng.getstate()
    assert _route_group_assignment(p, current, incumbent, rng) is None
    assert rng.getstate() == before


@pytest.mark.parametrize('seed', range(6))
def test_group_proposals_are_reproducible_and_preserve_customer_coverage(seed):
    p, incumbent = directed_group_problem()
    current = input_assignment(p)
    a = _route_group_assignment(p, current, incumbent, random.Random(seed))
    b = _route_group_assignment(p, current, incumbent, random.Random(seed))
    assert a == b and set(a) == set(current)
    assert a['C0'] == a['C1'] and a['C0'] != 'S' and a['C2'] == 'S'


def test_full_projection_and_decoder_preserve_task_multiset_and_ownership():
    p, _ = directed_group_problem()
    original = solve_alns(p, max_time=10, max_iterations=100, seed=12, route_group_moves=False)
    assert original['audit']['valid']
    assignment = input_assignment(p)
    proposed = _route_group_assignment(p, assignment, original, FirstOption())
    assert proposed is not None
    fixed = assigned_problem(p, proposed)
    snapshot = deepcopy(original)
    state = reassigned_state(fixed, original, time.perf_counter()+5)
    assert state is not None and original == snapshot
    assert sorted(t for r in state['e2'] for t in r['tasks']) == sorted((o.id, 'd') for o in p.orders)
    assert all(fixed.order_map[oid].satellite == route['satellite']
               for route in state['e2'] for oid, kind in route['tasks'])
    _, decoded, violations = evaluate(fixed, state)
    assert not violations
    assert audit_solution(fixed, decoded)['valid']


@pytest.mark.parametrize('model', [1, 2, 3, 4])
def test_public_route_group_path_only_m2_and_independent_audit(monkeypatch, model):
    from models.routing.solvers.alns import partitioned_assignment
    original = partitioned_assignment._route_group_assignment
    calls = []
    def counted(*args):
        candidate = original(*args)
        calls.append(candidate is not None)
        return candidate
    monkeypatch.setattr(partitioned_assignment, '_route_group_assignment', counted)
    p = tiny_problem(model, 1)
    result = solve_routing(model=model, method='alns', problem=p, alns_time=10, alns_iter=160, seed=11)
    assert bool(calls) == (model == 2)
    if model == 2:
        assert result['info']['assignment_group_attempts'] == sum(calls)
        assert result['info']['assignment_budget_policy'] == 'initial_40pct_then_project_and_refine'
    audit = audit_solution(p, result)
    assert audit['valid'] and audit['periodic_replay']['valid']
    assert result['info']['best_bound'] is None and not result['info']['proven_optimal']


def test_disabled_group_option_does_not_call_helper_and_preserves_input(monkeypatch):
    from models.routing.solvers.alns import partitioned_assignment
    def forbidden(*args):
        raise AssertionError('Disabled route-group option must use original proposals')
    monkeypatch.setattr(partitioned_assignment, '_route_group_assignment', forbidden)
    p = tiny_problem(2, 1)
    before = input_assignment(p)
    a = solve_alns(p, max_time=10, max_iterations=160, seed=12, route_group_moves=False)
    b = solve_alns(p, max_time=10, max_iterations=160, seed=12, route_group_moves=False)
    assert a['e1_trips'] == b['e1_trips'] and a['e2_trips'] == b['e2_trips']
    assert a['assignment'] == b['assignment'] and a['info']['objective'] == b['info']['objective']
    assert a['info']['assignment_group_attempts'] == 0 and input_assignment(p) == before
