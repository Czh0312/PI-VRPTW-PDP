"""Acceptance boundary and public scope of the late pickup pilot."""
from copy import deepcopy
import pytest
from models.routing.service import solve_routing
from models.common.cycle.audit import audit_solution
from models.routing.model_4.alns import single_batch
from models.routing.model_4.alns.evaluator import evaluate
from tests.routing.test_single_batch_solver_audit import tiny_problem, nonmetric_problem

@pytest.mark.parametrize('enabled', [False, True])
def test_only_late_uphill_pickups_are_vetoed_and_original_draw_is_consumed(monkeypatch, enabled):
    p = nonmetric_problem()
    good = dict(deliveries={'b1': [['T','S']]}, pickup_order={'b1':['S','T']},
                pickup_vehicle={'b1':{}}, e2=[
                    dict(satellite='S',vehicle=0,tasks=[('a','d'),('a','p')]),
                    dict(satellite='T',vehicle=0,tasks=[('b','d'),('b','p')])])
    saved = deepcopy(good)
    _, decoded, issues = evaluate(p, good)
    assert not issues and audit_solution(p, decoded)['valid']
    seen, draws = [], []
    class Rng:
        def choices(self, values, weights, k): return [8]
        def random(self):
            draws.append(1)
            return 0.0
    class Cache:
        def __init__(self, problem): self.hits = self.misses = 0
        def __call__(self, state):
            order = tuple(state['pickup_order']['b1'])
            seen.append(order); self.misses += 1
            return (0 if order == ('S','T') else 1), deepcopy(decoded), []
        def score_route(self, *args): raise AssertionError('No descent score expected')
    def move(problem, state, rng, op):
        assert op == 'e1_pickup'
        proposed = deepcopy(state)
        proposed['pickup_order']['b1'].reverse()
        return proposed
    monkeypatch.setattr(single_batch.random, 'Random', lambda seed: Rng())
    monkeypatch.setattr(single_batch, 'SingleBatchCache', Cache)
    monkeypatch.setattr(single_batch, 'initial_state', lambda *args, **kw: deepcopy(good))
    monkeypatch.setattr(single_batch, '_move', move)
    monkeypatch.setattr(single_batch.SingleBatchDescent, 'propose', lambda *args: None)
    r = single_batch.solve_alns(p, max_time=60, max_iterations=10, _late_pickup_greedy=enabled)
    assert good == saved
    assert r['info']['iterations'] == 10 and audit_solution(p,r)['valid']
    assert seen[:5] == [('S','T'),('T','S'),('S','T'),('T','S'),('S','T')]
    if enabled:
        assert seen[5:] == [('T','S')]*6
        assert len(draws) == 8
        assert r['info']['late_pickup_checks'] == 6
        assert r['info']['late_pickup_uphill'] == r['info']['late_pickup_vetoes'] == 6
    else:
        assert seen[5:] == [('T','S'),('S','T')]*3
        assert len(draws) == 5
        assert r['info']['late_pickup_checks'] == r['info']['late_pickup_vetoes'] == 0

@pytest.mark.parametrize('model', [1,2,3,4])
@pytest.mark.parametrize('seed', [1,2,3])
def test_public_scope_and_fresh_independent_audit(model, seed):
    p = tiny_problem(model, seed)
    before = deepcopy(p.orders)
    r = solve_routing(model=model, method='alns', problem=p, alns_time=5, alns_iter=100, seed=seed+11)
    assert p.orders == before
    # This fixture seed has infeasible time windows for all four models.
    # Preserve that outcome instead of asserting a feasible schedule exists.
    if seed == 3:
        assert r['info']['objective'] is None and r['info']['status'] != 'FEASIBLE'
        return
    expected = 'greedy_last_half' if model == 3 else 'original'
    assert r['info']['late_pickup_policy'] == expected
    audit = audit_solution(p,r)
    assert audit['valid'] and audit['periodic_replay']['valid']
    if model != 3:
        assert r['info']['late_pickup_checks'] == r['info']['late_pickup_vetoes'] == 0

def test_zero_iterations_never_checks_pickups():
    r = single_batch.solve_alns(tiny_problem(3,1), max_time=5, max_iterations=0)
    assert r['info']['iterations'] == 0
    assert r['info']['late_pickup_checks'] == r['info']['late_pickup_vetoes'] == 0
    assert r['audit']['valid'] and r['audit']['periodic_replay']['valid']
