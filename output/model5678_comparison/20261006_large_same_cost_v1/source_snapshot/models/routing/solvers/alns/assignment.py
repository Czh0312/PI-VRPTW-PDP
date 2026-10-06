"""Customer assignment moves inside one continuous route-search trajectory."""
import math
import random
import time
from collections import OrderedDict

from models.routing.common.cycle.assignment import assigned_problem, input_assignment, lift_result
from models.routing.common.cycle.precheck import check_necessary_conditions
from models.routing.common.cycle.state import empty_result
from .construction import repair_assignment_state
from .single_batch_cache import SingleBatchCache


class AssignmentSearch:
    """Bounded, per-solve partition contexts; no restarted inner ALNS runs."""

    def __init__(self, problem, seed):
        self.problem = problem
        self.rng = random.Random(seed)
        self.customers = sorted(input_assignment(problem))
        self.costs = {c: {s: problem.distance(s, c)+problem.distance(c, s)
                          for s in problem.satellites} for c in self.customers}
        self.nearest = {c: min(self.costs[c], key=self.costs[c].get) for c in self.customers}
        self.contexts = OrderedDict()
        self.active_problem = None
        self.travel_bounds = None
        self.tried_nearest = False
        self.attempts = self.projections = self.accepted = self.improvements = 0
        self.evicted_hits = self.evicted_misses = 0

    def context(self, assignment):
        key = tuple(sorted(assignment.items()))
        if key not in self.contexts:
            fixed = assigned_problem(self.problem, assignment)
            self.contexts[key] = (fixed, SingleBatchCache(fixed, capacity=32, e1_capacity=32))
            if len(self.contexts) > 8:
                old_key = next(k for k, (p, _) in self.contexts.items() if p is not self.active_problem)
                _, old = self.contexts.pop(old_key)
                self.evicted_hits += old.hits
                self.evicted_misses += old.misses
        self.contexts.move_to_end(key)
        return self.contexts[key]

    def propose(self, fixed, state, deadline):
        if not self.customers or len(self.problem.satellites) < 2:
            return None
        current = input_assignment(fixed)
        candidate = dict(current)
        if not self.tried_nearest:
            self.tried_nearest = True
            candidate = dict(self.nearest)
        if candidate == current:
            if len(self.customers) > 1 and self.rng.random() < .25:
                a, b = self.rng.sample(self.customers, 2)
                candidate[a], candidate[b] = candidate[b], candidate[a]
            else:
                # Distance is only proposal ordering, never a feasibility bound.
                sampled = self.rng.sample(self.customers, min(8, len(self.customers)))
                options = [(self.costs[c][s]-self.costs[c][current[c]], c, s)
                           for c in sampled for s in self.problem.satellites if s != current[c]]
                options.sort()
                pool = options if self.rng.random() < .2 else options[:min(3, len(options))]
                _, customer, site = self.rng.choice(pool)
                candidate[customer] = site
        if candidate == current or time.perf_counter() >= deadline:
            return None
        self.attempts += 1
        proposed_problem, cache = self.context(candidate)
        if not check_necessary_conditions(proposed_problem, self.travel_bounds)['passed']:
            return None
        proposed = repair_assignment_state(proposed_problem, state, deadline, scorer=cache.route_scorer)
        if proposed is None:
            return None
        self.projections += 1
        return proposed_problem, cache, proposed

    def finish(self, result, best_problem):
        check = result.pop('precheck', None)
        if result['info'].get('objective') is not None:
            result = lift_result(self.problem, result, input_assignment(best_problem))
        else:
            info = result['info']
            result = empty_result(self.problem, 'alns')
            result['info'].update(info)
        # Failure of the input partition cannot prove free-assignment infeasibility.
        if check is not None:
            result['initial_assignment_precheck'] = check
        result['info'].update(
            proven_optimal=False, proven_infeasible=False, best_bound=None, mip_gap=None,
            proof_scope=None, algorithm='joint_customer_assignment_route_alns',
            assignment_attempts=self.attempts, assignment_route_projections=self.projections,
            assignment_accepted=self.accepted, assignment_improvements=self.improvements,
            assignment_budget_policy='continuous_search_assignment_every_32_iterations',
            evaluation_cache_hits=self.evicted_hits+sum(c.hits for _, c in self.contexts.values()),
            evaluation_cache_misses=self.evicted_misses+sum(c.misses for _, c in self.contexts.values()))
        return result


def solve_alns(problem, max_time=10, max_iterations=5000, seed=42):
    if not math.isfinite(max_time) or max_time <= 0 or max_iterations < 0:
        raise ValueError('Invalid assignment-search budget')
    if not problem.reusable:
        from .partitioned_assignment import solve_alns as solve_delivery
        return solve_delivery(problem, max_time=max_time, max_iterations=max_iterations, seed=seed)
    from .single_batch import solve_alns as solve_routes
    begun = time.perf_counter()
    search = AssignmentSearch(problem, seed)
    fixed, _ = search.context(input_assignment(problem))
    return solve_routes(fixed, max_time=max_time, max_iterations=max_iterations, seed=seed,
                        _assignment_search=search, _begun=begun)
