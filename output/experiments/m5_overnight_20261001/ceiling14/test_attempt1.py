"""A cost ceiling must yield a bound, never a false conditional no-good."""
import pytest
from models.routing.solvers.exact.multibatch_physical_ceiling import physical_ceiling_certificate
from models.routing.solvers.exact.multibatch_physical_site import physical_site_certificate
from models.routing.solvers.exact.solver import solve_exact
from tests.routing.test_multibatch_route_service import congestion_problem
from tests.routing.test_multibatch_departure_loads import nonmetric_problem


@pytest.mark.parametrize('ceiling', [5, 5.5, 6, 7])
def test_independent_three_trip_optimum_six_survives_ceiling_inference(ceiling):
    pytest.importorskip('gurobipy')
    problem = congestion_problem()
    groups = [[('a', 'd'), ('a', 'p')]]
    # Unit capacity requires three trips (two orders, b split around long a);
    # each costs2. The known calendar construction has total6.
    original = physical_site_certificate(problem, 'S', time_limit=5,
        trip_groups=groups, partial_groups=True)
    assert original['subproblem_optimal'] and original['subproblem_objective']==pytest.approx(6)
    result = physical_ceiling_certificate(problem, 'S', ceiling, time_limit=5,
        trip_groups=groups, partial_groups=True)
    assert result['ceiling_model_infeasible'] == (ceiling < 6)
    assert result['bound'] <= 6
    assert result['bound'] == pytest.approx(min(ceiling, 6), abs=1e-5)
    assert not result['subproblem_infeasible']
    assert not result['proven_infeasible'] and not result['proven_optimal']


@pytest.mark.parametrize('seed', range(6))
def test_known_complete_optimum_projects_under_loose_site_ceiling(seed):
    pytest.importorskip('gurobipy')
    problem = nonmetric_problem(seed)
    initial = solve_exact(problem, time_limit=5)
    assert initial['info']['proven_optimal']
    for site in {o.satellite for o in problem.orders}:
        trips = [t for t in initial['e2_trips'] if t['satellite_id']==site]
        cost = sum(t['distance'] for t in trips)
        groups = [[(e['order_id'], e['kind']) for e in t['events']] for t in trips]
        result = physical_ceiling_certificate(problem, site, cost+1, time_limit=5,
            trip_groups=groups, initial_result=initial)
        assert not result['ceiling_model_infeasible'] and result['bound']<=cost+1e-6


@pytest.mark.parametrize('ceiling,floor', [(float('inf'), 0), (float('nan'), 0), (-1, 0), (5, 6)])
def test_invalid_ceiling_and_floor_rejected(ceiling, floor):
    with pytest.raises(ValueError):
        physical_ceiling_certificate(congestion_problem(), 'S', ceiling, fallback_bound=floor)
