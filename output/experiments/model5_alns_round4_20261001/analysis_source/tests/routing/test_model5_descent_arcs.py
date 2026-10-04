"""Solve-local directed arc reuse must preserve candidate ordering and scores."""
from functools import lru_cache
import random
from types import SimpleNamespace

from models.routing.solvers.alns import solver
from models.routing.solvers.alns.evaluator import evaluate
from models.routing.solvers.alns.intensification import CycleDescent, task_distance
from models.routing.solvers.alns.neighborhoods import clone_state, state_key
from tests.routing.test_model5_intensification import directed_cycle


def test_arc_cache_preserves_directed_candidates_and_timing_filters():
    problem, original = directed_cycle()
    cached = CycleDescent(problem)
    reference = CycleDescent(problem, cache_distances=False)
    rng = random.Random(20261001)
    state = original
    operators = ['relocate','pair','reverse','vehicle','split_merge','cutoff',
                 'e1_delivery','e1_pickup']
    for step in range(48):
        if step % 8 == 0:
            state = clone_state(original)
        state = solver._move(problem,state,rng,operators[step % len(operators)])
        for route in state['e2']:
            assert cached.task_distance(route['satellite'],route['tasks']) == task_distance(
                problem,route['satellite'],route['tasks'])
        assert cached._e2_candidates(state,float('inf')) == reference._e2_candidates(state,float('inf'))
        _,decoded,_ = evaluate(problem,state)
        while True:
            left = cached.propose(state,decoded,float('inf'))
            right = reference.propose(state,decoded,float('inf'))
            assert left == right
            if left is None:
                break
    assert cached.distance.cache_info().hits > 0
    assert cached.generated == reference.generated
    assert cached.filtered == reference.filtered


def test_arc_cache_is_bounded_and_new_context_reads_changed_input():
    fake = SimpleNamespace(orders=[],distance=lambda a,b:float(a*100+b))
    bounded = CycleDescent(fake)
    for a in range(65):
        for b in range(65):
            assert bounded.distance(a,b) == fake.distance(a,b)
    assert bounded.distance.cache_info().currsize == 4096
    problem,_ = directed_cycle()
    first = CycleDescent(problem)
    old = first.distance('A','B')
    network = problem.network
    network.dist[network.node_index['A']][network.node_index['B']] += 100
    second = CycleDescent(problem)
    assert second.distance('A','B') == old+100
    assert second.distance('B','A') == problem.distance('B','A')


def test_arc_reuse_preserves_search_trajectory_with_controlled_clock(monkeypatch):
    problem,_ = directed_cycle()
    # Remove wall-clock cooling/cutoff differences for this equivalence check.
    monkeypatch.setattr(solver.time,'perf_counter',lambda:1000.0)
    trace = []
    from models.routing.solvers.alns.cycle_cache import CycleEvaluator
    evaluate_original = CycleEvaluator.__call__
    def recording(self,problem,state):
        trace.append(state_key(state))
        return evaluate_original(self,problem,state)
    monkeypatch.setattr(CycleEvaluator,'__call__',recording)
    cached = solver.solve_alns(problem,max_time=60,max_iterations=1500,seed=43)
    prefix = list(trace);trace.clear()
    original_init = CycleDescent.__init__
    def uncached(self,problem,capacity=256,**kwargs):
        original_init(self,problem,capacity,cache_distances=False)
        self.distance = lru_cache(maxsize=0)(self.distance)
    monkeypatch.setattr(CycleDescent,'__init__',uncached)
    reference = solver.solve_alns(problem,max_time=60,max_iterations=1500,seed=43)
    assert trace == prefix
    for field in ('e1_trips','e2_trips','audit','cutoff_snapshots','depot_releases'):
        assert cached[field] == reference[field]
    for field in ('objective','incumbent_history','operator_attempts','operator_improvements'):
        assert cached['info'][field] == reference['info'][field]
    assert cached['audit']['valid'] and cached['audit']['periodic_replay']['valid']
