# -*- coding: utf-8 -*-
"""Joint E2 pricing MIP for Model 3 branch-price root.

Extends the per-satellite pricing MIP (e2_route_cg._solve_pricing_mip) with
E1-linking dual terms: sigma_start (Link_Start duals) and rho_pickup
(Link_Pickup duals). Two continuous variables S_var (route start) and R_var
(route return) are added; the reduced-cost objective includes
  - sigma_start * S_var - rho_pickup * R_var + sum (alpha_lo - alpha_hi) * A[t].

When both sigma and rho are zero (the typical LP-optimum case where big-M
linking rows are slack), this reduces to standard per-satellite pricing.
"""

import time

import gurobipy as gp
from gurobipy import GRB

from .e2_route_cg import (
    EPS,
    PRICING_PROOF_TOL,
    E2RouteColumn,
    _phys,
    _service_time,
    _signed_q,
    _signed_v,
    _route_cost,
    _extract_pricing_path,
    _status_name,
)


def solve_joint_pricing_mip(problem, sat_id, tasks,
                            pi_cover, nu_vehicle,
                            sigma_start, rho_pickup,
                            alpha_lo, alpha_hi,
                            pricing_time_limit=20.0,
                            pool_solutions=1,
                            output_flag=False,
                            same_pairs=None, diff_pairs=None,
                            gurobi_params=None,
                            proof_as_feasibility=True,
                            forbidden_paths=None,
                            cut_duals=None,
                            s_max_floor=None):
    """Solve the joint E2 pricing MIP for one satellite.

    Parameters
    ----------
    problem : ProblemInstance
    sat_id : str
    tasks : list[Task]
    pi_cover : dict[str, float]
        E2_Cover duals (free sign, equality rows).
    nu_vehicle : float
        E2_RouteLimit dual for this satellite (≤ 0, ≤ row).
    sigma_start : float
        Aggregated Link_Start dual for this satellite (≥ 0 if active).
    rho_pickup : float
        Aggregated Link_Pickup dual for this satellite (≥ 0 if active).
    alpha_lo : dict[str, float]
        E2_TaskTW lower-bound duals.
    alpha_hi : dict[str, float]
        E2_TaskTW upper-bound duals.
    pricing_time_limit : float
    pool_solutions : int
    output_flag : bool
    same_pairs, diff_pairs : list | None
        Ryan-Foster branching constraints.
    gurobi_params : dict | None
    proof_as_feasibility : bool
        If True, constrain reduced_cost <= -PRICING_PROOF_TOL, set obj=0.
    forbidden_paths : list | None
        Paths to lazily exclude (proof mode only).
    cut_duals : list | None
        SRI cut duals.
    s_max_floor : float | None
        Restrict to routes with latest feasible departure >= floor.

    Returns
    -------
    (list[E2RouteColumn], dict) — columns and info dict.
    """
    # --- Fast path: if all linking duals are zero, delegate to per-sat pricing ---
    _sigma = sigma_start if isinstance(sigma_start, (int, float)) else 0.0
    _rho = rho_pickup if isinstance(rho_pickup, (int, float)) else 0.0
    _alpha_active = bool(alpha_lo or alpha_hi)
    if abs(_sigma) < EPS and abs(_rho) < EPS and not _alpha_active:
        from .e2_route_cg import _solve_pricing_mip
        return _solve_pricing_mip(
            problem, sat_id, tasks, pi_cover, nu_vehicle,
            pricing_time_limit=pricing_time_limit,
            pool_solutions=pool_solutions,
            output_flag=output_flag,
            same_pairs=same_pairs, diff_pairs=diff_pairs,
            gurobi_params=gurobi_params,
            proof_as_feasibility=proof_as_feasibility,
            forbidden_paths=forbidden_paths,
            cut_duals=cut_duals,
            s_max_floor=s_max_floor,
        )

    # --- Full joint pricing MIP (sigma/rho non-zero or TW duals active) ---
    started = time.time()
    task_ids = [t.id for t in tasks]
    task_map = {t.id: t for t in tasks}
    nodes = [sat_id] + task_ids
    forbidden_path_set = {tuple(path) for path in (forbidden_paths or [])}

    m = gp.Model(f"E2_JointPricing_{sat_id}")
    m.setParam("OutputFlag", 1 if output_flag else 0)
    m.setParam("TimeLimit", pricing_time_limit)
    m.setParam("MIPGap", 0.0)
    if proof_as_feasibility:
        m.setParam("FeasibilityTol", 1e-9)
    for key, value in (gurobi_params or {}).items():
        m.setParam(key, value)
    if pool_solutions and pool_solutions > 1 and not proof_as_feasibility:
        m.setParam("PoolSearchMode", 2)
        m.setParam("PoolSolutions", int(pool_solutions))

    # Time window bounds (same as _solve_pricing_mip)
    max_latest = max(
        [t.tw[1] for t in tasks if t.tw[1] < float("inf")] + [0.0]
    )
    m_time = max(100000.0, max_latest + 10000.0)
    if s_max_floor is not None:
        time_lb = {sat_id: float(s_max_floor)}
        time_ub = {sat_id: m_time}
    else:
        time_lb = {sat_id: 0.0}
        time_ub = {sat_id: 0.0}
    for task in tasks:
        earliest, latest = task.tw
        time_lb[task.id] = max(0.0, earliest)
        time_ub[task.id] = latest if latest < float("inf") else m_time

    # --- Variables ---
    y = m.addVars(task_ids, vtype=GRB.BINARY, name="y")
    z = m.addVars(
        [(i, j) for i in nodes for j in nodes if i != j],
        vtype=GRB.BINARY,
        name="z",
    )
    A_vars = {
        node: m.addVar(
            lb=0.0 if node != sat_id else time_lb[node],
            ub=time_ub[node],
            name=f"A[{node}]",
        )
        for node in nodes
    }
    Q = m.addVars(nodes, lb=0.0, ub=problem.e2_weight_cap, name="Q")
    Vol = m.addVars(nodes, lb=0.0, ub=problem.e2_volume_cap, name="Vol")
    order = m.addVars(task_ids, lb=0.0, ub=len(task_ids), name="ord")

    # Joint additions: S_var (route start), R_var (route return)
    S_var = m.addVar(lb=0.0, ub=m_time, name="S_var")
    R_var = m.addVar(lb=0.0, ub=m_time, name="R_var")

    # SRI cut auxiliary variables (same as _solve_pricing_mip)
    cut_aux = {}
    for cut_idx, cut in enumerate(cut_duals or []):
        if cut.get("type") != "sri":
            continue
        cut_tasks = [tid for tid in cut["tasks"] if tid in y]
        if not cut_tasks:
            continue
        selected_count = gp.quicksum(y[tid] for tid in cut_tasks)
        if len(cut_tasks) == 3:
            h = m.addVar(vtype=GRB.BINARY, name=f"sri_coeff[{cut_idx}]")
            a, b, c = cut_tasks
            m.addConstr(h >= y[a] + y[b] - 1, name=f"sri_pair_ab_{cut_idx}")
            m.addConstr(h >= y[a] + y[c] - 1, name=f"sri_pair_ac_{cut_idx}")
            m.addConstr(h >= y[b] + y[c] - 1, name=f"sri_pair_bc_{cut_idx}")
            m.addConstr(2 * h <= selected_count, name=f"sri_coeff_ub_{cut_idx}")
        else:
            h = m.addVar(
                lb=0.0, ub=len(cut_tasks) // 2,
                vtype=GRB.INTEGER, name=f"sri_coeff[{cut_idx}]",
            )
            m.addConstr(2 * h <= selected_count, name=f"sri_coeff_ub_{cut_idx}")
            m.addConstr(2 * h >= selected_count - 1, name=f"sri_coeff_lb_{cut_idx}")
        cut_aux[cut_idx] = h

    # --- Constraints (same scaffold as _solve_pricing_mip) ---
    m.addConstr(gp.quicksum(z[sat_id, j] for j in task_ids) == 1, name="start_once")
    m.addConstr(gp.quicksum(z[i, sat_id] for i in task_ids) == 1, name="return_once")

    for h in task_ids:
        incoming = gp.quicksum(z[i, h] for i in nodes if i != h)
        outgoing = gp.quicksum(z[h, j] for j in nodes if j != h)
        m.addConstr(incoming == y[h], name=f"in_{h}")
        m.addConstr(outgoing == y[h], name=f"out_{h}")
        m.addConstr(A_vars[h] >= time_lb[h] * y[h], name=f"tw_lb_{h}")
        m.addConstr(A_vars[h] <= time_ub[h] * y[h], name=f"tw_ub_{h}")
        m.addConstr(order[h] >= y[h], name=f"ord_lb_{h}")
        m.addConstr(order[h] <= len(task_ids) * y[h], name=f"ord_used_{h}")
        m.addConstr(Q[h] <= problem.e2_weight_cap * y[h], name=f"w_used_{h}")
        m.addConstr(Vol[h] <= problem.e2_volume_cap * y[h], name=f"vol_used_{h}")

    for a, b in same_pairs or []:
        if a in y and b in y:
            m.addConstr(y[a] == y[b], name=f"branch_same_{a}_{b}")
    for a, b in diff_pairs or []:
        if a in y and b in y:
            m.addConstr(y[a] + y[b] <= 1, name=f"branch_diff_{a}_{b}")

    # MTZ subtour elimination
    for i in task_ids:
        for j in task_ids:
            if i == j:
                continue
            m.addConstr(
                order[j] >= order[i] + 1 - len(task_ids) * (1 - z[i, j]),
                name=f"mtz_{i}_{j}",
            )
    for j in task_ids:
        m.addConstr(
            order[j] >= 1 - len(task_ids) * (1 - z[sat_id, j]),
            name=f"start_order_{j}",
        )

    # Initial load / capacity totals
    del_tasks = [t for t in tasks if t.kind == "d"]
    pick_tasks = [t for t in tasks if t.kind == "p"]
    m.addConstr(
        Q[sat_id] == gp.quicksum(t.w * y[t.id] for t in del_tasks),
        name="initial_weight",
    )
    m.addConstr(
        Vol[sat_id] == gp.quicksum(t.v * y[t.id] for t in del_tasks),
        name="initial_volume",
    )
    m.addConstr(
        gp.quicksum(t.w * y[t.id] for t in pick_tasks) <= problem.e2_weight_cap,
        name="total_pickup_weight_cap",
    )
    m.addConstr(
        gp.quicksum(t.v * y[t.id] for t in pick_tasks) <= problem.e2_volume_cap,
        name="total_pickup_volume_cap",
    )
    if s_max_floor is None:
        m.addConstr(A_vars[sat_id] == 0.0, name="sat_time")
    else:
        m.addConstr(A_vars[sat_id] >= float(s_max_floor), name="sat_depart_floor")

    # --- Joint linking constraints: S_var/R_var tie to first/last task ---
    for h in task_ids:
        # S_var <= A[h] when h is the first task (z[sat, h] == 1)
        m.addConstr(
            S_var <= A_vars[h] + m_time * (1 - z[sat_id, h]),
            name=f"S_link_{h}",
        )
        # R_var >= A[h] + service + travel_back when h is the last task
        svc_h = _service_time(problem, task_map, h)
        travel_back = problem.travel_time(_phys(task_map, h), sat_id)
        m.addConstr(
            R_var >= A_vars[h] + svc_h + travel_back - m_time * (1 - z[h, sat_id]),
            name=f"R_link_{h}",
        )

    # Arc time + load propagation (same as _solve_pricing_mip)
    for i in nodes:
        s_i = _service_time(problem, task_map, i)
        for j in task_ids:
            if i == j:
                continue
            travel = problem.travel_time(_phys(task_map, i), _phys(task_map, j))
            if time_lb[i] + s_i + travel > time_ub[j] + EPS:
                z[i, j].UB = 0.0
            arc_m_time = max(0.0, time_ub[i] + s_i + travel)
            m.addConstr(
                A_vars[j] >= A_vars[i] + s_i + travel - arc_m_time * (1 - z[i, j]),
                name=f"time_{i}_{j}",
            )
            task_j = task_map[j]
            q_j = _signed_q(task_j)
            v_j = _signed_v(task_j)
            m_w = problem.e2_weight_cap + abs(q_j)
            m_v = problem.e2_volume_cap + abs(v_j)
            m.addConstr(Q[j] <= Q[i] - q_j + m_w * (1 - z[i, j]), name=f"w_up_{i}_{j}")
            m.addConstr(Q[j] >= Q[i] - q_j - m_w * (1 - z[i, j]), name=f"w_lo_{i}_{j}")
            m.addConstr(Vol[j] <= Vol[i] - v_j + m_v * (1 - z[i, j]), name=f"vol_up_{i}_{j}")
            m.addConstr(Vol[j] >= Vol[i] - v_j - m_v * (1 - z[i, j]), name=f"vol_lo_{i}_{j}")

    # Pickup-after-delivery precedence
    for cust_id in sorted({t.cust for t in tasks}):
        d_id = f"{cust_id}d"
        p_id = f"{cust_id}p"
        if d_id in task_map and p_id in task_map:
            service_d = _service_time(problem, task_map, d_id)
            prec_m_time = max(0.0, time_ub[d_id] + service_d)
            m.addConstr(
                A_vars[p_id] >= A_vars[d_id] + service_d
                - prec_m_time * (2 - y[d_id] - y[p_id]),
                name=f"prec_{cust_id}",
            )
            m.addConstr(
                order[p_id] >= order[d_id] + 1
                - len(task_ids) * (2 - y[d_id] - y[p_id]),
                name=f"prec_order_{cust_id}",
            )
            if (p_id, d_id) in z:
                m.addConstr(z[p_id, d_id] == 0, name=f"forbid_pd_{cust_id}")

    # Pairwise time-window ordering constraints
    for left_idx, i in enumerate(task_ids):
        service_i = _service_time(problem, task_map, i)
        for j in task_ids[left_idx + 1:]:
            service_j = _service_time(problem, task_map, j)
            i_before_j_possible = (
                time_lb[i] + service_i
                + problem.travel_time(_phys(task_map, i), _phys(task_map, j))
                <= time_ub[j] + EPS
            )
            j_before_i_possible = (
                time_lb[j] + service_j
                + problem.travel_time(_phys(task_map, j), _phys(task_map, i))
                <= time_ub[i] + EPS
            )
            if not i_before_j_possible and not j_before_i_possible:
                m.addConstr(y[i] + y[j] <= 1, name=f"time_pair_conflict_{i}_{j}")
            elif not i_before_j_possible:
                m.addConstr(
                    order[i] >= order[j] + 1
                    - len(task_ids) * (2 - y[i] - y[j]),
                    name=f"time_order_{j}_before_{i}",
                )
            elif not j_before_i_possible:
                m.addConstr(
                    order[j] >= order[i] + 1
                    - len(task_ids) * (2 - y[i] - y[j]),
                    name=f"time_order_{i}_before_{j}",
                )

    # --- Objective (joint: includes linking dual terms) ---
    route_cost = gp.quicksum(
        problem.distance(_phys(task_map, i), _phys(task_map, j)) * z[i, j]
        for (i, j) in z.keys()
    )
    dual_reward = gp.quicksum(pi_cover.get(tid, 0.0) * y[tid] for tid in task_ids)
    cut_reward = gp.quicksum(
        cut.get("dual", 0.0) * cut_aux[cut_idx]
        for cut_idx, cut in enumerate(cut_duals or [])
        if cut_idx in cut_aux
    )
    # Joint dual terms
    tw_dual_expr = gp.quicksum(
        (alpha_lo.get(tid, 0.0) - alpha_hi.get(tid, 0.0)) * A_vars[tid]
        for tid in task_ids
    )
    reduced_expr = (route_cost - dual_reward - float(nu_vehicle) - cut_reward
                    - float(_sigma) * S_var - float(_rho) * R_var + tw_dual_expr)

    if proof_as_feasibility:
        m.addConstr(reduced_expr <= -PRICING_PROOF_TOL, name="negative_reduced_cost")
        m.setObjective(0.0, GRB.MINIMIZE)
    else:
        m.setObjective(reduced_expr, GRB.MINIMIZE)
    m.optimize()

    # Lazy forbidden-path loop (proof mode only)
    lazy_forbidden_count = 0
    while proof_as_feasibility and forbidden_path_set and m.SolCount:
        sol_z = {key: var.X for key, var in z.items()}
        path = tuple(_extract_pricing_path(sat_id, task_ids, sol_z))
        if path not in forbidden_path_set:
            break
        arcs = list(zip(path[:-1], path[1:]))
        if not arcs or not all(arc in z for arc in arcs):
            break
        lazy_forbidden_count += 1
        m.addConstr(
            gp.quicksum(z[arc] for arc in arcs) <= len(arcs) - 1,
            name=f"lazy_forbid_existing_path_{lazy_forbidden_count}",
        )
        remaining = pricing_time_limit - (time.time() - started)
        if remaining <= 1.0:
            break
        m.setParam("TimeLimit", remaining)
        m.optimize()

    # --- Extract bound and reduced cost ---
    reduced_cost_bound = float("inf")
    if proof_as_feasibility:
        if m.status == GRB.INFEASIBLE:
            reduced_cost_bound = 0.0
        elif m.SolCount:
            reduced_cost_bound = float("-inf")
    else:
        try:
            reduced_cost_bound = float(m.ObjBound)
        except Exception:
            pass

    reduced_cost = None
    if m.SolCount:
        if proof_as_feasibility:
            reduced_cost = float(
                sum(problem.distance(_phys(task_map, i), _phys(task_map, j)) * z[i, j].X
                    for (i, j) in z.keys())
                - sum(pi_cover.get(tid, 0.0) * y[tid].X for tid in task_ids)
                - float(nu_vehicle)
                - sum(cut.get("dual", 0.0) * cut_aux[idx].X
                      for idx, cut in enumerate(cut_duals or []) if idx in cut_aux)
                - float(_sigma) * S_var.X - float(_rho) * R_var.X
                + sum((alpha_lo.get(tid, 0.0) - alpha_hi.get(tid, 0.0)) * A_vars[tid].X
                      for tid in task_ids)
            )
        else:
            reduced_cost = float(m.ObjVal)

    info = {
        "status": _status_name(m.status),
        "status_code": int(m.status),
        "reduced_cost": reduced_cost,
        "reduced_cost_bound": reduced_cost_bound,
        "pool_solution_count": int(m.SolCount),
        "runtime": float(getattr(m, "Runtime", time.time() - started)),
        "proof_as_feasibility": proof_as_feasibility,
        "lazy_forbidden_count": lazy_forbidden_count,
    }

    if not m.SolCount:
        return [], info

    # --- Column building (same pattern as _solve_pricing_mip) ---
    cols = []
    seen = set()
    excluded_task_sets = set()
    sol_count = min(int(m.SolCount), int(pool_solutions or 1))

    def _compute_joint_reduced_cost(sol_z, sol_y, s_val, r_val):
        return float(
            sum(problem.distance(_phys(task_map, i), _phys(task_map, j)) * v
                for (i, j), v in sol_z.items())
            - sum(pi_cover.get(tid, 0.0) * v for tid, v in sol_y.items())
            - float(nu_vehicle)
            - sum(cut.get("dual", 0.0) * cut_aux[idx].Xn
                  for idx, cut in enumerate(cut_duals or []) if idx in cut_aux)
            - float(_sigma) * s_val - float(_rho) * r_val
            + sum((alpha_lo.get(tid, 0.0) - alpha_hi.get(tid, 0.0)) * v_avars
                  for tid, v_avars in sol_y.items())
        )

    for sol_idx in range(sol_count):
        need_recompute = proof_as_feasibility
        if sol_idx:
            m.setParam("SolutionNumber", sol_idx)
            sol_z = {key: var.Xn for key, var in z.items()}
            sol_y = {key: var.Xn for key, var in y.items()}
            s_val = S_var.Xn
            r_val = R_var.Xn
            obj = (_compute_joint_reduced_cost(sol_z, sol_y, s_val, r_val)
                   if need_recompute else float(m.PoolObjVal))
        else:
            sol_z = {key: var.X for key, var in z.items()}
            sol_y = {key: var.X for key, var in y.items()}
            s_val = S_var.X
            r_val = R_var.X
            obj = (_compute_joint_reduced_cost(sol_z, sol_y, s_val, r_val)
                   if need_recompute else float(m.ObjVal))
        if obj >= -EPS:
            continue
        selected_set = frozenset(tid for tid, value in sol_y.items() if value > 0.5)
        excluded_task_sets.add(selected_set)
        path = _extract_pricing_path(sat_id, task_ids, sol_z)
        if len(path) <= 2:
            continue
        path = tuple(path)
        if path in seen:
            continue
        seen.add(path)
        selected = tuple(n for n in path if n in task_map)
        cols.append(E2RouteColumn(
            sat_id=sat_id,
            path=path,
            tasks=selected,
            cost=_route_cost(problem, task_map, path),
            source="joint_pricing",
        ))

    # Diverse task sets loop
    if len(cols) < int(pool_solutions or 1):
        m.setParam("PoolSearchMode", 0)
        m.setParam("PoolSolutions", 1)
        while len(cols) < int(pool_solutions or 1):
            remaining = pricing_time_limit - (time.time() - started)
            if remaining <= 1.0:
                break
            for selected_set in list(excluded_task_sets):
                cname = "exclude_tasks_" + str(abs(hash(selected_set)))
                if m.getConstrByName(cname) is not None:
                    continue
                if not selected_set:
                    continue
                m.addConstr(
                    gp.quicksum(y[tid] for tid in selected_set)
                    - gp.quicksum(y[tid] for tid in task_ids if tid not in selected_set)
                    <= len(selected_set) - 1,
                    name=cname,
                )
            m.setParam("TimeLimit", remaining)
            m.optimize()
            if not m.SolCount:
                break
            sol_y = {key: var.X for key, var in y.items()}
            s_val = S_var.X
            r_val = R_var.X
            if proof_as_feasibility:
                sol_z = {key: var.X for key, var in z.items()}
                proof_obj = _compute_joint_reduced_cost(sol_z, sol_y, s_val, r_val)
                if proof_obj >= -EPS:
                    break
            elif float(m.ObjVal) >= -EPS:
                break
            selected_set = frozenset(tid for tid, value in sol_y.items() if value > 0.5)
            if selected_set in excluded_task_sets:
                break
            excluded_task_sets.add(selected_set)
            sol_z = {key: var.X for key, var in z.items()}
            path = tuple(_extract_pricing_path(sat_id, task_ids, sol_z))
            if len(path) <= 2 or path in seen:
                continue
            seen.add(path)
            selected = tuple(n for n in path if n in task_map)
            cols.append(E2RouteColumn(
                sat_id=sat_id,
                path=path,
                tasks=selected,
                cost=_route_cost(problem, task_map, path),
                source="joint_pricing_diverse",
            ))
            info.update({
                "status": _status_name(m.status),
                "status_code": int(m.status),
                "reduced_cost": (proof_obj if proof_as_feasibility
                                 else (float(m.ObjVal) if m.SolCount else None)),
                "reduced_cost_bound": float("-inf"),
                "pool_solution_count": int(m.SolCount),
                "runtime": time.time() - started,
                "diverse_task_sets": True,
            })

    return cols, info
