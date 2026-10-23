"""Physical and economic checks for the optional marginal pickup neighborhood."""
from copy import deepcopy
import time

import pytest

from models.common.cycle import Order
from models.common.cycle.audit import audit_solution
from models.common.cycle.fixed_pool import FixedBatchPoolInstance
from models.common.domain.instance import ProblemInstance
from models.common.domain.node import Customer, Depot, Satellite
from models.routing.model_5.alns.evaluator import evaluate
from models.routing.model_5.alns.marginal_pickup import (
    MarginalPickupStockRepair, marginal_pickup_neighbors,
)
from models.routing.model_5.alns.solver import solve_alns
from tools.configuration.loader import load_json


def pickup_fixture(*, tight_cleaning=False, work_and_fixed_costs=False):
    nodes = {'DEPOT': Depot('DEPOT', 0, 0), **{s: Satellite(s, 0, 0) for s in ('S','T')}}
    nodes.update({c: Customer(c, 0, 0, 0, 0, (0,125), (0,125), s)
                  for c,s in zip('ABCD', ('S','T','S','T'))})
    distance = {a:{b:int(a != b) for b in nodes} for a in nodes}
    travel = deepcopy(distance)
    distance['S']['T'], distance['T']['S'] = 0.1, 0.2
    if tight_cleaning:
        distance['DEPOT']['S'] = distance['S']['DEPOT'] = 10
        travel['S']['T'] = travel['T']['S'] = 5
    fleet = dict(E1_vehicle_count=2, vehicles_per_satellite=1, E1_Qk=20, E1_Ck=20,
                 E2_Qv=20, E2_Cv=20, container_weight=1, container_volume=1)
    policy = dict(policy_version='multi_batch_cutoff_v1', cycle_start=0, cycle_end=125,
        time_tick_seconds=60, cleaning_duration=28 if tight_cleaning else 5,
        container_pool_size=4, e2_max_trips_per_vehicle=2,
        batches=[dict(id=f'b{i}', delivery_duration=30, return_duration=30) for i in (1,2)])
    orders = [Order(c,c,s,f'b{i//2+1}',1,1,(i//2*60,i//2*60+25),
                    (i//2*60,i//2*60+29),0,1)
              for i,(c,s) in enumerate(zip('ABCD',('S','T','S','T')))]
    economic = load_json('config/experiments/benchmark27_v1/economic.json')
    if work_and_fixed_costs:
        economic.update(e1_work_cost_per_minute=0.3, e1_trip_cost=2,
                        e1_vehicle_cost_per_cycle=3)
    problem = FixedBatchPoolInstance(ProblemInstance(nodes,fleet,distance_matrix=distance,
        travel_time_matrix=travel), orders,policy,{},economic)
    state = dict(e2=[dict(satellite=o.satellite,vehicle=0,tasks=[(o.id,'d'),(o.id,'p')])
                    for o in orders],
        deliveries={b.id:[['S','T'],[]] for b in problem.operating_policy.batches},
        pickup_order={b.id:['S','T'] for b in problem.operating_policy.batches},
        pickup_vehicle={b.id:({'S':1,'T':0} if tight_cleaning else {'S':0,'T':1})
                        for b in problem.operating_policy.batches})
    return problem,state


@pytest.mark.parametrize('fees',[False,True])
def test_marginal_cost_matches_replayed_transport_saving_without_changing_deliveries(fees):
    problem,state = pickup_fixture(work_and_fixed_costs=fees)
    original = deepcopy(state)
    score,decoded,issues = evaluate(problem,state)
    assert not issues and audit_solution(problem,decoded)['periodic_replay']['valid']
    candidates = list(marginal_pickup_neighbors(problem,state,decoded,time.perf_counter()+2))
    assert candidates
    deltas = [move['predicted_transport_delta'] for _,move in candidates]
    assert deltas == sorted(deltas)
    candidate,move = next((s,m) for s,m in candidates if m['batch']=='b1' and m['site']=='T')
    cost,result,issues = evaluate(problem,candidate)
    assert not issues and audit_solution(problem,result)['periodic_replay']['valid']
    assert result['container_pool_size'] == decoded['container_pool_size'] == 2
    assert cost < score and cost-score == pytest.approx(move['predicted_transport_delta'])
    assert len(result['e1_trips']) == len(decoded['e1_trips'])-1
    assert candidate['deliveries'] == state['deliveries'] and candidate['e2'] == state['e2']
    assert state == original
    candidate['pickup_order']['b1'].clear()
    assert state == original


def test_full_cost_rejects_transport_saving_that_misses_clean_stock_boundary():
    problem,state = pickup_fixture(tight_cleaning=True)
    score,decoded,issues = evaluate(problem,state)
    assert not issues and decoded['container_pool_size']==2
    assert audit_solution(problem,decoded)['periodic_replay']['valid']
    candidates = list(marginal_pickup_neighbors(problem,state,decoded,time.perf_counter()+2))
    candidate,move = next((s,m) for s,m in candidates if m['batch']=='b1')
    cost,result,issues = evaluate(problem,candidate)
    assert move['predicted_transport_delta'] < 0
    assert not issues and audit_solution(problem,result)['periodic_replay']['valid']
    assert result['container_pool_size']==4 and cost>score
    # Only expose this candidate: acceptance must use the full economic score.
    repair = MarginalPickupStockRepair(problem,lower_bound=2,max_depth=1)
    repair._mixed_neighbors = lambda *args: iter([(candidate,move)])
    assert repair.propose(state,decoded,lambda s:evaluate(problem,s),time.perf_counter()+2) is None


def test_limits_and_full_repair_with_new_stream():
    problem,state = pickup_fixture()
    score,decoded,_ = evaluate(problem,state)
    assert list(marginal_pickup_neighbors(problem,state,decoded,0)) == []
    repair = MarginalPickupStockRepair(problem,lower_bound=2,max_evaluations=1)
    assert repair.propose(state,decoded,lambda s:evaluate(problem,s),0) is None
    assert repair.calls==0
    candidate = repair.propose(state,decoded,lambda s:evaluate(problem,s),time.perf_counter()+2)
    assert repair.evaluations==repair.marginal_evaluations==repair.marginal_feasible==1
    cost,result,issues = evaluate(problem,candidate)
    assert not issues and cost<score and audit_solution(problem,result)['periodic_replay']['valid']
    assert repair.last_run['best_path'][0]['kind']=='e1_marginal_pickup'
    assert repair.propose(state,decoded,lambda s:evaluate(problem,s),time.perf_counter()+2) is None


def test_public_strategy_is_explicit_and_audited():
    problem,_ = pickup_fixture()
    with pytest.raises(ValueError,match='requires'):
        solve_alns(problem,stock_chain_strategy='cost_aware_marginal')
    result = solve_alns(problem,max_time=2,max_iterations=100,seed=42,
                        critical_stock_repair=True,stock_chain_strategy='cost_aware_marginal')
    assert result['info']['stock_chain_strategy']=='cost_aware_marginal'
    assert 'stock_chain_marginal_evaluations' in result['info']
    assert audit_solution(problem,result)['periodic_replay']['valid']
