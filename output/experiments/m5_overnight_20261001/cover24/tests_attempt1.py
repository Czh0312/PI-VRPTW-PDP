"""Cover retention with independent full optima and original cycle audits."""
from copy import deepcopy
import math
import pytest

from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.exact.multibatch_cover_pool import preserve_cover_routes, reoptimize_cover_pool
from models.routing.solvers.exact.multibatch_route_pool import candidate_pools
from tests.routing.test_multibatch_cycle import fixture, hand_solution
from tests.routing.test_multibatch_departure_loads import nonmetric_problem
from tests.routing.test_multibatch_primal_quality import quality_fixture


def singletons(problem, site):
    return [{'tasks': [(o.id, kind)], 'cost': -1e20, 'return_complete': -100}
            for o in problem.orders if o.satellite == site for kind in ('d', 'p')]


def cover_from_result(result):
    covers = {}
    for trip in result['e2_trips']:
        covers.setdefault(trip['satellite_id'], []).append(
            [(e['order_id'], e['kind']) for e in trip['events']])
    return covers


def test_complete_cover_is_appended_beyond_cap_without_mutation_or_cached_costs():
    p = fixture(pool=2)
    initial = hand_solution(p)
    pools = candidate_pools(p, initial, max_columns=1)
    original = deepcopy((initial, pools))
    pinned, proof = preserve_cover_routes(p, initial, pools, {'S': singletons(p, 'S')})
    assert (initial, pools) == original
    assert proof['added_columns'] > 0 and proof['original_columns_preserved']
    assert not proof['full_feasible'] and not proof['full_proven_optimal']
    for old, new in zip(pools, pinned):
        assert new[:len(old)] == old
        for column in new[len(old):]:
            places = ['S']+[p.order_map[k[0]].customer for k in column['tasks']]+['S']
            expected = math.fsum(p.distance(a, b) for a, b in zip(places, places[1:]))
            assert column['cost'] == expected and column['return_complete'] >= 0


@pytest.mark.parametrize('seed', range(6))
def test_nonmetric_complete_optimum_is_preserved_in_actual_pool_master(seed):
    pytest.importorskip('gurobipy')
    from models.routing.solvers.exact.solver import solve_exact
    p = nonmetric_problem(seed)
    initial = solve_exact(p, time_limit=5)
    assert initial['info']['proven_optimal']
    copied = deepcopy(initial)
    covers = {site: singletons(p, site) for site in {o.satellite for o in p.orders}}
    result = reoptimize_cover_pool(p, initial, covers, time_limit=2, max_columns=1,
        transfers=False, exchanges=False, recombinations=False)
    assert initial == copied and audit_solution(p, result)['valid']
    assert result['info']['objective'] == pytest.approx(initial['info']['objective'])
    assert result['info']['cover_preservation']['added_columns'] > 0
    assert result['info']['best_bound'] is None and result['info']['mip_gap'] is None
    assert not result['info']['proven_optimal'] and not result['info']['proven_infeasible']
    assert not result['info']['route_pool_complete']


def test_pin_independent_optimal_cover_improves_a_capped_bad_initial():
    pytest.importorskip('gurobipy')
    from models.routing.solvers.exact.multibatch_primal import construct_multibatch_incumbent
    from models.routing.solvers.exact.solver import solve_exact
    import time
    p = quality_fixture()
    initial = construct_multibatch_incumbent(p, time.perf_counter()+2, improve=False)
    optimum = solve_exact(p, time_limit=5)
    assert optimum['info']['proven_optimal'] and optimum['info']['objective'] == 4
    result = reoptimize_cover_pool(p, initial, cover_from_result(optimum), time_limit=2,
        max_columns=1, transfers=False, exchanges=False, recombinations=False,
        additional_columns_per_slot=1)
    assert initial['info']['objective'] == 6
    assert result['info']['objective'] == 4 and result['audit']['valid']
    assert result['info']['cover_preservation']['added_columns'] > 0
    assert not result['info']['proven_optimal']


@pytest.mark.parametrize('bad', [
    {}, {'S': []}, {'unknown': [[('o1', 'd')]]},
    {'S': [[('o1', 'd')]]},
    {'S': [[('o1', 'd'), ('o1', 'd')], [('o1', 'p'), ('o2', 'd'), ('o2', 'p')]]},
    {'S': [[('o1', 'd'), ('o2', 'd')], [('o1', 'p'), ('o2', 'p')]]},
])
def test_cover_guards(bad):
    p = fixture(pool=2)
    initial = hand_solution(p)
    pools = candidate_pools(p, initial, max_columns=1)
    if bad == {}:
        result, info = preserve_cover_routes(p, initial, pools, bad)
        assert result == pools and info['added_columns'] == 0
    else:
        with pytest.raises(ValueError):
            preserve_cover_routes(p, initial, pools, bad)


def test_deadline_and_invalid_parameters_preserve_full_feasible_initial():
    p = fixture(pool=2)
    initial = hand_solution(p)
    result = reoptimize_cover_pool(p, initial, {'S': singletons(p, 'S')}, time_limit=1e-12)
    assert result['audit']['valid'] and result['info']['route_pool_status'] == 'TIME_LIMIT_BUILD'
    for value in (0, -1, math.inf, math.nan):
        with pytest.raises(ValueError):
            reoptimize_cover_pool(p, initial, {}, time_limit=value)
    for value in (0, True, 1.5):
        with pytest.raises(ValueError):
            reoptimize_cover_pool(p, initial, {}, max_columns=value)
