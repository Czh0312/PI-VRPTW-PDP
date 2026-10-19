"""Regression checks for issues discovered by the calendar assessment."""
from dataclasses import replace
from importlib import import_module

import pytest

from models.common.cycle.calendar import CalendarCycleInstance
from models.routing.service import solve_routing
from tests.routing.test_model6_joint import joint_fixture, exact_available
from tests.routing.test_model78_calendar import calendar_fixture


@pytest.mark.parametrize('number', [7, 8])
def test_late_pickup_constructor_finds_narrow_feasible_terminal_cutoff(number):
    source = joint_fixture(late=True)
    orders = [replace(source.orders[0], pickup_window=(117, 117))]
    p = CalendarCycleInstance(source.network, orders, source.operating_policy,
                              dict(batch_count_candidates=[1]), source.cost_profile.config, model=number)
    # No stochastic moves: verify construction can reach the feasible band
    # [118,120], instead of relying on a lucky random perturbation.
    r = solve_routing(model=number, method='alns', problem=p, alns_time=.2, alns_iter=0)
    assert r['audit']['valid'] and r['audit']['periodic_replay']['valid']
    assert 118 <= r['batch_cutoffs']['b1'] <= 120


def test_missing_batch_domain_is_not_a_late_seed_crash():
    from models.routing.model_8.alns.fixed_calendar_search import _initial_cutoff_profiles
    source = calendar_fixture(8, counts=(1,), late_release=True)
    child = source.fixed_calendar(1)
    assert not child.legal_batches['o2']
    assert _initial_cutoff_profiles(child)
    result = solve_routing(model=8, method='alns', problem=source, alns_time=.1, alns_iter=0)
    assert result['info']['proven_infeasible']


def test_one_batch_m8_delegates_to_identical_fixed_calendar_alns(monkeypatch):
    from models.routing.model_8.alns import fixed_calendar_search as solver
    from models.routing.model_8.alns.search import solve_variable_count
    p = calendar_fixture(8, counts=(1,))
    calls = []
    def inspect(child, **kwargs):
        calls.append((child, kwargs))
        return {'same_domain': True}
    monkeypatch.setattr(solver, 'solve_alns', inspect)
    assert solve_variable_count(p.for_count(1), max_time=3, max_iterations=70, seed=43) == {'same_domain': True}
    assert len(calls) == 1 and calls[0][0] is p.fixed_calendar(1)
    assert calls[0][1] == dict(max_time=3, max_iterations=70, seed=43, initial_result=None)


def test_event_seed_slots_are_not_consumed_by_duplicate_times():
    from models.routing.model_8.alns.search import boundary_events
    p = calendar_fixture(8, counts=(2,)).for_count(2)
    events = boundary_events(p)
    assert events == sorted(set(events))
