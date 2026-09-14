"""Independent route-level audits for certified Models 1--4 solutions.

The exact MIPs certify their internal decision variables.  This module checks
the *extracted routes* without reading any Gurobi variable, so route extraction,
serialization, assignment and objective-accounting bugs cannot hide behind an
``OPTIMAL`` solver status.
"""

from dataclasses import dataclass
import math

from .task_builder import build_tasks_for_model


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
                _check_capacity(
                    route, e2_specs, problem.e2_weight_cap,
                    problem.e2_volume_cap, label, errors, tolerance,
                )
    for satellite in sorted(satellites):
        errors.extend(_coverage_errors(
            e2_actual_by_sat[satellite], expected_by_sat.get(satellite, set()),
            f"E2 {satellite}",
        ))

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
                problem, route, e1_specs, 0.0, extra,
            )
            e1_arrivals.update(arrivals)
            e1_violations.extend(f"E1 route {index}: {v}" for v in violations)
            for node, arrival in arrivals.items():
                task = e1_specs[node]
                if task.kind == "d":
                    satellite_ready[task.physical_id] = (
                        arrival + task.service_time
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
                    satellite_ready.get(satellite, 0.0), extra,
                )
                e2_arrivals.update(arrivals)
                e2_violations.extend(
                    f"E2 {satellite} route {index}: {v}" for v in violations
                )
                new_returns[satellite] = max(
                    new_returns[satellite], return_time,
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

    return {
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


__all__ = ["audit_exact_solution"]
