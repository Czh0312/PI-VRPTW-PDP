"""Exact E2 event reuse within one immutable M5 problem and fixed calendar."""
from collections import OrderedDict

from models.routing.model_5.alns.evaluator import _e2_schedule


def _copy(value):
    trips, failed, issues = value
    return ([dict(trip, events=[dict(event) for event in trip['events']])
             for trip in trips], failed, list(issues))


class E2ScheduleCache:
    """Cache the entire coupled E2 graph, including waits and supply times.

    Customer precedence and vehicle reuse couple E2 routes. Reusing an isolated
    route would be unsafe; this first pilot therefore keys all nonempty routes.
    E1 reverse routes and pickup hints cannot affect E2 times under M5's fixed
    delivery calendar. Their changes may reuse E2, but downstream cutoff,
    cleaning, stock and cost calculations still run in the full evaluator.
    """

    def __init__(self, problem, capacity=128):
        if capacity < 1:
            raise ValueError('E2 schedule cache capacity must be positive')
        if problem.model_number != 5 or len(problem.operating_policy.batches) < 2:
            raise ValueError('E2 schedule cache requires multi-batch M5')
        self.problem, self.capacity = problem, capacity
        self.entries = OrderedDict()
        self.hits = self.misses = 0

    def decode(self, state, goods_ready):
        start = self.problem.operating_policy.cycle_start
        key = (
            tuple((r['satellite'], r['vehicle'], tuple(map(tuple, r['tasks'])),
                   r.get('depart_after', start), r.get('return_after', start))
                  for r in state['e2'] if r['tasks']),
            tuple(sorted(goods_ready.items())),
        )
        if key in self.entries:
            self.hits += 1
            self.entries.move_to_end(key)
            return _copy(self.entries[key])
        self.misses += 1
        value = _e2_schedule(self.problem, state, goods_ready)
        self.entries[key] = _copy(value)
        if len(self.entries) > self.capacity:
            self.entries.popitem(last=False)
        return value
