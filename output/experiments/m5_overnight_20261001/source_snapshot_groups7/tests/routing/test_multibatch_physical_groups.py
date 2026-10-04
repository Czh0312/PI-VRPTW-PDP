"""Whole-trip conditions retain all paths and give projection-safe no-goods."""
import pytest

from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.exact.multibatch_physical_site import (
    build_physical_site,physical_site_certificate,project_physical_site,normalize_trip_groups)
from models.routing.solvers.exact.multibatch_physical_cuts import add_physical_cut,project_physical_costs
from models.routing.solvers.exact.multibatch_joint_inventory import build_joint_inventory,set_joint_projection
from models.routing.solvers.exact.solver import solve_exact
from tests.routing.test_multibatch_departure_loads import nonmetric_problem
from tests.routing.test_multibatch_joint_inventory import domains
from tests.routing.test_multibatch_route_service import congestion_problem


def groups_for(initial,site):
    return [[(e['order_id'],e['kind']) for e in t['events']]
            for t in initial['e2_trips'] if t['satellite_id']==site]


@pytest.mark.parametrize('seed',range(6))
@pytest.mark.parametrize('compressed',[False,True])
def test_full_optimum_projects_with_free_group_paths_and_physical_vehicles(seed,compressed):
    gp=pytest.importorskip('gurobipy')
    problem=nonmetric_problem(seed)
    initial=solve_exact(problem,time_limit=5)
    assert initial['info']['proven_optimal'] and audit_solution(problem,initial)['valid']
    for site in {o.satellite for o in problem.orders}:
        groups=groups_for(initial,site)
        model,context=build_physical_site(problem,site,trip_groups=groups,compress_routes=compressed)
        try:
            projection=project_physical_site(problem,context,initial,fix=True)
            model.optimize()
            assert model.Status==gp.GRB.OPTIMAL
            assert model.ObjVal==pytest.approx(projection['original_site_cost'])
            assert len(context['group_assignments'])==len(groups)*len(context['e2'])
        finally:model.dispose()
        cert=physical_site_certificate(problem,site,time_limit=5,trip_groups=groups,
            initial_result=initial,compress_routes=compressed)
        assert cert['subproblem_optimal']
        assert cert['bound']<=projection['original_site_cost']+1e-6
        assert not cert['proven_optimal'] and not cert['proven_infeasible']


@pytest.mark.parametrize('timed',[False,True])
@pytest.mark.parametrize('seed',range(6))
def test_group_activation_projects_a_known_full_optimum_into_compressed_cost_epigraph(seed,timed):
    gp=pytest.importorskip('gurobipy')
    problem=nonmetric_problem(seed)
    initial=solve_exact(problem,time_limit=5)
    model,context=build_joint_inventory(problem,domains(problem,timed=timed))
    cuts=[]
    try:
        for site in context['routes']:
            cert=physical_site_certificate(problem,site,time_limit=5,trip_groups=groups_for(initial,site))
            cuts.append(add_physical_cut(problem,model,context,cert,name='group_'+site))
        set_joint_projection(problem,context,initial,fix=True)
        project_physical_costs(problem,context,initial,cuts=cuts,fix=True)
        model.optimize()
        assert model.Status==gp.GRB.OPTIMAL
        assert model.ObjVal<=initial['info']['objective']+1e-6
        assert all(cut['active'].X==1 for cut in cuts)
    finally:model.dispose()
    model,context=build_joint_inventory(problem,domains(problem,timed=timed))
    try:
        for site in context['routes']:
            cert=physical_site_certificate(problem,site,time_limit=5,trip_groups=groups_for(initial,site))
            add_physical_cut(problem,model,context,cert,name='group_'+site)
        model.optimize()
        assert model.Status==gp.GRB.OPTIMAL
        assert model.ObjBound<=initial['info']['objective']+1e-6
    finally:model.dispose()


@pytest.mark.parametrize('compressed',[False,True])
def test_independent_calendar_counterexample_yields_only_conditional_group_no_good(compressed):
    gp=pytest.importorskip('gurobipy')
    problem=congestion_problem()
    groups=[[('a','d'),('a','p')],[('b','d'),('b','p')]]
    # Two trips each occupy the sole vehicle from a delivery before 10 to a
    # pickup after 50. No ordering of either group or the two trips can work.
    cert=physical_site_certificate(problem,'S',time_limit=5,trip_groups=groups,
                                   compress_routes=compressed)
    assert cert['subproblem_infeasible'] and not cert['proven_infeasible']
    model,context=build_joint_inventory(problem,domains(problem,timed=True))
    try:
        model.optimize();assert model.ObjVal==pytest.approx(8)
        cut=add_physical_cut(problem,model,context,cert)
        model.optimize()
        assert model.Status==gp.GRB.OPTIMAL and model.ObjVal==pytest.approx(10)
        assert cut['active'].X==0
        initial=solve_exact(problem,time_limit=5)
        set_joint_projection(problem,context,initial,fix=True)
        project_physical_costs(problem,context,initial,cuts=[cut],fix=True)
        model.optimize();assert model.Status==gp.GRB.OPTIMAL
        assert model.ObjVal<=initial['info']['objective']+1e-6
    finally:model.dispose()


def test_partition_guards_and_nonmatching_full_projection():
    pytest.importorskip('gurobipy')
    problem=congestion_problem()
    for groups in ([],[[('a','d')]],[[('a','d'),('a','d')]],[[('bad','p')]]):
        with pytest.raises(ValueError,match='Trip groups'):
            normalize_trip_groups(problem,'S',groups)
    initial=solve_exact(problem,time_limit=5)
    groups=[[('a','d'),('a','p')],[('b','d'),('b','p')]]
    model,context=build_physical_site(problem,'S',trip_groups=groups)
    try:
        with pytest.raises(ValueError,match='conditional trip groups'):
            project_physical_site(problem,context,initial)
    finally:model.dispose()
