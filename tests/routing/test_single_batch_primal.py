"""Split seeds preserve coupled timings, physical slots and exact proofs."""
from copy import deepcopy
from dataclasses import replace
import time
from types import SimpleNamespace

import pytest

from models.common.cycle import MultiBatchInstance, MultiBatchPolicy, Order
from models.common.cycle.audit import audit_solution
from models.common.domain.instance import ProblemInstance
from models.common.domain.node import Customer, Depot, Satellite
from models.routing.model_3.exact.initialization import construct_incumbent


def split_problem():
    policy = MultiBatchPolicy.from_config(dict(policy_version='multi_batch_cutoff_v1',
        cycle_start=0, cycle_end=80, batches=[dict(id='b1', delivery_duration=60, return_duration=15)],
        container_pool_size=3, cleaning_duration=2, time_tick_seconds=60,
        e2_max_trips_per_vehicle=6, e1_load_time=1, e2_dispatch_prep_time=1,
        e2_return_unload_time=1, e2_turnaround_time=1, depot_return_unload_time=1))
    nodes = {'D': Depot('D', 0, 0), 'S': Satellite('S', 0, 0)}
    orders = []
    for cid, delivery, pickup in [('A', 4, 15), ('B', 10, 21), ('C', 30, 40)]:
        nodes[cid] = Customer(cid, 0, 0, 0, 0, (0, 80), (0, 80), 'S')
        orders.append(Order(cid, cid, 'S', 'b1', 1, 1, (delivery, delivery),
                            (pickup, pickup), 1, 5))
    matrix = {a: {b: int(a != b) for b in nodes} for a in nodes}
    fleet = dict(E1_vehicle_count=1, vehicles_per_satellite=1, E1_Qk=10, E1_Ck=3,
                 E2_Qv=2, E2_Cv=1, container_weight=1, container_volume=1)
    return MultiBatchInstance(ProblemInstance(nodes, fleet, distance_matrix=matrix,
                                               travel_time_matrix=matrix), orders, policy, model=3)


def test_pickups_fit_between_delivery_waves_on_one_physical_vehicle():
    p = split_problem()
    orders = deepcopy(p.orders)
    result = construct_incumbent(p, time.perf_counter()+1)
    assert result is not None and result['info']['primal_strategy'].startswith('split_insertion')
    audit = audit_solution(p, result)
    assert audit['valid'], audit['errors']
    assert audit['periodic_replay']['valid']
    assert [r['trip_index'] for r in result['e2_trips']] == list(range(len(result['e2_trips'])))
    assert len(result['e2_trips']) <= 6
    a_trips = [t['trip_index'] for t in result['e2_trips']
               for e in t['events'] if e['order_id'] == 'A']
    assert len(set(a_trips)) == 2
    visits = [(e['order_id'], e['kind']) for t in result['e2_trips'] for e in t['events']]
    assert visits == [('A', 'd'), ('B', 'd'), ('A', 'p'), ('B', 'p'), ('C', 'd'), ('C', 'p')]
    assert p.orders == orders
    assert not result['info']['proven_optimal'] and not result['info']['proven_infeasible']


def test_exhaustion_and_insufficient_slots_never_return_partial_seed():
    p = split_problem()
    assert construct_incumbent(p, 0) is None
    p.operating_policy = replace(p.operating_policy, e2_max_trips_per_vehicle=1)
    assert construct_incumbent(p, time.perf_counter()+1) is None


def test_expiry_after_auditing_retains_complete_seed(monkeypatch):
    from models.routing.model_3.exact.accelerators.single_batch import primal as single_batch_primal
    p = split_problem()
    expired = [False]
    def audited(*args):
        result = audit_solution(*args)
        expired[0] = True
        return result
    monkeypatch.setattr(single_batch_primal, 'audit_solution', audited)
    monkeypatch.setattr(single_batch_primal, 'time', SimpleNamespace(
        perf_counter=lambda: float('inf') if expired[0] else 0))
    result = construct_incumbent(p, time.perf_counter()+1)
    assert result and expired[0] and audit_solution(p, result)['valid']


def test_split_start_is_extendable_in_milp_and_survives_build_timeout(monkeypatch):
    gp = pytest.importorskip('gurobipy')
    from models.routing.model_3.exact.search import engine as solver
    p = split_problem()
    start = construct_incumbent(p, time.perf_counter()+1)
    saved = deepcopy(start)
    m, ctx = solver.build_model(p)
    try:
        assert solver._set_start(p, m, ctx, start)
        m.update()
        for variable in m.getVars():
            if variable.Start != gp.GRB.UNDEFINED:
                variable.LB = variable.UB = variable.Start
        m.Params.TimeLimit = 3
        m.optimize()
        assert m.SolCount and m.ObjVal == pytest.approx(start['info']['objective'])
    finally:
        m.dispose()
    def timeout(*args, **kwargs):
        raise TimeoutError('forced build expiry')
    monkeypatch.setattr(solver, 'build_model', timeout)
    result = solver.solve_exact(p, time_limit=1)
    assert result['info']['status'] == 'TIME_LIMIT_BUILD'
    assert audit_solution(p, result)['valid']
    assert result['info']['objective'] == pytest.approx(start['info']['objective'])
    assert not result['info']['proven_optimal'] and not result['info']['proven_infeasible']
    assert start == saved


def test_assignment_refinement_preserves_fast_probe_breadth(monkeypatch):
    from models.routing.model_4.exact import initialization as primal
    from models.routing.model_4.exact.search import assignment
    from tests.routing.test_single_batch_solver_audit import tiny_problem
    from models.routing.model_4.exact.accelerators.domains import assignment_domains
    p = tiny_problem(4, 1)
    check = assignment_domains(p)
    original = primal.construct_incumbent
    calls = []
    def recorded(problem, deadline, *, split_trips=True):
        calls.append(split_trips)
        return original(problem, deadline, split_trips=split_trips)
    monkeypatch.setattr(primal, 'construct_incumbent', recorded)
    best, _, tried = assignment._assignment_seed(p, check['domains'], time.perf_counter()+1)
    assert best and audit_solution(p, best)['valid']
    # All unique cheap partitions are tried before the at-most-three refinements.
    assert tried == len(list(assignment._assignment_stream(p, check['domains'])))
    assert calls[:tried] == [False]*tried
    assert 1 <= len(calls[tried:]) <= 3 and all(calls[tried:])
