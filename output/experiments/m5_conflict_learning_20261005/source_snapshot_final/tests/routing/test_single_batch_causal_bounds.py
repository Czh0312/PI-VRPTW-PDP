"""Full original optima and fixed cross-trip schedules validate step bounds."""
from copy import deepcopy
from dataclasses import replace
import math
import time
from unittest.mock import patch

import pytest

from models.routing.common.cycle import MultiBatchInstance
from models.routing.common.cycle.audit import audit_solution
from models.routing.common.cycle.state import empty_result
from models.routing.solvers.exact.single_batch_formulation import build_model
from models.routing.solvers.exact.solver import _set_start
from tests.routing.test_single_batch_solver_audit import tiny_problem,enumerate_tiny


@pytest.mark.parametrize('model',[1,2,3,4])
@pytest.mark.parametrize('seed',[0,1,3,4])
@pytest.mark.parametrize('compressed',[False,True])
@pytest.mark.parametrize('cycles',[False,True])
def test_full_bound_model_matches_original_integer_oracle(model,seed,compressed,cycles):
    pytest.importorskip('gurobipy')
    from models.routing.solvers.exact import solver
    p = tiny_problem(model,seed)
    oracle = enumerate_tiny(p)
    def builder(*args,**kw):
        bounded = kw.pop('single_batch_causal_bounds',False)
        kw.pop('single_batch_cycle_cuts',None)
        return build_model(*args,**kw,compress_routes=compressed,linear_e2_links='complete',
                           short_cycle_cuts=cycles,causal_step_bounds=bounded)
    with patch.object(solver,'build_model',side_effect=builder):
        result = solver.solve_exact(p,time_limit=3,_single_batch_causal_bounds=True)
    if math.isfinite(oracle):
        assert result['info']['proven_optimal'],result['info']
        assert result['info']['objective']==pytest.approx(oracle)
        assert audit_solution(p,result)['valid'] and result['audit']['periodic_replay']['valid']
        assert result['info']['e2_causal_step_bounds']
        if p.reusable:
            assert sum(e['kind']=='p' for t in result['e2_trips'] for e in t['events'])==len(p.orders)
    else:
        assert result['info']['proven_infeasible'] and not result['info']['proven_optimal']


@pytest.mark.parametrize('compressed',[False,True])
@pytest.mark.parametrize('linear',[False,'complete'])
def test_many_unused_slots_and_noncanonical_vehicle_start_remain_feasible(compressed,linear):
    gp = pytest.importorskip('gurobipy')
    from tests.routing.test_single_batch_schedule_start import delayed_three_trip_start
    p,initial = delayed_three_trip_start()
    p.operating_policy = replace(p.operating_policy,e2_max_trips_per_vehicle=3)
    p.fleet.e2_per_sat_count = 3
    initial['operating_policy'] = p.operating_policy.as_dict()
    before = deepcopy(initial)
    assert audit_solution(p,initial)['valid']
    m,ctx = build_model(p,compress_routes=compressed,linear_e2_links=linear,causal_step_bounds=True)
    try:
        assert _set_start(p,m,ctx,initial) and initial==before
        m.update()
        for var in m.getVars():
            if var.Start!=gp.GRB.UNDEFINED:
                var.LB = var.UB = var.Start
        m.Params.TimeLimit = 3
        m.optimize()
        assert m.Status==gp.GRB.OPTIMAL
        assert any(r['used'].X<.5 and r['step_start'].LB>0 for r in ctx['e2'])
    finally:
        m.dispose()


@pytest.mark.parametrize('shared',[False,True])
@pytest.mark.parametrize('compressed',[False,True])
def test_cross_trip_pickup_start_preserves_original_tasks_and_integer_schedule(shared,compressed):
    gp = pytest.importorskip('gurobipy')
    from tests.routing.test_single_batch_primal import split_problem
    from models.routing.solvers.exact.primal import construct_incumbent
    p = split_problem()
    initial = construct_incumbent(p,time.perf_counter()+1)
    assert initial and audit_solution(p,initial)['valid']
    m,ctx = build_model(p,share_e2=shared,compress_routes=compressed,causal_step_bounds=True)
    try:
        assert _set_start(p,m,ctx,initial)
        m.update()
        for var in m.getVars():
            if var.Start!=gp.GRB.UNDEFINED:
                var.LB = var.UB = var.Start
        m.Params.TimeLimit = 3
        m.optimize()
        assert m.SolCount and m.ObjVal==pytest.approx(initial['info']['objective'])
    finally:
        m.dispose()


def singleton_chain(model):
    base = tiny_problem(model,1)
    base.fleet.e2_per_sat_count = 1
    orders = [replace(o,satellite='S',service_time=0,min_use_time=0,
                      delivery_window=(0,45),pickup_window=(0,60)) for o in base.orders]
    policy = replace(base.operating_policy,e1_load_time=0,e2_dispatch_prep_time=0,
        e2_return_unload_time=0,depot_return_unload_time=0,e1_turnaround_time=0,e2_turnaround_time=0,
        e2_max_trips_per_vehicle=len(orders)*(2 if model==3 else 1))
    p = MultiBatchInstance(base.network,orders,policy,model=model)
    for key in p._travel:
        p._travel[key] = 0
    initial = empty_result(p,'independent_boundary_chain')
    events = [dict(kind='d',site_id='S',start=0,quantity=len(orders))]
    finish = 0
    if p.reusable:
        events.append(dict(kind='p',site_id='S',start=policy.batches[0].cutoff,quantity=len(orders)))
        finish = policy.batches[0].cutoff
    initial['e1_trips'] = [dict(trip_id='in',vehicle_id=0,batch_id='b1',departure=0,return_complete=finish,events=events)]
    for o in orders:
        for kind in p.task_kinds:
            slot = len(initial['e2_trips'])
            initial['e2_trips'].append(dict(trip_id=f't{slot}',satellite_id='S',vehicle_id=0,trip_index=slot,
                departure=0,return_complete=0,events=[dict(kind=kind,order_id=o.id,start=0,quantity=1)]))
    checked = audit_solution(p,initial)
    assert checked['valid'],checked['errors']
    initial['info'].update(status='FEASIBLE',objective=checked['objective'])
    initial['audit'] = checked
    return p,initial


@pytest.mark.parametrize('model',[1,3])
@pytest.mark.parametrize('compressed',[False,True])
def test_zero_time_all_singletons_reach_last_task_and_route_end_upper_bounds(model,compressed):
    gp = pytest.importorskip('gurobipy')
    p,initial = singleton_chain(model)
    m,ctx = build_model(p,compress_routes=compressed,linear_e2_links='complete',causal_step_bounds=True)
    try:
        assert _set_start(p,m,ctx,initial)
        m.update()
        for var in m.getVars():
            if var.Start!=gp.GRB.UNDEFINED:
                var.LB = var.UB = var.Start
        m.optimize()
        assert m.Status==gp.GRB.OPTIMAL
        used = sorted((r for r in ctx['e2'] if r['used'].X>.5),key=lambda r:r['slot'])
        assert len(used)==len(p.orders)*len(p.task_kinds)
        last = initial['e2_trips'][-1]['events'][0]
        task = ctx['e2_task_steps'][last['order_id'],last['kind']]
        assert task.X==task.UB==ctx['e2_causal_limit']-1
        assert used[-1]['step_end'].X==ctx['e2_causal_limit']
    finally:
        m.dispose()


@pytest.mark.parametrize('model',[1,2,3,4])
def test_bounds_and_explicit_slack_shrink_without_extra_variables_or_rows(model):
    pytest.importorskip('gurobipy')
    p = tiny_problem(model,1)
    models = [build_model(p,compress_routes=True,linear_e2_links='complete',causal_step_bounds=v)
              for v in (False,True)]
    try:
        old,new = models
        assert (old[0].NumVars,old[0].NumConstrs,old[0].NumGenConstrs)==(new[0].NumVars,new[0].NumConstrs,new[0].NumGenConstrs)
        assert new[1]['e2_causal_largest_m'] < old[1]['e2_causal_largest_m']
        assert all(v.LB>=1 and v.UB<new[1]['e2_causal_limit'] for v in new[1]['e2_task_steps'].values())
    finally:
        for m,_ in models:
            m.dispose()


@pytest.mark.parametrize('value',[None,1,'bounds'])
def test_bound_hook_rejects_invalid_types(value):
    from models.routing.solvers.exact.solver import solve_exact
    with pytest.raises(ValueError,match='boolean'):
        solve_exact(tiny_problem(1,1),_single_batch_causal_bounds=value)
