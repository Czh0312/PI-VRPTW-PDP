"""Global task arcs must preserve complete physical-route semantics."""
from copy import deepcopy
from dataclasses import replace

import pytest

from models.common.cycle.audit import audit_solution
from models.routing.model_3.exact import engine as solver
from models.routing.model_3.exact.single_batch_formulation import build_model
from tests.routing.test_single_batch_solver_audit import tiny_problem
from tests.routing.test_single_batch_primal import split_problem


@pytest.mark.parametrize('model', [1, 2, 3, 4])
@pytest.mark.parametrize('seed', range(6))
def test_global_arcs_match_indexed_routes_with_multiple_vehicles_and_slots(monkeypatch, model, seed):
    pytest.importorskip('gurobipy')
    p = tiny_problem(model, seed)
    p.fleet.e1_count = 2
    p.fleet.e2_per_sat_count = 3
    p.operating_policy = replace(p.operating_policy, e2_max_trips_per_vehicle=3)
    results = []
    for compressed in (False, True):
        monkeypatch.setattr(solver, 'build_model', lambda *a, **kw: build_model(*a, compress_routes=compressed, **kw))
        r = solver.solve_exact(p, time_limit=5)
        if r.get('audit', {}).get('valid'):
            assert audit_solution(p, r)['valid']
            assert r['info']['proven_optimal']
        else:
            assert r['info']['proven_infeasible']
        results.append(r['info'])
    assert results[0]['proven_infeasible'] == results[1]['proven_infeasible']
    if not results[0]['proven_infeasible']:
        assert results[0]['objective'] == pytest.approx(results[1]['objective'])


def test_compressed_mixed_trip_start_is_not_overwritten_by_unused_route_copies():
    gp = pytest.importorskip('gurobipy')
    from models.routing.model_3.exact.initial_solution import construct_incumbent
    import time
    p = split_problem()
    p.fleet.e2_per_sat_count = 2
    start = construct_incumbent(p, time.perf_counter()+1)
    assert start and audit_solution(p, start)['valid']
    saved = deepcopy(start)
    m, ctx = build_model(p, compress_routes=True)
    try:
        assert solver._set_start(p, m, ctx, start)
        expected = {( (a['order_id'], a['kind']), (b['order_id'], b['kind']) )
                    for r in start['e2_trips'] for a,b in zip(r['events'], r['events'][1:])}
        m.update()
        assert {key for key,v in ctx['global_e2_arcs'].items() if v.Start == 1} == expected
        for v in m.getVars():
            if v.Start != gp.GRB.UNDEFINED:
                v.LB = v.UB = v.Start
        m.Params.TimeLimit = 3
        m.optimize()
        assert m.SolCount and m.ObjVal == pytest.approx(start['info']['objective'])
        assert start == saved
    finally:
        m.dispose()


def test_selected_arc_cannot_join_tasks_on_different_physical_routes():
    pytest.importorskip('gurobipy')
    p = tiny_problem(1, 0)
    # Zero travel/time avoids accidentally proving infeasible by a time window.
    p.fleet.e2_per_sat_count = 2
    from models.common.cycle.assignment import assigned_problem
    p = assigned_problem(p, {o.customer: 'S' for o in p.orders})
    m, ctx = build_model(p, compress_routes=True)
    try:
        r0,r1 = ctx['e2']
        r0['visit'][0].LB = 1
        r1['visit'][1].LB = 1
        keys = [(o.id,'d') for o in p.orders]
        ctx['global_e2_arcs'][keys[0],keys[1]].LB = 1
        m.Params.TimeLimit = 3
        m.optimize()
        assert m.Status == 3  # INFEASIBLE: endpoint memberships disagree.
    finally:
        m.dispose()


@pytest.mark.parametrize('threshold', [0, 10**9])
def test_size_dispatch_and_estimator_use_the_same_arc_representation(monkeypatch, threshold):
    pytest.importorskip('gurobipy')
    from models.routing.model_3.exact import strengthening, single_batch_formulation
    from models.routing.model_3.exact.domains import assignment_domains
    monkeypatch.setattr(strengthening, 'COMPRESS_ROUTE_MIN_INTERNAL_ARCS', threshold)
    monkeypatch.setattr(single_batch_formulation, 'COMPRESS_ROUTE_MIN_INTERNAL_ARCS', threshold)
    p = tiny_problem(4, 1)
    p.fleet.e2_per_sat_count = 3
    p.operating_policy = replace(p.operating_policy, e2_max_trips_per_vehicle=12)
    check = assignment_domains(p)
    m, ctx = build_model(p, domain_check=check)
    try:
        assert ctx['compressed_e2_routes'] == (threshold == 0)
        distinct = {v.index for r in ctx['e2'] for v in r['x'].values()}
        assert strengthening.joint_arc_estimate(p, check) == len(distinct)
    finally:
        m.dispose()
