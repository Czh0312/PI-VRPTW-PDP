"""Compare independent complete formulations, including timing/capacity edges."""
import copy
import random

import pytest

gp = pytest.importorskip('gurobipy')
from archive.routing_baselines.common.commodity_flow import solve_commodity_flow
from models.common.task_builder import build_tasks_for_model
from models.common.solution_audit import audit_exact_solution
from models.common.domain.instance import ProblemInstance
from models.common.domain.node import Customer, Depot, Satellite
from importlib import import_module


def problem(seed):
    rng = random.Random(seed)
    nodes = {'D':Depot('D',0,0), 'S0':Satellite('S0',3,0,service_time=.3),
             'S1':Satellite('S1',-3,0,service_time=.3)}
    for i in range(4):
        nodes[f'C{i}'] = Customer(f'C{i}',rng.uniform(-7,7),rng.uniform(-3,3),
            demand=rng.randint(1,4), service_time=.5, tw_del=(0,35),
            tw_pick=(12 if seed % 2 else 0,70),assigned_sat=f'S{i%2}')
    fleet = dict(E1_vehicle_count=2,E1_Qk=30,E1_Ck=6,vehicles_per_satellite=2,
                 E2_Qv=10,E2_Cv=2,container_weight=1,container_volume=1,speed=1)
    p = ProblemInstance(nodes,fleet)
    p.aggregate_demands_from_pre_assignment()
    return p


@pytest.mark.parametrize('seed', [17,28,39])
@pytest.mark.parametrize('model', [1,2,3,4])
def test_complete_flow_matches_vehicle_indexed_model(seed, model):
    p = problem(seed)
    e1,e2 = build_tasks_for_model(p,model)
    solver = import_module(f'archive.routing_baselines.variants.model_{model}').solve_exact
    common = dict(problem=p,e1_tasks=e1,e2_tasks_by_sat=e2,vehicles_per_satellite=2,
                  time_limit=30,verbose=False,return_info=True,gurobi_params={'Threads':2})
    reference = solver(**common,backend='indexed')
    actual = solver(**common,backend='commodity_flow')
    assert reference[-1]['proven_optimal'], reference[-1]
    assert actual[-1]['proven_optimal'], actual[-1]
    assert actual[-1]['objective'] == pytest.approx(reference[-1]['objective'],abs=1e-6)
    audit = audit_exact_solution(p,model,actual[0],actual[1],
        assignment=actual[2] if model in (2,4) else None,objective=actual[-1]['objective'])
    assert audit['valid'], audit


def test_zero_load_and_zero_time_tasks_cannot_form_disconnected_cycles():
    p = problem(17)
    for node in p.nodes.values():
        node.x = 0
        node.y = 0
        node.service_time = 0
    p._build_matrices()
    e1,e2 = build_tasks_for_model(p,3)
    for task in e1 + [t for ts in e2.values() for t in ts]:
        task.w = task.v = 0
        task.tw = (0,100)
    r1,r2,info = solve_commodity_flow(p,3,2,e1_tasks=e1,e2_tasks_by_sat=e2,
        time_limit=10,return_info=True,auto_warm_start=False)
    assert info['proven_optimal']
    assert sum(len(r)-2 for r in r1) == len(e1)
    assert sum(len(r)-2 for rs in r2.values() for r in rs) == sum(map(len,e2.values()))


def test_positive_gap_request_is_not_reported_as_exact_proof():
    p = problem(17)
    e1,e2 = build_tasks_for_model(p,3)
    *_,info = solve_commodity_flow(p,3,2,e1_tasks=e1,e2_tasks_by_sat=e2,
        time_limit=10,mip_gap=.5,return_info=True,auto_warm_start=False)
    assert not info['proven_optimal']
