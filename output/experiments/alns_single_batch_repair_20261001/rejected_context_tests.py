"""M2 retains its incumbent context without crossing assignments or solves."""
from copy import deepcopy

from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.alns import partitioned_assignment, single_batch
from tests.routing.test_single_batch_solver_audit import tiny_problem


def capture_zero_iteration_subsolves(monkeypatch):
    original = single_batch.solve_alns
    calls = []
    def solve(fixed, **kwargs):
        context = kwargs['_context']
        calls.append(dict(problem=fixed, context=context,
            key=tuple((o.id, o.satellite) for o in fixed.orders),
            cached_entries=len(context.entries), hits_at_entry=context.hits))
        kwargs['max_iterations'] = 0
        return original(fixed, **kwargs)
    monkeypatch.setattr(single_batch, 'solve_alns', solve)
    return calls


def test_m2_reuses_protected_incumbent_context_and_keeps_other_assignments_separate(monkeypatch):
    problem = tiny_problem(2, 0)
    saved = deepcopy(problem.orders)
    calls = capture_zero_iteration_subsolves(monkeypatch)
    result = partitioned_assignment.solve_alns(problem, max_time=10, max_iterations=30, seed=42)
    assert len(calls) == 30 and len({c['key'] for c in calls}) > 2
    initial = calls[0]
    repeated = [c for c in calls[1:] if c['key'] == initial['key']]
    assert repeated and all(c['context'] is initial['context'] and c['problem'] is initial['problem'] for c in repeated)
    assert all(c['cached_entries'] for c in repeated)
    for call in calls:
        assert call['context'].problem is call['problem']
        assert call['hits_at_entry'] == 0
        assert all(c['key'] == call['key'] for c in calls if c['context'] is call['context'])
    assert result['info']['assignment_context_hits'] >= len(repeated)
    assert result['info']['assignment_context_count'] == result['info']['assignment_context_capacity'] == 2
    assert audit_solution(problem, result)['periodic_replay']['valid']
    assert problem.orders == saved


def test_m2_contexts_are_fresh_for_each_solve_on_the_same_input(monkeypatch):
    problem = tiny_problem(2, 0)
    calls = capture_zero_iteration_subsolves(monkeypatch)
    a = partitioned_assignment.solve_alns(problem, max_time=10, max_iterations=12, seed=42)
    first = list(calls)
    calls.clear()
    b = partitioned_assignment.solve_alns(problem, max_time=10, max_iterations=12, seed=42)
    assert not ({id(c['context']) for c in first} & {id(c['context']) for c in calls})
    assert a['e1_trips'] == b['e1_trips'] and a['e2_trips'] == b['e2_trips']
    assert a['assignment'] == b['assignment']
    assert a['audit']['valid'] and b['audit']['valid']
