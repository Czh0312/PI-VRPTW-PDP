"""Independent optima, projection completeness and causal counterexamples."""
from copy import deepcopy
import math
import pytest
from models.routing.model_5.exact.multibatch_cover_schedule import build_cover_schedule, conditional_cover_schedule
from models.routing.model_5.exact.multibatch_cover_pool import preserve_cover_routes
from models.common.cycle.audit import audit_solution
from tests.routing.test_multibatch_cycle import fixture,hand_solution
from tests.routing.test_multibatch_route_pool import three_slot_case
from tests.routing.test_multibatch_departure_loads import nonmetric_problem
from tests.routing.test_multibatch_primal_quality import quality_fixture


def covers_of(result):
    covers={}
    for trip in result['e2_trips']:
        covers.setdefault(trip['satellite_id'],[]).append([(e['order_id'],e['kind']) for e in trip['events']])
    return covers


@pytest.mark.parametrize('seed',range(6))
def test_fixed_optimal_nonmetric_paths_attain_independent_complete_optimum(seed):
    pytest.importorskip('gurobipy')
    from models.routing.model_5.exact.engine import solve_exact
    p=nonmetric_problem(seed);opt=solve_exact(p,time_limit=5);assert opt['info']['proven_optimal']
    covers=covers_of(opt);copied=deepcopy(covers)
    trial=conditional_cover_schedule(p,covers,time_limit=3)
    assert covers==copied and trial['certificate']['conditional_optimal']
    assert audit_solution(p,trial['result'])['valid']
    assert trial['result']['info']['objective']==pytest.approx(opt['info']['objective'])
    assert trial['certificate']['global_lower_bound'] is None
    assert not trial['certificate']['full_proven_optimal']
    assert not trial['result']['info']['proven_optimal']


def test_independently_optimal_quality_cover_and_slot_count():
    pytest.importorskip('gurobipy')
    from models.routing.model_5.exact.engine import solve_exact
    p=quality_fixture();opt=solve_exact(p,time_limit=5);assert opt['info']['proven_optimal']
    trial=conditional_cover_schedule(p,covers_of(opt),time_limit=3)
    assert trial['certificate']['conditional_optimal']
    assert trial['certificate']['audited_full_upper']==pytest.approx(4)
    assert trial['certificate']['conditional_lower_bound']<4
    assert trial['certificate']['slots']==sum(min(len(c),p.operating_policy.e2_max_trips_per_vehicle)*p.fleet.e2_per_sat_count for c in covers_of(opt).values())


def test_all_positions_still_reject_zero_time_cross_trip_cycle():
    pytest.importorskip('gurobipy')
    p,_=three_slot_case(zero=True)
    cover={'S':[[('extra','p'),('o1','d')],[('o1','p'),('extra','d')],[('o2','d'),('o2','p')]]}
    trial=conditional_cover_schedule(p,cover,time_limit=3)
    assert trial['certificate']['conditional_infeasible'] and trial['result'] is None
    assert not trial['certificate']['full_proven_infeasible']


def test_original_positive_E1_turnaround_is_indexed():
    pytest.importorskip('gurobipy')
    p=fixture(pool=2,e1_turnaround_time=2);initial=hand_solution(p)
    trial=conditional_cover_schedule(p,covers_of(initial),time_limit=3)
    assert trial['certificate']['backend']=='indexed'
    assert trial['certificate']['conditional_optimal'] and trial['result']['audit']['valid']


@pytest.mark.parametrize('bad',[{}, {'unknown':[]}, {'S':[]}, {'S':[[('o1','d')]]}])
def test_missing_active_sites_and_tasks_rejected(bad):
    with pytest.raises(ValueError):build_cover_schedule(fixture(pool=2),bad)


@pytest.mark.parametrize('budget',[0,-1,math.inf,math.nan])
def test_bad_budget_rejected(budget):
    with pytest.raises(ValueError):conditional_cover_schedule(fixture(pool=2),{},time_limit=budget)
