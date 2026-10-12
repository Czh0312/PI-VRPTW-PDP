"""A shared vehicle must deliver overlapping appointments before waiting."""
from dataclasses import replace
import random

from models.common.cycle import MultiBatchInstance, Order
from models.common.cycle.audit import audit_solution
from models.routing.model_3.alns.construction import appointment_state, chronological_state
from models.routing.model_3.alns.evaluator import evaluate
from tests.routing.test_multibatch_cycle import fixture


def test_grouped_appointments_allow_one_vehicle_to_deliver_before_returns():
    original = fixture(one_batch=True, pool=2)
    orders = [Order(f'o{i}', 'C', 'S', 'b1', 1, 1, (11,15), (24,28), 1, 5) for i in (1,2)]
    problem = MultiBatchInstance(original.network, orders, original.operating_policy)
    _, _, separate_errors = evaluate(problem, chronological_state(problem,random.Random(42)))
    assert 'E2_TIME_WINDOW' in separate_errors
    state = appointment_state(problem, random.Random(42))
    _, result, errors = evaluate(problem,state)
    assert not errors
    assert audit_solution(problem,result)['valid']
    assert len(result['e2_trips'])==1
    assert [e['kind'] for e in result['e2_trips'][0]['events']]==['d','d','p','p']


def test_grouping_does_not_hide_an_insufficient_container_pool():
    original = fixture(one_batch=True, pool=2)
    orders = [Order(f'o{i}', 'C', 'S', 'b1', 1, 1, (11,15), (24,28), 1, 5) for i in (1,2)]
    problem = MultiBatchInstance(original.network, orders, replace(original.operating_policy,container_pool_size=1))
    _, result, errors = evaluate(problem,appointment_state(problem,random.Random(42)))
    assert 'CLEAN_STOCK_SHORTAGE' in errors
    assert not audit_solution(problem,result)['valid']


def test_forward_only_grouping_does_not_block_early_jobs_with_a_late_appointment():
    from models.common.domain.node import Customer
    from models.common.domain.instance import ProblemInstance
    original=fixture(one_batch=True,pool=3)
    nodes=dict(original.network.nodes)
    nodes['F']=Customer('F',12,0,0,0,(0,135),(0,135),'S')
    fleet=dict(E1_vehicle_count=1,vehicles_per_satellite=1,E1_Qk=50,E1_Ck=50,
               E2_Qv=50,E2_Cv=50,container_weight=1,container_volume=1)
    calendar=replace(original.operating_policy,cycle_end=135,
                     batches=(replace(original.operating_policy.batches[0],cutoff=100,end=130),))
    orders=[Order('early','C','S','b1',1,1,(11,14),(20,120),1),
            Order('nearby_early','F','S','b1',1,1,(13,16),(20,120),1),
            Order('future_same_location','C','S','b1',1,1,(80,90),(100,120),1)]
    problem=MultiBatchInstance(ProblemInstance(nodes,fleet),orders,calendar,model=1)
    _,result,errors=evaluate(problem,appointment_state(problem,random.Random(42)))
    assert not errors
    assert audit_solution(problem,result)['valid']
    assert [e['order_id'] for e in result['e2_trips'][0]['events']]==['early','nearby_early']
