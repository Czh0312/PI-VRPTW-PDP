"""Minute calendar decisions retain the second-resolution physical instance."""
from copy import deepcopy
from dataclasses import replace
import random

import pytest

from models.common.cycle.audit import audit_solution
from models.common.cycle.calendar import CalendarCycleInstance
from models.routing.model_8.alns.calendar_moves import move_boundaries
from models.routing.model_8.alns.construction import initial_calendars, _initial_cutoff_profiles
from models.routing.model_8.exact import solve_exact
from models.routing.model_8.alns import solve_alns
from tests.routing.test_productive_batches import productive_fixture
from tools.configuration.loader import load_routing_case
from models.common.cycle import load_instance


def gridded(count=2):
    source = productive_fixture(8, count=count)
    # A 7-second service duration must stay 7 seconds, not become one minute.
    policy = replace(source.operating_policy, time_tick_seconds=1,
        cycle_end=7920, cleaning_duration=300,
        batches=tuple(replace(b, start=b.start*60, cutoff=b.cutoff*60, end=b.end*60)
                      for b in source.operating_policy.batches))
    orders = [replace(o, delivery_window=(0,6600), pickup_window=(0,7500),
                      service_time=7, min_use_time=13) for o in source.orders]
    return CalendarCycleInstance(source.network, orders, policy,
        dict(batch_count_candidates=[count], calendar_decision_grid_seconds=60),
        source.cost_profile.config, model=8)


def test_grid_is_strict_in_replay_but_proposals_are_quantized():
    p = gridded()
    assert p.calendar_decision_step == 60
    assert p.for_count(2).orders[0].service_time == 7
    with pytest.raises(ValueError, match='decision grid'):
        p.validated_boundaries(2, [0,3961,7920])
    assert p.proposed_boundaries(2, [0,3961,7920]) == (0,3960,7920)
    child = p.fixed_calendar(2)
    bad = {**child.default_cutoffs, 'b1': child.default_cutoffs['b1']+1}
    with pytest.raises(ValueError, match='decision grid'):
        child.validated_cutoffs(bad)
    assert all(v % 60 == 0 for v in child.proposed_cutoffs(bad).values())
    for bounds in initial_calendars(p.for_count(2)):
        assert all(t % 60 == 0 for t in bounds)
    for operator in ('align_boundary','shift_boundary','redistribute_lengths'):
        bounds = p.balanced_boundaries(2)
        for seed in range(20):
            bounds = move_boundaries(p.for_count(2),bounds,random.Random(seed),operator)
            assert all(t % 60 == 0 for t in bounds)
    for cuts in _initial_cutoff_profiles(child):
        assert all(t % 60 == 0 for t in cuts.values())


@pytest.mark.parametrize('count',[1,2])
def test_exact_and_alns_obey_same_grid_and_independent_two_cycle_audit(count):
    p = gridded(count)
    exact = solve_exact(p,time_limit=15)
    alns = solve_alns(p,max_time=.4,max_iterations=1000,seed=42)
    assert exact['info']['proven_optimal'], exact['info']
    for result in (exact,alns):
        audit = audit_solution(p,result)
        assert audit['valid'] and audit['periodic_replay']['valid'], audit
        assert all(t % 60 == 0 for t in result['batch_boundaries'])
        assert all(t % 60 == 0 for t in result['batch_cutoffs'].values())
        assert result['calendar_config']['calendar_decision_grid_seconds'] == 60
    assert alns['info']['objective'] >= exact['info']['objective']-1e-6


@pytest.mark.parametrize('size,case',[('small','S01'),('medium','M01'),('large','L01')])
def test_real_cases_keep_physical_times_and_accept_all_four_counts(size,case):
    config,fleet = load_routing_case(
        f'data/formal_benchmark27_uniform_7_20_50_v1/model_8/{size}/{case}/case.json',
        use_pool_config=False)
    original = load_instance(config,fleet)
    config = deepcopy(config)
    config['joint_batch_options'].update(batch_count_candidates=[1,2,3,4],
                                         calendar_decision_grid_seconds=60)
    p = load_instance(config,fleet)
    assert p.orders == original.orders
    assert p.operating_policy == original.operating_policy
    for count in range(1,5):
        child = p.for_count(count)
        assert all(t % child.calendar_decision_step == 0
                   for t in p.balanced_boundaries(count))
        fixed = p.fixed_calendar(count)
        assert all(t % fixed.calendar_decision_step == 0 for t in fixed.default_cutoffs.values())


def test_default_domain_is_unchanged_and_grid_cannot_masquerade_as_m7():
    p = productive_fixture(8)
    assert p.calendar_decision_grid_seconds is None
    assert 'calendar_decision_grid_seconds' not in p.calendar_contract
    assert p.validated_boundaries(2,[0,65,132]) == (0,65,132)
    with pytest.raises(ValueError, match='only by M8'):
        CalendarCycleInstance(p.network,p.orders,p.operating_policy,
            dict(batch_count_candidates=[2],calendar_decision_grid_seconds=60),
            p.cost_profile.config,model=7)


def test_milp_rejects_an_otherwise_feasible_time_between_minutes():
    from models.routing.model_8.exact.model import build_model
    p = gridded(1)
    plain = CalendarCycleInstance(p.network,p.orders,p.operating_policy,
        dict(batch_count_candidates=[1]),p.cost_profile.config,model=8)
    statuses = []
    for problem in (plain,p):
        model,ctx = build_model(problem,batch_count=1,compact_e2=True)
        try:
            model.Params.OutputFlag = 0
            model.Params.TimeLimit = 10
            model.addConstr(ctx['cutoffs']['b1']==61)
            model.optimize()
            statuses.append(model.Status)
        finally:
            model.dispose()
    assert statuses==[2,3]  # Feasible without grid; certified infeasible on minute grid.
