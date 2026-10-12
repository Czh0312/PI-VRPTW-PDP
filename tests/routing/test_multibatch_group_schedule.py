"""Complete optimal projection, permutation counterexample, budget boundary."""
import math,time
import pytest
from models.routing.model_5.exact.multibatch_group_schedule import conditional_group_schedule, enumerate_group_paths
from models.routing.model_5.exact.multibatch_cover_schedule import conditional_cover_schedule
from models.common.cycle.audit import audit_solution
from tests.routing.test_multibatch_cover_schedule import covers_of
from tests.routing.test_multibatch_route_pool import three_slot_case
from tests.routing.test_multibatch_departure_loads import nonmetric_problem
from tests.routing.test_multibatch_primal_quality import quality_fixture
from tests.routing.test_multibatch_cycle import fixture,hand_solution


@pytest.mark.parametrize('seed',range(6))
def test_all_group_permutations_project_independently_optimal_nonmetric_model(seed):
    pytest.importorskip('gurobipy')
    from models.routing.model_5.exact.engine import solve_exact
    p=nonmetric_problem(seed);opt=solve_exact(p,time_limit=5);assert opt['info']['proven_optimal']
    groups=covers_of(opt);trial=conditional_group_schedule(p,groups,time_limit=3)
    assert trial['certificate']['conditional_optimal'] and trial['certificate']['permutations_complete']
    assert trial['certificate']['permutations_checked']==sum(math.factorial(len(g)) for rows in groups.values() for g in rows)
    assert trial['result']['info']['objective']==pytest.approx(opt['info']['objective'])
    assert audit_solution(p,trial['result'])['valid']
    assert not trial['certificate']['full_proven_optimal'] and trial['certificate']['global_lower_bound'] is None


def test_fixed_order_cycle_cannot_be_lifted_to_unordered_group_no_good():
    pytest.importorskip('gurobipy')
    p,_=three_slot_case(zero=True)
    p.fleet.e2_per_sat_count=2
    cyclic={'S':[[('extra','p'),('o1','d')],[('o1','p'),('extra','d')],[('o2','d'),('o2','p')]]}
    assert conditional_cover_schedule(p,cyclic,time_limit=3)['certificate']['conditional_infeasible']
    trial=conditional_group_schedule(p,cyclic,time_limit=3)
    assert trial['certificate']['conditional_optimal'] and not trial['certificate']['conditional_infeasible']
    assert trial['result']['audit']['valid']


def test_independent_quality_optimum_and_cached_fields_irrelevant():
    pytest.importorskip('gurobipy')
    from models.routing.model_5.exact.engine import solve_exact
    p=quality_fixture();opt=solve_exact(p,time_limit=5);assert opt['info']['proven_optimal']
    trial=conditional_group_schedule(p,covers_of(opt),time_limit=3)
    assert trial['result']['info']['objective']==pytest.approx(4)
    assert trial['certificate']['cost_dominated_paths_retained']


def test_partial_permutation_domain_does_not_prove_infeasibility_or_bound():
    p=quality_fixture();groups={'S':[[('A','d'),('B','d'),('A','p'),('B','p')]]}
    trial=conditional_group_schedule(p,groups,time_limit=1e-12)
    assert not trial['certificate']['permutations_complete']
    assert not trial['certificate']['conditional_infeasible'] and trial['certificate']['conditional_lower_bound']==0
    assert trial['result'] is None


def test_original_positive_E1_turnaround_and_vehicle_rules():
    pytest.importorskip('gurobipy')
    p=fixture(pool=2,e1_turnaround_time=2);trial=conditional_group_schedule(p,covers_of(hand_solution(p)),time_limit=3)
    assert trial['certificate']['backend']=='indexed' and trial['result']['audit']['valid']


@pytest.mark.parametrize('bad',[{}, {'unknown':[]}, {'S':[]}, {'S':[[('o1','d')]]}])
def test_bad_groups_are_not_certificates(bad):
    with pytest.raises(ValueError):enumerate_group_paths(fixture(pool=2),bad)
