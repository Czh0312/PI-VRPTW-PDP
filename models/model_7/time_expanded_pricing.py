"""Exact E2 pricing oracle for the M7 time-expanded route master.

The oracle prices one satellite-ready state.  It deliberately solves the
elementary resource-constrained route problem as a MIP, which is slower than a
labeling implementation but is a sound minimal prototype: an ``OPTIMAL``
pricing status proves that state has no omitted negative reduced-cost column.

It is compatible with ``time_expanded_route_master`` because a route column
appears only in these fixed master-row families:

* ``E2_Cov[c,s]``;
* ``E2_VehicleLimit[s]``;
* ``E2_StateUse[s,h]``;
* ``E2_StateCustomer[c,s,h]``.

Future Ryan--Foster branch rows must be supplied here too.  The base oracle is
not itself a global solver; it is one proof component of branch-price.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Dict, Iterable, Mapping, Sequence, Tuple

import gurobipy as gp
from gurobipy import GRB


EPS = 1e-7


@dataclass(frozen=True)
class M7PricingResult:
    """Outcome of one satellite/state pricing solve."""

    sat_id: str
    state_idx: int | None
    ready_time: float
    status_code: int
    status: str
    best_reduced_cost: float | None
    reduced_cost_lower_bound: float | None
    proven_no_negative_column: bool
    columns: Tuple[dict, ...]
    runtime: float


def price_m7_ready_state(
    problem,
    sat_id: str,
    ready_time: float,
    *,
    state_idx: int | None = None,
    cover_duals: Mapping[Tuple[str, str], float],
    vehicle_dual: float,
    state_use_dual: float,
    state_customer_duals: Mapping[str, float],
    excluded_paths: Iterable[Sequence[str]] = (),
    same_customer_pairs: Iterable[Tuple[str, str]] = (),
    different_customer_pairs: Iterable[Tuple[str, str]] = (),
    pair_branch_duals: Mapping[Tuple[str, str], float] | None = None,
    time_limit: float | None = None,
    output_flag: bool = False,
    pool_solutions: int = 1,
    eps: float = EPS,
) -> M7PricingResult:
    """Solve the elementary M7 route-pricing problem at a fixed ready time.

    ``same_customer_pairs`` and ``different_customer_pairs`` encode inherited
    Ryan--Foster branches.  ``pair_branch_duals`` contains the dual price of a
    master row whose coefficient is one exactly when both customers occur on
    the route; it is included in the reduced cost.  This keeps the interface
    ready for an external branch-price tree without pretending that branching
    is already implemented here.

    ``excluded_paths`` contains routes already present in the current RMP at
    this same ``(sat_id, state_idx)``.  Their variables can legitimately have
    negative reduced cost while sitting at their upper bound, especially for
    an interior barrier LP solution.  Exact no-good constraints therefore
    make this oracle search for an *omitted* negative column rather than
    rediscovering a known route indefinitely.
    """

    customer_by_id = {customer.id: customer for customer in problem.customers}
    if sat_id not in {sat.id for sat in problem.satellites}:
        raise ValueError(f"unknown satellite {sat_id!r}")
    if ready_time < -eps:
        raise ValueError("ready_time must be non-negative")

    task_ids = tuple(f"{customer_id}d" for customer_id in customer_by_id)
    customer_ids = tuple(customer_by_id)
    task_for_customer = {customer_id: f"{customer_id}d" for customer_id in customer_ids}
    nodes = (sat_id,) + task_ids
    same_pairs = _canonical_pairs(same_customer_pairs, customer_by_id)
    different_pairs = _canonical_pairs(different_customer_pairs, customer_by_id)
    if set(same_pairs) & set(different_pairs):
        raise ValueError("a customer pair cannot be both together and separate")
    branch_duals = {
        _canonical_pair(pair[0], pair[1]): float(dual)
        for pair, dual in (pair_branch_duals or {}).items()
    }
    for first, second in branch_duals:
        if first not in customer_by_id or second not in customer_by_id or first == second:
            raise ValueError(f"branch dual references an invalid customer pair {(first, second)!r}")

    horizon, big_m = _safe_time_bounds(problem, ready_time)
    model = gp.Model(f"M7_TimeExpandedPricing_{sat_id}_{ready_time:.9g}")
    model.setParam("OutputFlag", 1 if output_flag else 0)
    model.setParam("MIPGap", 0.0)
    if time_limit is not None:
        model.setParam("TimeLimit", float(time_limit))
    if pool_solutions > 1:
        model.setParam("PoolSearchMode", 2)
        model.setParam("PoolSolutions", int(pool_solutions))

    y = model.addVars(task_ids, vtype=GRB.BINARY, name="y")
    z = model.addVars(
        ((i, j) for i in nodes for j in nodes if i != j),
        vtype=GRB.BINARY,
        name="z",
    )

    seen_excluded_paths: set[Tuple[str, ...]] = set()
    for excluded_idx, raw_path in enumerate(excluded_paths):
        path = tuple(str(node_id) for node_id in raw_path)
        if path in seen_excluded_paths:
            continue
        seen_excluded_paths.add(path)
        if len(path) < 3 or path[0] != sat_id or path[-1] != sat_id:
            raise ValueError(f"invalid excluded route for {sat_id!r}: {path!r}")
        edges = tuple(zip(path, path[1:]))
        if any(edge not in z for edge in edges):
            raise ValueError(f"excluded route uses an invalid pricing arc: {path!r}")
        # With exactly one departure/return at the satellite and elementary
        # customer visits, retaining every edge of an existing route forces
        # precisely that route.  Removing at least one edge is thus an exact
        # no-good cut, not a relaxation or heuristic filter.
        model.addConstr(
            gp.quicksum(z[edge] for edge in edges) <= len(edges) - 1,
            f"ExcludeKnown_{excluded_idx}",
        )
    arrival = model.addVars(nodes, lb=0.0, ub=horizon, name="arrival")
    load = model.addVars(nodes, lb=0.0, ub=problem.e2_weight_cap, name="load")
    volume = model.addVars(nodes, lb=0.0, ub=problem.e2_volume_cap, name="volume")
    order = model.addVars(task_ids, lb=0.0, ub=len(task_ids), name="order")
    pair_used = model.addVars(tuple(branch_duals), vtype=GRB.BINARY, name="pair_used")

    model.addConstr(arrival[sat_id] == float(ready_time), "ReadyTime")
    model.addConstr(gp.quicksum(z[sat_id, task_id] for task_id in task_ids) == 1, "StartOnce")
    model.addConstr(gp.quicksum(z[task_id, sat_id] for task_id in task_ids) == 1, "ReturnOnce")

    for task_id in task_ids:
        customer = customer_by_id[task_id[:-1]]
        incoming = gp.quicksum(z[i, task_id] for i in nodes if i != task_id)
        outgoing = gp.quicksum(z[task_id, j] for j in nodes if j != task_id)
        model.addConstr(incoming == y[task_id], f"In_{task_id}")
        model.addConstr(outgoing == y[task_id], f"Out_{task_id}")
        earliest, latest = customer.tw_del
        model.addConstr(arrival[task_id] >= float(earliest) * y[task_id], f"TWlo_{task_id}")
        task_horizon = float(latest) if isfinite(float(latest)) else horizon
        model.addConstr(arrival[task_id] <= task_horizon * y[task_id], f"TWhi_{task_id}")
        model.addConstr(order[task_id] >= y[task_id], f"OrderLo_{task_id}")
        model.addConstr(order[task_id] <= len(task_ids) * y[task_id], f"OrderUsed_{task_id}")
        model.addConstr(load[task_id] <= problem.e2_weight_cap * y[task_id], f"LoadUsed_{task_id}")
        model.addConstr(volume[task_id] <= problem.e2_volume_cap * y[task_id], f"VolumeUsed_{task_id}")

    for i in task_ids:
        for j in task_ids:
            if i == j:
                continue
            model.addConstr(
                order[j] >= order[i] + 1 - len(task_ids) * (1 - z[i, j]),
                f"MTZ_{i}_{j}",
            )
    for task_id in task_ids:
        model.addConstr(
            order[task_id] >= 1 - len(task_ids) * (1 - z[sat_id, task_id]),
            f"StartOrder_{task_id}",
        )

    container_weight = float(problem.fleet.container_weight)
    container_volume = float(problem.fleet.container_volume)
    model.addConstr(
        load[sat_id] == gp.quicksum(
            (float(customer_by_id[task_id[:-1]].weight_del) + container_weight) * y[task_id]
            for task_id in task_ids
        ),
        "InitialWeight",
    )
    model.addConstr(
        volume[sat_id] == gp.quicksum(container_volume * y[task_id] for task_id in task_ids),
        "InitialVolume",
    )

    for i in nodes:
        previous_customer = None if i == sat_id else customer_by_id[i[:-1]]
        service = 0.0 if previous_customer is None else float(previous_customer.service_time)
        physical_i = sat_id if previous_customer is None else previous_customer.id
        for j in task_ids:
            if i == j:
                continue
            customer_j = customer_by_id[j[:-1]]
            travel = float(problem.travel_time(physical_i, customer_j.id))
            earliest_i = float(ready_time) if previous_customer is None else float(previous_customer.tw_del[0])
            latest_j = float(customer_j.tw_del[1])
            if isfinite(latest_j) and earliest_i + service + travel > latest_j + eps:
                z[i, j].UB = 0.0
                continue
            model.addConstr(
                arrival[j] >= arrival[i] + service + travel - big_m * (1 - z[i, j]),
                f"Time_{i}_{j}",
            )
            demand_weight = float(customer_j.weight_del) + container_weight
            model.addConstr(
                load[j] <= load[i] - demand_weight + (problem.e2_weight_cap + demand_weight) * (1 - z[i, j]),
                f"LoadUp_{i}_{j}",
            )
            model.addConstr(
                load[j] >= load[i] - demand_weight - (problem.e2_weight_cap + demand_weight) * (1 - z[i, j]),
                f"LoadLo_{i}_{j}",
            )
            model.addConstr(
                volume[j] <= volume[i] - container_volume + (problem.e2_volume_cap + container_volume) * (1 - z[i, j]),
                f"VolumeUp_{i}_{j}",
            )
            model.addConstr(
                volume[j] >= volume[i] - container_volume - (problem.e2_volume_cap + container_volume) * (1 - z[i, j]),
                f"VolumeLo_{i}_{j}",
            )

    for first, second in same_pairs:
        model.addConstr(y[task_for_customer[first]] == y[task_for_customer[second]], f"RF_Same_{first}_{second}")
    for first, second in different_pairs:
        model.addConstr(y[task_for_customer[first]] + y[task_for_customer[second]] <= 1, f"RF_Diff_{first}_{second}")
    for first, second in branch_duals:
        first_y = y[task_for_customer[first]]
        second_y = y[task_for_customer[second]]
        model.addConstr(pair_used[first, second] <= first_y, f"PairUB1_{first}_{second}")
        model.addConstr(pair_used[first, second] <= second_y, f"PairUB2_{first}_{second}")
        model.addConstr(pair_used[first, second] >= first_y + second_y - 1, f"PairLB_{first}_{second}")

    route_cost = gp.quicksum(
        float(problem.distance(
            sat_id if i == sat_id else customer_by_id[i[:-1]].id,
            sat_id if j == sat_id else customer_by_id[j[:-1]].id,
        )) * z[i, j]
        for i, j in z.keys()
    )
    cover_reward = gp.quicksum(
        float(cover_duals.get((customer_id, sat_id), 0.0)) * y[task_for_customer[customer_id]]
        for customer_id in customer_ids
    )
    state_customer_reward = gp.quicksum(
        float(state_customer_duals.get(customer_id, 0.0))
        * y[task_for_customer[customer_id]]
        for customer_id in customer_ids
    )
    pair_reward = gp.quicksum(
        branch_duals[pair] * pair_used[pair] for pair in branch_duals
    )
    model.setObjective(
        route_cost
        - cover_reward
        - float(vehicle_dual)
        - float(state_use_dual)
        - state_customer_reward
        - pair_reward,
        GRB.MINIMIZE,
    )
    model.optimize()

    status = _status_name(model.status)
    best_rc = float(model.ObjVal) if model.SolCount else None
    rc_bound = float(model.ObjBound) if model.status != GRB.INFEASIBLE else float("inf")
    # At a MIP time limit, Gurobi's ``ObjBound`` remains a valid global lower
    # bound for this minimisation pricing problem.  A non-negative bound is
    # already a mathematical certificate that no omitted negative-reduced-
    # cost route exists; insisting on an OPTIMAL status would needlessly keep
    # a branch node open even after the desired conclusion is proved.
    proven_none = (
        model.status == GRB.INFEASIBLE
        or (
            model.status in {GRB.OPTIMAL, GRB.TIME_LIMIT}
            and isfinite(rc_bound)
            and rc_bound >= -eps
        )
    )
    columns = _extract_negative_columns(
        problem,
        model,
        z,
        nodes,
        sat_id,
        customer_by_id,
        ready_time,
        state_idx,
        cover_duals,
        vehicle_dual,
        state_use_dual,
        state_customer_duals,
        branch_duals,
        pair_used,
        pool_solutions=pool_solutions,
        eps=eps,
    )
    return M7PricingResult(
        sat_id=sat_id,
        state_idx=state_idx,
        ready_time=float(ready_time),
        status_code=int(model.status),
        status=status,
        best_reduced_cost=best_rc,
        reduced_cost_lower_bound=rc_bound,
        proven_no_negative_column=proven_none,
        columns=tuple(columns),
        runtime=float(model.Runtime),
    )


def _extract_negative_columns(
    problem,
    model,
    z,
    nodes: Sequence[str],
    sat_id: str,
    customer_by_id,
    ready_time: float,
    state_idx: int | None,
    cover_duals,
    vehicle_dual: float,
    state_use_dual: float,
    state_customer_duals,
    branch_duals,
    pair_used,
    *,
    pool_solutions: int,
    eps: float,
) -> list[dict]:
    if not model.SolCount:
        return []
    columns: list[dict] = []
    seen = set()
    for solution_number in range(min(int(model.SolCount), max(1, int(pool_solutions)))):
        if solution_number:
            model.setParam("SolutionNumber", solution_number)
            selected = {key: variable.Xn for key, variable in z.items()}
            pair_values = {key: variable.Xn for key, variable in pair_used.items()}
        else:
            selected = {key: variable.X for key, variable in z.items()}
            pair_values = {key: variable.X for key, variable in pair_used.items()}
        path = _path_from_arcs(selected, nodes, sat_id)
        if path is None or path in seen:
            continue
        seen.add(path)
        task_ids = path[1:-1]
        cost = _path_distance(problem=problem, sat_id=sat_id, path=path)
        reduced_cost = (
            cost
            - sum(float(cover_duals.get((task_id[:-1], sat_id), 0.0)) for task_id in task_ids)
            - float(vehicle_dual)
            - float(state_use_dual)
            - sum(float(state_customer_duals.get(task_id[:-1], 0.0)) for task_id in task_ids)
            - sum(float(branch_duals[pair]) * pair_values.get(pair, 0.0) for pair in branch_duals)
        )
        if reduced_cost < -eps:
            columns.append({
                "sat_id": sat_id,
                "path": path,
                "tasks": task_ids,
                "cost": cost,
                "ready_time": float(ready_time),
                "state_idx": state_idx,
                "source": "time_expanded_pricing",
                "reduced_cost": reduced_cost,
            })
    return columns


def _path_from_arcs(selected, nodes: Sequence[str], sat_id: str) -> Tuple[str, ...] | None:
    path = [sat_id]
    current = sat_id
    visited = {sat_id}
    while True:
        next_node = next(
            (node for node in nodes if node != current and selected.get((current, node), 0.0) > 0.5),
            None,
        )
        if next_node is None:
            return None
        if next_node == sat_id:
            path.append(sat_id)
            return tuple(path) if len(path) >= 3 else None
        if next_node in visited:
            return None
        path.append(next_node)
        visited.add(next_node)
        current = next_node


def _path_distance(*, problem, sat_id: str, path: Sequence[str]) -> float:
    """Distance of a task-ID path, using the instance's canonical matrix."""

    total = 0.0
    previous = sat_id
    for task_id in path[1:]:
        current = sat_id if task_id == sat_id else task_id[:-1]
        total += float(problem.distance(previous, current))
        previous = current
    return total


def _safe_time_bounds(problem, ready_time: float) -> Tuple[float, float]:
    finite = [float(ready_time), 0.0]
    for customer in problem.customers:
        finite.append(float(customer.tw_del[0]))
        if isfinite(float(customer.tw_del[1])):
            finite.append(float(customer.tw_del[1]))
    locations = [problem.depot.id] + [sat.id for sat in problem.satellites] + [c.id for c in problem.customers]
    max_arc = max(
        (float(problem.travel_time(i, j)) for i in locations for j in locations),
        default=0.0,
    )
    services = [float(getattr(node, "service_time", 0.0)) for node in problem.nodes.values()]
    total_service = sum(services)
    max_service = max(services, default=0.0)
    horizon = max(finite) + (len(problem.customers) + 2) * max_arc + total_service + 1.0
    # For an inactive arc i->j, A[i] can be at its upper bound while A[j]=0.
    # Its time row must therefore absorb *both* service at i and travel.
    return horizon, horizon + max_service + max_arc


def _canonical_pairs(pairs: Iterable[Tuple[str, str]], customer_by_id) -> Tuple[Tuple[str, str], ...]:
    result = set()
    for first, second in pairs:
        pair = _canonical_pair(first, second)
        if first not in customer_by_id or second not in customer_by_id or first == second:
            raise ValueError(f"invalid customer pair {pair!r}")
        result.add(pair)
    return tuple(sorted(result))


def _canonical_pair(first: str, second: str) -> Tuple[str, str]:
    return (first, second) if first < second else (second, first)


def _status_name(status_code: int) -> str:
    names = {
        GRB.OPTIMAL: "OPTIMAL",
        GRB.TIME_LIMIT: "TIME_LIMIT",
        GRB.INFEASIBLE: "INFEASIBLE",
        GRB.INF_OR_UNBD: "INF_OR_UNBD",
        GRB.INTERRUPTED: "INTERRUPTED",
        GRB.SUBOPTIMAL: "SUBOPTIMAL",
        GRB.NUMERIC: "NUMERIC",
    }
    return names.get(status_code, str(status_code))
