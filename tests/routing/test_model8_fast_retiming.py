"""Retain feasible calendar neighbors; reconstruct failed ones and diversify."""
from copy import deepcopy

import pytest

from models.common.cycle.joint import audit_joint_solution
from models.routing.model_8.alns import search
from models.routing.model_8.alns.fixed_calendar_search import solve_alns
from tests.routing.test_model78_calendar import calendar_fixture


@pytest.mark.parametrize('period,mode', [(1, 'feasible'), (8, 'feasible'), (8, 'failed'), (8, 'noop')])
def test_calendar_repair_retains_or_reconstructs_as_needed(monkeypatch, period, mode):
    parent = calendar_fixture(8, counts=(2,))
    warm = solve_alns(parent.fixed_calendar(2), max_time=2, max_iterations=0)
    assert warm['audit']['valid']
    warm['batch_boundaries'] = parent.balanced_boundaries(2)
    saved = deepcopy(warm)
    # Start directly from the audited incumbent; isolate boundary neighbors.
    monkeypatch.setattr(search, 'initial_calendars', lambda variable: [])
    from models.routing.model_8.alns import calendar_polish, polish
    monkeypatch.setattr(calendar_polish, 'deadline_calendar_seeds', lambda variable: [])
    monkeypatch.setattr(polish, 'polish_routes', lambda fixed, ev, assignment, state, deadline: (state, 0, 0))
    monkeypatch.setattr(calendar_polish, 'polish_calendar',
        lambda variable, context, bounds, assignment, state, best, deadline, seed:
        ((bounds, assignment, state, best), 0, 0))
    monkeypatch.setattr(search.SearchControl, 'select_name', lambda *args: 'shift_boundary')
    monkeypatch.setattr(search.SearchControl, 'accept', lambda *args, **kwargs: True)
    force_failure = [False]

    def move(variable, bounds, rng, op, best):
        if mode == 'noop':
            return bounds
        force_failure[0] = mode == 'failed'
        return (bounds[0], 64 if bounds[1] == 65 else 65, bounds[-1])

    monkeypatch.setattr(search, 'move_boundaries', move)
    original_evaluate = search.JointEvaluator.__call__

    def evaluate(self, assignment, state):
        if force_failure[0]:
            force_failure[0] = False
            return self.penalty, None, ('RETAINED_SCHEDULE_FAILED',)
        return original_evaluate(self, assignment, state)

    monkeypatch.setattr(search.JointEvaluator, '__call__', evaluate)
    result = search.solve_variable_count(parent.for_count(2), max_time=5, max_iterations=32,
                                        seed=42, initial_result=warm, calendar_reconstruction_period=period)
    info = result['info']
    assert info['iterations'] == 32
    assert result['audit']['valid'] and result['audit']['periodic_replay']['valid']
    boundaries = tuple(b['start'] for b in result['batches']) + (parent.operating_policy.cycle_end,)
    assert audit_joint_solution(parent.fixed_calendar(2, boundaries), result)['valid']
    assert info['objective'] <= warm['info']['objective'] + 1e-8
    assert warm == saved
    if mode == 'feasible':
        assert info['calendar_retiming_feasible'] == 32
        assert info['calendar_reconstructions'] == 32 // period
    elif mode == 'failed':
        assert info['calendar_retiming_feasible'] == 0
        assert info['calendar_reconstructions'] == 32
    else:
        assert info['operator_noops']['shift_boundary'] == 32
        assert info['calendar_retiming_attempts'] == info['calendar_reconstructions'] == 0


@pytest.mark.parametrize('value', [0, -1, True, 1.5])
def test_public_entry_rejects_invalid_reconstruction_period(value):
    from models.routing.model_8.alns.solver import solve_alns as solve
    with pytest.raises(ValueError, match='calendar_reconstruction_period'):
        solve(calendar_fixture(8), max_time=.1, max_iterations=0, calendar_reconstruction_period=value)


def test_public_fast_retiming_records_policy_and_aggregates_visit_metrics():
    from models.routing.model_8.alns.solver import solve_alns as solve
    result = solve(calendar_fixture(8, counts=(2,)), max_time=.5, max_iterations=120,
                   calendar_reconstruction_period=8)
    assert result['audit']['valid'] and result['audit']['periodic_replay']['valid']
    assert result['info']['calendar_reconstruction_period'] == 8
    for name in ('calendar_retiming_attempts', 'calendar_reconstructions', 'calendar_cache_misses'):
        assert result['info'][name] == sum(v.get(name, 0) for v in result['count_search_visits'])
