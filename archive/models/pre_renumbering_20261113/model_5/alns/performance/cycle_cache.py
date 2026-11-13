"""Solve-local memoization of deterministic cycle schedule components.

E2 event caching is opt-in. Cutoff quantities, stock releases and full
feasibility are still recomputed by evaluate.
"""
from collections import OrderedDict

from models.routing.model_5.alns.evaluator import evaluate, _e1_returns


def _copy_schedule(value):
    trips, issues, releases = value
    return ([dict(trip, events=[dict(event) for event in trip['events']]) for trip in trips],
            list(issues), list(releases))


class CycleEvaluator:
    def __init__(self, problem, capacity=256, *, cache_e2_schedule=False):
        if capacity < 1:
            raise ValueError('Cycle cache capacity must be positive')
        self.problem, self.capacity = problem, capacity
        self.entries = OrderedDict()
        self.hits = self.misses = 0
        self.e2_cache = None
        if cache_e2_schedule:
            from models.routing.model_5.alns.performance.e2_schedule_cache import E2ScheduleCache

            self.e2_cache = E2ScheduleCache(problem)

    def __call__(self, problem, state):
        if problem is not self.problem:
            raise ValueError('Cycle evaluator belongs to one problem instance')
        return evaluate(problem, state, _memo=self)

    def returns(self, state, quantities):
        key = tuple((b.id, tuple(map(tuple, state['deliveries'][b.id])),
                     tuple(state['pickup_order'][b.id]),
                     tuple(sorted(state['pickup_vehicle'][b.id].items())),
                     tuple(sorted(quantities[b.id].items())))
                    for b in self.problem.operating_policy.batches)
        if key in self.entries:
            self.hits += 1
            self.entries.move_to_end(key)
            # Results are caller-owned; never expose mutable cached schedules.
            return _copy_schedule(self.entries[key])
        self.misses += 1
        result = _e1_returns(self.problem, state, quantities)
        self.entries[key] = _copy_schedule(result)
        if len(self.entries) > self.capacity:
            self.entries.popitem(last=False)
        return result
