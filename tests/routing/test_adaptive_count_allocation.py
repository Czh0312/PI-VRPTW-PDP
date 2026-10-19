"""M8 must refine promising K without shrinking the declared search domain."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from models.routing.model_8.alns.k_search import scheduler as batch_search
from tests.routing.test_count_budget_recycling import Clock, candidate


@pytest.fixture
def scheduler(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(batch_search, 'time', clock)
    monkeypatch.setattr(batch_search, '_audit', lambda p, r: dict(
        valid=True, objective=r['info']['objective'], errors=[]))
    monkeypatch.setattr(batch_search, '_attach', lambda p, c, r, k: r.update(cycle_batch_count=k))
    monkeypatch.setattr(batch_search, '_metadata', lambda *a: None)
    monkeypatch.setattr(batch_search, 'joint_empty_result', lambda *a: candidate())
    problem = SimpleNamespace(model_number=8, batch_count_candidates=tuple(range(1, 101)),
                              for_count=lambda k: k)

    def run(solve, **kwargs):
        return batch_search.solve_counts(problem, solve_candidate=solve, method='alns',
            budget=30, seed=42, max_iterations=kwargs.pop('max_iterations', 10**6),
            budget_strategy='adaptive', **kwargs)

    return SimpleNamespace(clock=clock, problem=problem, run=run)


def test_large_domain_keeps_coverage_and_refines_audited_winner(scheduler):
    h = scheduler
    calls = []

    def solve(k, **kw):
        calls.append((k, deepcopy(kw)))
        h.clock.value += kw['max_time']
        return candidate(10 if k == 2 else 20, iterations=1)

    result = h.run(solve)
    assert [r['batch_count'] for r in result['count_search']] == list(range(1, 101))
    assert {k for k, _ in calls} == set(range(1, 101))
    assert calls[0][1]['max_time'] >= 30/3
    assert calls[-1][0] == 2
    assert calls[-1][1]['initial_result']['info']['objective'] == 10
    assert calls[-1][1]['seed'] != calls[1][1]['seed']
    assert h.clock.value == pytest.approx(30)
    assert result['info']['iterations'] == len(calls)
    assert not result['info']['proven_optimal']


def test_refinement_never_replaces_better_incumbent(scheduler):
    h = scheduler
    initial = candidate(5, optimal=True, bound=5)
    initial['cycle_batch_count'] = 7
    saved = deepcopy(initial)

    def solve(k, **kw):
        h.clock.value += kw['max_time']
        return candidate(30, iterations=1)

    result = h.run(solve, initial_result=initial)
    assert result['info']['objective'] == 5
    assert result['cycle_batch_count'] == 7
    assert result['info']['best_bound'] is None
    assert not result['info']['proven_optimal']
    assert initial == saved


def test_unvisited_domain_and_global_iteration_limit_remain_honest(scheduler):
    h = scheduler

    def solve(k, **kw):
        h.clock.value += kw['max_time']
        return candidate(10, iterations=kw['max_iterations'])

    result = h.run(solve, max_iterations=3)
    assert result['info']['iterations'] == 3
    assert result['info']['stop_reason'] == 'ITERATION_LIMIT'
    assert any(not r['visits'] for r in result['count_search'])
    assert len(result['count_search']) == 100
    assert not result['info']['proven_optimal'] and result['info']['best_bound'] is None


def test_no_solution_does_not_become_infeasibility_proof(scheduler):
    h = scheduler

    def solve(k, **kw):
        h.clock.value += kw['max_time']
        return candidate(iterations=1)

    result = h.run(solve)
    assert result['info']['status'] == 'HEURISTIC_NO_SOLUTION'
    assert not result['info']['proven_infeasible']
    assert h.clock.value == pytest.approx(30)


def test_exact_cannot_use_heuristic_allocation(scheduler):
    with pytest.raises(ValueError, match='requires ALNS'):
        batch_search.solve_counts(scheduler.problem, solve_candidate=lambda *a, **kw: candidate(),
            method='exact', budget=30, seed=42, budget_strategy='adaptive')
