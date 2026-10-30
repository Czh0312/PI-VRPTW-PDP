"""Deterministic scouting-budget contracts, without performance timing tests."""
from types import SimpleNamespace

import pytest

from models.routing.model_8.alns.k_search import scheduler


def simulate(monkeypatch, orders=7, counts=(1, 2, 3, 4), strategy='adaptive', iterations=1000):
    clock = SimpleNamespace(value=0.)
    clock.perf_counter = lambda: clock.value
    monkeypatch.setattr(scheduler, 'time', clock)
    monkeypatch.setattr(scheduler, '_audit', lambda p, r: dict(valid=True, errors=[], objective=r['info']['objective']))
    monkeypatch.setattr(scheduler, '_attach', lambda p, c, r, k: r.update(cycle_batch_count=k))
    monkeypatch.setattr(scheduler, '_metadata', lambda *args: None)
    p = SimpleNamespace(model_number=8, batch_count_candidates=counts,
                        orders=tuple(range(orders)), for_count=lambda k: k)
    calls = []
    def solve(k, **kwargs):
        calls.append((k, kwargs['max_time']))
        clock.value += kwargs['max_time']
        return dict(info=dict(status='FEASIBLE', objective=40.+k, best_bound=None,
                    proven_optimal=False, proven_infeasible=False, iterations=0, history=[]),
                    audit=dict(valid=True))
    result = scheduler.solve_counts(p, solve_candidate=solve, method='alns', budget=20.,
        seed=42, max_iterations=iterations, budget_strategy=strategy)
    return result, calls, clock.value


def test_small_multi_batch_scouts_receive_time_before_cost_ranking(monkeypatch):
    result, calls, elapsed = simulate(monkeypatch)
    assert [k for k, _ in calls[:3]] == [1, 2, 3]
    assert [budget for _, budget in calls[:3]] == pytest.approx([2., 4., 4.])
    assert elapsed == pytest.approx(20.)
    assert [row['batch_count'] for row in result['count_search']] == [1, 2, 3, 4]
    assert set(k for k, _ in calls) == {1, 2, 3, 4}
    assert not result['info']['proven_optimal'] and result['info']['best_bound'] is None


def test_larger_instance_scouting_policy_is_preserved(monkeypatch):
    _, calls, elapsed = simulate(monkeypatch, orders=20)
    assert [budget for _, budget in calls[:3]] == pytest.approx([20./3, 5./3, 5./3])
    assert elapsed == pytest.approx(20.)


def test_all_multi_batch_domains_get_equal_scouting_weight(monkeypatch):
    result, calls, elapsed = simulate(monkeypatch, counts=(2, 3, 4, 5))
    assert [budget for _, budget in calls[:3]] == pytest.approx([10./3]*3)
    assert set(k for k, _ in calls) == {2, 3, 4, 5}
    assert elapsed == pytest.approx(20.)
    assert not result['info']['proven_infeasible']


@pytest.mark.parametrize('strategy', ['recycle', 'single_pass'])
def test_explicit_legacy_comparators_keep_equal_allocations(monkeypatch, strategy):
    _, calls, elapsed = simulate(monkeypatch, strategy=strategy)
    assert [k for k, _ in calls] == [1, 2, 3, 4]
    assert [budget for _, budget in calls] == pytest.approx([5.]*4)
    assert elapsed == pytest.approx(20.)


def test_zero_iterations_remain_construction_only(monkeypatch):
    result, calls, elapsed = simulate(monkeypatch, iterations=0)
    assert [budget for _, budget in calls] == pytest.approx([5.]*4)
    assert elapsed == pytest.approx(20.)
    assert result['info']['iterations'] == 0
