"""Restricted global master for M6 ``(delivery-ready, pickup-ready)`` programs.

Each :class:`~models.model_6.certified_time_expansion.M6SatelliteProgram`
packages every E2 route selected at one satellite.  This is necessary in M6:
a customer's delivery and pickup may use different E2 vehicles, so an
individual route is not an independent master column.  A selected program is
indexed by its E1 delivery-ready time ``h`` and pickup-ready time ``p``.

The master retains the global E1 arc-flow, free customer-to-satellite
assignment, E1 capacity, and time constraints.  It chooses at most one
program state at each served satellite, then links E1 delivery completion to
``h`` and E1 pickup arrival to ``p``.

This is intentionally a *restricted* master.  It is exact only relative to
the supplied program/state universe.  In particular, it must never report a
Gurobi ``OPTIMAL`` result as a global M6 certificate; a future branch-price
implementation must dynamically generate every negative program and every
needed ``(h, p)`` state, then close the branch tree.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Iterable, Mapping, Sequence

import gurobipy as gp
from gurobipy import GRB

from models.alns_base import is_tfp_active
from models.model_6.certified_time_expansion import M6SatelliteProgram


@dataclass(frozen=True)
class M6ProgramState:
    """One finite ``(h, p)`` state currently represented at a satellite."""

    sat_id: str
    state_idx: int
    delivery_ready_time: float
    pickup_ready_time: float


def build_m6_two_state_global_program_master(
    problem,
    programs: Iterable[M6SatelliteProgram],
    vehicles_per_satellite: int,
    *,
    TFp: float = 9999.0,
    relax: bool = False,
    fixed_assignment: Mapping[str, str] | None = None,
    forbidden_assignments: Iterable[tuple[str, str]] = (),
    fixed_state_use: Mapping[tuple[str, int], int] | None = None,
    fixed_e1_arcs: Mapping[tuple[str, str, int], int] | None = None,
    fixed_e2_arcs: Mapping[tuple[str, int, str, str], int] | None = None,
):
    """Build an E1 + finite-M6-program restricted master.

    At integer values, a served satellite chooses exactly one program and
    exactly one corresponding state.  The program already proves all of its
    E2 deliveries, pickups, capacities, cross-route precedence, and returns;
    this master enforces the remaining global E1/assignment coupling.

    ``fixed_assignment``, ``forbidden_assignments``, ``fixed_state_use``,
    ``fixed_e1_arcs``, and ``fixed_e2_arcs`` are branch-node restrictions.  They are propagated
    through the master without changing its proof scope.

    ``relax=True`` is useful only as a diagnostic RMP.  The dual is not yet a
    complete pricing contract because adding a new ``(h, p)`` state creates
    fixed E1 linking rows.  The returned model therefore carries no exactness
    implication by itself.
    """

    columns = tuple(programs)
    depot_id = problem.depot.id
    F = tuple(s.id for s in problem.satellites)
    C = tuple(c.id for c in problem.customers)
    K = tuple(range(problem.e1_vehicle_count))
    customers = {c.id: c for c in problem.customers}
    satellite_set = set(F)
    cw = float(problem.fleet.container_weight)
    cv = float(problem.fleet.container_volume)
    cust_w = {c: float(customers[c].weight_del) + cw for c in C}
    fixed = {str(customer_id): str(sat_id) for customer_id, sat_id in (fixed_assignment or {}).items()}
    forbidden = {(str(customer_id), str(sat_id)) for customer_id, sat_id in forbidden_assignments}
    if not set(fixed) <= set(C):
        raise ValueError("fixed_assignment contains an unknown customer")
    if any(sat_id not in satellite_set for sat_id in fixed.values()):
        raise ValueError("fixed_assignment contains an unknown satellite")
    if any(customer_id not in customers or sat_id not in satellite_set for customer_id, sat_id in forbidden):
        raise ValueError("forbidden_assignments contains an unknown customer or satellite")
    if any((customer_id, sat_id) in forbidden for customer_id, sat_id in fixed.items()):
        raise ValueError("a fixed customer assignment cannot also be forbidden")

    for column in columns:
        if column.sat_id not in satellite_set:
            raise ValueError(f"program uses unknown satellite {column.sat_id!r}")
        if not set(column.customers) <= set(C):
            raise ValueError(f"program contains unknown customer(s): {column.customers!r}")
        if not column.customers:
            raise ValueError("empty M6 satellite programs are not master columns")
        if not column.schedule.feasible:
            raise ValueError("cannot add an infeasible M6 program column")
        if len(column.routes) > vehicles_per_satellite:
            raise ValueError("program uses more E2 vehicles than are available")
        if column.pickup_ready_time + 1e-9 < max(
            column.schedule.route_return_times, default=column.ready_time
        ):
            raise ValueError("program pickup-ready time precedes an E2 return")

    # State equality intentionally uses exact float equality.  The state
    # values originate from validated program witnesses; no epsilon merge is
    # safe because E1 link constraints use the values as coefficients.
    state_values: dict[str, list[tuple[float, float]]] = {s: [] for s in F}
    for column in columns:
        value = (float(column.ready_time), float(column.pickup_ready_time))
        if value not in state_values[column.sat_id]:
            state_values[column.sat_id].append(value)
    states = tuple(
        M6ProgramState(s, idx, h, p)
        for s in F
        for idx, (h, p) in enumerate(state_values[s])
    )
    states_by_key = {(state.sat_id, state.state_idx): state for state in states}
    state_lookup = {
        (state.sat_id, state.delivery_ready_time, state.pickup_ready_time): state.state_idx
        for state in states
    }
    column_state = {
        idx: state_lookup[(column.sat_id, float(column.ready_time), float(column.pickup_ready_time))]
        for idx, column in enumerate(columns)
    }
    fixed_states = {
        (str(sat_id), int(state_idx)): int(value)
        for (sat_id, state_idx), value in (fixed_state_use or {}).items()
    }
    if any(value not in {0, 1} for value in fixed_states.values()):
        raise ValueError("fixed_state_use values must be 0 or 1")
    if not set(fixed_states) <= set(states_by_key):
        raise ValueError("fixed_state_use references an unknown current state")
    e2_task_ids = {f"{customer_id}{kind}" for customer_id in C for kind in ("d", "p")}
    fixed_e2 = {
        (str(sat_id), int(state_idx), str(i), str(j)): int(value)
        for (sat_id, state_idx, i, j), value in (fixed_e2_arcs or {}).items()
    }
    if any(value not in {0, 1} for value in fixed_e2.values()):
        raise ValueError("fixed_e2_arcs values must be 0 or 1")
    for sat_id, state_idx, i, j in fixed_e2:
        if (sat_id, state_idx) not in states_by_key:
            raise ValueError("fixed_e2_arcs references an unknown current state")
        if i not in e2_task_ids | {sat_id} or j not in e2_task_ids | {sat_id} or i == j:
            raise ValueError("fixed_e2_arcs references an invalid E2 arc")

    e1_del_nodes = tuple(f"{s}d" for s in F)
    e1_pick_nodes = tuple(f"{s}p" for s in F)
    e1_nodes = (depot_id,) + e1_del_nodes + e1_pick_nodes
    fixed_arcs = {
        (str(i), str(j), int(k)): int(value)
        for (i, j, k), value in (fixed_e1_arcs or {}).items()
    }
    if any(value not in {0, 1} for value in fixed_arcs.values()):
        raise ValueError("fixed_e1_arcs values must be 0 or 1")
    if any(i not in e1_nodes or j not in e1_nodes or k not in K for i, j, k in fixed_arcs):
        raise ValueError("fixed_e1_arcs references an unknown E1 arc")
    e1_svc = {depot_id: 0.0}
    e1_svc.update(
        {
            event: float(getattr(problem.nodes[event[:-1]], "service_time", 0.0))
            for event in e1_del_nodes + e1_pick_nodes
        }
    )

    def _physical(node_id: str) -> str:
        if node_id in F or node_id == depot_id:
            return node_id
        if node_id.endswith(("d", "p")) and node_id[:-1] in satellite_set:
            return node_id[:-1]
        return node_id

    time_map = {
        (i, j): float(problem.travel_time(_physical(i), _physical(j)))
        for i in e1_nodes for j in e1_nodes
    }
    dist_map = {
        (i, j): float(problem.distance(_physical(i), _physical(j)))
        for i in e1_nodes for j in e1_nodes
    }
    finite_times = [0.0]
    for customer in customers.values():
        for window in (customer.tw_del, customer.tw_pick):
            finite_times.append(float(window[0]))
            if isfinite(float(window[1])):
                finite_times.append(float(window[1]))
    finite_times.extend(
        value
        for state in states
        for value in (state.delivery_ready_time, state.pickup_ready_time)
    )
    if is_tfp_active(TFp):
        finite_times.append(float(TFp))
    max_arc = max(time_map.values(), default=0.0)
    time_horizon = (
        max(finite_times)
        + (len(e1_nodes) + 2) * max_arc
        + sum(e1_svc.values())
        + 1.0
    )
    time_big_m = time_horizon + max_arc + max(e1_svc.values(), default=0.0)
    total_del_w = sum(cust_w.values())
    total_del_v = len(C) * cv
    total_pick_w = len(C) * cw
    total_pick_v = len(C) * cv

    model = gp.Model("M6_TwoState_GlobalProgram_RMP")
    model.setParam("OutputFlag", 0)
    var_type = GRB.CONTINUOUS if relax else GRB.BINARY
    route_ids = tuple(range(len(columns)))
    state_keys = tuple(states_by_key)
    lam = model.addVars(route_ids, vtype=var_type, lb=0.0, ub=1.0, name="program")
    w = model.addVars(C, F, vtype=var_type, lb=0.0, ub=1.0, name="w")
    served_s = model.addVars(F, vtype=var_type, lb=0.0, ub=1.0, name="served_s")
    state_used = model.addVars(state_keys, vtype=var_type, lb=0.0, ub=1.0, name="state_used")
    x = model.addVars(e1_nodes, e1_nodes, K, vtype=var_type, lb=0.0, ub=1.0, name="x")
    y = model.addVars(C, F, K, vtype=var_type, lb=0.0, ub=1.0, name="y")
    A_e1 = model.addVars(e1_nodes, K, lb=0.0, ub=time_horizon, name="A_e1")
    Q_k = model.addVars(e1_nodes, K, lb=0.0, ub=problem.e1_weight_cap, name="Q_k")
    Vol_k = model.addVars(e1_nodes, K, lb=0.0, ub=problem.e1_volume_cap, name="Vol_k")
    for key, value in fixed_states.items():
        model.addConstr(state_used[key] == value, f"BranchState_{key[0]}_{key[1]}")
    for (i, j, k), value in fixed_arcs.items():
        model.addConstr(x[i, j, k] == value, f"BranchE1Arc_{i}_{j}_{k}")

    model.setObjective(
        gp.quicksum(
            dist_map[i, j] * x[i, j, k]
            for k in K for i in e1_nodes for j in e1_nodes if i != j
        ) + gp.quicksum(columns[rid].cost * lam[rid] for rid in route_ids),
        GRB.MINIMIZE,
    )

    # E2-arc branching is aggregated at the program level.  At a state whose
    # use variable equals one, the "yes" child requires every selected
    # program to contain the physical arc; the "no" child excludes it.  This
    # remains a valid linear branch restriction even before the state itself
    # has been fixed integrally.
    for (sat_id, state_idx, i, j), value in fixed_e2.items():
        arc_use = gp.quicksum(
            lam[rid]
            for rid, column in enumerate(columns)
            if (
                column.sat_id == sat_id
                and column_state[rid] == state_idx
                and any(left == i and right == j for route in column.routes for left, right in zip(route, route[1:]))
            )
        )
        if value:
            model.addConstr(arc_use == state_used[sat_id, state_idx], f"BranchE2ArcYes_{sat_id}_{state_idx}_{i}_{j}")
        else:
            model.addConstr(arc_use == 0.0, f"BranchE2ArcNo_{sat_id}_{state_idx}_{i}_{j}")

    # Customer assignment is induced by the chosen complete satellite program.
    program_assign_rows = {}
    for c in C:
        model.addConstr(gp.quicksum(w[c, s] for s in F) == 1, f"Assign_{c}")
        for s in F:
            program_assign_rows[c, s] = model.addConstr(
                w[c, s] == gp.quicksum(
                    lam[rid] for rid, column in enumerate(columns)
                    if column.sat_id == s and c in column.customers
                ),
                f"ProgramAssign_{c}_{s}",
            )
            if fixed.get(c) == s:
                model.addConstr(w[c, s] == 1.0, f"BranchFix_{c}_{s}")
            elif c in fixed or (c, s) in forbidden:
                model.addConstr(w[c, s] == 0.0, f"BranchForbid_{c}_{s}")

    # State selection is fixed-row for the current pool.  A program column
    # belongs to exactly one validated state and is the only E2 activity at
    # that satellite in an integer solution.
    program_state_rows = {}
    for s in F:
        model.addConstr(
            gp.quicksum(state_used[s, idx] for idx, _ in enumerate(state_values[s])) == served_s[s],
            f"OneStateIfServed_{s}",
        )
        for idx, _ in enumerate(state_values[s]):
            program_state_rows[s, idx] = model.addConstr(
                gp.quicksum(
                    lam[rid] for rid, column in enumerate(columns)
                    if column.sat_id == s and column_state[rid] == idx
                ) == state_used[s, idx],
                f"ProgramStateUse_{s}_{idx}",
            )
        for c in C:
            model.addConstr(served_s[s] >= w[c, s], f"Served_ge_{c}_{s}")
        model.addConstr(served_s[s] <= gp.quicksum(w[c, s] for c in C), f"Served_le_{s}")

    # E1 route topology and safe symmetry breaking.
    for node in e1_del_nodes + e1_pick_nodes:
        model.addConstr(
            gp.quicksum(x[i, node, k] for i in e1_nodes for k in K if i != node) <= 1,
            f"E1_Visit_{node}",
        )
    for k in K:
        out = gp.quicksum(x[depot_id, node, k] for node in e1_del_nodes + e1_pick_nodes)
        model.addConstr(out <= 1, f"E1_Start_{k}")
        model.addConstr(
            gp.quicksum(x[node, depot_id, k] for node in e1_del_nodes + e1_pick_nodes) <= 1,
            f"E1_End_{k}",
        )
        for node in e1_del_nodes + e1_pick_nodes:
            model.addConstr(
                gp.quicksum(x[i, node, k] for i in e1_nodes if i != node)
                == gp.quicksum(x[node, j, k] for j in e1_nodes if j != node),
                f"E1_Flow_{node}_{k}",
            )
            model.addConstr(
                gp.quicksum(x[i, node, k] for i in e1_nodes if i != node) <= out,
                f"E1_Subtour_{node}_{k}",
            )
        model.addConstr(A_e1[depot_id, k] == 0.0, f"E1_DepotTime_{k}")
        for node in e1_nodes:
            model.addConstr(x[node, node, k] == 0.0, f"E1_NoSelf_{node}_{k}")
    for idx in range(len(K) - 1):
        model.addConstr(
            gp.quicksum(x[depot_id, node, K[idx]] for node in e1_del_nodes + e1_pick_nodes)
            >= gp.quicksum(x[depot_id, node, K[idx + 1]] for node in e1_del_nodes + e1_pick_nodes),
            f"E1_VehicleSym_{idx}",
        )
    for s in F:
        for k in K:
            model.addConstr(x[f"{s}p", f"{s}d", k] == 0.0, f"E1_ForbidArc_{s}_{k}")
        del_visit = gp.quicksum(x[i, f"{s}d", k] for i in e1_nodes for k in K if i != f"{s}d")
        pick_visit = gp.quicksum(x[i, f"{s}p", k] for i in e1_nodes for k in K if i != f"{s}p")
        model.addConstr(del_visit == served_s[s], f"E1_DelVisit_{s}")
        model.addConstr(pick_visit == served_s[s], f"E1_PickVisit_{s}")

    # E1 initial loads and load propagation use the assignment implied by
    # program columns.  The ``y`` product is exact at integer values.
    visits_d = {
        (s, k): gp.quicksum(x[i, f"{s}d", k] for i in e1_nodes if i != f"{s}d")
        for s in F for k in K
    }
    for c in C:
        for s in F:
            for k in K:
                model.addConstr(y[c, s, k] <= w[c, s], f"Y_ub1_{c}_{s}_{k}")
                model.addConstr(y[c, s, k] <= visits_d[s, k], f"Y_ub2_{c}_{s}_{k}")
                model.addConstr(y[c, s, k] >= w[c, s] + visits_d[s, k] - 1.0, f"Y_lb_{c}_{s}_{k}")
    for k in K:
        model.addConstr(
            Q_k[depot_id, k] == gp.quicksum(cust_w[c] * y[c, s, k] for c in C for s in F),
            f"E1_InitW_{k}",
        )
        model.addConstr(
            Vol_k[depot_id, k] == gp.quicksum(cv * y[c, s, k] for c in C for s in F),
            f"E1_InitV_{k}",
        )
    model.addConstr(gp.quicksum(Q_k[depot_id, k] for k in K) == total_del_w, "E1_TotalWeight")
    model.addConstr(gp.quicksum(Vol_k[depot_id, k] for k in K) == total_del_v, "E1_TotalVol")
    for idx in range(len(K) - 1):
        model.addConstr(Q_k[depot_id, K[idx]] >= Q_k[depot_id, K[idx + 1]], f"E1_LoadSym_{idx}")

    def _e1_weight_change(node: str):
        sat_id = node[:-1]
        if node.endswith("d"):
            return gp.quicksum(cust_w[c] * w[c, sat_id] for c in C)
        return -gp.quicksum(cw * w[c, sat_id] for c in C)

    def _e1_volume_change(node: str):
        sat_id = node[:-1]
        if node.endswith("d"):
            return gp.quicksum(cv * w[c, sat_id] for c in C)
        return -gp.quicksum(cv * w[c, sat_id] for c in C)

    for i in e1_nodes:
        for j in e1_del_nodes + e1_pick_nodes:
            if i == j:
                continue
            q_change = _e1_weight_change(j)
            v_change = _e1_volume_change(j)
            max_change_w = total_del_w if j.endswith("d") else total_pick_w
            max_change_v = total_del_v if j.endswith("d") else total_pick_v
            m_w = float(problem.e1_weight_cap) + max(max_change_w, 1.0)
            m_v = float(problem.e1_volume_cap) + max(max_change_v, 1.0)
            for k in K:
                model.addConstr(Q_k[j, k] <= Q_k[i, k] - q_change + m_w * (1 - x[i, j, k]), f"E1_W_up_{i}_{j}_{k}")
                model.addConstr(Q_k[j, k] >= Q_k[i, k] - q_change - m_w * (1 - x[i, j, k]), f"E1_W_lo_{i}_{j}_{k}")
                model.addConstr(Vol_k[j, k] <= Vol_k[i, k] - v_change + m_v * (1 - x[i, j, k]), f"E1_V_up_{i}_{j}_{k}")
                model.addConstr(Vol_k[j, k] >= Vol_k[i, k] - v_change - m_v * (1 - x[i, j, k]), f"E1_V_lo_{i}_{j}_{k}")

    for i in e1_nodes:
        for j in e1_del_nodes + e1_pick_nodes:
            if i == j or (j.endswith("d") and i == f"{j[:-1]}p"):
                continue
            for k in K:
                model.addConstr(
                    A_e1[j, k] >= A_e1[i, k] + e1_svc[i] + time_map[i, j] - time_big_m * (1 - x[i, j, k]),
                    f"E1_Time_{i}_{j}_{k}",
                )
    for s in F:
        for c in C:
            due = float(customers[c].tw_del[1])
            if not isfinite(due):
                continue
            for k in K:
                model.addConstr(
                    A_e1[f"{s}d", k] <= due + time_big_m * (2 - visits_d[s, k] - w[c, s]),
                    f"E1_Due_{c}_{s}_{k}",
                )
    if is_tfp_active(TFp):
        for s in F:
            for k in K:
                pick_visit = gp.quicksum(x[i, f"{s}p", k] for i in e1_nodes if i != f"{s}p")
                model.addConstr(A_e1[f"{s}p", k] >= TFp - time_big_m * (1 - pick_visit), f"E1_TFp_{s}_{k}")

    # Program state / E1 time coupling.  ``h`` is the time after E1 service
    # at S_d; ``p`` is the E1 arrival time at S_p, after all program routes
    # have returned.  At the integer level these rows are exactly the M6
    # delivery-start and pickup-return semantics.
    for state in states:
        key = (state.sat_id, state.state_idx)
        for k in K:
            del_visit = visits_d[state.sat_id, k]
            pick_visit = gp.quicksum(
                x[i, f"{state.sat_id}p", k] for i in e1_nodes if i != f"{state.sat_id}p"
            )
            model.addConstr(
                A_e1[f"{state.sat_id}d", k] + e1_svc[f"{state.sat_id}d"]
                <= state.delivery_ready_time + time_big_m * (2 - del_visit - state_used[key]),
                f"State_DeliveryReady_{state.sat_id}_{state.state_idx}_{k}",
            )
            model.addConstr(
                A_e1[f"{state.sat_id}p", k]
                >= state.pickup_ready_time - time_big_m * (2 - pick_visit - state_used[key]),
                f"State_PickupReady_{state.sat_id}_{state.state_idx}_{k}",
            )

    context = {
        "columns": columns,
        "states": states,
        "column_state": column_state,
        "time_horizon": time_horizon,
        # These fixed rows are precisely the coefficients needed by a new
        # column at an *existing* state.  A future dynamic-state generator
        # must add the corresponding E1 link rows before pricing a new state.
        "program_assign_rows": program_assign_rows,
        "program_state_rows": program_state_rows,
        "fixed_assignment": fixed,
        "forbidden_assignments": frozenset(forbidden),
        "fixed_state_use": fixed_states,
        "fixed_e1_arcs": fixed_arcs,
        "fixed_e2_arcs": fixed_e2,
    }
    variables = {
        "program": lam,
        "w": w,
        "served_s": served_s,
        "state_used": state_used,
        "x": x,
        "A_e1": A_e1,
    }
    return model, variables, context


def solve_m6_two_state_global_program_pool(*args, **kwargs):
    """Solve a finite global M6 program pool without claiming certification."""

    model, variables, context = build_m6_two_state_global_program_master(*args, **kwargs)
    model.optimize()
    status = "OPTIMAL" if model.status == GRB.OPTIMAL else str(model.status)
    selected = [
        context["columns"][rid]
        for rid, value in variables["program"].items()
        if model.SolCount and value.X > 0.5
    ]
    return {
        "status": status,
        "proven_optimal": False,
        "proof_scope": "restricted_m6_two_state_global_program_pool_only",
        "objective": float(model.ObjVal) if model.SolCount else None,
        "selected_programs": selected,
        "states": context["states"],
    }
