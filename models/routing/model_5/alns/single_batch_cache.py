"""Bounded caches owned by one immutable single-batch search context."""
from collections import OrderedDict
from functools import lru_cache

from models.routing.model_5.alns.cycle_cache import CycleEvaluator
from models.routing.model_5.alns.evaluator import evaluate, route_distance
from models.routing.model_5.alns.neighborhoods import EvaluationCache
from models.routing.model_5.alns.single_batch_scoring import SingleBatchRouteScorer
from models.routing.model_5.alns.single_batch_timing import propagate_times


class SingleBatchEvaluator(CycleEvaluator):
    """Prepare task attributes and reuse distances, while decoding times afresh."""
    propagate = staticmethod(propagate_times)
    def __init__(self, problem, capacity=128, *, distance_capacity=512, load_capacity=512):
        if len(problem.operating_policy.batches) != 1:
            raise ValueError('Single-batch cache requires B=1')
        super().__init__(problem, capacity)
        self.task_data = {(o.id, k): problem.task(o.id, k)
                          for o in problem.orders for k in problem.task_kinds}
        self.expected_tasks = frozenset(self.task_data)
        if not isinstance(load_capacity, int) or load_capacity < 0:
            raise ValueError('Load-summary capacity must be a nonnegative integer')
        self.load_summary = (lru_cache(maxsize=load_capacity)(self._load_summary)
                             if load_capacity else None)
        self.distance_capacity = distance_capacity
        self.distances = OrderedDict()
        self.distance_hits = self.distance_misses = 0

    def task(self, oid, kind):
        try:
            return self.task_data[oid, kind]
        except KeyError:
            # Preserve the reference API's error for malformed external tasks.
            return self.problem.task(oid, kind)

    def _load_summary(self, tasks):
        # Only fixed task attributes are cached. Vehicle/site, waits, every
        # event load and all timing/coverage checks remain decoder inputs.
        try:
            delivery = [self.task_data[oid, kind] for oid, kind in tasks if kind == 'd']
        except KeyError:
            # Let the original decoder preserve errors for external tasks.
            return None
        return (frozenset(t[0].batch for t in delivery),
                sum(t[1] for t in delivery), sum(t[2] for t in delivery))

    def __call__(self, problem, state):
        if problem is not self.problem:
            raise ValueError('Single-batch evaluator belongs to one problem instance')
        return evaluate(problem, state, _memo=self, _prepared=self)

    def route_distance(self, problem, trip, layer):
        if problem is not self.problem:
            raise ValueError('Prepared distance belongs to one problem instance')
        field = 'site_id' if layer == 'e1' else 'order_id'
        key = (layer, None if layer == 'e1' else trip['satellite_id'],
               tuple(e[field] for e in trip['events']))
        if key in self.distances:
            self.distance_hits += 1
            self.distances.move_to_end(key)
            return self.distances[key]
        self.distance_misses += 1
        value = route_distance(problem, trip, layer)
        self.distances[key] = value
        if len(self.distances) > self.distance_capacity:
            self.distances.popitem(last=False)
        return value


class SingleBatchCache(EvaluationCache):
    def __init__(self, problem, capacity=256, *, e1_capacity=128, load_capacity=512):
        super().__init__(problem, SingleBatchEvaluator(problem, capacity=e1_capacity,
                         load_capacity=load_capacity), capacity)
        self._route_scorer = None

    @property
    def route_scorer(self):
        # Skip preparation when a short solve never reaches route repair.
        if self._route_scorer is None:
            self._route_scorer = SingleBatchRouteScorer(self.problem)
        return self._route_scorer

    def score_route(self, problem, route, goods, vehicle_ready):
        return self.route_scorer(problem, route, goods, vehicle_ready)
