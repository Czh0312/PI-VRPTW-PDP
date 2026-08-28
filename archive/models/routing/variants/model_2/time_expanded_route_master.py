"""Restricted time-expanded route master for the certified M2 path.

This is deliberately a *master-building primitive*, not an ``exact`` solver.
It is the correct restricted master for the future branch-price algorithm:
every E2 column is indexed by a finite satellite-ready state, so a new column
has coefficients in fixed coverage, vehicle-limit, state-use, and
state-customer linking rows only.
Consequently its reduced cost can be priced completely.

Calling :func:`solve_m2_time_expanded_pool` optimizes only the supplied route
columns and always returns ``proven_optimal=False``.  A global certificate
requires the future loop to price every state to optimality at every branch
node and to exhaust the branch tree.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, Mapping, Sequence, Tuple

import gurobipy as gp
from gurobipy import GRB

from models.routing.variants.model_2.certified_time_expansion import (
    ReadyState,
    enumerate_ready_states,
    forward_m2_route,
)
from models.routing.variants.model_2.route_pool import _setup_geometry, _status_name
from models.routing.common.exact_utils import extract_routes_e1


@dataclass(frozen=True)
class TimeExpandedRouteColumn:
    """One feasible M2 E2 path at one satellite-ready state."""

    sat_id: str
    state_idx: int
    ready_time: float
    path: Tuple[str, ...]
    tasks: Tuple[str, ...]
    cost: float
    return_time: float
    source: str = "pool"


def expand_route_columns_by_ready_state(
    problem,
    route_columns: Iterable[Mapping[str, Any]],
    *,
    ready_states: Mapping[str, Sequence[ReadyState]] | None = None,
) -> Tuple[Tuple[TimeExpandedRouteColumn, ...], Dict[str, Tuple[ReadyState, ...]]]:
    """Turn physical M2 route paths into state-indexed feasible columns.

    Costs and covered tasks are recomputed from the path rather than trusted
    from a heuristic column file.  Invalid paths are ignored, which is safe
    for a restricted master; a future exact run must use pricing to discover
    every omitted feasible path.
    """

    states = _coerce_ready_states(problem, ready_states)
    seen_paths: set[Tuple[str, Tuple[str, ...]]] = set()
    expanded: list[TimeExpandedRouteColumn] = []
    seen_columns: set[Tuple[str, int, Tuple[str, ...]]] = set()

    for raw in route_columns:
        if not isinstance(raw, Mapping):
            continue
        path = tuple(raw.get("path", ()))
        sat_id = str(raw.get("sat_id") or (path[0] if path else ""))
        if not path or sat_id not in states:
            continue
        if len(path) < 3 or path[0] != sat_id or path[-1] != sat_id:
            continue
        requested_state_idx = raw.get("state_idx")
        path_key = (sat_id, path)
        # An unscoped seed route is expanded to every feasible state and is
        # therefore path-deduplicated.  A pricing result is state-specific:
        # the same path may legitimately be added later at another state.
        if requested_state_idx is None:
            if path_key in seen_paths:
                continue
            seen_paths.add(path_key)
        source = str(raw.get("source", "pool"))

        if requested_state_idx is None:
            state_candidates = enumerate(states[sat_id])
        else:
            try:
                requested_state_idx = int(requested_state_idx)
            except (TypeError, ValueError):
                continue
            if not 0 <= requested_state_idx < len(states[sat_id]):
                continue
            state_candidates = ((requested_state_idx, states[sat_id][requested_state_idx]),)

        for state_idx, state in state_candidates:
            schedule = forward_m2_route(
                problem, sat_id, path, ready_time=state.ready_time
            )
            if not schedule.feasible:
                continue
            key = (sat_id, state_idx, path)
            if key in seen_columns:
                continue
            seen_columns.add(key)
            expanded.append(
                TimeExpandedRouteColumn(
                    sat_id=sat_id,
                    state_idx=state_idx,
                    ready_time=state.ready_time,
                    path=path,
                    tasks=tuple(path[1:-1]),
                    cost=float(schedule.distance),
                    return_time=float(schedule.return_time),
                    source=source,
                )
            )

    return tuple(expanded), states


def build_m2_time_expanded_master(
    problem,
    route_columns: Iterable[Mapping[str, Any]],
    vehicles_per_satellite: int,
    *,
    ready_states: Mapping[str, Sequence[ReadyState]] | None = None,
    relax: bool = False,
    artificial_cost: float | None = None,
):
    """Build the M2 ready-state route master.

    At integral values, ``ready_state[s,h]=1`` selects one common time at
    which all E2 routes at satellite ``s`` may depart.  The E1 truck is only
    required to complete delivery by that time.  This is exact for M2:
    delaying an E2 departure to the common state is never harmful because the
    model has no satellite loading resource or E2 route-return coupling.

    ``relax=True`` creates the LP used by column generation.  No incumbent
    cutoff is added: keeping the RMP objective unconstrained makes the dual
    pricing contract transparent.  ``artificial_cost`` optionally adds
    expensive coverage artificials for a phase-I/column-generation start.
    They are never part of the original formulation; a caller may claim an
    original-LP bound only after their total value is zero.
    """

    geo = _setup_geometry(problem, vehicles_per_satellite)
    depot_id = geo["depot_id"]
    F = geo["F"]
    C = geo["C"]
    K = geo["K"]
    e1_nodes = geo["e1_nodes"]
    e1_del_nodes = geo["e1_del_nodes"]
    e1_svc = geo["e1_svc"]
    dist_map = geo["dist_map"]
    time_map = geo["time_map"]
    time_horizon = geo["time_horizon"]
    # ``route_pool._setup_geometry`` provides a broad horizon, but its legacy
    # M_time is only horizon + max_arc.  An inactive time row also needs to
    # absorb service at its tail.  Keep a locally safe value for this master.
    max_e1_service = max(e1_svc.values(), default=0.0)
    max_e1_arc = max(
        (time_map[i, j] for i in e1_nodes for j in e1_nodes), default=0.0
    )
    e1_time_big_m = time_horizon + max_e1_service + max_e1_arc
    cust_w = geo["cust_w"]
    cust_tw_del = geo["cust_tw_del"]
    cw = geo["cw"]
    cv = geo["cv"]
    inf = geo["inf"]

    columns, states = expand_route_columns_by_ready_state(
        problem, route_columns, ready_states=ready_states
    )
    route_ids = tuple(range(len(columns)))
    state_keys = tuple(
        (sat_id, state_idx)
        for sat_id in F
        for state_idx in range(len(states[sat_id]))
    )

    model = gp.Model("M2_TimeExpanded_RouteMaster")
    if not relax:
        model.setParam("OutputFlag", 1)
    var_type = GRB.CONTINUOUS if relax else GRB.BINARY

    # E1 + assignment variables are retained from the complete M2 model.
    w = model.addVars(C, F, vtype=var_type, lb=0.0, ub=1.0, name="w")
    lam = model.addVars(route_ids, vtype=var_type, lb=0.0, ub=1.0, name="lambda")
    artificial = (
        model.addVars(C, F, vtype=var_type, lb=0.0, ub=1.0, name="artificial")
        if artificial_cost is not None else None
    )
    x = model.addVars(e1_nodes, e1_nodes, K, vtype=var_type, lb=0.0, ub=1.0, name="x")
    served_s = model.addVars(F, vtype=var_type, lb=0.0, ub=1.0, name="served_s")
    ready_state = model.addVars(
        state_keys, vtype=var_type, lb=0.0, ub=1.0, name="ready_state"
    )
    y_lin = model.addVars(C, F, K, vtype=var_type, lb=0.0, ub=1.0, name="y_lin")
    A_e1 = model.addVars(e1_nodes, K, lb=0.0, ub=time_horizon, name="A_e1")
    Q_k = model.addVars(
        e1_nodes, K, lb=0.0, ub=problem.e1_weight_cap, name="Q_k"
    )
    Vol_k = model.addVars(
        e1_nodes, K, lb=0.0, ub=problem.e1_volume_cap, name="Vol_k"
    )

    obj_e1 = gp.quicksum(
        dist_map[i, j] * x[i, j, k]
        for k in K for i in e1_nodes for j in e1_nodes if i != j
    )
    obj_e2 = gp.quicksum(columns[rid].cost * lam[rid] for rid in route_ids)
    obj_artificial = (
        float(artificial_cost) * gp.quicksum(artificial[customer_id, sat_id]
                                             for customer_id in C for sat_id in F)
        if artificial is not None else 0.0
    )
    model.setObjective(obj_e1 + obj_e2 + obj_artificial, GRB.MINIMIZE)

    # ---- Free assignment ----
    for customer_id in C:
        model.addConstr(
            gp.quicksum(w[customer_id, sat_id] for sat_id in F) == 1,
            f"Assign_{customer_id}",
        )

    # ---- E1 arc-flow ----
    for node_id in e1_nodes:
        for truck_id in K:
            model.addConstr(x[node_id, node_id, truck_id] == 0, f"E1_NoSelf_{node_id}_{truck_id}")

    for truck_id in K:
        model.addConstr(A_e1[depot_id, truck_id] == 0.0, f"E1_DepotTime_{truck_id}")
        model.addConstr(
            gp.quicksum(x[depot_id, j, truck_id] for j in e1_del_nodes) <= 1,
            f"E1_Start_{truck_id}",
        )
        model.addConstr(
            gp.quicksum(x[i, depot_id, truck_id] for i in e1_del_nodes) <= 1,
            f"E1_End_{truck_id}",
        )
        for node_id in e1_del_nodes:
            model.addConstr(
                gp.quicksum(x[i, node_id, truck_id] for i in e1_nodes if i != node_id)
                == gp.quicksum(x[node_id, j, truck_id] for j in e1_nodes if j != node_id),
                f"E1_Flow_{node_id}_{truck_id}",
            )
        depot_out = gp.quicksum(x[depot_id, j, truck_id] for j in e1_del_nodes)
        for node_id in e1_del_nodes:
            model.addConstr(
                gp.quicksum(x[i, node_id, truck_id] for i in e1_nodes if i != node_id)
                <= depot_out,
                f"E1_Subtour_{node_id}_{truck_id}",
            )

    for idx in range(len(K) - 1):
        model.addConstr(
            gp.quicksum(x[depot_id, j, K[idx]] for j in e1_del_nodes)
            >= gp.quicksum(x[depot_id, j, K[idx + 1]] for j in e1_del_nodes),
            f"E1_VehicleSym_{idx}",
        )

    # ---- Optional satellite visits and ready-state selection ----
    visits_d: Dict[Tuple[str, int], gp.LinExpr] = {}
    for sat_id in F:
        for truck_id in K:
            visits_d[sat_id, truck_id] = gp.quicksum(
                x[i, f"{sat_id}d", truck_id]
                for i in e1_nodes if i != f"{sat_id}d"
            )
        for customer_id in C:
            model.addConstr(served_s[sat_id] >= w[customer_id, sat_id], f"Served_ge_{customer_id}_{sat_id}")
        model.addConstr(
            served_s[sat_id] <= gp.quicksum(w[customer_id, sat_id] for customer_id in C),
            f"Served_le_{sat_id}",
        )
        model.addConstr(
            gp.quicksum(visits_d[sat_id, truck_id] for truck_id in K) == served_s[sat_id],
            f"E1_DelVisit_{sat_id}",
        )
        model.addConstr(
            gp.quicksum(
                ready_state[sat_id, state_idx]
                for state_idx in range(len(states[sat_id]))
            ) == served_s[sat_id],
            f"ReadyStateChoice_{sat_id}",
        )
        for state_idx, state in enumerate(states[sat_id]):
            for truck_id in K:
                # If this truck serves s and state h is selected, E1 must
                # finish satellite service before h.  The route columns at h
                # then start at h (possibly waiting at customers).
                model.addConstr(
                    A_e1[f"{sat_id}d", truck_id] + e1_svc[f"{sat_id}d"]
                    <= state.ready_time
                    + e1_time_big_m * (2 - ready_state[sat_id, state_idx] - visits_d[sat_id, truck_id]),
                    f"E1_ReadyBy_{sat_id}_{state_idx}_{truck_id}",
                )

    # ---- E1 initial-load linearization and capacity propagation ----
    for customer_id in C:
        for sat_id in F:
            for truck_id in K:
                model.addConstr(y_lin[customer_id, sat_id, truck_id] <= w[customer_id, sat_id], f"Y_ub1_{customer_id}_{sat_id}_{truck_id}")
                model.addConstr(y_lin[customer_id, sat_id, truck_id] <= visits_d[sat_id, truck_id], f"Y_ub2_{customer_id}_{sat_id}_{truck_id}")
                model.addConstr(
                    y_lin[customer_id, sat_id, truck_id]
                    >= w[customer_id, sat_id] + visits_d[sat_id, truck_id] - 1,
                    f"Y_lb_{customer_id}_{sat_id}_{truck_id}",
                )

    total_del_w = sum(cust_w[customer_id] for customer_id in C)
    total_del_v = len(C) * cv
    for truck_id in K:
        model.addConstr(
            Q_k[depot_id, truck_id]
            == gp.quicksum(
                cust_w[customer_id] * y_lin[customer_id, sat_id, truck_id]
                for customer_id in C for sat_id in F
            ),
            f"E1_InitW_{truck_id}",
        )
        model.addConstr(
            Vol_k[depot_id, truck_id]
            == gp.quicksum(
                cv * y_lin[customer_id, sat_id, truck_id]
                for customer_id in C for sat_id in F
            ),
            f"E1_InitV_{truck_id}",
        )
    model.addConstr(gp.quicksum(Q_k[depot_id, truck_id] for truck_id in K) == total_del_w, "E1_TotalWeight")
    model.addConstr(gp.quicksum(Vol_k[depot_id, truck_id] for truck_id in K) == total_del_v, "E1_TotalVol")
    for idx in range(len(K) - 1):
        model.addConstr(Q_k[depot_id, K[idx]] >= Q_k[depot_id, K[idx + 1]], f"E1_LoadSym_{idx}")

    for i in e1_nodes:
        for j in e1_del_nodes:
            if i == j:
                continue
            sat_id = j[:-1]
            q_j = gp.quicksum(cust_w[customer_id] * w[customer_id, sat_id] for customer_id in C)
            v_j = gp.quicksum(cv * w[customer_id, sat_id] for customer_id in C)
            M_w = problem.e1_weight_cap + max(total_del_w, 1.0)
            M_v = problem.e1_volume_cap + max(total_del_v, 1.0)
            for truck_id in K:
                model.addConstr(Q_k[j, truck_id] <= Q_k[i, truck_id] - q_j + M_w * (1 - x[i, j, truck_id]), f"E1_W_up_{i}_{j}_{truck_id}")
                model.addConstr(Q_k[j, truck_id] >= Q_k[i, truck_id] - q_j - M_w * (1 - x[i, j, truck_id]), f"E1_W_lo_{i}_{j}_{truck_id}")
                model.addConstr(Vol_k[j, truck_id] <= Vol_k[i, truck_id] - v_j + M_v * (1 - x[i, j, truck_id]), f"E1_V_up_{i}_{j}_{truck_id}")
                model.addConstr(Vol_k[j, truck_id] >= Vol_k[i, truck_id] - v_j - M_v * (1 - x[i, j, truck_id]), f"E1_V_lo_{i}_{j}_{truck_id}")

    for i in e1_nodes:
        for j in e1_del_nodes:
            if i == j:
                continue
            for truck_id in K:
                model.addConstr(
                    A_e1[j, truck_id]
                    >= A_e1[i, truck_id] + e1_svc.get(i, 0.0) + time_map[i, j]
                    - e1_time_big_m * (1 - x[i, j, truck_id]),
                    f"E1_Time_{i}_{j}_{truck_id}",
                )

    for sat_id in F:
        for customer_id in C:
            due = cust_tw_del[customer_id][1]
            if due >= inf:
                continue
            for truck_id in K:
                model.addConstr(
                    A_e1[f"{sat_id}d", truck_id]
                    <= due * visits_d[sat_id, truck_id] + e1_time_big_m * (1 - w[customer_id, sat_id]),
                    f"E1_Due_{customer_id}_{sat_id}_{truck_id}",
                )

    # ---- State-indexed E2 set partitioning ----
    route_by_customer_sat: Dict[Tuple[str, str], list[int]] = {}
    # This is deliberately a fixed row family.  At an integer solution it is
    # redundant (a customer is covered at most once and exactly one ready
    # state is selected), but in the LP it prevents an integral route from
    # being split over fractional ready-state choices solely through the
    # aggregate VehicleLimit * ready_state link.
    route_by_customer_sat_state: Dict[Tuple[str, str, int], list[int]] = {
        (customer_id, sat_id, state_idx): []
        for customer_id in C
        for sat_id, state_idx in state_keys
    }
    route_by_sat: Dict[str, list[int]] = {sat_id: [] for sat_id in F}
    route_by_state: Dict[Tuple[str, int], list[int]] = {
        key: [] for key in state_keys
    }
    for route_id, column in enumerate(columns):
        route_by_sat[column.sat_id].append(route_id)
        route_by_state[column.sat_id, column.state_idx].append(route_id)
        for task_id in column.tasks:
            customer_id = task_id[:-1]
            route_by_customer_sat.setdefault((customer_id, column.sat_id), []).append(route_id)
            route_by_customer_sat_state[
                customer_id, column.sat_id, column.state_idx
            ].append(route_id)

    for customer_id in C:
        for sat_id in F:
            coverage = gp.quicksum(
                lam[route_id]
                for route_id in route_by_customer_sat.get((customer_id, sat_id), ())
            )
            if artificial is not None:
                coverage += artificial[customer_id, sat_id]
            model.addConstr(
                coverage == w[customer_id, sat_id],
                f"E2_Cov_{customer_id}_{sat_id}",
            )
    for sat_id in F:
        model.addConstr(
            gp.quicksum(lam[route_id] for route_id in route_by_sat[sat_id])
            <= vehicles_per_satellite,
            f"E2_VehicleLimit_{sat_id}",
        )
        for state_idx in range(len(states[sat_id])):
            # This fixed row is the key pricing contract.  Do not replace it
            # with a per-column lambda <= ready_state implication: that would
            # create a new row whenever pricing finds a new path.
            model.addConstr(
                gp.quicksum(lam[route_id] for route_id in route_by_state[sat_id, state_idx])
                <= vehicles_per_satellite * ready_state[sat_id, state_idx],
                f"E2_StateUse_{sat_id}_{state_idx}",
            )
            for customer_id in C:
                # Integer-redundant strengthening: when a state is active,
                # E2_Cov already permits a customer in at most one selected
                # route; when it is inactive, E2_StateUse permits none.
                # The fixed row substantially tightens the root LP and has a
                # well-defined coefficient for every future priced route.
                model.addConstr(
                    gp.quicksum(
                        lam[route_id]
                        for route_id in route_by_customer_sat_state[
                            customer_id, sat_id, state_idx
                        ]
                    )
                    <= ready_state[sat_id, state_idx],
                    f"E2_StateCustomer_{customer_id}_{sat_id}_{state_idx}",
                )

    variables = {
        "w": w,
        "lambda": lam,
        "artificial": artificial,
        "x": x,
        "served_s": served_s,
        "ready_state": ready_state,
        "y_lin": y_lin,
        "A_e1": A_e1,
        "Q_k": Q_k,
        "Vol_k": Vol_k,
    }
    context = {
        "depot_id": depot_id,
        "F": F,
        "C": C,
        "K": K,
        "e1_nodes": e1_nodes,
        "e1_del_nodes": e1_del_nodes,
        "states": states,
        "columns": columns,
        "M_time": e1_time_big_m,
        "vehicles_per_satellite": vehicles_per_satellite,
        "artificial_cost": artificial_cost,
    }
    return model, variables, context


def extract_time_expanded_pricing_duals(model, context):
    """Return all base reduced-cost duals for a solved LP master.

    For a candidate column at ``(s, h)`` the base reduced cost is exactly

    ``cost - sum(pi_cover[c,s]) - pi_vehicle[s] - pi_state[s,h]
    - sum(pi_state_customer[c,s,h])``.

    Branch rows and valid cuts, when added later, must contribute their own
    coefficients as well.  This helper intentionally raises unless the model
    is an LP solution, rather than silently returning misleading values.
    """

    if model.IsMIP:
        raise RuntimeError("pricing duals require the LP relaxation, not a MIP master")
    if model.status != GRB.OPTIMAL:
        raise RuntimeError("pricing duals require an optimal restricted LP")
    F = context["F"]
    C = context["C"]
    states = context["states"]
    cover = {
        (customer_id, sat_id): model.getConstrByName(f"E2_Cov_{customer_id}_{sat_id}").Pi
        for customer_id in C for sat_id in F
    }
    vehicle = {
        sat_id: model.getConstrByName(f"E2_VehicleLimit_{sat_id}").Pi
        for sat_id in F
    }
    state_use = {
        (sat_id, state_idx): model.getConstrByName(f"E2_StateUse_{sat_id}_{state_idx}").Pi
        for sat_id in F for state_idx in range(len(states[sat_id]))
    }
    state_customer = {
        (customer_id, sat_id, state_idx): model.getConstrByName(
            f"E2_StateCustomer_{customer_id}_{sat_id}_{state_idx}"
        ).Pi
        for customer_id in C
        for sat_id in F
        for state_idx in range(len(states[sat_id]))
    }
    return {
        "cover": cover,
        "vehicle": vehicle,
        "state_use": state_use,
        "state_customer": state_customer,
    }


def audit_time_expanded_reduced_costs(model, variables, context, *, tolerance: float = 1e-7) -> float:
    """Check that every existing base column has the advertised reduced cost.

    This is a regression guard against accidentally adding a lambda-containing
    master row without also extending pricing.  It applies to the unbranched
    root only; a future branch-price node must include its branch-row duals in
    the corresponding calculation.
    """

    duals = extract_time_expanded_pricing_duals(model, context)
    max_error = 0.0
    for route_id, column in enumerate(context["columns"]):
        expected = (
            column.cost
            - sum(duals["cover"][task_id[:-1], column.sat_id] for task_id in column.tasks)
            - duals["vehicle"][column.sat_id]
            - duals["state_use"][column.sat_id, column.state_idx]
            - sum(
                duals["state_customer"][
                    task_id[:-1], column.sat_id, column.state_idx
                ]
                for task_id in column.tasks
            )
        )
        actual = variables["lambda"][route_id].RC
        max_error = max(max_error, abs(actual - expected))
    if max_error > tolerance:
        raise RuntimeError(
            "time-expanded pricing misses a master-row contribution: "
            f"max reduced-cost error={max_error:.3e}"
        )
    return max_error


def solve_m2_time_expanded_pool(
    problem,
    route_columns: Iterable[Mapping[str, Any]],
    vehicles_per_satellite: int,
    *,
    ready_states: Mapping[str, Sequence[ReadyState]] | None = None,
    time_limit: float | None = None,
    verbose: bool = False,
):
    """Optimize a supplied state-expanded route pool, never globally certify it."""

    model, variables, context = build_m2_time_expanded_master(
        problem,
        route_columns,
        vehicles_per_satellite,
        ready_states=ready_states,
        relax=False,
    )
    model.setParam("OutputFlag", 1 if verbose else 0)
    model.setParam("MIPGap", 0.0)
    if time_limit is not None:
        model.setParam("TimeLimit", float(time_limit))
    model.optimize()

    has_solution = bool(model.SolCount)
    if model.status == GRB.OPTIMAL:
        solution_type = "restricted_time_expanded_pool_optimal"
    elif has_solution:
        solution_type = "restricted_time_expanded_pool_feasible_not_proven"
    else:
        solution_type = "none"
    info = {
        "status_code": int(model.status),
        "status": _status_name(model.status),
        "solution_type": solution_type,
        "proven_optimal": False,
        "proof_scope": "restricted_time_expanded_route_pool_only",
        "route_universe_complete": False,
        "objective": float(model.ObjVal) if has_solution else None,
        # A dual bound can exist before an integer incumbent.  Expose it for
        # diagnostics, without changing the non-certified proof scope.
        "best_bound": None,
        "mip_gap": float(model.MIPGap) if has_solution else None,
        "columns": len(context["columns"]),
        "ready_state_count": sum(len(v) for v in context["states"].values()),
    }
    try:
        info["best_bound"] = float(model.ObjBound)
    except Exception:
        pass
    if not has_solution:
        return {"e1_routes": None, "e2_routes": None, "assignment": None, "info": info}

    sol_x = {key: var.X for key, var in variables["x"].items()}
    e1_routes = [
        route for _, route in extract_routes_e1(
            sol_x, context["depot_id"], context["e1_del_nodes"], context["K"]
        )
    ]
    e2_routes = {sat_id: [] for sat_id in context["F"]}
    for route_id, column in enumerate(context["columns"]):
        if variables["lambda"][route_id].X > 0.5:
            e2_routes[column.sat_id].append(list(column.path))
    assignment = {
        customer_id: max(context["F"], key=lambda sat_id: variables["w"][customer_id, sat_id].X)
        for customer_id in context["C"]
    }
    selected_states = {
        sat_id: [
            state_idx for state_idx in range(len(context["states"][sat_id]))
            if variables["ready_state"][sat_id, state_idx].X > 0.5
        ]
        for sat_id in context["F"]
    }
    info["selected_ready_states"] = selected_states
    return {
        "e1_routes": e1_routes,
        "e2_routes": e2_routes,
        "assignment": assignment,
        "info": info,
    }


def _coerce_ready_states(
    problem,
    ready_states: Mapping[str, Sequence[ReadyState]] | None,
) -> Dict[str, Tuple[ReadyState, ...]]:
    if ready_states is None:
        ready_states = enumerate_ready_states(problem)
    sat_ids = tuple(s.id for s in problem.satellites)
    result: Dict[str, Tuple[ReadyState, ...]] = {}
    for sat_id in sat_ids:
        states = tuple(ready_states.get(sat_id, ()))
        if not states:
            raise ValueError(f"satellite {sat_id!r} has no ready states")
        for state in states:
            if state.sat_id != sat_id:
                raise ValueError(f"ready state {state!r} belongs to the wrong satellite")
        result[sat_id] = states
    return result
