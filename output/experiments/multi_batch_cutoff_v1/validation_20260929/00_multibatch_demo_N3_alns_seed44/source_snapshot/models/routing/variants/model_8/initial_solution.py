"""Construct a feasible starting state; never solve or freeze an M3 subproblem."""
from models.routing.alns.initial_solution import build_strong_initial_solution
from models.routing.alns.variants.model_3 import Evaluator as PlainRouteEvaluator
from copy import deepcopy
import time

from .state import Evaluator, state_from_routes


def construct_initial_state(problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite,
                            TFp=9999.0, deadline=float('inf')):
    # Reuse the ordinary route simulator and constructive insertion routine.
    # No call to any solve_exact/solve_alns, no separate optimization budget.
    simulator = PlainRouteEvaluator(problem, e1_tasks, e2_tasks_by_sat, TFp,
                                     vehicles_per_satellite=vehicles_per_satellite)
    solution = build_strong_initial_solution(problem, e1_tasks, e2_tasks_by_sat, simulator)
    routes = ([r.full_path() for r in solution.e1_routes],
              {s: [r.full_path() for r in paths] for s, paths in solution.e2_routes.items()})
    e = Evaluator(problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite, TFp)
    try:
        state = state_from_routes(problem, vehicles_per_satellite, routes)
        e.audit(state)
        return state
    except ValueError as original_error:
        # Bounded constructive insertion fallback for small, tight instances.
        # Partial coverage is checked against exactly the tasks inserted so far;
        # the final state is checked against the complete original instance.
        tasks = [(s, t) for s, ts in e2_tasks_by_sat.items() for t in ts]
        if len(tasks) > 28 or len(routes[0]) > problem.e1_vehicle_count:
            raise original_error
        tasks.sort(key=lambda item: (item[1].tw[1], item[1].tw[0], item[1].id))
        seed = state_from_routes(problem, vehicles_per_satellite,
                                 (routes[0], {s.id: [] for s in problem.satellites}))
        beam, inserted = [seed], {s.id: [] for s in problem.satellites}
        for sat, task in tasks:
            inserted[sat].append(task)
            partial = Evaluator(problem, e1_tasks, inserted, vehicles_per_satellite, TFp)
            candidates = {}
            for old in beam:
                empty_seen = False
                for k, route in enumerate(old['e2_routes'][sat]):
                    if len(route) == 2:
                        if empty_seen:
                            continue
                        empty_seen = True
                    for pos in range(1, len(route)):
                        if time.perf_counter() >= deadline:
                            raise ValueError('可行初始解构造达到总时间预算')
                        trial = deepcopy(old)
                        trial['e2_routes'][sat][k].insert(pos, task.id)
                        try:
                            audit = partial.audit(trial)
                        except ValueError:
                            continue
                        key = tuple((s, tuple(tuple(r) for r in paths)) for s, paths in sorted(trial['e2_routes'].items()))
                        candidates[key] = (audit['objective'], trial)
            if not candidates:
                raise original_error
            beam = [r[1] for r in sorted(candidates.values(), key=lambda r: r[0])[:64]]
        e.audit(beam[0])
        return beam[0]
