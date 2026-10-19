"""Audited seed improvements at coupled E1 supply / E2 service boundaries."""
import time
from math import sqrt

import pytest

from models.common.cycle import MultiBatchInstance, Order
from models.common.cycle.audit import audit_solution
from models.common.domain.instance import ProblemInstance
from models.common.domain.node import Customer, Depot, Satellite
from models.routing.model_5.exact.primal.construction import construct_multibatch_incumbent
from tests.routing.test_multibatch_cycle import policy_config


def quality_fixture(merge_e1=False, collinear=False):
    nodes = {'D': Depot('D', 0, 0)}
    if merge_e1:
        locations = ([('A', 1, 0, 'S1'), ('B', -1, 0, 'S2'), ('C', 2, 0, 'S3')]
                     if collinear else [('A', 2, 1, 'S1'), ('B', -2, 0, 'S2'), ('C', 3, -1, 'S3')])
        for _, x, y, site in locations:
            nodes[site] = Satellite(site, x, y)
    else:
        nodes['S'] = Satellite('S', 0, 0)
        locations = [('A', 1, 0, 'S'), ('B', 2, 0, 'S')]
    orders = []
    for oid, x, y, site in locations:
        nodes[oid] = Customer(oid, x, y, 0, 0, (0, 125), (0, 125), site)
        orders.append(Order(oid, oid, site, 'b1', 1, 1,
                            (0, (2 if collinear else 6) if merge_e1 else 10), (20, 25), 0, 5))
    fleet = dict(E1_vehicle_count=3 if merge_e1 else 1, vehicles_per_satellite=1,
                 E1_Qk=500, E1_Ck=500, E2_Qv=500, E2_Cv=500,
                 container_weight=1, container_volume=1)
    policy = policy_config()
    policy.update(container_pool_size=len(orders), e2_max_trips_per_vehicle=1)
    return MultiBatchInstance(ProblemInstance(nodes, fleet), orders, policy)


@pytest.mark.parametrize('merge_e1,collinear,old_cost,new_cost', [
    (False, False, 6, 4), (True, True, 8, 6),
    (True, False, 2*sqrt(5)+4+2*sqrt(10), 4*sqrt(5)+4),
])
def test_improvement_is_valid_and_compatible_with_full_milp(merge_e1, collinear, old_cost, new_cost):
    problem = quality_fixture(merge_e1, collinear)
    baseline = construct_multibatch_incumbent(problem, time.perf_counter()+2, improve=False)
    improved = construct_multibatch_incumbent(problem, time.perf_counter()+2)
    assert baseline['info']['objective'] == pytest.approx(old_cost)
    assert improved['info']['objective'] == pytest.approx(new_cost)
    assert not improved['info']['proven_optimal']
    independent = audit_solution(problem, improved)
    assert independent['valid'], independent['errors']
    assert independent['periodic_replay']['valid']
    if merge_e1:
        # Full distance-first consolidation misses a delivery window. A partial
        # merge saves a tour while retaining every customer's tight due time.
        assert len(improved['e1_trips']) == 2
        if not collinear:
            assert improved['info']['primal_strategy'].startswith('merge_e1_')
    else:
        assert [(e['order_id'], e['kind']) for e in improved['e2_trips'][0]['events']] == [
            ('A', 'd'), ('B', 'd'), ('B', 'p'), ('A', 'p')]
    gp = pytest.importorskip('gurobipy')
    from models.routing.model_5.exact import enhanced_solver as solver
    from models.routing.model_5.exact.formulation import build_model
    model, ctx = build_model(problem)
    try:
        assert solver._set_start(problem, model, ctx, improved)
        model.update()
        for variable in model.getVars():
            if variable.Start != gp.GRB.UNDEFINED:
                variable.LB = variable.UB = variable.Start
        model.Params.TimeLimit = 5
        model.optimize()
        assert model.SolCount and model.ObjVal == pytest.approx(new_cost)
    finally:
        model.dispose()


def test_improvement_deadline_retains_newly_audited_seed(monkeypatch):
    from models.routing.model_5.exact.primal import construction as multibatch_primal
    problem = quality_fixture()
    expired = False

    def audited(*args, **kwargs):
        nonlocal expired
        report = audit_solution(*args, **kwargs)
        if report['valid'] and report['objective'] == 4:
            expired = True
        return report

    monkeypatch.setattr(multibatch_primal, 'audit_solution', audited)
    monkeypatch.setattr(multibatch_primal.time, 'perf_counter', lambda: 2 if expired else 0)
    result = construct_multibatch_incumbent(problem, 1)
    assert expired and result['info']['objective'] == 4
    assert audit_solution(problem, result)['valid']


def test_failed_consolidation_retains_capacity_feasible_seed():
    problem = quality_fixture(merge_e1=True)
    problem.fleet.e1_volume_cap = 1
    baseline = construct_multibatch_incumbent(problem, time.perf_counter()+2, improve=False)
    improved = construct_multibatch_incumbent(problem, time.perf_counter()+2)
    assert baseline is not None and improved == baseline
    assert audit_solution(problem, improved)['valid']
