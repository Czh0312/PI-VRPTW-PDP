# models/routing/alns/initial_solution.py
# -*- coding: utf-8 -*-
"""Stronger feasible initial solution builders for ALNS models."""

from models.routing.alns.types import Route, Solution, FEAS_TOL, is_tfp_active


def build_strong_initial_solution(problem, e1_tasks, e2_tasks_by_sat,
                                  evaluator):
    """Build a feasibility-oriented initial solution.

    The legacy constructor mixes first-echelon deliveries and pickups before
    second-echelon routes are known. This builder orders the construction by
    dependency instead:
    1. first-echelon satellite deliveries,
    2. second-echelon customer delivery-pickup routes,
    3. first-echelon satellite pickups after the E2 returns are known.
    """
    depot_id = problem.depot.id

    del_tasks = [t for t in e1_tasks if t.kind == "d"]
    pick_tasks = [t for t in e1_tasks if t.kind == "p"]
    sat_urgency = _satellite_urgency(e2_tasks_by_sat)

    best_feasible = None
    best_any = None
    for e1_delivery_routes in _candidate_e1_delivery_routes(
            problem, evaluator, depot_id, del_tasks, sat_urgency):
        sol = _complete_solution_from_e1_deliveries(
            problem, e2_tasks_by_sat, evaluator, e1_delivery_routes,
            pick_tasks, sat_urgency)
        if best_any is None or sol.cost < best_any.cost - FEAS_TOL:
            best_any = sol
        if evaluator.is_feasible(sol):
            if best_feasible is None or sol.cost < best_feasible.cost - FEAS_TOL:
                best_feasible = sol

    return best_feasible or best_any or Solution()


def _complete_solution_from_e1_deliveries(problem, e2_tasks_by_sat, evaluator,
                                          e1_delivery_routes, pick_tasks,
                                          sat_urgency):
    sol = Solution()
    depot_id = problem.depot.id
    sat_ids = [s.id for s in problem.satellites]
    sol.e1_routes = [route.copy() for route in e1_delivery_routes]

    e2_start_by_sat, _ = evaluator.e1_schedule(sol)
    e2_start_by_sat = e2_start_by_sat or {}

    e2_last_return_by_sat = {}
    for sat_id in sat_ids:
        tasks = list(e2_tasks_by_sat.get(sat_id, []))
        if not tasks:
            sol.e2_routes[sat_id] = []
            continue

        start_time = e2_start_by_sat.get(sat_id, 0.0)
        routes = _build_e2_pair_insertion_routes(
            problem, evaluator, sat_id, tasks, start_time)
        sol.e2_routes[sat_id] = routes

        last_return = 0.0
        for route in routes:
            feasible, _, return_time = evaluator.simulate_e2_route(
                route, sat_id, start_time)
            if feasible and return_time is not None:
                last_return = max(last_return, return_time)
        e2_last_return_by_sat[sat_id] = last_return

    _insert_e1_pickups_after_e2_returns(
        problem, evaluator, depot_id, sol.e1_routes, pick_tasks,
        e2_last_return_by_sat, sat_urgency)

    sol.cost = evaluator.total_cost(sol)
    sol.task_to_phys = dict(evaluator.task_to_phys)
    return sol


def _candidate_e1_delivery_routes(problem, evaluator, depot_id, del_tasks,
                                  sat_urgency):
    seen = set()
    builders = (
        _build_e1_delivery_routes,
        _build_e1_urgent_split_delivery_routes,
        _build_e1_direct_delivery_routes,
    )
    for builder in builders:
        routes = builder(problem, evaluator, depot_id, del_tasks, sat_urgency)
        key = tuple(tuple(route.nodes) for route in routes)
        if key in seen:
            continue
        seen.add(key)
        yield routes


def _build_e1_direct_delivery_routes(problem, evaluator, depot_id, del_tasks,
                                     sat_urgency):
    """Use one direct E1 route per satellite when the fleet permits it.

    This candidate is important on large tight-window instances: a
    capacity-minimal packing can delay a satellite enough to make all of its
    E2 customer routes infeasible, even though direct replenishment is valid.
    """
    max_routes = max(1, getattr(problem, "e1_vehicle_count", 1))
    if len(del_tasks) > max_routes:
        return _build_e1_urgent_split_delivery_routes(
            problem, evaluator, depot_id, del_tasks, sat_urgency)
    ordered = sorted(
        del_tasks,
        key=lambda t: (
            sat_urgency.get(t.cust, float("inf")),
            _task_due(t),
            evaluator.dist(depot_id, t.cust),
        ),
    )
    return [Route(depot_id, [task.id]) for task in ordered]


def _satellite_urgency(e2_tasks_by_sat):
    urgency = {}
    for sat_id, tasks in e2_tasks_by_sat.items():
        due_dates = [
            t.tw[1] for t in tasks
            if t.kind == "d" and t.tw[1] != float("inf")
        ]
        ready_dates = [t.tw[0] for t in tasks if t.kind == "d"]
        if due_dates:
            urgency[sat_id] = min(due_dates)
        elif ready_dates:
            urgency[sat_id] = min(ready_dates)
        else:
            urgency[sat_id] = float("inf")
    return urgency


def _task_due(task):
    return task.tw[1] if task.tw[1] != float("inf") else 1e12


def _route_cost(evaluator, route):
    return evaluator.route_distance(route)


def _try_insert_node(route, node_id, pos, evaluator, check_fn):
    old_cost = _route_cost(evaluator, route)
    route.nodes.insert(pos, node_id)
    ok = check_fn(route)
    new_cost = _route_cost(evaluator, route)
    route.nodes.pop(pos)
    if not ok:
        return None
    return new_cost - old_cost


def _best_distance_insert_position(route, node_id, evaluator, min_pos=0,
                                   max_pos=None):
    if max_pos is None:
        max_pos = len(route.nodes)
    best = None
    old_cost = _route_cost(evaluator, route)
    for pos in range(min_pos, max_pos + 1):
        route.nodes.insert(pos, node_id)
        inc = _route_cost(evaluator, route) - old_cost
        route.nodes.pop(pos)
        if best is None or inc < best[0] - FEAS_TOL:
            best = (inc, pos)
    return best


def _first_pickup_pos(route, evaluator):
    for idx, node_id in enumerate(route.nodes):
        task = evaluator.e1_task_map.get(node_id)
        if task and task.kind == "p":
            return idx
    return len(route.nodes)


def _last_delivery_pos(route, evaluator):
    pos = -1
    for idx, node_id in enumerate(route.nodes):
        task = evaluator.e1_task_map.get(node_id)
        if task and task.kind == "d":
            pos = idx
    return pos


def _build_e1_delivery_routes(problem, evaluator, depot_id, del_tasks,
                              sat_urgency):
    if not del_tasks:
        return []

    orders = [
        sorted(
            del_tasks,
            key=lambda t: (
                sat_urgency.get(t.cust, float("inf")),
                _task_due(t),
                evaluator.dist(depot_id, t.cust),
            ),
        ),
        sorted(
            del_tasks,
            key=lambda t: (_task_due(t), evaluator.dist(depot_id, t.cust)),
        ),
        sorted(del_tasks, key=lambda t: evaluator.dist(depot_id, t.cust)),
    ]

    single_candidates = []
    for order in orders:
        route = Route(depot_id, [t.id for t in order])
        if evaluator.check_e1_route_feasible(route):
            single_candidates.append(route)
    if single_candidates:
        return [min(single_candidates, key=lambda r: _route_cost(evaluator, r))]

    routes = []
    max_routes = max(1, getattr(problem, "e1_vehicle_count", len(del_tasks)))
    for task in orders[0]:
        best = None
        for route in routes:
            max_pos = _first_pickup_pos(route, evaluator)
            for pos in range(max_pos + 1):
                inc = _try_insert_node(
                    route, task.id, pos, evaluator,
                    evaluator.check_e1_route_feasible)
                if inc is None:
                    continue
                if best is None or inc < best[0] - FEAS_TOL:
                    best = (inc, route, pos)

        new_route = Route(depot_id, [task.id])
        if len(routes) < max_routes and evaluator.check_e1_route_feasible(
                new_route):
            inc = _route_cost(evaluator, new_route)
            if best is None or inc < best[0] - FEAS_TOL:
                best = (inc, None, None)

        if best is not None:
            _, route, pos = best
            if route is None:
                routes.append(new_route)
            else:
                route.nodes.insert(pos, task.id)
            continue

        if not routes or len(routes) < max_routes:
            routes.append(Route(depot_id, [task.id]))
        else:
            fallback = min(
                (
                    (_best_distance_insert_position(
                        route, task.id, evaluator, 0,
                        _first_pickup_pos(route, evaluator)),
                     route)
                    for route in routes
                ),
                key=lambda item: item[0][0],
            )
            (_, pos), route = fallback
            route.nodes.insert(pos, task.id)

    return routes


def _build_e1_urgent_split_delivery_routes(problem, evaluator, depot_id,
                                           del_tasks, sat_urgency):
    if not del_tasks:
        return []

    ordered = sorted(
        del_tasks,
        key=lambda t: (
            sat_urgency.get(t.cust, float("inf")),
            _task_due(t),
            evaluator.dist(depot_id, t.cust),
        ),
    )
    max_routes = max(1, getattr(problem, "e1_vehicle_count", len(ordered)))
    routes = []

    for task in ordered:
        new_route = Route(depot_id, [task.id])
        if len(routes) < max_routes and evaluator.check_e1_route_feasible(
                new_route):
            routes.append(new_route)
            continue

        best = None
        for route in routes:
            max_pos = _first_pickup_pos(route, evaluator)
            for pos in range(max_pos + 1):
                inc = _try_insert_node(
                    route, task.id, pos, evaluator,
                    evaluator.check_e1_route_feasible)
                if inc is None:
                    continue
                if best is None or inc < best[0] - FEAS_TOL:
                    best = (inc, route, pos)
        if best is not None:
            _, route, pos = best
            route.nodes.insert(pos, task.id)
        elif routes:
            fallback = min(
                (
                    (_best_distance_insert_position(
                        route, task.id, evaluator, 0,
                        _first_pickup_pos(route, evaluator)),
                     route)
                    for route in routes
                ),
                key=lambda item: item[0][0],
            )
            (_, pos), route = fallback
            route.nodes.insert(pos, task.id)
        else:
            routes.append(new_route)

    return routes


def _build_e2_pair_insertion_routes(problem, evaluator, sat_id, tasks,
                                    start_time):
    by_customer = {}
    for task in tasks:
        by_customer.setdefault(task.cust, {})[task.kind] = task

    items = []
    for cust_id, pair in by_customer.items():
        d_task = pair.get("d")
        p_task = pair.get("p")
        due = _task_due(d_task or p_task)
        ready = (d_task or p_task).tw[0]
        items.append((due, ready, evaluator.dist(sat_id, cust_id), d_task,
                      p_task))
    items.sort(key=lambda item: (item[0], item[1], item[2]))

    routes = []
    max_routes = max(1, getattr(evaluator, "vehicles_per_satellite", 1))
    for _, _, _, d_task, p_task in items:
        task_ids = []
        if d_task is not None:
            task_ids.append(d_task.id)
        if p_task is not None:
            task_ids.append(p_task.id)

        best = _best_e2_item_insert(
            routes, task_ids, evaluator, sat_id, start_time)

        new_route = Route(sat_id, list(task_ids))
        if len(routes) < max_routes and evaluator.check_e2_route_feasible(
                new_route, sat_id, start_time):
            inc = _route_cost(evaluator, new_route)
            if best is None or inc < best[0] - FEAS_TOL:
                best = (inc, None, None)

        if best is not None:
            _, route, positions = best
            if route is None:
                routes.append(new_route)
            else:
                _apply_positions(route, task_ids, positions)
            continue

        if not routes or len(routes) < max_routes:
            routes.append(new_route)
        else:
            route, positions = _least_distance_e2_insert(
                routes, task_ids, evaluator)
            _apply_positions(route, task_ids, positions)

    return routes


def _best_e2_item_insert(routes, task_ids, evaluator, sat_id, start_time):
    best = None
    for route in routes:
        old_cost = _route_cost(evaluator, route)
        for positions in _e2_positions(route, task_ids):
            _apply_positions(route, task_ids, positions)
            try:
                # Cost was already computed for every position. Use that exact
                # value first: a non-improving insertion cannot win even if it
                # passes the more expensive time/load simulation. Keep the
                # original enumeration and tie-breaking, with no shortlists.
                inc = _route_cost(evaluator, route) - old_cost
                if best is not None and inc >= best[0] - FEAS_TOL:
                    continue
                if evaluator.check_e2_route_feasible(route, sat_id, start_time):
                    best = (inc, route, positions)
            finally:
                _remove_inserted(route, positions)
    return best


def _least_distance_e2_insert(routes, task_ids, evaluator):
    best = None
    for route in routes:
        old_cost = _route_cost(evaluator, route)
        for positions in _e2_positions(route, task_ids):
            _apply_positions(route, task_ids, positions)
            inc = _route_cost(evaluator, route) - old_cost
            _remove_inserted(route, positions)
            if best is None or inc < best[0] - FEAS_TOL:
                best = (inc, route, positions)
    return best[1], best[2]


def _e2_positions(route, task_ids):
    n = len(route.nodes)
    if len(task_ids) == 1:
        for pos in range(n + 1):
            yield (pos,)
    else:
        for d_pos in range(n + 1):
            for p_pos in range(d_pos + 1, n + 2):
                yield (d_pos, p_pos)


def _apply_positions(route, task_ids, positions):
    for task_id, pos in sorted(zip(task_ids, positions),
                               key=lambda item: item[1]):
        route.nodes.insert(pos, task_id)


def _remove_inserted(route, positions):
    for pos in sorted(positions, reverse=True):
        route.nodes.pop(pos)


def _insert_e1_pickups_after_e2_returns(problem, evaluator, depot_id, routes,
                                        pick_tasks, min_pickup_time_by_sat,
                                        sat_urgency):
    if not pick_tasks:
        return

    max_routes = max(1, getattr(problem, "e1_vehicle_count", len(routes) + 1))
    ordered = sorted(
        pick_tasks,
        key=lambda t: (
            min_pickup_time_by_sat.get(t.cust, 0.0),
            sat_urgency.get(t.cust, float("inf")),
            evaluator.dist(depot_id, t.cust),
        ),
        reverse=True,
    )

    for task in ordered:
        best = None
        candidate_routes = sorted(
            routes,
            key=lambda r: 0 if f"{task.cust}d" in r.nodes else 1)
        for route in candidate_routes:
            min_pos = _last_delivery_pos(route, evaluator) + 1
            for pos in range(min_pos, len(route.nodes) + 1):
                old_cost = _route_cost(evaluator, route)
                route.nodes.insert(pos, task.id)
                ok = (
                    evaluator.check_e1_route_feasible(route)
                    and _e1_pickups_late_enough(
                        route, evaluator, min_pickup_time_by_sat)
                )
                new_cost = _route_cost(evaluator, route)
                route.nodes.pop(pos)
                if not ok:
                    continue
                inc = new_cost - old_cost
                if best is None or inc < best[0] - FEAS_TOL:
                    best = (inc, route, pos)

        new_route = Route(depot_id, [task.id])
        if len(routes) < max_routes:
            ok = (
                evaluator.check_e1_route_feasible(new_route)
                and _e1_pickups_late_enough(
                    new_route, evaluator, min_pickup_time_by_sat)
            )
            if ok:
                inc = _route_cost(evaluator, new_route)
                if best is None or inc < best[0] - FEAS_TOL:
                    best = (inc, None, None)

        if best is not None:
            _, route, pos = best
            if route is None:
                routes.append(new_route)
            else:
                route.nodes.insert(pos, task.id)
            continue

        if not routes or len(routes) < max_routes:
            routes.append(new_route)
        else:
            fallback = min(
                (
                    (_best_distance_insert_position(
                        route, task.id, evaluator,
                        _last_delivery_pos(route, evaluator) + 1),
                     route)
                    for route in routes
                ),
                key=lambda item: item[0][0],
            )
            (_, pos), route = fallback
            route.nodes.insert(pos, task.id)


def _e1_pickups_late_enough(route, evaluator, min_pickup_time_by_sat):
    from models.routing.common.operating_policy import policy_for
    policy = policy_for(evaluator.problem)
    current_time = policy.dispatch_time
    prev = route.base
    for node_id in route.nodes:
        current_time += evaluator.travel(prev, node_id)
        task = evaluator.e1_task_map.get(node_id)
        if task:
            if current_time < task.tw[0]:
                current_time = task.tw[0]
            if current_time > task.tw[1] + FEAS_TOL:
                return False
            tfp = getattr(evaluator, "TFp", 9999.0)
            if task.kind == "p" and is_tfp_active(tfp):
                if current_time < tfp - FEAS_TOL:
                    return False
            if task.kind == "p":
                min_time = min_pickup_time_by_sat.get(task.cust, 0.0)
                if current_time < min_time - FEAS_TOL:
                    # E1 回收车允许在卫星等待至 E2 全部返回
                    # （与 MIP Link_Pickup 语义一致）。
                    current_time = min_time
            current_time += evaluator.service_time(node_id)
        prev = node_id
    if policy.single_day and route.nodes:
        if (current_time + evaluator.travel(prev, route.base) + policy.depot_return_unload_time
                > policy.day_end + FEAS_TOL):
            return False
    return True
