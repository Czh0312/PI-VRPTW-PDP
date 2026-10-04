"""Periodic repair restarts use an audited incumbent and its problem context."""
from copy import deepcopy
import random

import pytest

from models.routing.common.cycle.assignment import assigned_problem, input_assignment
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.alns import single_batch
from models.routing.solvers.alns.evaluator import initial_state
from models.routing.solvers.alns.neighborhoods import state_key
from models.routing.solvers.alns.assignment import AssignmentSearch
from tests.routing.test_single_batch_solver_audit import tiny_problem


@pytest.mark.parametrize('model', [1, 3])
def test_restart_repairs_best_state_after_current_has_changed(model, monkeypatch):
    problem = tiny_problem(model, 0)
    original = initial_state(problem, random.Random(42))
    changed = deepcopy(original)
    changed['e2'].reverse()
    if state_key(changed) == state_key(original):
        changed['e2'][0]['tasks'].reverse()
    assert state_key(changed) != state_key(original)
    calls = []
    def repair(p, state, rng, mode, deadline, **kwargs):
        calls.append(state_key(state))
        return deepcopy(changed)
    monkeypatch.setattr(single_batch, '_move', lambda *args: deepcopy(changed))
    monkeypatch.setattr(single_batch, 'destroy_repair', repair)
    monkeypatch.setattr(single_batch.SingleBatchDescent, 'propose', lambda *args: None)
    result = single_batch.solve_alns(problem, max_time=20, max_iterations=151, _incumbent_restart=True)
    assert result['info']['operator_attempts']['restart'] == 1
    assert result['info']['periodic_restart_policy'] == 'destroy_audited_incumbent'
    assert calls[-1] == state_key(original)
    assert result['audit']['valid'] and audit_solution(problem, result)['periodic_replay']['valid']
    assert original == initial_state(problem, random.Random(42))


@pytest.mark.parametrize('model', [1, 3])
def test_no_periodic_restart_before_interval_or_with_zero_iterations(model):
    problem = tiny_problem(model, 0)
    for iterations in (0, 150):
        result = single_batch.solve_alns(problem, max_time=20, max_iterations=iterations, _incumbent_restart=True)
        assert result['info']['operator_attempts']['restart'] == 0
        assert result['audit']['valid']


@pytest.mark.parametrize('model', [1, 3])
def test_restart_reports_repairs_and_keeps_fixed_iteration_results_repeatable(model):
    base = tiny_problem(model, 1)
    problem = assigned_problem(base, input_assignment(base))
    a = single_batch.solve_alns(problem, max_time=20, max_iterations=320, seed=42, _incumbent_restart=True)
    b = single_batch.solve_alns(problem, max_time=20, max_iterations=320, seed=42, _incumbent_restart=True)
    assert a['e1_trips'] == b['e1_trips'] and a['e2_trips'] == b['e2_trips']
    assert a['info']['operator_attempts']['restart'] > 0
    assert a['info']['operator_attempts'] == b['info']['operator_attempts']
    assert a['info']['incumbent_history'][-1]['objective'] == a['info']['objective']
    assert a['audit']['valid'] and audit_solution(problem, a)['periodic_replay']['valid']


def test_joint_restart_uses_best_assignment_context_after_equal_cost_assignment_move(monkeypatch):
    problem = tiny_problem(4, 0)
    search = AssignmentSearch(problem, 42)
    best_problem, best_cache = search.context(input_assignment(problem))
    swapped = {c: next(s for s in problem.satellites if s != site)
               for c, site in input_assignment(problem).items()}
    other_problem, other_cache = search.context(swapped)
    other_state = initial_state(other_problem, random.Random(42))
    monkeypatch.setattr(search, 'propose', lambda *args: (other_problem, other_cache, deepcopy(other_state)))
    monkeypatch.setattr(single_batch, '_move', lambda p, state, *args: deepcopy(state))
    calls = []
    def repair(p, state, rng, mode, deadline, *, scorer):
        calls.append((p, scorer))
        return deepcopy(state)
    monkeypatch.setattr(single_batch, 'destroy_repair', repair)
    result = single_batch.solve_alns(problem, max_time=20, max_iterations=151,
                                      _assignment_search=search, _incumbent_restart=True)
    assert result['info']['operator_attempts']['restart'] == 1
    assert calls[-1] == (best_problem, best_cache.route_scorer)
    assert search.active_problem is best_problem
    assert result['assignment'] == input_assignment(problem)
    assert audit_solution(problem, result)['periodic_replay']['valid']
