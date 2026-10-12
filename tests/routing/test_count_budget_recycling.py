"""Deterministic scheduler contracts, with no wall-time performance assertions."""
from copy import deepcopy
from importlib import import_module
from types import SimpleNamespace

import pytest


class Clock:
    value = 0.

    def perf_counter(self):
        return self.value


def candidate(cost=None, *, bound=None, optimal=False, infeasible=False, iterations=0, reject=False):
    return dict(info=dict(status='OPTIMAL' if optimal else 'INFEASIBLE' if infeasible else
                          'FEASIBLE' if cost is not None else 'TIME_LIMIT', objective=cost,
                          best_bound=bound, proven_optimal=optimal, proven_infeasible=infeasible,
                          iterations=iterations, history=[]),
                audit=dict(valid=cost is not None), reject=reject)


@pytest.fixture(params=[(n, m) for n in (6, 7, 8) for m in ('alns', 'exact')])
def harness(request, monkeypatch):
    number, method = request.param
    module = import_module(f'models.routing.model_{number}.{method}.batch_search')
    clock = Clock()
    monkeypatch.setattr(module, 'time', clock)
    monkeypatch.setattr(module, '_audit', lambda p, r: dict(
        valid=not r.get('reject', False), objective=r['info']['objective'], errors=['injected'] if r.get('reject') else []))
    monkeypatch.setattr(module, '_attach', lambda p, c, r, k: r.update(cycle_batch_count=k))
    monkeypatch.setattr(module, '_metadata', lambda *a: None)
    monkeypatch.setattr(module, 'joint_empty_result', lambda *a: candidate())
    problem = SimpleNamespace(model_number=number, batch_count_candidates=(1, 2), for_count=lambda k: k)
    solve = getattr(module, 'solve_uniform' if number == 6 else 'solve_counts')

    def run(fn, **kwargs):
        return solve(problem, solve_candidate=fn, method=method, budget=kwargs.pop('budget', 10),
                     seed=42, max_iterations=kwargs.pop('max_iterations', 100), **kwargs)

    return SimpleNamespace(clock=clock, problem=problem, run=run, method=method, module=module)


def test_late_fast_exclusion_recycles_to_earlier_count(harness):
    h = harness
    calls = []

    def solve(k, **kw):
        calls.append((k, kw))
        h.clock.value += .1 if k == 2 else kw.get('max_time', kw.get('time_limit'))
        return candidate(infeasible=True) if k == 2 else candidate(20-len(calls), bound=0)

    result = h.run(solve)
    assert [k for k, _ in calls] == [1, 2, 1]
    assert h.clock.value == pytest.approx(10)
    assert result['info']['objective'] == 17
    assert result['count_search'][1]['proven_infeasible']
    assert result['count_search'][0]['iterations'] == 0
    assert calls[0][1]['seed'] != calls[2][1]['seed']
    if h.method == 'alns':
        seed = calls[2][1]['initial_result']
        assert seed['info']['objective'] == 19
        assert not seed['info']['proven_optimal'] and seed['info']['best_bound'] is None
    costs = [r['objective'] for r in result['anytime'] if r['objective'] is not None]
    assert costs == sorted(costs, reverse=True)


def test_no_solution_is_revisited_and_incumbent_never_regresses(harness):
    h = harness
    seen = {1: 0, 2: 0}

    def solve(k, **kw):
        seen[k] += 1
        h.clock.value += min(1., kw.get('max_time', kw.get('time_limit')))
        cost = None if seen[k] == 1 else 10 if seen[k] == 2 else 30
        return candidate(cost)

    result = h.run(solve, budget=6)
    assert all(v >= 2 for v in seen.values())
    assert result['info']['objective'] == 10
    assert not result['info']['proven_infeasible']


def test_unrun_counts_and_unknown_bounds_block_global_proof(harness):
    h = harness

    def solve(k, **kw):
        h.clock.value += 10
        return candidate(10, bound=10, optimal=True)

    result = h.run(solve)
    assert result['count_search'][1]['status'] == 'NOT_RUN_TIME_LIMIT'
    assert result['info']['best_bound'] is None
    assert not result['info']['proven_optimal']


def test_known_bounds_aggregate_across_rounds_without_regression(harness):
    h = harness
    seen = {1: 0, 2: 0}

    def solve(k, **kw):
        seen[k] += 1
        h.clock.value += 1
        return candidate(10 if k == 1 else 20, bound=({1: [8, 7], 2: [6, 9]}[k][seen[k]-1]))

    result = h.run(solve, budget=4)
    assert not result['info']['proven_optimal']
    if h.method == 'exact':
        assert result['info']['best_bound'] == 8
        assert [r['best_bound'] for r in result['count_search']] == [8, 9]
    else:
        assert result['info']['best_bound'] is None


def test_global_bound_closure_does_not_require_every_k_optimal(harness):
    h = harness

    def solve(k, **kw):
        h.clock.value += 1
        return candidate(10, bound=10, optimal=True) if k == 1 else candidate(bound=10)

    result = h.run(solve, budget=2)
    assert result['info']['proven_optimal'] == (h.method == 'exact')
    if h.method == 'exact':
        assert result['info']['proof_aggregation'] == 'all_count_bounds_dominate_incumbent'
        assert result['info']['stop_reason'] == 'PROOF_COMPLETE'


def test_audit_failure_blocks_certificate_even_with_other_valid_incumbent(harness):
    h = harness

    def solve(k, **kw):
        h.clock.value += 1
        return candidate(10, bound=10, optimal=True, reject=k == 2)

    result = h.run(solve, budget=2)
    assert result['info']['objective'] == 10
    assert not result['info']['proven_optimal']
    assert result['info']['audit_failure_count'] == 1
    assert result['info']['best_bound'] is None


def test_conflicting_certificate_is_quarantined(harness):
    h = harness

    def solve(k, **kw):
        h.clock.value += 1
        return candidate(10, bound=12)

    result = h.run(solve, budget=2)
    assert not result['info']['proven_optimal']
    if h.method == 'exact':
        assert result['info']['certificate_error_count'] == 2
        assert result['info']['best_bound'] is None


def test_iteration_budget_is_global_and_zero_means_construction_only(harness):
    h = harness
    if h.method == 'exact':
        return
    calls = []

    def solve(k, **kw):
        calls.append(kw['max_iterations'])
        h.clock.value += .1
        return candidate(10, iterations=kw['max_iterations'])

    result = h.run(solve, max_iterations=3)
    assert result['info']['iterations'] == sum(calls) == 3
    assert result['info']['stop_reason'] == 'ITERATION_LIMIT'
    calls.clear()
    result = h.run(solve, max_iterations=0)
    assert calls == [0, 0]
    assert result['info']['iterations'] == 0


def test_excess_candidate_iterations_are_rejected(harness):
    h = harness
    if h.method == 'exact':
        return
    with pytest.raises(ValueError, match='iteration budget'):
        h.run(lambda k, **kw: candidate(10, iterations=kw['max_iterations']+1))


def test_single_pass_comparator_and_initial_proof_reset(harness):
    h = harness
    initial = candidate(5, bound=5, optimal=True)
    initial['cycle_batch_count'] = 1
    saved = deepcopy(initial)

    def solve(k, **kw):
        h.clock.value += 1
        return candidate(10)

    result = h.run(solve, initial_result=initial, budget_strategy='single_pass')
    assert len(result['count_search_visits']) == 2
    assert result['info']['stop_reason'] == 'SINGLE_PASS_COMPLETE'
    assert result['info']['objective'] == 5
    assert not result['info']['proven_optimal']
    assert initial == saved


def test_all_counts_proven_infeasible_finish_early(harness):
    h = harness

    def solve(k, **kw):
        h.clock.value += .1
        return candidate(infeasible=True)

    result = h.run(solve)
    assert result['info']['proven_infeasible']
    assert h.clock.value == pytest.approx(.2)
    assert result['info']['stop_reason'] == 'PROOF_COMPLETE'


def test_calendar_construction_consumes_the_outer_budget(harness):
    h = harness

    def build(k):
        h.clock.value += 10
        return k

    h.problem.for_count = build
    result = h.run(lambda *a, **kw: pytest.fail('deadline exhausted before solver'))
    assert not result['count_search_visits']
    assert result['count_search'][0]['construction_time'] == 10
    assert all(r['status'] == 'NOT_RUN_TIME_LIMIT' for r in result['count_search'])
