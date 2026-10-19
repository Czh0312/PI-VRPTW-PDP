"""Original task/route enumeration validates full-model opposite-arc cuts."""
from dataclasses import replace
from itertools import permutations
import math
import time
from unittest.mock import patch

import pytest

from models.common.cycle.audit import audit_solution
from models.routing.model_3.exact.branch_cut.short_cycles import add_two_cycle_rows
from models.routing.model_3.exact.formulation import build_model
from models.routing.model_3.exact.enhanced_solver import _set_start
from tests.routing.test_single_batch_solver_audit import tiny_problem,enumerate_tiny


def test_all_elementary_task_permutations_and_route_partitions_satisfy_actual_rows():
    gp = pytest.importorskip('gurobipy')
    p = tiny_problem(4,1)
    tasks = [(o.id,k) for o in p.orders for k in p.task_kinds]
    model = gp.Model()
    model.Params.OutputFlag = 0
    arcs = {(a,b):model.addVar(vtype='B') for a in tasks for b in tasks if a!=b}
    rows = add_two_cycle_rows(model,arcs,lambda:None)
    model.update()
    keys = {var.index:key for key,var in arcs.items()}
    actual = []
    for row in rows:
        expression = model.getRow(row)
        actual.append(([(keys[expression.getVar(i).index],expression.getCoeff(i))
                       for i in range(expression.size())],row.RHS))
    assert len(actual)==15
    checked = 0
    try:
        # A SUPERSET of all time/capacity-feasible original routes: do not
        # omit pickup tasks or require a production feasibility helper.
        for sequence in permutations(tasks):
            for boundaries in range(1 << (len(tasks)-1)):
                selected = {(a,b) for i,(a,b) in enumerate(zip(sequence,sequence[1:]))
                            if not boundaries & (1 << i)}
                assert all(sum(coef for key,coef in terms if key in selected)<=rhs
                           for terms,rhs in actual)
                checked += 1
        assert checked==23040
    finally:
        model.dispose()


@pytest.mark.parametrize('model',[1,2,3,4])
@pytest.mark.parametrize('seed',[0,1,3,4])
@pytest.mark.parametrize('compressed',[False,True])
@pytest.mark.parametrize('linear',[False,'complete'])
def test_complete_cut_solver_matches_original_integer_oracle(model,seed,compressed,linear):
    pytest.importorskip('gurobipy')
    from models.routing.model_3.exact import enhanced_solver as solver
    p = tiny_problem(model,seed)
    oracle = enumerate_tiny(p)
    def build(*args,**kwargs):
        enabled = kwargs.pop('single_batch_cycle_cuts',False)
        return build_model(*args,**kwargs,short_cycle_cuts=enabled,
                           compress_routes=compressed,linear_e2_links=linear)
    with patch.object(solver,'build_model',side_effect=build):
        result = solver.solve_exact(p,time_limit=3,_single_batch_cycle_cuts=True)
    info = result['info']
    if math.isfinite(oracle):
        assert info['proven_optimal'],info
        assert info['objective']==pytest.approx(oracle)
        assert audit_solution(p,result)['valid'] and result['audit']['periodic_replay']['valid']
        assert info['e2_short_cycle_cuts']
        assert len(result['e2_trips'])>0
        if p.reusable:
            assert sum(e['kind']=='p' for t in result['e2_trips'] for e in t['events'])==len(p.orders)
            assert sum(e['kind']=='d' for t in result['e2_trips'] for e in t['events'])==len(p.orders)
    else:
        assert info['proven_infeasible'] and not info['proven_optimal']


@pytest.mark.parametrize('model',[1,2,3,4])
@pytest.mark.parametrize('compressed',[False,True])
def test_explicit_rows_add_no_variables_and_default_model_has_no_new_cuts(model,compressed):
    pytest.importorskip('gurobipy')
    p = tiny_problem(model,1)
    baseline,original = build_model(p,compress_routes=compressed)
    candidate,new = build_model(p,compress_routes=compressed,short_cycle_cuts=True)
    try:
        assert not original['e2_short_cycle_cuts'] and original['e2_short_cycle_rows']==[]
        assert new['e2_short_cycle_rows']
        assert candidate.NumVars==baseline.NumVars
        assert candidate.NumConstrs==baseline.NumConstrs+len(new['e2_short_cycle_rows'])
        assert candidate.NumGenConstrs==baseline.NumGenConstrs
        assert (original['capacity_separator'] is None)==(new['capacity_separator'] is None)
        assert original['shared_e2_arcs']==new['shared_e2_arcs']
    finally:
        baseline.dispose()
        candidate.dispose()


def test_missing_reverse_self_arcs_and_duplicate_physical_location_keep_task_identity():
    gp = pytest.importorskip('gurobipy')
    m = gp.Model()
    m.Params.OutputFlag = 0
    a,b,c = ('same_customer_order_A','d'),('same_customer_order_B','d'),('same_customer_order_A','p')
    arcs = {key:m.addVar(vtype='B') for key in ((a,b),(b,a),(a,c),(a,a))}
    try:
        rows = add_two_cycle_rows(m,arcs,lambda:None)
        m.update()
        assert len(rows)==1 and m.getRow(rows[0]).size()==2
        for key,var in arcs.items():
            var.LB = var.UB = int(key in ((a,b),(a,c)))
        m.optimize()
        assert m.Status==gp.GRB.OPTIMAL
        arcs[b,a].LB = arcs[b,a].UB = 1
        m.optimize()
        assert m.Status==gp.GRB.INFEASIBLE
    finally:
        m.dispose()


def test_zero_duration_mtz_fractional_cycle_is_cut_by_actual_opposite_arc_row():
    gp = pytest.importorskip('gurobipy')
    m = gp.Model()
    m.Params.OutputFlag = 0
    a,b = ('a','d'),('b','p')
    arcs = {key:m.addVar(lb=.75,ub=.75) for key in ((a,b),(b,a))}
    rank = m.addVars(2,lb=1,ub=1)
    m.addConstr(rank[1]>=rank[0]+1-6*(1-arcs[a,b]))
    m.addConstr(rank[0]>=rank[1]+1-6*(1-arcs[b,a]))
    try:
        m.optimize()
        assert m.Status==gp.GRB.OPTIMAL
        add_two_cycle_rows(m,arcs,lambda:None)
        m.optimize()
        assert m.Status==gp.GRB.INFEASIBLE
    finally:
        m.dispose()


@pytest.mark.parametrize('compressed',[False,True])
def test_actual_audited_cross_trip_pickup_start_still_completes(monkeypatch,compressed):
    gp = pytest.importorskip('gurobipy')
    from tests.routing.test_single_batch_primal import split_problem
    from models.routing.model_3.exact.initialization import construct_incumbent
    p = split_problem()
    initial = construct_incumbent(p,time.perf_counter()+1)
    assert initial and audit_solution(p,initial)['valid']
    m,ctx = build_model(p,compress_routes=compressed,short_cycle_cuts=True)
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


def test_row_generation_uses_build_deadline_and_cannot_silently_skip_a_failure():
    def expired():
        raise TimeoutError('expired cycle construction')
    with pytest.raises(TimeoutError,match='expired cycle'):
        add_two_cycle_rows(None,{(('a','d'),('b','p')):None},expired)


@pytest.mark.parametrize('value',[None,1,'pairs'])
def test_invalid_hook_type_is_rejected(value):
    from models.routing.model_3.exact.enhanced_solver import solve_exact
    with pytest.raises(ValueError,match='boolean'):
        solve_exact(tiny_problem(1,1),_single_batch_cycle_cuts=value)


def test_cut_option_cannot_ignore_unshared_arcs_or_other_batch_models():
    with pytest.raises(ValueError,match='shared'):
        build_model(tiny_problem(1,1),share_e2=False,short_cycle_cuts=True)
    from models.routing.model_3.exact.model import _build_complete_model as dispatch
    p = tiny_problem(1,1)
    p.operating_policy = replace(p.operating_policy,batches=p.operating_policy.batches*2)
    with pytest.raises(ValueError,match='B=1'):
        dispatch(p,single_batch_cycle_cuts=True)
