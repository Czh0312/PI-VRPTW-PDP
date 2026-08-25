"""Exact E2-program pricing at one fixed M4 ``(h, p)`` state.

The restricted global master uses *satellite programs* rather than individual
E2 routes: delivery and pickup for a customer may belong to different E2
vehicles, and the resulting cross-route precedence is part of M4 feasibility.
This module prices one such complete program with a MIP.  It is deliberately
limited to an already existing finite ``(h, p)`` state; discovering missing
states and branching remain responsibilities of the future outer B&P loop.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from math import isfinite
from typing import Mapping, Sequence

import gurobipy as gp
from gurobipy import GRB

from models.model_4.certified_time_expansion import M4SatelliteProgram, make_m4_satellite_program


EPS = 1e-7


@dataclass(frozen=True)
class M4ProgramPricingResult:
    """Result of pricing one satellite program at an existing state."""

    status: str
    # ``pricing_optimal`` means the requested pricing subproblem (minimum-RC
    # or negative-column feasibility) completed.  It does not by itself mean
    # that no negative column exists; inspect ``pricing_closed`` for that.
    pricing_optimal: bool
    pricing_closed: bool
    reduced_cost: float | None
    reduced_cost_bound: float | None
    program: M4SatelliteProgram | None
    # Validated negative columns from Gurobi's exact solution pool.  ``program``
    # remains the best one for backward-compatible callers.
    programs: tuple[M4SatelliteProgram, ...]
    program_reduced_costs: tuple[float, ...]
    selected_customers: tuple[str, ...]
    runtime: float | None


def price_m4_satellite_program(
    problem,
    sat_id: str,
    *,
    delivery_ready_time: float,
    pickup_ready_time: float,
    customer_rc_terms: Mapping[str, float],
    state_rc_term: float = 0.0,
    vehicles_per_satellite: int | None = None,
    customer_ids: Sequence[str] | None = None,
    required_customer_ids: Sequence[str] = (),
    forbidden_customer_ids: Sequence[str] = (),
    required_arcs: Sequence[tuple[str, str]] = (),
    forbidden_arcs: Sequence[tuple[str, str]] = (),
    excluded_programs: Sequence[M4SatelliteProgram] = (),
    time_limit: float | None = 30.0,
    output_flag: bool = False,
    gurobi_params: Mapping[str, object] | None = None,
    solution_pool_size: int = 1,
    prove_no_negative_only: bool = False,
    reduced_cost_tolerance: float = EPS,
    subtour_strengthening_size: int = 0,
    warm_start_program: M4SatelliteProgram | None = None,
    required_vehicle_count: int | None = None,
) -> M4ProgramPricingResult:
    """Minimize the exact reduced-cost expression for one fixed state.

    ``customer_rc_terms[c]`` is the coefficient added to the reduced cost
    when customer ``c`` is included in the program.  It is deliberately
    expressed as a coefficient, rather than called a dual/reward, to avoid
    hidden sign conventions.  ``state_rc_term`` is the constant coefficient
    of selecting any program at this state.  The MIP requires a nonempty
    program; callers discard the result unless its returned reduced cost is
    negative.

    ``required_customer_ids`` and ``forbidden_customer_ids`` are exact
    branch-node restrictions on this program.  They are intentionally scoped
    to a single priced column; fixing ``w[c,s]=1`` in the global master does
    *not* imply every fractional LP column at ``s`` must include ``c``.

    ``required_arcs`` and ``forbidden_arcs`` are physical E2-arc branch
    restrictions. A required arc must be used by exactly one E2 vehicle;
    a forbidden arc is unavailable to every vehicle.

    ``excluded_programs`` supplies exact labelled no-good cuts for columns
    already in the restricted master at this same state.  This prevents an
    existing variable at its upper bound from being rediscovered as a
    misleading negative reduced-cost "new" column.

    The returned column is a validated :class:`M4SatelliteProgram`: all E2
    routes start after ``h``, return no later than ``p``, and customer d→p
    precedence is enforced even when the two tasks use different vehicles.
    With ``solution_pool_size > 1`` the result can additionally contain the
    best available *negative* distinct programs.  The pool is an acceleration
    only: closing still relies on the best-pricing MIP being ``OPTIMAL``.

    With ``prove_no_negative_only=True``, the MIP instead asks whether a
    column with ``rc <= -reduced_cost_tolerance`` exists.  Infeasibility is a
    complete pricing-closure proof, while any feasible witness is a valid
    negative column.  This is equivalent to minimization for column
    generation and can be substantially easier at the final closure round.
    """

    if pickup_ready_time + EPS < delivery_ready_time:
        raise ValueError("pickup-ready time must not precede delivery-ready time")
    satellite_ids = {sat.id for sat in problem.satellites}
    if sat_id not in satellite_ids:
        raise ValueError(f"unknown satellite {sat_id!r}")
    all_customers = {customer.id: customer for customer in problem.customers}
    C = tuple(customer_ids) if customer_ids is not None else tuple(all_customers)
    if not C:
        raise ValueError("pricing needs at least one customer candidate")
    if len(set(C)) != len(C) or any(customer_id not in all_customers for customer_id in C):
        raise ValueError("customer_ids must be distinct customer identifiers in the problem")
    required = {str(customer_id) for customer_id in required_customer_ids}
    forbidden = {str(customer_id) for customer_id in forbidden_customer_ids}
    if required & forbidden:
        raise ValueError("a customer cannot be both required and forbidden")
    if not required <= set(C) or not forbidden <= set(C):
        raise ValueError("branch restrictions must use customers in customer_ids")
    vehicle_count = int(vehicles_per_satellite or problem.fleet.e2_per_sat_count)
    if vehicle_count <= 0:
        raise ValueError("vehicles_per_satellite must be positive")
    if int(solution_pool_size) <= 0:
        raise ValueError("solution_pool_size must be positive")
    if float(reduced_cost_tolerance) < 0.0:
        raise ValueError("reduced_cost_tolerance must be nonnegative")
    if int(subtour_strengthening_size) not in {0, 2, 3}:
        raise ValueError("subtour_strengthening_size must be 0, 2, or 3")
    if required_vehicle_count is not None:
        if not 1 <= int(required_vehicle_count) <= vehicle_count:
            raise ValueError("required_vehicle_count must be between 1 and the E2 fleet size")
    if warm_start_program is not None:
        if warm_start_program.sat_id != sat_id:
            raise ValueError("warm-start program belongs to a different satellite")
        if abs(float(warm_start_program.ready_time) - float(delivery_ready_time)) > EPS:
            raise ValueError("warm-start program has a different delivery-ready state")
        if abs(float(warm_start_program.pickup_ready_time) - float(pickup_ready_time)) > EPS:
            raise ValueError("warm-start program has a different pickup-ready state")

    cw = float(problem.fleet.container_weight)
    cv = float(problem.fleet.container_volume)
    cust_w = {c: float(all_customers[c].weight_del) + cw for c in C}
    tasks = tuple(task for c in C for task in (f"{c}d", f"{c}p"))
    task_customer = {task: task[:-1] for task in tasks}
    V = tuple(range(vehicle_count))
    nodes = (sat_id,) + tasks
    required_arc_set = {(str(i), str(j)) for i, j in required_arcs}
    forbidden_arc_set = {(str(i), str(j)) for i, j in forbidden_arcs}
    if required_arc_set & forbidden_arc_set:
        raise ValueError("an E2 arc cannot be both required and forbidden")
    for i, j in required_arc_set | forbidden_arc_set:
        if i not in nodes or j not in nodes or i == j:
            raise ValueError("branch E2 arcs must be distinct valid nodes at this satellite")
    task_lower = {}
    node_upper = {sat_id: float(delivery_ready_time)}
    for task in tasks:
        lower, upper = (
            all_customers[task_customer[task]].tw_del
            if task.endswith("d") else all_customers[task_customer[task]].tw_pick
        )
        task_lower[task] = float(lower)
        node_upper[task] = min(
            float(pickup_ready_time),
            float(upper) if isfinite(float(upper)) else float(pickup_ready_time),
        )

    model = gp.Model(f"M4_TwoStateProgramPricing_{sat_id}")
    model.setParam("OutputFlag", 1 if output_flag else 0)
    # ``None`` deliberately leaves Gurobi's default infinite TimeLimit in
    # place.  This is used by the resumable 10-customer exact campaign; a
    # numeric value retains the bounded-probe behaviour used elsewhere.
    if time_limit is not None:
        model.setParam("TimeLimit", float(time_limit))
    model.setParam("MIPGap", 0.0)
    model.setParam("IntFeasTol", 1e-9)
    model.setParam("FeasibilityTol", 1e-9)
    # Keep the certificate contract independent of tuning: a parameter may
    # make pricing faster, but a column is accepted only on GRB.OPTIMAL.
    # This mirrors the strict full-MIP entry point and makes experiments
    # reproducible instead of baking a machine-specific choice into pricing.
    if gurobi_params:
        for name, value in gurobi_params.items():
            model.setParam(name, value)
    if solution_pool_size > 1:
        # Search for several distinct feasible programs during the same exact
        # B&B.  PoolSearchMode=2 remains a complete MIP solve; we only use
        # members with independently negative reduced cost.
        model.setParam("PoolSearchMode", 2)
        model.setParam("PoolSolutions", int(solution_pool_size))

    selected = model.addVars(C, vtype=GRB.BINARY, name="selected")
    z = model.addVars(
        [(i, j, v) for v in V for i in nodes for j in nodes if i != j],
        vtype=GRB.BINARY,
        name="z",
    )
    # A pickup of a customer cannot precede that customer's delivery on the
    # same E2 vehicle.  Cross-vehicle d→p precedence remains allowed and is
    # imposed below, so this removes only physically impossible arcs.
    for c in C:
        for v in V:
            z[f"{c}p", f"{c}d", v].UB = 0.0
    # Explicitly remove arcs that cannot satisfy the destination time upper
    # bound even when their source starts at its earliest possible time.
    # Presolve can infer many of these, but fixing their binary upper bounds
    # ahead of branching considerably reduces the route-connectivity search.
    for i in nodes:
        earliest_i = float(delivery_ready_time) if i == sat_id else task_lower[i]
        service_i = 0.0 if i == sat_id else float(all_customers[task_customer[i]].service_time)
        physical_i = sat_id if i == sat_id else task_customer[i]
        for j in tasks:
            if i == j:
                continue
            physical_j = task_customer[j]
            travel_ij = float(problem.travel_time(physical_i, physical_j))
            if earliest_i + service_i + travel_ij > node_upper[j] + EPS:
                for v in V:
                    z[i, j, v].UB = 0.0
    arrival = {
        (node, v): model.addVar(lb=0.0, ub=node_upper[node], name=f"A_{node}_{v}")
        for node in nodes for v in V
    }
    load = model.addVars(nodes, V, lb=0.0, ub=problem.e2_weight_cap, name="Q")
    volume = model.addVars(nodes, V, lb=0.0, ub=problem.e2_volume_cap, name="Vol")
    order = model.addVars(tasks, V, lb=0.0, ub=float(len(tasks)), name="order")

    _add_excluded_program_cuts(
        model,
        z,
        selected,
        sat_id,
        tasks,
        V,
        delivery_ready_time,
        pickup_ready_time,
        excluded_programs,
    )
    for i, j in required_arc_set:
        model.addConstr(
            gp.quicksum(z[i, j, v] for v in V) == 1.0,
            f"BranchRequiredArc_{i}_{j}",
        )
    for i, j in forbidden_arc_set:
        model.addConstr(
            gp.quicksum(z[i, j, v] for v in V) == 0.0,
            f"BranchForbiddenArc_{i}_{j}",
        )

    model.addConstr(gp.quicksum(selected[c] for c in C) >= 1.0, "NonemptyProgram")
    for c in required:
        model.addConstr(selected[c] == 1.0, f"BranchRequired_{c}")
    for c in forbidden:
        model.addConstr(selected[c] == 0.0, f"BranchForbidden_{c}")
    served_task: dict[tuple[str, int], gp.LinExpr] = {}
    for task in tasks:
        c = task_customer[task]
        for v in V:
            served_task[task, v] = gp.quicksum(z[i, task, v] for i in nodes if i != task)
        model.addConstr(
            gp.quicksum(served_task[task, v] for v in V) == selected[c],
            f"Cover_{task}",
        )

    used_by_vehicle = {}
    for v in V:
        used = gp.quicksum(z[sat_id, task, v] for task in tasks)
        used_by_vehicle[v] = used
        model.addConstr(
            used == gp.quicksum(z[task, sat_id, v] for task in tasks),
            f"DepotBalance_{v}",
        )
        model.addConstr(used <= 1.0, f"VehicleStart_{v}")
        model.addConstr(arrival[sat_id, v] == float(delivery_ready_time), f"SatelliteReady_{v}")
        for task in tasks:
            incoming = served_task[task, v]
            outgoing = gp.quicksum(z[task, j, v] for j in nodes if j != task)
            model.addConstr(incoming == outgoing, f"Flow_{task}_{v}")
            model.addConstr(order[task, v] >= incoming, f"OrderUsedLo_{task}_{v}")
            model.addConstr(order[task, v] <= len(tasks) * incoming, f"OrderUsedHi_{task}_{v}")
            model.addConstr(load[task, v] <= problem.e2_weight_cap * incoming, f"LoadUsed_{task}_{v}")
            model.addConstr(volume[task, v] <= problem.e2_volume_cap * incoming, f"VolumeUsed_{task}_{v}")
            # The variable upper bound enforces every finite latest time.
            # A lower window must deactivate to zero for an unused task.
            model.addConstr(
                arrival[task, v] >= task_lower[task] - max(0.0, task_lower[task]) * (1 - incoming),
                f"TWLo_{task}_{v}",
            )
            # Arrival variables are meaningful only if this vehicle serves
            # the task.  Pinning unused ones to zero is equivalent for the
            # integer model and materially tightens inactive time arcs.
            model.addConstr(
                arrival[task, v] <= node_upper[task] * incoming,
                f"TWUsedHi_{task}_{v}",
            )
        for i in tasks:
            for j in tasks:
                if i == j:
                    continue
                model.addConstr(
                    order[j, v] >= order[i, v] + 1.0 - len(tasks) * (1 - z[i, j, v]),
                    f"OrderArc_{i}_{j}_{v}",
                )
        # A customer task may not be traversed in both directions by the
        # same route.  (Depot↔task pairs are intentionally excluded: they
        # form a valid one-task route.)  This is redundant for integral MTZ
        # paths but cuts a common fractional two-cycle relaxation.
        for left_index, i in enumerate(tasks):
            for j in tasks[left_index + 1:]:
                model.addConstr(
                    z[i, j, v] + z[j, i, v] <= 1.0,
                    f"NoTaskTwoCycle_{i}_{j}_{v}",
                )
        for task in tasks:
            model.addConstr(
                order[task, v] >= 1.0 - len(tasks) * (1 - z[sat_id, task, v]),
                f"OrderStart_{task}_{v}",
            )

        # Exact departure load: no pickup may finance a later delivery.
        model.addConstr(
            load[sat_id, v] == gp.quicksum(cust_w[c] * served_task[f"{c}d", v] for c in C),
            f"InitialLoad_{v}",
        )
        model.addConstr(
            volume[sat_id, v] == gp.quicksum(cv * served_task[f"{c}d", v] for c in C),
            f"InitialVolume_{v}",
        )
        for i in nodes:
            service_i = 0.0 if i == sat_id else float(all_customers[task_customer[i]].service_time)
            physical_i = sat_id if i == sat_id else task_customer[i]
            for j in tasks:
                if i == j:
                    continue
                physical_j = task_customer[j]
                travel = float(problem.travel_time(physical_i, physical_j))
                # A_j has lower bound zero, so this is the smallest safe
                # inactive-arc M for A_j >= A_i + service + travel.
                time_m = max(0.0, node_upper[i] + service_i + travel)
                model.addConstr(
                    arrival[j, v] >= arrival[i, v] + service_i + travel - time_m * (1 - z[i, j, v]),
                    f"Time_{i}_{j}_{v}",
                )
                amount_w = cw if j.endswith("p") else cust_w[task_customer[j]]
                amount_v = cv
                signed_w = amount_w if j.endswith("d") else -amount_w
                signed_v = amount_v if j.endswith("d") else -amount_v
                m_w = float(problem.e2_weight_cap) + abs(signed_w)
                m_v = float(problem.e2_volume_cap) + abs(signed_v)
                model.addConstr(load[j, v] <= load[i, v] - signed_w + m_w * (1 - z[i, j, v]), f"LoadUp_{i}_{j}_{v}")
                model.addConstr(load[j, v] >= load[i, v] - signed_w - m_w * (1 - z[i, j, v]), f"LoadLo_{i}_{j}_{v}")
                model.addConstr(volume[j, v] <= volume[i, v] - signed_v + m_v * (1 - z[i, j, v]), f"VolumeUp_{i}_{j}_{v}")
                model.addConstr(volume[j, v] >= volume[i, v] - signed_v - m_v * (1 - z[i, j, v]), f"VolumeLo_{i}_{j}_{v}")
        for task in tasks:
            physical = task_customer[task]
            return_m = max(
                0.0,
                node_upper[task] + float(all_customers[physical].service_time)
                + float(problem.travel_time(physical, sat_id)) - float(pickup_ready_time),
            )
            model.addConstr(
                arrival[task, v] + float(all_customers[physical].service_time)
                + float(problem.travel_time(physical, sat_id))
                <= float(pickup_ready_time) + return_m * (1 - z[task, sat_id, v]),
                f"ReturnByPickup_{task}_{v}",
            )

    if subtour_strengthening_size >= 2:
        # For every touched task subset, a depot-rooted E2 route must enter
        # it at least once.  These size-2/3 SECs are integer-redundant but
        # cut fractional task cycles that are weakly represented by MTZ.
        for v in V:
            for subset_size in range(2, int(subtour_strengthening_size) + 1):
                for subset in combinations(tasks, subset_size):
                    subset_set = set(subset)
                    entering = gp.quicksum(
                        z[i, j, v]
                        for i in nodes
                        if i not in subset_set
                        for j in subset
                    )
                    for task in subset:
                        model.addConstr(
                            entering >= served_task[task, v],
                            f"SubtourEnter_{subset_size}_{'_'.join(subset)}_{task}_{v}",
                        )

    # A customer's paired tasks are both selected and pickup service starts
    # only after delivery service completes, even across different vehicles.
    for c in C:
        for vd in V:
            for vp in V:
                precedence_m = max(
                    0.0,
                    node_upper[f"{c}d"] + float(all_customers[c].service_time),
                )
                model.addConstr(
                    arrival[f"{c}p", vp]
                    >= arrival[f"{c}d", vd] + float(all_customers[c].service_time)
                    - precedence_m * (2 - served_task[f"{c}d", vd] - served_task[f"{c}p", vp]),
                    f"DeliveryBeforePickup_{c}_{vd}_{vp}",
                )
        # With unused arrivals pinned to zero above, the following is the
        # exact aggregate form of d→p precedence in every integer program.
        # It provides a non-big-M cross-vehicle strengthening at fractional
        # pricing LP nodes.
        model.addConstr(
            gp.quicksum(arrival[f"{c}p", v] for v in V)
            >= gp.quicksum(arrival[f"{c}d", v] for v in V)
            + float(all_customers[c].service_time) * selected[c],
            f"AggregateDeliveryBeforePickup_{c}",
        )
    for idx in range(len(V) - 1):
        model.addConstr(
            used_by_vehicle[V[idx]] >= used_by_vehicle[V[idx + 1]],
            f"VehicleSym_{idx}",
        )
        model.addConstr(load[sat_id, V[idx]] >= load[sat_id, V[idx + 1]], f"LoadSym_{idx}")
    if required_vehicle_count is not None:
        model.addConstr(
            gp.quicksum(used_by_vehicle[v] for v in V) == int(required_vehicle_count),
            "RequiredVehicleCount",
        )

    distance_cost = gp.quicksum(
        float(problem.distance(
            sat_id if i == sat_id else task_customer[i],
            sat_id if j == sat_id else task_customer[j],
        )) * z[i, j, v]
        for i, j, v in z.keys()
    )
    reduced_cost = (
        distance_cost
        + gp.quicksum(float(customer_rc_terms.get(c, 0.0)) * selected[c] for c in C)
        + float(state_rc_term)
    )
    if prove_no_negative_only:
        model.addConstr(
            reduced_cost <= -float(reduced_cost_tolerance),
            "NegativeReducedCostWitness",
        )
        model.setObjective(0.0, GRB.MINIMIZE)
    else:
        model.setObjective(reduced_cost, GRB.MINIMIZE)

    if warm_start_program is not None:
        # A complete discrete start avoids a repair sub-MIP.  Vehicle labels
        # are normalized to the model's used/load symmetry before assigning
        # route arcs; continuous schedule/load/order values can then be
        # recovered by Gurobi from the fixed discrete solution.
        for variable in selected.values():
            variable.Start = 0.0
        for customer_id in warm_start_program.customers:
            if customer_id not in selected:
                raise ValueError("warm-start program contains a non-candidate customer")
            selected[customer_id].Start = 1.0
        for variable in z.values():
            variable.Start = 0.0

        def _delivery_load(route):
            return sum(
                cust_w[task[:-1]]
                for task in route[1:-1]
                if task.endswith("d") and task[:-1] in cust_w
            )

        ordered_routes = sorted(
            (tuple(route) for route in warm_start_program.routes),
            key=_delivery_load,
            reverse=True,
        )
        if len(ordered_routes) > len(V):
            raise ValueError("warm-start program uses too many E2 vehicles")
        if (required_vehicle_count is not None
                and len(ordered_routes) != int(required_vehicle_count)):
            raise ValueError("warm-start program uses a different E2 vehicle count")
        for vehicle, route in zip(V, ordered_routes):
            if len(route) < 3 or route[0] != sat_id or route[-1] != sat_id:
                raise ValueError("warm-start program has invalid route endpoints")
            for i, j in zip(route, route[1:]):
                if (i, j, vehicle) not in z:
                    raise ValueError("warm-start program contains an unavailable arc")
                z[i, j, vehicle].Start = 1.0
    model.optimize()

    pricing_closed = bool(
        (prove_no_negative_only and model.status == GRB.INFEASIBLE)
        or (
            not prove_no_negative_only
            and model.status in {GRB.OPTIMAL, GRB.INFEASIBLE}
            and (model.status == GRB.INFEASIBLE or model.SolCount == 0 or model.ObjVal >= -float(reduced_cost_tolerance))
        )
    )
    status = (
        "NO_NEGATIVE_COLUMN_PROVED"
        if prove_no_negative_only and model.status == GRB.INFEASIBLE
        else _status_name(model.status)
    )
    has_solution = model.SolCount > 0
    program = None
    programs: list[M4SatelliteProgram] = []
    program_reduced_costs: list[float] = []
    selected_customers: tuple[str, ...] = ()
    if has_solution:
        solution_count = min(int(model.SolCount), int(solution_pool_size))
        seen_programs: set[tuple] = set()
        for solution_number in range(solution_count):
            if solution_pool_size > 1:
                model.setParam("SolutionNumber", solution_number)
            candidate_customers = tuple(
                c for c in C if _solution_value(selected[c], solution_pool_size > 1) > 0.5
            )
            paths = []
            for v in V:
                if sum(
                    _solution_value(z[sat_id, task, v], solution_pool_size > 1)
                    for task in tasks
                ) <= 0.5:
                    continue
                path = _extract_path(
                    sat_id,
                    tasks,
                    v,
                    z,
                    use_pool_solution=solution_pool_size > 1,
                )
                if path is None:
                    raise RuntimeError("pricing solution contains an invalid E2 path")
                paths.append(path)
            try:
                candidate = make_m4_satellite_program(
                    problem,
                    sat_id,
                    paths,
                    ready_time=float(delivery_ready_time),
                    pickup_ready_time=float(pickup_ready_time),
                )
            except ValueError as exc:
                # A priced MIP solution must be a valid program.  Raising here
                # prevents an unvalidated numerical artifact from entering a pool.
                raise RuntimeError(f"priced M4 program failed independent validation: {exc}") from exc
            if solution_number == 0:
                program = candidate
                selected_customers = candidate_customers
            pool_reduced_cost = (
                float(candidate.cost)
                + sum(float(customer_rc_terms.get(c, 0.0)) for c in candidate.customers)
                + float(state_rc_term)
            )
            key = _program_key(candidate)
            if pool_reduced_cost < -EPS and key not in seen_programs:
                seen_programs.add(key)
                programs.append(candidate)
                program_reduced_costs.append(pool_reduced_cost)

    return M4ProgramPricingResult(
        status=status,
        pricing_optimal=model.status == GRB.OPTIMAL,
        pricing_closed=pricing_closed,
        reduced_cost=(
            float(program.cost)
            + sum(float(customer_rc_terms.get(c, 0.0)) for c in program.customers)
            + float(state_rc_term)
            if program is not None else None
        ),
        # ObjBound remains a valid lower bound at a MIP time limit even when
        # no incumbent has been found.  Exposing it lets the eligible
        # single-satellite direct-program formulation report an audited
        # global lower bound instead of discarding useful diagnostic proof
        # progress.  Infeasible pricing has no finite objective bound.
        reduced_cost_bound=(
            0.0 if pricing_closed and prove_no_negative_only
            else (
                float(model.ObjBound)
                if (
                    not prove_no_negative_only
                    and model.status != GRB.INFEASIBLE
                    and isfinite(float(model.ObjBound))
                )
                else None
            )
        ),
        program=program,
        programs=tuple(programs),
        program_reduced_costs=tuple(program_reduced_costs),
        selected_customers=selected_customers,
        runtime=float(model.Runtime),
    )


def existing_state_pricing_terms(model, context, sat_id: str, state_idx: int) -> tuple[dict[str, float], float]:
    """Return the exact RC coefficients for a new column at an existing state.

    In the global RMP, ``ProgramAssign[c,s]`` is
    ``w[c,s] - sum(lambda covering c) = 0``.  Hence a new program covering
    ``c`` has coefficient ``-1`` and contributes ``+Pi`` to its minimization
    reduced cost.  ``ProgramStateUse[s,h,p]`` has coefficient ``+1`` for the
    column and contributes ``-Pi``.  No other master row contains an existing
    state-program column.

    This function purposefully rejects non-optimal masters: Gurobi duals are
    not a pricing certificate until the LP itself is solved optimally.
    """

    if model.status != GRB.OPTIMAL:
        raise ValueError("pricing duals require an optimal restricted-master LP")
    assignment_rows = context.get("program_assign_rows", {})
    state_rows = context.get("program_state_rows", {})
    state_key = (sat_id, int(state_idx))
    if state_key not in state_rows:
        raise KeyError(f"unknown existing program state {state_key!r}")
    terms = {
        customer_id: float(row.Pi)
        for (customer_id, satellite), row in assignment_rows.items()
        if satellite == sat_id
    }
    return terms, -float(state_rows[state_key].Pi)


def _add_excluded_program_cuts(
    model,
    z,
    selected,
    sat_id: str,
    tasks: Sequence[str],
    vehicles: Sequence[int],
    delivery_ready_time: float,
    pickup_ready_time: float,
    excluded_programs: Sequence[M4SatelliteProgram],
) -> None:
    """Forbid known labelled arc patterns at the priced state only.

    The program representation preserves the canonical E2 vehicle order
    (used-prefix, then nonincreasing departure load) used by this pricer.
    Therefore a selected program's route index is its vehicle index.  The
    no-good is active only when the candidate has exactly the same customer
    set as the existing program.  This is essential: forbidding its arcs
    unconditionally would also (incorrectly) forbid a larger program that
    contains one of its routes plus additional routes.
    """

    task_set = set(tasks)
    cut_index = 0
    for program in excluded_programs:
        if (
            program.sat_id != sat_id
            or float(program.ready_time) != float(delivery_ready_time)
            or float(program.pickup_ready_time) != float(pickup_ready_time)
            or len(program.routes) > len(vehicles)
        ):
            continue
        selected_arcs = []
        valid = True
        for vehicle, path in zip(vehicles, program.routes):
            if len(path) < 3 or path[0] != sat_id or path[-1] != sat_id:
                valid = False
                break
            for i, j in zip(path, path[1:]):
                if i not in task_set | {sat_id} or j not in task_set | {sat_id} or i == j or (i, j, vehicle) not in z:
                    valid = False
                    break
                selected_arcs.append(z[i, j, vehicle])
            if not valid:
                break
        if not valid or not selected_arcs:
            continue
        known_customers = set(program.customers)
        candidate_customers = {task[:-1] for task in tasks}
        if not known_customers <= candidate_customers:
            continue
        exact_customer_set = model.addVar(vtype=GRB.BINARY, name=f"ExcludeCustomerSet_{cut_index}")
        for customer_id in known_customers:
            model.addConstr(exact_customer_set <= selected[customer_id], f"ExcludeHas_{cut_index}_{customer_id}")
        for customer_id in candidate_customers - known_customers:
            model.addConstr(exact_customer_set <= 1 - selected[customer_id], f"ExcludeOnly_{cut_index}_{customer_id}")
        # b = 1 iff every known customer is selected and every other
        # candidate customer is absent.  The lower bound is the conjunction
        # linearization, so the arc no-good is inactive for subsets/supersets.
        model.addConstr(
            exact_customer_set >= gp.quicksum(selected[c] for c in known_customers)
            - gp.quicksum(selected[c] for c in candidate_customers - known_customers)
            - (len(known_customers) - 1),
            f"ExcludeExactSet_{cut_index}",
        )
        model.addConstr(
            gp.quicksum(selected_arcs)
            <= len(selected_arcs) - 1 + len(selected_arcs) * (1 - exact_customer_set),
            f"ExcludeKnownProgram_{cut_index}",
        )
        cut_index += 1


def _solution_value(variable, use_pool_solution: bool) -> float:
    """Read either the incumbent or the selected Gurobi pool member."""

    return float(variable.Xn if use_pool_solution else variable.X)


def _extract_path(
    sat_id: str,
    tasks: Sequence[str],
    vehicle: int,
    z,
    *,
    use_pool_solution: bool = False,
) -> tuple[str, ...] | None:
    """Extract one selected satellite-to-satellite path and reject cycles."""

    path = [sat_id]
    current = sat_id
    seen: set[str] = {sat_id}
    for _ in range(len(tasks) + 1):
        next_nodes = [
            node
            for node in (sat_id,) + tuple(tasks)
            if node != current and _solution_value(z[current, node, vehicle], use_pool_solution) > 0.5
        ]
        if len(next_nodes) != 1:
            return None
        nxt = next_nodes[0]
        path.append(nxt)
        if nxt == sat_id:
            return tuple(path)
        if nxt in seen:
            return None
        seen.add(nxt)
        current = nxt
    return None


def _program_key(program: M4SatelliteProgram) -> tuple:
    return (
        program.sat_id,
        float(program.ready_time),
        float(program.pickup_ready_time),
        tuple(tuple(route) for route in program.routes),
    )


def _status_name(status_code: int) -> str:
    return {
        GRB.OPTIMAL: "OPTIMAL",
        GRB.INFEASIBLE: "INFEASIBLE",
        GRB.INF_OR_UNBD: "INF_OR_UNBD",
        GRB.TIME_LIMIT: "TIME_LIMIT",
        GRB.INTERRUPTED: "INTERRUPTED",
        GRB.SUBOPTIMAL: "SUBOPTIMAL",
        GRB.NUMERIC: "NUMERIC",
    }.get(status_code, str(status_code))
