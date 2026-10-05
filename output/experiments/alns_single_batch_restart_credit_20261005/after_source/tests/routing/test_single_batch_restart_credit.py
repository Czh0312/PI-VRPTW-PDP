"""Forced reconstruction regression: credit, policy, RNG, and precedence."""
from copy import deepcopy
import pytest
from models.routing.solvers.alns import single_batch
from models.routing.solvers.alns.evaluator import evaluate
from models.routing.common.cycle.audit import audit_solution
from tests.routing.test_single_batch_solver_audit import nonmetric_problem


def harness(monkeypatch, kind, descent=False):
    problem = nonmetric_problem()
    good = dict(deliveries={'b1': [['T', 'S']]}, pickup_order={'b1': ['S', 'T']},
                pickup_vehicle={'b1': {}}, e2=[
                    dict(satellite='S', vehicle=0, tasks=[('a', 'd'), ('a', 'p')]),
                    dict(satellite='T', vehicle=0, tasks=[('b', 'd'), ('b', 'p')])])
    _, decoded, issues = evaluate(problem, good)
    assert not issues and audit_solution(problem, decoded)['valid']
    alternate = deepcopy(good)
    alternate['pickup_order']['b1'].reverse()
    weights, draws, reconstructions, visited = [], [], [], []

    class Rng:
        def choices(self, values, weights, k):
            assert k == 1
            snapshots.append(tuple(weights))
            return [8]  # Select pickup; a restart must not become a pickup.

        def random(self):
            draws.append(1)
            return 0.0

    snapshots = weights

    class Cache:
        def __init__(self, problem):
            self.hits = self.misses = 0

        def __call__(self, state):
            self.misses += 1
            changed = state['pickup_order']['b1'] == ['T', 'S']
            score = int(changed) if kind != 'downhill' else int(not changed)
            return score, deepcopy(decoded), []

        def score_route(self, *args):
            raise AssertionError('No local score expected')

    def initial(problem, rng, randomized=False):
        if randomized:
            reconstructions.append(1)
        return deepcopy(alternate if randomized and kind != 'noop' else good)

    def move(problem, current, rng, operator):
        assert operator == 'e1_pickup'
        visited.append(tuple(current['pickup_order']['b1']))
        return deepcopy(current)

    monkeypatch.setattr(single_batch.random, 'Random', lambda seed: Rng())
    monkeypatch.setattr(single_batch, 'SingleBatchCache', Cache)
    monkeypatch.setattr(single_batch, 'initial_state', initial)
    monkeypatch.setattr(single_batch, '_move', move)
    monkeypatch.setattr(single_batch.SingleBatchDescent, 'propose',
                        lambda *args: deepcopy(alternate) if descent else None)
    return problem, weights, draws, reconstructions, visited


@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('kind', ['noop', 'uphill', 'downhill'])
def test_restart_has_own_credit_and_acceptance_without_skipping_selection_draw(monkeypatch, enabled, kind):
    problem, weights, draws, reconstructions, visited = harness(monkeypatch, kind)
    result = single_batch.solve_alns(problem, max_time=60, max_iterations=152,
                                     _restart_isolation=enabled)
    info = result['info']
    assert reconstructions == [1]
    assert len(weights) == info['iterations'] == 152
    assert sum(info['operator_attempts'].values()) == 152
    assert info['operator_attempts'].get('restart', 0) == int(enabled)
    assert info['operator_attempts']['e1_pickup'] == 152 - int(enabled)
    assert (weights[150] == weights[151]) is enabled
    assert info['operator_noops'].get('restart', 0) == int(enabled and kind == 'noop')
    assert len(draws) == int(kind == 'uphill')
    assert info['late_pickup_checks'] == int(not enabled and kind != 'noop')
    assert info['late_pickup_vetoes'] == int(not enabled and kind == 'uphill')
    expected_changed = kind == 'downhill' or (kind == 'uphill' and enabled)
    assert visited[-1] == (('T', 'S') if expected_changed else ('S', 'T'))
    assert audit_solution(problem, result)['valid']


@pytest.mark.parametrize('enabled', [False, True])
def test_descent_keeps_precedence_over_scheduled_restart(monkeypatch, enabled):
    problem, weights, draws, reconstructions, visited = harness(monkeypatch, 'uphill', descent=True)
    result = single_batch.solve_alns(problem, max_time=60, max_iterations=152,
                                     _restart_isolation=enabled)
    assert reconstructions == []
    assert result['info']['operator_attempts']['descent'] > 0
    assert result['info']['operator_attempts'].get('restart', 0) == 0
    assert sum(result['info']['operator_attempts'].values()) == 152
