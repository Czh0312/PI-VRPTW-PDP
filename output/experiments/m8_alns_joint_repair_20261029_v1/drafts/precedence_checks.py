"""Repair proposals must respect supply and physical-vehicle precedence."""
from collections import Counter
from copy import deepcopy
from dataclasses import replace
import time

from models.common.cycle.calendar import CalendarCycleInstance
from models.common.cycle.instance import Order
from models.common.domain.instance import ProblemInstance
from models.common.domain.node import Satellite, Customer
from models.routing.model_8.alns.evaluator import JointEvaluator
from models.routing.model_8.alns.neighborhoods import repair_orders, _route_score, _goods_ready
from tests.routing.test_model8_evaluation_pool import sample
from tests.routing.test_model6_joint import joint_fixture


def test_pickup_surrogate_uses_the_assigned_batch_supply_time():
    p, assignment, state = sample()
    fixed = JointEvaluator(p).fixed(assignment)
    goods = _goods_ready(fixed, state)
    order = fixed.order_map['o2']
    _, finish = _route_score(fixed, dict(satellite='S', vehicle=0, tasks=[('o2', 'p')]), goods, 0, supply_aware=True)
    earliest = max(order.delivery_window[0], goods['S', order.batch]+fixed.operating_policy.e2_dispatch_prep_time
                   +fixed.travel('S', order.customer))+order.service_time+order.min_use_time
    assert finish >= earliest


def test_request_reinsertion_does_not_pick_up_before_a_later_trip_on_the_same_vehicle():
    p, assignment, state = sample()
    saved = deepcopy(state)
    ev = JointEvaluator(p)
    fixed = ev.fixed(assignment)
    repaired = repair_orders(fixed, state, [fixed.order_map['o2']], time.perf_counter()+10., avoid_precedence_cycles=True)
    assert repaired is not None
    repaired['cutoffs'] = dict(state['cutoffs'])
    _, result, issues = ev(assignment, repaired)
    assert result is not None and not issues and result['audit']['periodic_replay']['valid']
    assert Counter(task for route in repaired['e2'] for task in route['tasks']) == Counter(
        task for route in state['e2'] for task in route['tasks'])
    delivery = next(i for i, route in enumerate(repaired['e2']) if ('o2', 'd') in route['tasks'])
    pickup = next(i for i, route in enumerate(repaired['e2']) if ('o2', 'p') in route['tasks'])
    assert pickup >= delivery and state == saved


def test_new_pickup_trip_is_inserted_after_its_delivery_trip():
    source = joint_fixture(zero=True)
    orders = [source.orders[0], replace(source.orders[1], delivery_window=(70, 90))]
    p = CalendarCycleInstance(source.network, orders, source.operating_policy,
        {'batch_count_candidates': [2]}, source.cost_profile.config, model=8).fixed_calendar(2)
    assignment = {'o1': 'b1', 'o2': 'b2'}
    _, _, state = sample()
    ev = JointEvaluator(p)
    fixed = ev.fixed(assignment)
    def prefer_new_pickup(problem, route, goods, ready):
        value, end = _route_score(problem, route, goods, ready)
        return (-1000. if route['tasks'] == [('o2', 'p')] else value), end
    repaired = repair_orders(fixed, state, [fixed.order_map['o2']], time.perf_counter()+10., scorer=prefer_new_pickup, avoid_precedence_cycles=True)
    assert repaired is not None
    repaired['cutoffs'] = dict(state['cutoffs'])
    positions = {task: i for i, route in enumerate(repaired['e2']) for task in route['tasks']}
    assert positions['o2', 'p'] > positions['o2', 'd']
    assert ev(assignment, repaired)[1]['audit']['periodic_replay']['valid']


def test_earlier_list_position_on_another_vehicle_is_still_allowed():
    source = joint_fixture(zero=True, one_batch=True)
    source.fleet.e2_per_sat_count = 2
    p = CalendarCycleInstance(source.network, source.orders, source.operating_policy,
        {'batch_count_candidates': [1]}, source.cost_profile.config, model=8).fixed_calendar(1)
    assignment = {'o1': 'b1', 'o2': 'b1'}
    state = dict(deliveries={'b1': [['S']]}, pickup_order={'b1': ['S']},
        pickup_vehicle={'b1': {'S': 0}}, cutoffs=dict(p.default_cutoffs), e2=[
            dict(satellite='S', vehicle=1, tasks=[('o1', 'd'), ('o1', 'p')]),
            dict(satellite='S', vehicle=0, tasks=[('o2', 'd'), ('o2', 'p')])])
    ev = JointEvaluator(p)
    fixed = ev.fixed(assignment)
    def separate_delivery(problem, route, goods, ready):
        value, end = _route_score(problem, route, goods, ready)
        return value+(1000. if ('o2', 'd') in route['tasks'] and len(route['tasks']) > 1 else 0.), end
    repaired = repair_orders(fixed, state, [fixed.order_map['o2']], time.perf_counter()+10., scorer=separate_delivery, avoid_precedence_cycles=True)
    repaired['cutoffs'] = dict(state['cutoffs'])
    d = next((i, route['vehicle']) for i, route in enumerate(repaired['e2']) if ('o2', 'd') in route['tasks'])
    pick = next((i, route['vehicle']) for i, route in enumerate(repaired['e2']) if ('o2', 'p') in route['tasks'])
    assert pick[0] < d[0] and pick[1] != d[1]
    assert ev(assignment, repaired)[1]['audit']['periodic_replay']['valid']


def test_new_pickup_trip_can_follow_a_delivery_at_a_satellite_boundary():
    source = joint_fixture(zero=True, one_batch=True)
    nodes = {**source.network.nodes, 'T': Satellite('T', 0, 0),
             'C3': Customer('C3', 0, 0, 0, 0, (0, 130), (0, 130), 'T')}
    network = ProblemInstance(nodes, dict(E1_vehicle_count=1, vehicles_per_satellite=1,
        E1_Qk=100, E1_Ck=100, E2_Qv=100, E2_Cv=100, container_weight=1, container_volume=1))
    orders = [replace(source.orders[0], delivery_window=(40, 90), pickup_window=(0, 95)),
              replace(source.orders[1], customer='C3', satellite='T'),
              Order('anchor', 'C2', 'S', 'b1', 1, 1, (40, 90), (50, 95), 0, 2, 0, ('b1',))]
    p = CalendarCycleInstance(network, orders, source.operating_policy,
        {'batch_count_candidates': [1]}, source.cost_profile.config, model=8).fixed_calendar(1)
    assignment = {o.id: 'b1' for o in orders}
    state = dict(deliveries={'b1': [['S', 'T']]}, pickup_order={'b1': ['S', 'T']},
        pickup_vehicle={'b1': {'S': 0, 'T': 0}}, cutoffs=dict(p.default_cutoffs), e2=[
            dict(satellite='S', vehicle=0, tasks=[('o1', 'd'), ('anchor', 'd'), ('o1', 'p'), ('anchor', 'p')]),
            dict(satellite='T', vehicle=0, tasks=[('o2', 'd'), ('o2', 'p')])])
    ev = JointEvaluator(p)
    fixed = ev.fixed(assignment)
    def separate_pickup(problem, route, goods, ready):
        value, end = _route_score(problem, route, goods, ready)
        return (-1000. if route['tasks'] == [('o1', 'p')] else value), end
    repaired = repair_orders(fixed, state, [fixed.order_map['o1']], time.perf_counter()+10., scorer=separate_pickup, avoid_precedence_cycles=True)
    assert repaired is not None
    repaired['cutoffs'] = dict(state['cutoffs'])
    assert ev(assignment, repaired)[1]['audit']['periodic_replay']['valid']
