"""Full-schedule projections and genuine one-vehicle congestion lower bounds."""
import math

import pytest

from models.common.cycle.audit import audit_solution
from models.routing.model_5.exact.accelerators.multibatch.physical_site import build_physical_site, physical_site_certificate, project_physical_site
from models.routing.model_5.exact.search.engine import solve_exact
from tests.routing.test_multibatch_departure_loads import nonmetric_problem
from tests.routing.test_multibatch_route_service import congestion_problem


@pytest.mark.parametrize('seed', range(6))
@pytest.mark.parametrize('compressed', [False, True])
def test_original_full_schedule_projects_with_supply_and_early_return_conditions(seed, compressed):
    gp = pytest.importorskip('gurobipy')
    problem = nonmetric_problem(seed)
    initial = solve_exact(problem, time_limit=5)
    assert initial['info']['proven_optimal'] and audit_solution(problem, initial)['valid']
    for site in {o.satellite for o in problem.orders}:
        supplies = {t['batch_id']: e['start']+problem.service(site)
            for t in initial['e1_trips'] for e in t['events'] if e['kind'] == 'd' and e['site_id'] == site}
        returned = {e['order_id']: t['return_complete'] for t in initial['e2_trips'] if t['satellite_id'] == site
                    for e in t['events'] if e['kind'] == 'p'}
        counts = {b.id: sum(o.count for o in problem.orders if o.satellite == site and returned[o.id] <= b.cutoff)
                  for b in problem.operating_policy.batches}
        model, context = build_physical_site(problem, site, supply_thresholds=supplies,
            minimum_returns=counts, maximum_returns=counts, compress_routes=compressed)
        try:
            assert context['local_problem'].network is problem.network
            projected = project_physical_site(problem, context, initial, fix=True)
            model.optimize()
            assert model.Status == gp.GRB.OPTIMAL
            assert model.ObjVal == pytest.approx(projected['original_site_cost'], abs=1e-6)
            assert context['projection_proof']['local_relaxed_pool'] >= sum(o.count for o in context['local_problem'].orders)
        finally: model.dispose()
        cert = physical_site_certificate(problem, site, time_limit=3, supply_thresholds=supplies,
            minimum_returns=counts, maximum_returns=counts, initial_result=initial, compress_routes=compressed)
        assert cert['bound'] <= projected['original_site_cost']+1e-6
        assert not cert['proven_optimal'] and not cert['proven_infeasible']


@pytest.mark.parametrize('compressed', [False, True])
def test_physical_site_bound_closes_independent_one_vehicle_waiting_counterexample(compressed):
    pytest.importorskip('gurobipy')
    problem = congestion_problem()
    # Each delivery has a separate unit-volume trip, both before time 10.
    # Both pickups occur after time 50. If each delivery shares its pickup,
    # the one vehicle cannot complete the second delivery before 10.
    # Therefore at least 3 nonempty trips, each cost 2: bound 6 is attained.
    cert = physical_site_certificate(problem, 'S', time_limit=5, compress_routes=compressed)
    assert cert['subproblem_optimal'] and cert['subproblem_objective'] == pytest.approx(6)
    assert 6-1e-6 <= cert['bound'] <= 6
    assert not cert['proven_optimal'] and not cert['proven_infeasible']
    full = solve_exact(problem, time_limit=5)
    assert full['info']['objective'] == pytest.approx(10)


def test_impossible_conditional_returns_only_prove_subproblem_infeasibility():
    pytest.importorskip('gurobipy')
    problem = congestion_problem()
    cert = physical_site_certificate(problem, 'S', time_limit=5, minimum_returns={'b1': 1})
    assert cert['subproblem_infeasible'] and not cert['proven_infeasible']
    assert cert['bound'] == 0 and cert['raw_bound'] is None


@pytest.mark.parametrize('compressed', [False, True])
def test_certified_independent_trip_floor_preserves_the_full_projection(compressed):
    gp = pytest.importorskip('gurobipy')
    problem = congestion_problem()
    initial = solve_exact(problem, time_limit=5)
    # Unit capacity needs at least two trips, each of cost 2. The certified
    # independent cover floor 4 is weaker than the physical-calendar floor 6.
    model, context = build_physical_site(problem, 'S', cost_floor=4,
                                         compress_routes=compressed)
    try:
        row = model.getConstrByName('physical_certified_cost_floor')
        assert row.Sense == '>' and row.RHS == 4
        projection = project_physical_site(problem, context, initial, fix=True)
        model.optimize()
        assert model.Status == gp.GRB.OPTIMAL
        assert model.ObjVal == pytest.approx(projection['original_site_cost'])
    finally:
        model.dispose()
    cert = physical_site_certificate(problem, 'S', time_limit=5, fallback_bound=4,
                                     compress_routes=compressed)
    assert cert['projection_proof']['externally_certified_cost_floor'] == 4
    assert cert['subproblem_optimal'] and 6-1e-6 <= cert['bound'] <= 6
    assert not cert['proven_optimal']


def test_condition_guard_and_projection_reject_nonmatching_full_schedule():
    pytest.importorskip('gurobipy')
    problem = congestion_problem()
    for kwargs in [dict(supply_thresholds={'bad': 0}), dict(supply_thresholds={'b1': True}),
                   dict(minimum_returns={'b1': 3}), dict(minimum_returns={'b1': -1}),
                   dict(maximum_returns={'b1': True}), dict(maximum_returns={'b1': 3})]:
        with pytest.raises(ValueError, match='Conditions'):
            build_physical_site(problem, 'S', **kwargs)
    with pytest.raises(ValueError, match='active'): build_physical_site(problem, 'bad')
    initial = solve_exact(problem, time_limit=5)
    model, context = build_physical_site(problem, 'S', minimum_returns={'b1': 1})
    try:
        with pytest.raises(ValueError, match='minimum early returns'):
            project_physical_site(problem, context, initial)
    finally: model.dispose()
    for budget in (0, -1, math.inf):
        with pytest.raises(ValueError, match='time_limit'):
            physical_site_certificate(problem, 'S', time_limit=budget)
    for floor in (-1, math.inf, math.nan):
        with pytest.raises(ValueError, match='cost_floor'):
            build_physical_site(problem, 'S', cost_floor=floor)
        with pytest.raises(ValueError, match='fallback_bound'):
            physical_site_certificate(problem, 'S', fallback_bound=floor)


@pytest.mark.parametrize('returned', [29, 30, 31])
def test_maximum_count_needs_reverse_classification_at_the_integer_cutoff(returned):
    gp = pytest.importorskip('gurobipy')
    from tests.routing.test_multibatch_cycle import fixture
    problem = fixture(pool=2)
    model, context = build_physical_site(problem, 'S', maximum_returns={'b1':0})
    try:
        variable = context['pickup_returns']['o1']
        variable.LB = variable.UB = returned
        model.Params.DualReductions = 0
        model.optimize()
        assert model.Status == (gp.GRB.INFEASIBLE if returned <= 30 else gp.GRB.OPTIMAL)
        if returned <= 30:
            # Deliberately restore the old one-way mistake: both flags can be
            # zero even though o1 actually returns before/at the cutoff.
            model.remove(next(row for row in model.getGenConstrs()
                              if row.GenConstrName == 'physical_early_no_o1_b1'))
            model.optimize()
            assert model.Status == gp.GRB.OPTIMAL
            assert context['return_flags']['o1','b1'].X == 0
    finally:
        model.dispose()


def test_maximum_projection_rejects_too_many_actual_early_returns():
    pytest.importorskip('gurobipy')
    from tests.routing.test_multibatch_cycle import fixture, hand_solution
    problem = fixture(pool=2)
    model, context = build_physical_site(problem, 'S', maximum_returns={'b1':0})
    try:
        with pytest.raises(ValueError, match='maximum early returns'):
            project_physical_site(problem, context, hand_solution(problem, 30))
        project_physical_site(problem, context, hand_solution(problem, 31), fix=True)
        model.optimize()
        assert model.SolCount
    finally:
        model.dispose()
