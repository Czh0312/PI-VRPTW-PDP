# -*- coding: utf-8 -*-
"""Small-scale exact arc-flow MIP for Model 2.

Model 2 = Model 1 (pure delivery) + free customer-satellite assignment.

Unlike the historical restricted route-pool attempt, this current formulation
does not restrict the E2 route universe:
E2 arcs are represented explicitly, so a Gurobi ``OPTIMAL`` status is a global
exact certificate for the formulated Model 2.

Core decisions
--------------
- ``w[c,s]``: customer c is assigned to satellite s (single assignment);
- E1 truck arcs over optional satellite-delivery nodes;
- E2 vehicle arcs at every satellite over all customer delivery tasks;
- E1/E2 timing and loads are jointly optimized.

Only customer delivery tasks exist.  There are no pickup tasks, no E1 pickup
nodes, no pickup-return linkage, and no same-customer delivery-before-pickup
precedence.  The model is intended for small-scale exact benchmarks; large
instances should use Model 2 ALNS.
"""
import gurobipy as gp
from gurobipy import GRB


def build_model(problem, vehicles_per_satellite=4, prune_e2_arcs=True):
    """Build the full, unrestricted Model 2 arc-flow MIP."""
    from models.routing.common.operating_policy import policy_for
    policy_for(problem).require_model(2)
    inf = float("inf")
    depot_id = problem.depot.id
    F = [s.id for s in problem.satellites]
    customers = [c for c in problem.customers if c.demand > 1e-9]
    C = [c.id for c in customers]
    cw = problem.fleet.container_weight
    cv = problem.fleet.container_volume
    K = list(range(problem.e1_vehicle_count))

    # Delivery demand includes the reusable container, matching Models 1/3/4.
    cust_w = {c.id: c.weight_del + cw for c in customers}
    cust_tw = {c.id: c.tw_del for c in customers}
    cust_svc = {c.id: c.service_time for c in customers}

    # E1 has only optional satellite-delivery nodes in Model 2.
    e1_del_nodes = [f"{s}d" for s in F]
    e1_nodes = [depot_id] + e1_del_nodes
    e1_svc = {depot_id: 0.0}
    for s in F:
        e1_svc[f"{s}d"] = getattr(problem.nodes[s], "service_time", 0.0)

    # E2 has one delivery task per customer.
    e2_task_ids = [f"{c}d" for c in C]
    e2_svc = {t: cust_svc[t[:-1]] for t in e2_task_ids}
    e2_tw = {t: cust_tw[t[:-1]] for t in e2_task_ids}

    # Distance/travel maps over all logical nodes.
    all_loc = set([depot_id] + F + e1_del_nodes + e2_task_ids)

    def phys(node_id):
        if node_id in F or node_id == depot_id:
            return node_id
        if node_id.endswith("d"):
            base = node_id[:-1]
            if base in F or base in set(C):
                return base
        return node_id

    dist_map = {}
    time_map = {}
    for i in all_loc:
        phys_i = phys(i)
        for j in all_loc:
            dist_map[i, j] = problem.distance(phys_i, phys(j))
            time_map[i, j] = problem.travel_time(phys_i, phys(j))

    # A safe time horizon.  Any feasible solution has an earliest schedule no
    # later than all finite deadlines plus every possible service/travel term.
    finite_times = [0.0]
    for task in e2_task_ids:
        e_j, l_j = e2_tw[task]
        finite_times.append(e_j)
        if l_j < inf:
            finite_times.append(l_j)
    max_arc = max(time_map.values(), default=0.0)
    total_svc = sum(e1_svc.values()) + sum(e2_svc.values())
    task_count = len(e1_del_nodes) + len(F) * len(e2_task_ids)
    time_horizon = (
        max(finite_times)
        + (task_count + 2) * max_arc
        + total_svc
        + 1.0
    )
    M_time = time_horizon + max_arc

    # Exact E2 arc preprocessing. A_start is non-negative, so a task's absolute
    # earliest window is also a valid lower bound on its absolute service time.
    # If the earliest possible departure from i already reaches j after j's
    # deadline, the arc cannot occur in any feasible solution.
    e2_infeasible = set()
    e2_arc_candidates = 0
    for s in F:
        valid = [s] + e2_task_ids
        for i in valid:
            earliest_i = 0.0 if i == s else e2_tw[i][0]
            service_i = e2_svc.get(i, 0.0)
            for j in valid:
                if i == j:
                    continue
                e2_arc_candidates += 1
                if not prune_e2_arcs or j == s:
                    continue
                latest_j = e2_tw[j][1]
                if (latest_j < inf
                        and earliest_i + service_i + time_map[i, j]
                        > latest_j + 1e-9):
                    e2_infeasible.add((s, i, j))

    total_del_w = sum(cust_w.values())
    total_del_v = len(C) * cv

    m = gp.Model("2E_VRPTW_M2_FreeAssignment_Exact")

    # -------- Free customer-satellite assignment --------
    w = m.addVars(C, F, vtype=GRB.BINARY, name="w")
    for c in C:
        m.addConstr(gp.quicksum(w[c, s] for s in F) == 1, f"Assign_{c}")

    # -------- E1 arc flow over optional satellite-delivery nodes --------
    x = m.addVars(e1_nodes, e1_nodes, K, vtype=GRB.BINARY, name="x")
    A_e1 = m.addVars(e1_nodes, K, vtype=GRB.CONTINUOUS, lb=0.0,
                     ub=time_horizon, name="A_e1")
    Q_k = m.addVars(e1_nodes, K, vtype=GRB.CONTINUOUS, lb=0.0,
                    ub=problem.e1_weight_cap, name="Q_k")
    Vol_k = m.addVars(e1_nodes, K, vtype=GRB.CONTINUOUS, lb=0.0,
                      ub=problem.e1_volume_cap, name="Vol_k")

    # Each satellite-delivery node is visited at most once across all trucks.
    for j in e1_del_nodes:
        m.addConstr(
            gp.quicksum(x[i, j, k] for i in e1_nodes for k in K if i != j) <= 1,
            f"E1_Visit_{j}",
        )

    for k in K:
        m.addConstr(
            gp.quicksum(x[depot_id, j, k] for j in e1_del_nodes) <= 1,
            f"E1_Start_{k}",
        )
        m.addConstr(
            gp.quicksum(x[i, depot_id, k] for i in e1_del_nodes) <= 1,
            f"E1_End_{k}",
        )
        for h in e1_del_nodes:
            m.addConstr(
                gp.quicksum(x[i, h, k] for i in e1_nodes if i != h)
                - gp.quicksum(x[h, j, k] for j in e1_nodes if j != h) == 0,
                f"E1_Flow_{h}_{k}",
            )
        depot_out = gp.quicksum(x[depot_id, j, k] for j in e1_del_nodes)
        for j in e1_del_nodes:
            m.addConstr(
                gp.quicksum(x[i, j, k] for i in e1_nodes if i != j)
                <= depot_out,
                f"E1_Subtour_{j}_{k}",
            )
        m.addConstr(A_e1[depot_id, k] == 0, f"E1_DepotTime_{k}")
        for i in e1_nodes:
            m.addConstr(x[i, i, k] == 0, f"E1_NoSelf_{i}_{k}")

    # Symmetry breaking among homogeneous E1 trucks.
    for idx in range(len(K) - 1):
        m.addConstr(
            gp.quicksum(x[depot_id, j, K[idx]] for j in e1_del_nodes)
            >= gp.quicksum(x[depot_id, j, K[idx + 1]] for j in e1_del_nodes),
            f"E1_VehicleSym_{idx}",
        )

    # A satellite is opened iff at least one customer is assigned to it.
    served_s = m.addVars(F, vtype=GRB.BINARY, name="served_s")
    visits_d = {}
    for s in F:
        visits_d[s] = {}
        for k in K:
            visits_d[s, k] = gp.quicksum(
                x[i, f"{s}d", k] for i in e1_nodes if i != f"{s}d"
            )
        for c in C:
            m.addConstr(served_s[s] >= w[c, s], f"Served_ge_{c}_{s}")
        m.addConstr(
            served_s[s] <= gp.quicksum(w[c, s] for c in C),
            f"Served_le_{s}",
        )
        m.addConstr(
            gp.quicksum(visits_d[s, k] for k in K) == served_s[s],
            f"E1_DelVisit_{s}",
        )

    # E1 initial load linearization: y[c,s,k] = w[c,s] AND truck-k visits S{s}d.
    y = m.addVars(C, F, K, vtype=GRB.BINARY, name="y")
    for c in C:
        for s in F:
            for k in K:
                m.addConstr(y[c, s, k] <= w[c, s], f"Y_ub1_{c}_{s}_{k}")
                m.addConstr(y[c, s, k] <= visits_d[s, k], f"Y_ub2_{c}_{s}_{k}")
                m.addConstr(
                    y[c, s, k] >= w[c, s] + visits_d[s, k] - 1,
                    f"Y_lb_{c}_{s}_{k}",
                )
    for k in K:
        m.addConstr(
            Q_k[depot_id, k]
            == gp.quicksum(cust_w[c] * y[c, s, k] for c in C for s in F),
            f"E1_InitW_{k}",
        )
        m.addConstr(
            Vol_k[depot_id, k]
            == gp.quicksum(cv * y[c, s, k] for c in C for s in F),
            f"E1_InitV_{k}",
        )
    m.addConstr(
        gp.quicksum(Q_k[depot_id, k] for k in K) == total_del_w,
        "E1_TotalWeight",
    )
    m.addConstr(
        gp.quicksum(Vol_k[depot_id, k] for k in K) == total_del_v,
        "E1_TotalVol",
    )
    for idx in range(len(K) - 1):
        m.addConstr(
            Q_k[depot_id, K[idx]] >= Q_k[depot_id, K[idx + 1]],
            f"E1_LoadSym_{idx}",
        )

    # E1 load propagation at satellite-delivery nodes.
    for i in e1_nodes:
        for j in e1_del_nodes:
            if i == j:
                continue
            s = j[:-1]
            q_j = gp.quicksum(cust_w[c] * w[c, s] for c in C)
            v_j = gp.quicksum(cv * w[c, s] for c in C)
            M_w = problem.e1_weight_cap + total_del_w
            M_v = problem.e1_volume_cap + total_del_v
            for k in K:
                m.addConstr(
                    Q_k[j, k] <= Q_k[i, k] - q_j + M_w * (1 - x[i, j, k]),
                    f"E1_W_up_{i}_{j}_{k}",
                )
                m.addConstr(
                    Q_k[j, k] >= Q_k[i, k] - q_j - M_w * (1 - x[i, j, k]),
                    f"E1_W_lo_{i}_{j}_{k}",
                )
                m.addConstr(
                    Vol_k[j, k] <= Vol_k[i, k] - v_j + M_v * (1 - x[i, j, k]),
                    f"E1_V_up_{i}_{j}_{k}",
                )
                m.addConstr(
                    Vol_k[j, k] >= Vol_k[i, k] - v_j - M_v * (1 - x[i, j, k]),
                    f"E1_V_lo_{i}_{j}_{k}",
                )

    # E1 travel-time propagation.
    for i in e1_nodes:
        for j in e1_del_nodes:
            if i == j:
                continue
            for k in K:
                m.addConstr(
                    A_e1[j, k]
                    >= A_e1[i, k] + e1_svc.get(i, 0.0) + time_map[i, j]
                    - M_time * (1 - x[i, j, k]),
                    f"E1_Time_{i}_{j}_{k}",
                )

    # If c is assigned to s, E1 must deliver to s before c's delivery deadline.
    # Uses the same implication form as M1/M3: A_e1[s_d, k] <= l_d + M*(2 - visits - w).
    for s in F:
        for c in C:
            l_d = cust_tw[c][1]
            if l_d >= inf:
                continue
            for k in K:
                m.addConstr(
                    A_e1[f"{s}d", k]
                    <= l_d + M_time * (2 - visits_d[s, k] - w[c, s]),
                    f"E1_Due_{c}_{s}_{k}",
                )

    # -------- E2 arc flow at every satellite --------
    z = {}
    Q_r = {}
    Vol_r = {}
    A_e2 = {}
    V_by_sat = {}
    for s in F:
        vehs = [(s, v) for v in range(vehicles_per_satellite)]
        V_by_sat[s] = vehs
        valid = [s] + e2_task_ids
        for v in vehs:
            for i in valid:
                Q_r[i, v] = m.addVar(
                    # The state is the load *after* serving node i.  Every
                    # delivery-only route starts within capacity and can only
                    # decrease thereafter, so this upper bound is valid for
                    # every node (including an unvisited one, which can be
                    # assigned state zero).
                    lb=0.0, ub=problem.e2_weight_cap, name=f"Q_r_{i}_{v}"
                )
                Vol_r[i, v] = m.addVar(
                    lb=0.0, ub=problem.e2_volume_cap, name=f"Vol_r_{i}_{v}"
                )
                A_e2[i, v] = m.addVar(
                    lb=0.0, ub=time_horizon, name=f"A_e2_{i}_{v}"
                )
                for j in valid:
                    if i != j and (s, i, j) not in e2_infeasible:
                        z[i, j, v] = m.addVar(
                            vtype=GRB.BINARY, name=f"z_{i}_{j}_{v}"
                        )

    # ``served_by_vehicle[s,c,v]`` is the incoming E2 flow of customer c on
    # one labelled vehicle.  It is binary in every integer solution because a
    # vehicle has one predecessor/successor at a visited customer.  Retaining
    # it as a linear expression avoids unnecessary auxiliary binaries while
    # letting the load equations below link route selection to capacity.
    served_by_vehicle = {}
    for s in F:
        valid = [s] + e2_task_ids
        for c in C:
            task_id = f"{c}d"
            for v in V_by_sat[s]:
                served_by_vehicle[s, c, v] = gp.quicksum(
                    z[i, task_id, v]
                    for i in valid
                    if i != task_id and (i, task_id, v) in z
                )

    # Serving c from s is equivalent to w[c,s].
    for s in F:
        for c in C:
            coverage = gp.quicksum(
                served_by_vehicle[s, c, v] for v in V_by_sat[s]
            )
            m.addConstr(coverage == w[c, s], f"E2_Cov_{c}_{s}")

    for s in F:
        vehs = V_by_sat[s]
        valid = [s] + e2_task_ids
        for v in vehs:
            m.addConstr(
                gp.quicksum(z[s, j, v] for j in e2_task_ids if (s, j, v) in z)
                == gp.quicksum(z[i, s, v] for i in e2_task_ids if (i, s, v) in z),
                f"E2_DepotBal_{s}_{v}",
            )
            m.addConstr(
                gp.quicksum(z[s, j, v] for j in e2_task_ids if (s, j, v) in z)
                <= 1,
                f"E2_Start_{s}_{v}",
            )
            for h in e2_task_ids:
                m.addConstr(
                    gp.quicksum(
                        z[i, h, v] for i in valid
                        if i != h and (i, h, v) in z
                    )
                    - gp.quicksum(
                        z[h, j, v] for j in valid
                        if j != h and (h, j, v) in z
                    )
                    == 0,
                    f"E2_Flow_{h}_{v}",
                )
            m.addConstr(A_e2[s, v] == 0, f"E2_SatAnchor_{s}_{v}")
        # Vehicles at one satellite are homogeneous.  Requiring used vehicle
        # labels to form a prefix is safe: any feasible solution can be
        # relabelled within a satellite (including all timing/load states) to
        # satisfy it without changing its routes, assignment, or objective.
        # We deliberately do not impose an order on route contents/times;
        # those stronger rules need not be symmetry-preserving.
        for v_idx in range(vehicles_per_satellite - 1):
            v = (s, v_idx)
            v_next = (s, v_idx + 1)
            m.addConstr(
                gp.quicksum(
                    z[s, j, v] for j in e2_task_ids if (s, j, v) in z
                )
                >= gp.quicksum(
                    z[s, j, v_next]
                    for j in e2_task_ids if (s, j, v_next) in z
                ),
                f"E2_UseSym_{s}_{v_idx}",
            )

    # Relative E2 timing along each route.
    for s in F:
        valid = [s] + e2_task_ids
        for v in V_by_sat[s]:
            for i in valid:
                for j in e2_task_ids:
                    if i == j or (i, j, v) not in z:
                        continue
                    if (s, i, j) in e2_infeasible:
                        continue
                    m.addConstr(
                        A_e2[j, v]
                        >= A_e2[i, v] + e2_svc.get(i, 0.0) + time_map[i, j]
                        - M_time * (1 - z[i, j, v]),
                        f"E2_Time_{i}_{j}_{v}",
                    )

    # E2 initial load.  In a pure-delivery route there is a canonical state
    # representation: depart with exactly the goods/containers delivered by
    # that vehicle, and finish its final delivery with zero load.  Requiring
    # this equality does not remove a route solution--any unused departure
    # load in the former formulation can simply remain at the satellite--but
    # gives the LP relaxation a direct route-to-capacity link.
    for s in F:
        vehs = V_by_sat[s]
        for v in vehs:
            m.addConstr(
                Q_r[s, v]
                == gp.quicksum(
                    cust_w[c] * served_by_vehicle[s, c, v] for c in C
                ),
                f"E2_InitWExact_{s}_{v}",
            )
            m.addConstr(
                Vol_r[s, v]
                == gp.quicksum(
                    cv * served_by_vehicle[s, c, v] for c in C
                ),
                f"E2_InitVExact_{s}_{v}",
            )
        # Redundant in integer solutions, but a useful compact coupling of
        # assignment and aggregate E2 capacity at the root relaxation.
        m.addConstr(
            gp.quicksum(Q_r[s, v] for v in vehs)
            == gp.quicksum(cust_w[c] * w[c, s] for c in C),
            f"E2_TotalInitW_{s}",
        )
        m.addConstr(
            gp.quicksum(Vol_r[s, v] for v in vehs)
            == gp.quicksum(cv * w[c, s] for c in C),
            f"E2_TotalInitV_{s}",
        )
        # Sorting the canonical departure loads is also label-symmetric: after
        # sorting used routes by their load, all unused (zero-load) vehicles
        # remain at the end.  It is safe together with E2_UseSym and removes
        # additional permutations among equally feasible vehicle labels.
        for v_idx in range(vehicles_per_satellite - 1):
            m.addConstr(
                Q_r[s, (s, v_idx)] >= Q_r[s, (s, v_idx + 1)],
                f"E2_LoadSym_{s}_{v_idx}",
            )

    # E2 load propagation (all tasks are deliveries).
    for s in F:
        valid = [s] + e2_task_ids
        for v in V_by_sat[s]:
            for i in valid:
                for j in e2_task_ids:
                    if i == j or (i, j, v) not in z:
                        continue
                    c = j[:-1]
                    q_j = cust_w[c]
                    v_j = cv
                    # With 0 <= Q_r <= cap and 0 <= Vol_r <= cap, these are
                    # the exact inactive-arc constants.  Separate upper and
                    # lower values are materially tighter than the inherited
                    # 100000 constant while preserving all feasible routes.
                    M_w_up = problem.e2_weight_cap + q_j
                    M_w_lo = max(0.0, problem.e2_weight_cap - q_j)
                    M_v_up = problem.e2_volume_cap + v_j
                    M_v_lo = max(0.0, problem.e2_volume_cap - v_j)
                    m.addConstr(
                        Q_r[j, v] <= Q_r[i, v] - q_j + M_w_up * (1 - z[i, j, v]),
                        f"E2_W_up_{i}_{j}_{v}",
                    )
                    m.addConstr(
                        Q_r[j, v] >= Q_r[i, v] - q_j - M_w_lo * (1 - z[i, j, v]),
                        f"E2_W_lo_{i}_{j}_{v}",
                    )
                    m.addConstr(
                        Vol_r[j, v] <= Vol_r[i, v] - v_j + M_v_up * (1 - z[i, j, v]),
                        f"E2_V_up_{i}_{j}_{v}",
                    )
                    m.addConstr(
                        Vol_r[j, v] >= Vol_r[i, v] - v_j - M_v_lo * (1 - z[i, j, v]),
                        f"E2_V_lo_{i}_{j}_{v}",
                    )

    # -------- E1-E2 synchronization --------
    A_start = m.addVars(
        F, range(vehicles_per_satellite),
        vtype=GRB.CONTINUOUS, lb=0.0, ub=time_horizon, name="A_start",
    )
    A_return = m.addVars(
        F, range(vehicles_per_satellite),
        vtype=GRB.CONTINUOUS, lb=0.0, ub=time_horizon, name="A_return",
    )
    for s in F:
        for v_idx in range(vehicles_per_satellite):
            v = (s, v_idx)
            used = gp.quicksum(
                z[s, j, v] for j in e2_task_ids if (s, j, v) in z
            )
            # No Link_StartUsed/Link_ReturnUsed: M1 does not constrain
            # A_start/A_return for unused vehicles.  Forcing them to 0
            # over-constrains the model.
            for k in K:
                m.addConstr(
                    A_start[s, v_idx]
                    >= A_e1[f"{s}d", k] + e1_svc[f"{s}d"]
                    - M_time * (2 - visits_d[s, k] - used),
                    f"Link_Start_{s}_{v_idx}_{k}",
                )

            for task in e2_task_ids:
                e_j, l_j = e2_tw[task]
                served = gp.quicksum(
                    z[i, task, v]
                    for i in [s] + e2_task_ids
                    if i != task and (i, task, v) in z
                )
                absolute_arrival = A_start[s, v_idx] + A_e2[task, v]
                m.addConstr(
                    absolute_arrival >= e_j - M_time * (1 - served),
                    f"Link_TW_lo_{s}_{v_idx}_{task}",
                )
                if l_j < inf:
                    m.addConstr(
                        absolute_arrival <= l_j + M_time * (1 - served),
                        f"Link_TW_hi_{s}_{v_idx}_{task}",
                    )

            for i in e2_task_ids:
                if (i, s, v) not in z:
                    continue
                m.addConstr(
                    A_return[s, v_idx]
                    >= A_start[s, v_idx] + A_e2[i, v]
                    + e2_svc.get(i, 0.0) + time_map[i, s]
                    - M_time * (1 - z[i, s, v]),
                    f"Link_Ret_lo_{s}_{v_idx}_{i}",
                )
                m.addConstr(
                    A_return[s, v_idx]
                    <= A_start[s, v_idx] + A_e2[i, v]
                    + e2_svc.get(i, 0.0) + time_map[i, s]
                    + M_time * (1 - z[i, s, v]),
                    f"Link_Ret_hi_{s}_{v_idx}_{i}",
                )

    # -------- Objective: total E1 + E2 distance --------
    obj = (
        gp.quicksum(
            dist_map[i, j] * x[i, j, k]
            for k in K for i in e1_nodes for j in e1_nodes if i != j
        )
        + gp.quicksum(dist_map[i, j] * z[i, j, v] for (i, j, v) in z)
    )
    m.setObjective(obj, GRB.MINIMIZE)

    ctx = {
        "depot_id": depot_id,
        "F": F,
        "C": C,
        "K": K,
        "e1_nodes": e1_nodes,
        "e1_del_nodes": e1_del_nodes,
        "e2_task_ids": e2_task_ids,
        "V_by_sat": V_by_sat,
        "time_horizon": time_horizon,
        "M_time": M_time,
        "formulation_stats": {
            "e2_arc_candidates_per_vehicle_pool": e2_arc_candidates,
            "e2_arcs_pruned_per_vehicle_pool": len(e2_infeasible),
            "e2_arc_binaries_created": len(z),
        },
    }
    vars_dict = {
        "w": w,
        "served_s": served_s,
        "y": y,
        "x": x,
        "z": z,
        "A_e1": A_e1,
        "Q_k": Q_k,
        "Vol_k": Vol_k,
        "A_e2": A_e2,
        "Q_r": Q_r,
        "Vol_r": Vol_r,
        "A_start": A_start,
        "A_return": A_return,
    }
    return m, vars_dict, ctx


def apply_route_warm_start(vars_dict, ctx, problem, warm_start_routes):
    """Apply a complete discrete M1/M2 route start."""
    if not warm_start_routes:
        return
    e1_routes, e2_routes_by_sat = warm_start_routes
    w = vars_dict["w"]
    served_s = vars_dict["served_s"]
    y = vars_dict["y"]
    x = vars_dict["x"]
    z = vars_dict["z"]
    F, C, K = ctx["F"], ctx["C"], ctx["K"]
    e2_task_ids = set(ctx["e2_task_ids"])

    for var_dict in (w, served_s, y, x, z):
        for var in var_dict.values():
            var.Start = 0.0

    assignment = {}
    for sat_id, routes in (e2_routes_by_sat or {}).items():
        for route in routes or []:
            for node_id in route:
                if node_id in e2_task_ids:
                    assignment[node_id[:-1]] = sat_id
    for customer in C:
        sat_id = assignment.get(customer)
        if sat_id not in F:
            node = problem.nodes.get(customer)
            sat_id = getattr(node, "assigned_sat", None)
        if sat_id in F:
            assignment[customer] = sat_id
            w[customer, sat_id].Start = 1.0
            served_s[sat_id].Start = 1.0

    def e1_weight(route):
        delivered = {
            node[:-1] for node in route or []
            if node.endswith("d") and node[:-1] in F
        }
        return sum(
            problem.nodes[c].weight_del + problem.fleet.container_weight
            for c, sat in assignment.items() if sat in delivered
        )

    ordered_e1 = sorted(
        (route for route in (e1_routes or []) if route),
        key=e1_weight,
        reverse=True,
    )
    for k, route in zip(K, ordered_e1):
        for i, j in zip(route, route[1:]):
            if (i, j, k) in x:
                x[i, j, k].Start = 1.0
        delivered = {
            node[:-1] for node in route
            if node.endswith("d") and node[:-1] in F
        }
        for customer, sat_id in assignment.items():
            if sat_id in delivered and (customer, sat_id, k) in y:
                y[customer, sat_id, k].Start = 1.0

    def e2_weight(route):
        return sum(
            problem.nodes[node[:-1]].weight_del
            + problem.fleet.container_weight
            for node in route or [] if node in e2_task_ids
        )

    for sat_id, routes in (e2_routes_by_sat or {}).items():
        if sat_id not in ctx["V_by_sat"]:
            continue
        ordered = sorted(
            (route for route in (routes or []) if route),
            key=e2_weight,
            reverse=True,
        )
        for vehicle, route in zip(ctx["V_by_sat"][sat_id], ordered):
            for i, j in zip(route, route[1:]):
                if (i, j, vehicle) in z:
                    z[i, j, vehicle].Start = 1.0
