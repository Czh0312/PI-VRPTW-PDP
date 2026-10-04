"""Bounded caches owned by one immutable single-batch search context."""
from .cycle_cache import CycleEvaluator
from .neighborhoods import EvaluationCache
from .single_batch_scoring import SingleBatchRouteScorer


class SingleBatchCache(EvaluationCache):
    def __init__(self, problem, capacity=256, *, e1_capacity=128):
        if len(problem.operating_policy.batches) != 1:
            raise ValueError('Single-batch cache requires B=1')
        super().__init__(problem, CycleEvaluator(problem, capacity=e1_capacity), capacity)
        self._route_scorer = None

    @property
    def route_scorer(self):
        # Skip preparation when a short solve never reaches route repair.
        if self._route_scorer is None:
            self._route_scorer = SingleBatchRouteScorer(self.problem, score_capacity=512)
        return self._route_scorer

    def score_route(self, problem, route, goods, vehicle_ready):
        return self.route_scorer(problem, route, goods, vehicle_ready)
