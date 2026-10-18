"""Iff activation and complete optimal projections under valid group cuts."""
import itertools,math
import pytest
from models.routing.model_5.exact.accelerators.multibatch.group_cuts import add_group_schedule_cut, project_group_schedule_cut
from models.routing.model_5.exact.accelerators.multibatch.group_schedule import conditional_group_schedule
from models.routing.model_5.exact.accelerators.multibatch.joint_inventory import build_joint_inventory, set_joint_projection
from tests.routing.test_multibatch_joint_inventory import domains
from tests.routing.test_multibatch_cover_schedule import covers_of
from tests.routing.test_multibatch_departure_loads import nonmetric_problem
from tests.routing.test_multibatch_cycle import fixture


@pytest.mark.parametrize('flags',list(itertools.product([0,1],repeat=4)))
@pytest.mark.parametrize('infeasible',[False,True])
def test_total_floor_or_no_good_activates_iff_every_group_matches(flags,infeasible):
    gp=pytest.importorskip('gurobipy');p=fixture(pool=2)
    groups={'S':[[('o1','d')],[('o1','p')],[('o2','d')],[('o2','p')]]}
    model=gp.Model();model.Params.OutputFlag=0;model.Params.DualReductions=0
    model.addVar(lb=1,ub=1,obj=5)
    choices={(i,None):model.addVar(lb=f,ub=f,obj=1) for i,f in enumerate(flags)};model.update()
    context=dict(routes={'S':dict(choices=choices,rows=[dict(keys=g,column=dict(cost=1)) for g in groups['S']])})
    cert=dict(scope='M5_conditional_all_path_complete_task_groups_total_cost',groups=groups,
        permutations_complete=True,canonical_position_completeness=True,conditional_lower_bound=12,
        conditional_infeasible=infeasible,status=3 if infeasible else 2)
    try:
        cut=add_group_schedule_cut(p,model,context,cert);model.optimize()
        if all(flags) and infeasible:assert model.Status==gp.GRB.INFEASIBLE
        else:
            assert model.Status==gp.GRB.OPTIMAL
            assert model.ObjVal==pytest.approx(12 if all(flags) else 5+sum(flags))
            assert cut['active'].X==int(all(flags))
        assert cut['cost_target']=='E1_plus_actual_E2_epigraph'
        assert not cut['proven_optimal'] and not cut['proven_infeasible']
    finally:model.dispose()


@pytest.mark.parametrize('seed',range(6))
@pytest.mark.parametrize('timed',[False,True])
def test_independent_complete_optimum_projects_to_cut_and_bounds_full_optimum(seed,timed):
    gp=pytest.importorskip('gurobipy')
    from models.routing.model_5.exact.search.engine import solve_exact
    p=nonmetric_problem(seed);opt=solve_exact(p,time_limit=5);assert opt['info']['proven_optimal']
    cert=conditional_group_schedule(p,covers_of(opt),time_limit=3)['certificate']
    assert cert['conditional_optimal']
    model,context=build_joint_inventory(p,domains(p,timed=timed))
    try:
        cut=add_group_schedule_cut(p,model,context,cert)
        set_joint_projection(p,context,opt,fix=True);projection=project_group_schedule_cut(p,context,opt,cut,fix=True)
        assert projection['active']==1
        model.optimize();assert model.Status==gp.GRB.OPTIMAL and model.ObjVal<=opt['info']['objective']+1e-6
    finally:model.dispose()
    model,context=build_joint_inventory(p,domains(p,timed=timed))
    try:
        add_group_schedule_cut(p,model,context,cert);model.optimize()
        assert model.Status==gp.GRB.OPTIMAL and model.ObjBound<=opt['info']['objective']+1e-6
    finally:model.dispose()


@pytest.mark.parametrize('change',[dict(scope='fixed_order'),dict(permutations_complete=False),
    dict(canonical_position_completeness=False),dict(conditional_lower_bound=math.inf),dict(groups={}),
    dict(conditional_infeasible=True,status=9)])
def test_invalid_scope_incomplete_domains_and_status_do_not_create_cuts(change):
    gp=pytest.importorskip('gurobipy');p=fixture(pool=2)
    groups={'S':[[('o1','d')],[('o1','p')],[('o2','d')],[('o2','p')]]}
    cert=dict(scope='M5_conditional_all_path_complete_task_groups_total_cost',groups=groups,
        permutations_complete=True,canonical_position_completeness=True,conditional_lower_bound=0,
        conditional_infeasible=False,status=2)
    model,context=build_joint_inventory(p,domains(p))
    try:
        with pytest.raises(ValueError):add_group_schedule_cut(p,model,context,{**cert,**change})
    finally:model.dispose()


@pytest.mark.parametrize('number',[3,4,6])
def test_other_business_models_cannot_accept_group_schedule_cuts(number):
    pytest.importorskip('gurobipy')
    from types import SimpleNamespace
    base=fixture(pool=2);p=SimpleNamespace(model_number=number,free_assignment=False,reusable=True,operating_policy=base.operating_policy)
    with pytest.raises(ValueError,match='multibatch M5'):
        add_group_schedule_cut(p,None,None,{})
