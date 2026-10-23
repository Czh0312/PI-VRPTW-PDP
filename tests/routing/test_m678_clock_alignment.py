"""Same-K physical domains and cross-model witnesses on the refined grid."""
from copy import deepcopy
from dataclasses import replace
import json
import random

import pytest

from models.common.cycle.audit import audit_solution
from models.common.cycle.calendar import CalendarCycleInstance, attach_calendar_decisions
from models.common.cycle.joint import lift_result
from models.common.cycle.fixed_pool import FixedBatchPoolInstance, attach_pool_cost
from models.common.cycle.uniform import UniformCycleInstance, attach_uniform_decisions
from tests.routing.test_productive_batches import productive_fixture, explicit_plan


def family(number, *, origin=0, subdivision=1, minimum=1, releases=(0, 0, 0)):
    source = productive_fixture(8, count=3, orders=3)
    policy = replace(source.operating_policy, cycle_start=origin, cycle_end=origin+130,
        time_tick_subdivision=subdivision,
        batches=tuple(replace(b, start=origin+60*i, cutoff=origin+60*i+30, end=origin+60*(i+1))
                      for i, b in enumerate(source.operating_policy.batches)))
    orders = [replace(o, delivery_window=(origin, origin+110), pickup_window=(origin, origin+125),
                      release_time=origin+r) for o, r in zip(source.orders, releases)]
    options = dict(batch_count_candidates=[3], min_phase_minutes=minimum)
    if number == 6:
        return UniformCycleInstance(source.network, orders, policy,
            dict(options, schedule_mode='uniform_cycle'), source.cost_profile.config)
    return CalendarCycleInstance(source.network, orders, policy, options,
                                  source.cost_profile.config, model=number)


@pytest.mark.parametrize('origin,subdivision', [(0, 1), (10, 1), (10, 2)])
def test_m8_grid_contains_m6_m7_equal_calendar_and_original_grid(origin, subdivision):
    parents = [family(n, origin=origin, subdivision=subdivision) for n in (6, 7, 8)]
    children = [p.for_count(3) for p in parents]
    clocks = [p.operating_policy for p in children]
    assert all(p.time_tick_subdivision == 3*subdivision for p in clocks)
    assert all(p.cycle_start == 3*origin and p.cycle_end == 3*(origin+130) for p in clocks)
    equal_bounds = tuple([b.start for b in clocks[0].batches]+[clocks[0].cycle_end])
    assert parents[2].validated_boundaries(3, equal_bounds) == equal_bounds
    # The old 43/43/44-tick partition also survives after coordinate scaling.
    unequal = tuple(3*(origin+t) for t in (0, 43, 86, 130))
    assert parents[2].validated_boundaries(3, unequal) == unequal
    with pytest.raises(ValueError, match='equal batch'):
        parents[1].validated_boundaries(3, unequal)
    for child in children:
        for old, new in zip(parents[0].orders, child.orders):
            assert child.operating_policy.minutes(new.release_time) == parents[0].operating_policy.minutes(old.release_time)
        assert child.operating_policy.minutes(child.operating_policy.cleaning_duration) == parents[0].operating_policy.minutes(parents[0].operating_policy.cleaning_duration)


def test_minimum_phase_scales_in_variable_bounds_and_calendar_seeds():
    from models.routing.model_8.alns.construction import initial_calendars
    from models.routing.model_8.alns.calendar_moves import move_boundaries
    p = family(8, minimum=20, releases=(0, 40, 80))
    variable = p.for_count(3)
    assert variable.minimum_phase_ticks == 60
    assert variable.start_bounds['b2'] == (120, 150)
    assert variable.cutoff_bounds['b2'] == (180, 210)
    seeds = list(initial_calendars(variable))
    assert len(seeds) > 1
    for seed in seeds:
        assert p.validated_boundaries(3, seed) == seed
    rng = random.Random(42)
    for op in ('shift_boundary', 'align_boundary', 'redistribute_lengths'):
        moved = move_boundaries(variable, p.balanced_boundaries(3), rng, op)
        assert min(b-a for a, b in zip(moved, moved[1:])) >= 120


def test_one_physical_schedule_replays_with_identical_cost_in_all_four_models():
    results = []
    for number in (6, 7, 8):
        p = family(number)
        child = p.for_count(3) if number == 6 else p.fixed_calendar(3)
        assignment = {o.id: b.id for o, b in zip(child.orders, child.operating_policy.batches)}
        pickups = {o.id: child.batch_map[assignment[o.id]].start+3 for o in child.orders}
        fixed, raw = explicit_plan(child, assignment, pickups)
        if number == 8:
            # Fix precisely M8's selected order assignment, calendar and clock.
            m5 = FixedBatchPoolInstance(fixed.network, fixed.orders, fixed.operating_policy,
                                       {}, p.cost_profile.config)
            m5_result = deepcopy(raw)
            attach_pool_cost(m5, m5_result, child.pool_max)
            m5_audit = audit_solution(m5, json.loads(json.dumps(m5_result)))
            assert m5_audit['valid'] and m5_audit['periodic_replay']['valid']
            results.append(m5_result)
        result = lift_result(child, raw, assignment, child.pool_max, 'alns')
        if number == 6:
            attach_uniform_decisions(p, result, 3)
        else:
            attach_calendar_decisions(p, result, 3, p.balanced_boundaries(3))
        result = json.loads(json.dumps(result))
        checked = audit_solution(p, result)
        assert checked['valid'] and checked['periodic_replay']['valid'], checked['errors']
        results.append(result)
        bad = deepcopy(result)
        bad['time_tick_subdivision'] = 1
        assert not audit_solution(p, bad)['valid']
    assert all(r['e1_trips'] == results[0]['e1_trips'] for r in results)
    assert all(r['e2_trips'] == results[0]['e2_trips'] for r in results)
    assert all(r['cost_components'] == results[0]['cost_components'] for r in results)


def test_m8_contract_distinguishes_previous_unrefined_domain():
    assert family(8).calendar_contract['count_clock_rule'] == 'equal_partition_refinement_v1'
