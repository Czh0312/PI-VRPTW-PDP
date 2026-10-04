"""Integer activation boundaries, cost epigraphs, and full M5 projections."""
import math

import pytest

from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.exact.multibatch_joint_inventory import build_joint_inventory, set_joint_projection
from models.routing.solvers.exact.multibatch_physical_cuts import (
    add_physical_cut, lift_physical_costs, project_physical_costs)
from models.routing.solvers.exact.multibatch_physical_site import physical_site_certificate
from models.routing.solvers.exact.solver import solve_exact
from tests.routing.test_multibatch_departure_loads import nonmetric_problem
from tests.routing.test_multibatch_joint_inventory import domains
from tests.routing.test_multibatch_route_service import congestion_problem


@pytest.mark.parametrize('supply', range(4))
@pytest.mark.parametrize('early', range(3))
@pytest.mark.parametrize('maximum', range(3))
@pytest.mark.parametrize('infeasible', [False, True])
def test_cost_floor_and_no_good_activate_iff_all_integer_conditions_hold(supply, early, maximum, infeasible):
    gp = pytest.importorskip('gurobipy')
    problem = congestion_problem()
    model = gp.Model()
    model.Params.OutputFlag = 0
    model.Params.DualReductions = 0
    supply_var = model.addVar(vtype=gp.GRB.INTEGER, ub=3)
    model.addConstr(supply_var == supply)
    route = model.addVar(lb=1, ub=1, obj=4)
    allocation = {}
    for index, order in enumerate(problem.orders):
        for batch in problem.operating_policy.batches:
            value = int((index < early and batch.id == 'b1') or
                        (index >= early and batch.id == problem.operating_policy.batches[-1].id))
            allocation[order.id, batch.id] = model.addVar(lb=value, ub=value, vtype=gp.GRB.BINARY)
    model.update()
    context = dict(e1=dict(supplies={('S','b1'):supply_var}, allocation=allocation),
        routes={'S':dict(rows=[dict(column=dict(cost=4))], choices={(0,None):route})})
    cert = dict(satellite='S', bound=6, supply_thresholds={'b1':2}, minimum_returns={'b1':1},
        maximum_returns={'b1':maximum},
        scope='M5_conditional_physical_E2_site_cost_relaxation',
        subproblem_infeasible=infeasible, status=gp.GRB.INFEASIBLE if infeasible else gp.GRB.OPTIMAL)
    try:
        cut = add_physical_cut(problem, model, context, cert, base_floor=4)
        model.optimize()
        enabled = supply >= 2 and 1 <= early <= maximum
        if infeasible and enabled:
            assert model.Status == gp.GRB.INFEASIBLE
        else:
            assert model.Status == gp.GRB.OPTIMAL
            assert model.ObjVal == pytest.approx(6 if enabled and not infeasible else 4)
            assert cut['active'].X == int(enabled)
            assert route.X == 1  # compressed cost stays 4, theta may rise to 6
        assert cut['cost_target'] == 'actual_site_cost_epigraph'
        assert not cut['proven_optimal'] and not cut['proven_infeasible']
        assert lift_physical_costs(model, context) is context['physical_costs']
    finally:
        model.dispose()


@pytest.mark.parametrize('seed', range(6))
@pytest.mark.parametrize('timed', [False, True])
def test_nonmetric_full_optimum_projects_with_actual_cost_and_conditional_flags(seed, timed):
    gp = pytest.importorskip('gurobipy')
    problem = nonmetric_problem(seed)
    initial = solve_exact(problem, time_limit=5)
    assert initial['info']['proven_optimal'] and audit_solution(problem, initial)['valid']
    model, context = build_joint_inventory(problem, domains(problem, timed=timed))
    cuts = []
    try:
        for site in context['routes']:
            thresholds = {t['batch_id']:e['start']+problem.service(site)
                for t in initial['e1_trips'] for e in t['events'] if e['kind']=='d' and e['site_id']==site}
            counts = {b.id:sum(e['quantity'] for t in initial['e2_trips'] if t['satellite_id']==site
                and t['return_complete']<=b.cutoff for e in t['events'] if e['kind']=='p')
                for b in problem.operating_policy.batches}
            cert = physical_site_certificate(problem, site, time_limit=5,
                supply_thresholds=thresholds, minimum_returns=counts, maximum_returns=counts,
                initial_result=initial)
            assert cert['subproblem_optimal']
            cuts.append(add_physical_cut(problem, model, context, cert, name=f'cut_{site}'))
        set_joint_projection(problem, context, initial, fix=True)
        projection = project_physical_costs(problem, context, initial, cuts=cuts, fix=True)
        model.optimize()
        assert model.Status == gp.GRB.OPTIMAL
        assert model.ObjVal <= initial['info']['objective']+1e-6
        for site in context['routes']:
            for batch in problem.operating_policy.batches:
                projected = sum(o.count*context['e1']['allocation'][o.id, b.id].X
                    for o in problem.orders if o.satellite == site
                    for b in problem.operating_policy.batches
                    if problem.batch_index[b.id] <= problem.batch_index[batch.id])
                actual = sum(e['quantity'] for t in initial['e2_trips'] if t['satellite_id'] == site
                    and t['return_complete'] <= batch.cutoff for e in t['events'] if e['kind'] == 'p')
                assert projected == pytest.approx(actual, abs=1e-8)
        assert sum(projection['actual_site_costs'].values()) == pytest.approx(initial['audit']['distances']['e2'])
    finally:
        model.dispose()
    # A fresh un-fixed relaxation also remains below the independently solved
    # full optimum, even when the oracle floor raises a compressed objective.
    model, context = build_joint_inventory(problem, domains(problem, timed=timed))
    try:
        for site in context['routes']:
            cert = physical_site_certificate(problem, site, time_limit=5)
            add_physical_cut(problem, model, context, cert, name=f'cut_{site}')
        model.optimize()
        assert model.Status == gp.GRB.OPTIMAL
        assert model.ObjBound <= initial['info']['objective']+1e-6
    finally:
        model.dispose()


def test_physical_oracle_floor_closes_one_vehicle_waiting_gap():
    gp = pytest.importorskip('gurobipy')
    problem = congestion_problem()
    model, context = build_joint_inventory(problem, domains(problem, timed=True))
    try:
        model.optimize()
        assert model.ObjVal == pytest.approx(8)
        cert = physical_site_certificate(problem, 'S', time_limit=5)
        cut = add_physical_cut(problem, model, context, cert, base_floor=4)
        model.optimize()
        assert model.Status == gp.GRB.OPTIMAL
        assert 10-1e-6 <= model.ObjBound <= 10
        assert isinstance(cut['active'], int) and cut['active'] == 1
        assert not cut['proven_optimal']
    finally:
        model.dispose()


def test_invalid_conditions_scope_and_floors_cannot_create_certified_cuts():
    pytest.importorskip('gurobipy')
    problem = congestion_problem()
    cert = physical_site_certificate(problem, 'S', time_limit=5)
    cases = [dict(scope='restricted_route_pool'), dict(satellite='bad'), dict(bound=math.inf),
             dict(supply_thresholds={'b1':True}), dict(supply_thresholds={'bad':0}),
             dict(minimum_returns={'b1':3}), dict(maximum_returns={'b1':3}),
             dict(maximum_returns={'b1':False}), dict(subproblem_infeasible=True, status=9)]
    for changes in cases:
        model, context = build_joint_inventory(problem, domains(problem, timed=True))
        try:
            with pytest.raises(ValueError):
                add_physical_cut(problem, model, context, {**cert, **changes})
        finally:
            model.dispose()
