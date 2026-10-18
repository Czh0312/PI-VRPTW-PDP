"""Large K domains retain honest certificates under focused exact budgets."""
from importlib import import_module
from types import SimpleNamespace

import pytest

from tests.routing.test_count_budget_recycling import Clock, candidate


@pytest.mark.parametrize('number', [6, 7, 8])
@pytest.mark.parametrize('bounds', [False, True])
def test_focused_exact_time_keeps_unvisited_counts_and_their_proof_status(number, bounds, monkeypatch):
    module = import_module(f'models.routing.model_{number}.exact.search.batch_search')
    clock = Clock()
    monkeypatch.setattr(module, 'time', clock)
    monkeypatch.setattr(module, '_audit', lambda p, r: dict(valid=True, objective=r['info']['objective']))
    monkeypatch.setattr(module, '_attach', lambda p, c, r, k: r.update(cycle_batch_count=k))
    monkeypatch.setattr(module, '_metadata', lambda *args: None)
    from models.common.cycle import pool_bound
    monkeypatch.setattr(pool_bound, 'necessary_cost_lower_bound', lambda *args, **kw: 0.)
    monkeypatch.setattr(pool_bound, 'necessary_pool_lower_bound', lambda *args, **kw: 1)
    problem = SimpleNamespace(model_number=number, pool_max=100,
                              batch_count_candidates=tuple(range(1, 101)), for_count=lambda k: k)
    calls = []
    def solve(k, **kwargs):
        calls.append((k, kwargs['time_limit']))
        clock.value += kwargs['time_limit']
        return candidate(10 if k == 2 else 20, bound=0.)
    aggregate = module.solve_uniform if number == 6 else module.solve_counts
    result = aggregate(problem, solve_candidate=solve, method='exact', budget=10,
                       seed=42, budget_strategy='adaptive', analytical_bounds=bounds)
    assert clock.value == pytest.approx(10)
    assert calls[0][1] == pytest.approx(2)
    assert len(result['count_search']) == 100
    assert len(calls) == 4
    assert result['info']['objective'] == 10
    assert not result['info']['proven_optimal'] and not result['info']['proven_infeasible']
    assert result['info']['best_bound'] == (0. if bounds else None)
    remaining = [row for row in result['count_search'] if not row['visits']]
    assert remaining and all(row['status'] == 'NOT_RUN_TIME_LIMIT' for row in remaining)
    assert all(not row['proven_optimal'] and not row['proven_infeasible'] for row in remaining)
