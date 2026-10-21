"""Independent route-level audits for certified Models 1--4 solutions.

The exact MIPs certify their internal decision variables.  This module checks
the *extracted routes* without reading any Gurobi variable, so route extraction,
serialization, assignment and objective-accounting bugs cannot hide behind an
``OPTIMAL`` solver status.
"""

from dataclasses import dataclass
import math
from graphlib import TopologicalSorter, CycleError

from models.common.task_builder import build_tasks_for_model
from models.common.operating_policy import policy_for


@dataclass(frozen=True)
class _TaskSpec:
    node_id: str
    physical_id: str
    customer_id: str
    kind: str
    weight: float
    volume: float
    time_window: tuple
    service_time: float


def _spec(node_id, physical_id, customer_id, kind, weight, volume,
          time_window, service_time):
    return _TaskSpec(
        str(node_id), str(physical_id), str(customer_id), str(kind),
        float(weight), float(volume),
        (float(time_window[0]), float(time_window[1])),
        float(service_time),
    )


def _fixed_specs(problem, model):
    e1_tasks, e2_tasks_by_sat = build_tasks_for_model(problem, model)
    e1 = {}
    for task in e1_tasks:
        # M1's E1 graph uses physical satellite ids; M3 uses logical Sd/Sp ids.
        node_id = task.cust if model == 1 else task.id
        e1[node_id] = _spec(
            node_id, task.cust, task.cust, task.kind, task.w, task.v,
            task.tw, getattr(problem.nodes[task.cust], "service_time", 0.0),
        )
    e2 = {}
    expected_by_sat = {}
    for satellite, tasks in e2_tasks_by_sat.items():
        expected_by_sat[satellite] = set()
        for task in tasks:
            expected_by_sat[satellite].add(task.id)
            e2[task.id] = _spec(
                task.id, task.cust, task.cust, task.kind, task.w, task.v,
                task.tw,
                getattr(problem.nodes[task.cust], "service_time", 0.0),
            )
    return e1, e2, expected_by_sat


def _free_specs(problem, model, assignment, tfp):
    customers = [
        c for c in problem.customers
        if model == 4 or c.demand > 1e-9
    ]
    customer_ids = {c.id for c in customers}
    missing = sorted(customer_ids - set(assignment or {}))
    unknown_satellites = sorted({
        satellite for customer, satellite in (assignment or {}).items()
        if customer in customer_ids and satellite not in {s.id for s in problem.satellites}
    })
    errors = []
    if missing:
        errors.append(f"assignment missing customers: {missing}")
    if unknown_satellites:
        errors.append(f"assignment uses unknown satellites: {unknown_satellites}")

    cw = problem.fleet.container_weight
    cv = problem.fleet.container_volume
    served = {}
    for customer in customers:
        satellite = (assignment or {}).get(customer.id)
        if satellite is not None:
            served.setdefault(satellite, []).append(customer)

    e1 = {}
    e2 = {}
    expected_by_sat = {s.id: set() for s in problem.satellites}
    for satellite, assigned in served.items():
        sat_node = problem.nodes[satellite]
        delivery_weight = sum(c.weight_del + cw for c in assigned)
        delivery_volume = len(assigned) * cv
        delivery_due = min((c.tw_del[1] for c in assigned), default=math.inf)
        del_id = f"{satellite}d"
        e1[del_id] = _spec(
            del_id, satellite, satellite, "d", delivery_weight,
            delivery_volume, (0.0, delivery_due), sat_node.service_time,
        )
        if model == 4:
            pick_id = f"{satellite}p"
            e1[pick_id] = _spec(
                pick_id, satellite, satellite, "p", len(assigned) * cw,
                len(assigned) * cv, (0.0, math.inf), sat_node.service_time,
            )

        for customer in assigned:
            if customer.demand > 1e-9:
                node_id = f"{customer.id}d"
                e2[node_id] = _spec(
                    node_id, customer.id, customer.id, "d",
                    customer.weight_del + cw, cv, customer.tw_del,
                    customer.service_time,
                )
                expected_by_sat[satellite].add(node_id)
            if model == 4:
                node_id = f"{customer.id}p"
                e2[node_id] = _spec(
                    node_id, customer.id, customer.id, "p", cw, cv,
                    customer.tw_pick, customer.service_time,
                )
                expected_by_sat[satellite].add(node_id)
    return e1, e2, expected_by_sat, errors


def _physical(node_id, specs, problem):
    if node_id in specs:
        return specs[node_id].physical_id
    if node_id in problem.nodes:
        return node_id
    raise KeyError(node_id)


def _route_distance(problem, route, specs):
    total = 0.0
    for left, right in zip(route, route[1:]):
        total += problem.distance(
            _physical(left, specs, problem),
            _physical(right, specs, problem),
        )
    return total


def _check_route_shape(route, endpoint, allowed, label, errors):
    if not isinstance(route, (list, tuple)) or len(route) < 3:
        errors.append(f"{label} is not a non-empty closed route: {route!r}")
        return False
    if route[0] != endpoint or route[-1] != endpoint:
        errors.append(f"{label} must start/end at {endpoint}: {route!r}")
    interior = list(route[1:-1])
    unknown = [node for node in interior if node not in allowed]
    if unknown:
        errors.append(f"{label} contains unknown tasks: {unknown}")
    duplicates = sorted({node for node in interior if interior.count(node) > 1})
    if duplicates:
        errors.append(f"{label} repeats tasks: {duplicates}")
    return not unknown


def _check_capacity(route, specs, weight_cap, volume_cap, label, errors, tol):
    tasks = [specs[node] for node in route[1:-1] if node in specs]
    weight = sum(task.weight for task in tasks if task.kind == "d")
    volume = sum(task.volume for task in tasks if task.kind == "d")
    if weight > weight_cap + tol or volume > volume_cap + tol:
        errors.append(
            f"{label} departure capacity exceeded: "
            f"weight={weight:.6g}/{weight_cap:.6g}, "
            f"volume={volume:.6g}/{volume_cap:.6g}"
        )
    for task in tasks:
        sign = -1.0 if task.kind == "d" else 1.0
        weight += sign * task.weight
        volume += sign * task.volume
        if weight < -tol or volume < -tol:
            errors.append(f"{label} has negative load after {task.node_id}")
        if weight > weight_cap + tol or volume > volume_cap + tol:
            errors.append(f"{label} capacity exceeded after {task.node_id}")


def _schedule_route(problem, route, specs, start_time, extra_ready):
    arrivals = {}
    violations = []
    current = float(start_time)
    previous = route[0]
    for node_id in route[1:-1]:
        task = specs[node_id]
        current += problem.travel_time(
            _physical(previous, specs, problem), task.physical_id)
        current = max(
            current,
            task.time_window[0],
            float(extra_ready.get(node_id, 0.0)),
        )
        arrivals[node_id] = current
        if current > task.time_window[1] + 1e-6:
            violations.append(
                f"{node_id} arrives at {current:.6g} after "
                f"{task.time_window[1]:.6g}"
            )
        current += task.service_time
        previous = node_id
    current += problem.travel_time(
        _physical(previous, specs, problem),
        _physical(route[-1], specs, problem),
    )
    return arrivals, current, violations


def _timeline(problem, route, specs, starts, departure, unload):
    """Separate physical arrival, waiting, service, and return unloading."""
    current, previous = departure, route[0]
    events = []
    for node in route[1:-1]:
        task = specs[node]
        arrival = current + problem.travel_time(_physical(previous, specs, problem), task.physical_id)
        start = starts[node]
        current = start + task.service_time
        events.append(dict(node=node, kind=task.kind, physical_id=task.physical_id,
                           arrival=arrival, service_start=start, service_end=current,
                           waiting=max(0.0, start-arrival)))
        previous = node
    returned = current + problem.travel_time(_physical(previous, specs, problem), route[-1])
    return dict(route=list(route), departure=departure, events=events,
                return_arrival=returned, return_complete=returned+unload,
                waiting=sum(e['waiting'] for e in events),
                duration=returned+unload-departure)


def _coverage_errors(actual, expected, label):
    errors = []
    counts = {node: actual.count(node) for node in set(actual)}
    missing = sorted(expected - set(actual))
    extra = sorted(set(actual) - expected)
    repeated = sorted(node for node, count in counts.items() if count != 1)
    if missing:
        errors.append(f"{label} missing tasks: {missing}")
    if extra:
        errors.append(f"{label} unexpected tasks: {extra}")
    if repeated:
        errors.append(f"{label} tasks not covered exactly once: {repeated}")
    return errors


def audit_exact_solution(problem, model, e1_routes, e2_routes_by_sat, *,
                         assignment=None, objective=None, tfp=9999.0,
                         tolerance=1e-5):
    """Audit one extracted Models 1--4 solution and return a JSON-safe report."""
    model = int(model)
    policy = policy_for(problem)
    policy.require_model(model)
    if model not in (1, 2, 3, 4):
        raise ValueError(f"route audit supports Models 1--4, got {model}")

    if model in (1, 3):
        e1_specs, e2_specs, expected_by_sat = _fixed_specs(problem, model)
        setup_errors = []
    else:
        e1_specs, e2_specs, expected_by_sat, setup_errors = _free_specs(
            problem, model, assignment, tfp,
        )

    errors = list(setup_errors)
    e1_routes = list(e1_routes or [])
    e2_routes_by_sat = dict(e2_routes_by_sat or {})
    depot = problem.depot.id
    satellites = {s.id for s in problem.satellites}

    if len(e1_routes) > problem.e1_vehicle_count:
        errors.append(
            f"E1 uses {len(e1_routes)} routes, capacity is "
            f"{problem.e1_vehicle_count}"
        )
    e1_actual = []
    for index, route in enumerate(e1_routes):
        label = f"E1 route {index}"
        if _check_route_shape(route, depot, e1_specs, label, errors):
            e1_actual.extend(route[1:-1])
            _check_capacity(
                route, e1_specs, problem.e1_weight_cap,
                problem.e1_volume_cap, label, errors, tolerance,
            )
            if policy.single_day:
                pickup_seen = False
                for node in route[1:-1]:
                    if e1_specs[node].kind == 'p':
                        pickup_seen = True
                    elif pickup_seen:
                        errors.append(f"{label}: delivery {node} after pickup (single-day backhaul)")
    errors.extend(_coverage_errors(e1_actual, set(e1_specs), "E1"))

    e2_actual_by_sat = {satellite: [] for satellite in satellites}
    for satellite, routes in e2_routes_by_sat.items():
        if satellite not in satellites:
            errors.append(f"E2 output contains unknown satellite {satellite}")
            continue
        routes = list(routes or [])
        if len(routes) > problem.fleet.e2_per_sat_count:
            errors.append(
                f"{satellite} uses {len(routes)} E2 routes, capacity is "
                f"{problem.fleet.e2_per_sat_count}"
            )
        for index, route in enumerate(routes):
            label = f"E2 {satellite} route {index}"
            if _check_route_shape(route, satellite, e2_specs, label, errors):
                e2_actual_by_sat[satellite].extend(route[1:-1])
                if policy.single_day:
                    positions = {n: i for i, n in enumerate(route)}
                    for n in route[1:-1]:
                        task = e2_specs[n]
                        delivery = f'{task.customer_id}d'
                        if task.kind == 'p' and delivery in positions and positions[n] < positions[delivery]:
                            errors.append(f'{label}: {n} precedes its delivery in route order')
                _check_capacity(
                    route, e2_specs, problem.e2_weight_cap,
                    problem.e2_volume_cap, label, errors, tolerance,
                )
    for satellite in sorted(satellites):
        errors.extend(_coverage_errors(
            e2_actual_by_sat[satellite], expected_by_sat.get(satellite, set()),
            f"E2 {satellite}",
        ))
        if policy.single_day:
            # Strict event precedence still matters when all travel/service
            # durations happen to be zero.
            graph = TopologicalSorter()
            for route in e2_routes_by_sat.get(satellite, []):
                for left, right in zip(route[1:-1], route[2:-1]):
                    graph.add(right, left)
            for n in expected_by_sat.get(satellite, set()):
                task = e2_specs[n]
                d = f'{task.customer_id}d'
                if task.kind == 'p' and d in e2_specs:
                    graph.add(n, d)
            try:
                graph.prepare()
            except CycleError:
                errors.append(f'E2 {satellite}: cyclic customer delivery/pickup precedence')

    # Construct a feasible earliest schedule by monotone fixed-point updates.
    # The only cross-route dependencies are E1-delivery -> E2-start,
    # customer-delivery -> customer-pickup and E2-return -> E1-pickup.
    satellite_returns = {satellite: 0.0 for satellite in satellites}
    delivery_completions = {}
    final_e1_arrivals = {}
    final_e2_arrivals = {}
    timing_violations = []
    max_iterations = 12 + 4 * (len(e1_specs) + len(e2_specs))
    converged = False
    for _ in range(max_iterations):
        old_returns = dict(satellite_returns)
        old_deliveries = dict(delivery_completions)
        satellite_ready = {}
        e1_arrivals = {}
        e1_violations = []
        for index, route in enumerate(e1_routes):
            if any(node not in e1_specs for node in route[1:-1]):
                continue
            extra = {}
            for node in route[1:-1]:
                task = e1_specs[node]
                if task.kind == "p":
                    extra[node] = max(
                        satellite_returns.get(task.physical_id, 0.0),
                        tfp if tfp < 9000.0 else 0.0,
                    )
            arrivals, _return, violations = _schedule_route(
                problem, route, e1_specs, policy.dispatch_time, extra,
            )
            e1_arrivals.update(arrivals)
            e1_violations.extend(f"E1 route {index}: {v}" for v in violations)
            for node, arrival in arrivals.items():
                task = e1_specs[node]
                if task.kind == "d":
                    satellite_ready[task.physical_id] = (
                        arrival + task.service_time + policy.e2_dispatch_prep_time
                    )

        new_returns = {satellite: 0.0 for satellite in satellites}
        new_deliveries = {}
        e2_arrivals = {}
        e2_violations = []
        for satellite, routes in e2_routes_by_sat.items():
            if satellite not in satellites:
                continue
            for index, route in enumerate(routes or []):
                if any(node not in e2_specs for node in route[1:-1]):
                    continue
                extra = {
                    node: delivery_completions.get(
                        e2_specs[node].customer_id, 0.0
                    )
                    for node in route[1:-1]
                    if e2_specs[node].kind == "p"
                }
                arrivals, return_time, violations = _schedule_route(
                    problem, route, e2_specs,
                    satellite_ready.get(satellite, policy.dispatch_time), extra,
                )
                e2_arrivals.update(arrivals)
                e2_violations.extend(
                    f"E2 {satellite} route {index}: {v}" for v in violations
                )
                new_returns[satellite] = max(
                    new_returns[satellite], return_time + policy.e2_return_unload_time,
                )
                for node, arrival in arrivals.items():
                    task = e2_specs[node]
                    if task.kind == "d":
                        new_deliveries[task.customer_id] = (
                            arrival + task.service_time
                        )

        final_e1_arrivals = e1_arrivals
        final_e2_arrivals = e2_arrivals
        timing_violations = e1_violations + e2_violations
        satellite_returns = new_returns
        delivery_completions = new_deliveries
        changed = any(
            abs(new_returns.get(key, 0.0) - old_returns.get(key, 0.0))
            > tolerance for key in set(new_returns) | set(old_returns)
        ) or any(
            abs(new_deliveries.get(key, 0.0) - old_deliveries.get(key, 0.0))
            > tolerance for key in set(new_deliveries) | set(old_deliveries)
        )
        if not changed:
            converged = True
            break
    if not converged:
        errors.append("route timing dependencies did not converge")
    errors.extend(timing_violations)

    for node, arrival in final_e2_arrivals.items():
        task = e2_specs[node]
        if task.kind == "p":
            required = delivery_completions.get(task.customer_id)
            if required is not None and arrival < required - tolerance:
                errors.append(
                    f"{node} precedes delivery completion "
                    f"({arrival:.6g} < {required:.6g})"
                )
    for node, arrival in final_e1_arrivals.items():
        task = e1_specs[node]
        if task.kind == "p" and arrival < satellite_returns.get(
                task.physical_id, 0.0) - tolerance:
            errors.append(
                f"{node} precedes E2 return at {task.physical_id}"
            )

    route_objective = 0.0
    try:
        route_objective += sum(
            _route_distance(problem, route, e1_specs) for route in e1_routes
        )
        route_objective += sum(
            _route_distance(problem, route, e2_specs)
            for routes in e2_routes_by_sat.values() for route in (routes or [])
        )
    except KeyError as exc:
        errors.append(f"cannot calculate route objective: unknown node {exc.args[0]}")
        route_objective = None

    objective_delta = None
    if objective is not None and route_objective is not None:
        objective_delta = route_objective - float(objective)
        if abs(objective_delta) > tolerance:
            errors.append(
                f"route objective mismatch: routes={route_objective:.9g}, "
                f"solver={float(objective):.9g}, delta={objective_delta:.6g}"
            )

    report = {
        "valid": not errors,
        "model": model,
        "errors": errors,
        "route_objective": route_objective,
        "solver_objective": objective,
        "objective_delta": objective_delta,
        "e1_route_count": len(e1_routes),
        "e2_route_count": sum(
            len(routes or []) for routes in e2_routes_by_sat.values()
        ),
        "timing_converged": converged,
    }
    if policy.single_day:
        report['operating_policy'] = policy.as_dict(problem)
        # Invalid/unknown nodes must not crash diagnostic serialization.
        e1_timeline = [
            _timeline(problem, r, e1_specs, final_e1_arrivals, policy.dispatch_time,
                      policy.depot_return_unload_time)
            for r in e1_routes if len(r) >= 3 and r[0] == depot and r[-1] == depot
            and all(n in final_e1_arrivals for n in r[1:-1])]
        e2_timeline = {
            s: [_timeline(problem, r, e2_specs, final_e2_arrivals,
                          satellite_ready.get(s, policy.dispatch_time), policy.e2_return_unload_time)
                for r in routes if len(r) >= 3 and r[0] == s and r[-1] == s
                and all(n in final_e2_arrivals for n in r[1:-1])]
            for s, routes in e2_routes_by_sat.items() if s in satellites}
        for label, timelines in [('E1', e1_timeline)] + [
                (f'E2 {s}', rows) for s, rows in e2_timeline.items()]:
            for index, row in enumerate(timelines):
                if row['return_complete'] > policy.day_end + tolerance:
                    errors.append(f"{label} route {index}: return completion {row['return_complete']:.6g} "
                                  f"exceeds day_end {policy.day_end:.6g}")
        count = {c.id: int(c.container_count_pick) for c in problem.customers}
        delivered = sum(count.get(e2_specs[n].customer_id, 0) for n in e2_actual_by_sat_nodes(e2_actual_by_sat)
                        if n in e2_specs and e2_specs[n].kind == 'd')
        collected = sum(count.get(e2_specs[n].customer_id, 0) for n in e2_actual_by_sat_nodes(e2_actual_by_sat)
                        if n in e2_specs and e2_specs[n].kind == 'p')
        by_sat = {s: sum(count[c.id] for c in problem.customers if c.assigned_sat == s)
                  for s in satellites}
        returned = sum(by_sat.get(e1_specs[n].physical_id, 0) for n in e1_actual
                       if n in e1_specs and e1_specs[n].kind == 'p')
        inventory = {}
        for s in sorted(satellites):
            additions = [dict(time=row['return_complete'], quantity=sum(
                count[e2_specs[e['node']].customer_id] for e in row['events'] if e['kind'] == 'p'))
                for row in e2_timeline.get(s, [])]
            pickup = final_e1_arrivals.get(f'{s}p')
            inventory[s] = dict(initial_empty=0, ready_time=satellite_returns[s],
                                pickup_start=pickup, pickup_quantity=by_sat[s],
                                peak_empty=by_sat[s], final_empty=0 if pickup is not None else by_sat[s],
                                inbound_events=additions)
        e2_flat = [row for rows in e2_timeline.values() for row in rows]
        report.update(timeline={'e1': e1_timeline, 'e2': e2_timeline},
                      satellite_inventory=inventory,
                      container_balance=dict(delivered=delivered, collected=collected, returned_to_depot=returned),
                      time_metrics=dict(e1_waiting=sum(r['waiting'] for r in e1_timeline),
                                        e2_waiting=sum(r['waiting'] for r in e2_flat),
                                        e1_duration=sum(r['duration'] for r in e1_timeline),
                                        e2_duration=sum(r['duration'] for r in e2_flat),
                                        completion_time=max([policy.dispatch_time] + [
                                            r['return_complete'] for r in e1_timeline + e2_flat])),
                      e1_delivery_only_routes=sum(all(e['kind'] == 'd' for e in r['events']) for r in e1_timeline),
                      e1_pickup_only_routes=sum(all(e['kind'] == 'p' for e in r['events']) for r in e1_timeline))
        expected = sum(count.values())
        if (delivered, collected, returned) != (expected, expected, expected):
            errors.append('single-day container balance mismatch')
        report['valid'] = not errors
    return report


def e2_actual_by_sat_nodes(actual):
    return (node for nodes in actual.values() for node in nodes)


__all__ = ["audit_exact_solution"]
