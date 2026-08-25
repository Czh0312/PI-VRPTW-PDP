# -*- coding: utf-8 -*-
"""Route-based column-generation probe for Model 3 E2 routing.

This is not yet a full branch-price implementation. It solves the
per-satellite E2 route LP with a MIP pricing problem so we can test whether
route variables produce stronger bounds than the current arc-flow Big-M
formulation.
"""

from dataclasses import dataclass
import heapq
import time

import gurobipy as gp
from gurobipy import GRB


EPS = 1e-6
PRICING_PROOF_TOL = 1e-5
ARTIFICIAL_COST = 100000.0


@dataclass(frozen=True)
class E2RouteColumn:
    sat_id: str
    path: tuple
    tasks: tuple
    cost: float
    source: str = "generated"


def _status_name(status_code):
    status_names = {
        GRB.OPTIMAL: "OPTIMAL",
        GRB.INFEASIBLE: "INFEASIBLE",
        GRB.INF_OR_UNBD: "INF_OR_UNBD",
        GRB.TIME_LIMIT: "TIME_LIMIT",
        GRB.INTERRUPTED: "INTERRUPTED",
        GRB.SUBOPTIMAL: "SUBOPTIMAL",
        GRB.NUMERIC: "NUMERIC",
    }
    return status_names.get(status_code, str(status_code))


def _phys(task_map, node_id):
    task = task_map.get(node_id)
    return task.cust if task is not None else node_id


def _service_time(problem, task_map, node_id):
    node = problem.nodes.get(_phys(task_map, node_id))
    return getattr(node, "service_time", 0.0) if node is not None else 0.0


def _signed_q(task):
    return task.w if task.kind == "d" else -task.w


def _signed_v(task):
    return task.v if task.kind == "d" else -task.v


def _route_cost(problem, task_map, path):
    return sum(
        problem.distance(_phys(task_map, path[i]), _phys(task_map, path[i + 1]))
        for i in range(len(path) - 1)
    )


def _singleton_column(problem, sat_id, task):
    task_map = {task.id: task}
    path = (sat_id, task.id, sat_id)
    travel = problem.travel_time(sat_id, task.cust)
    earliest, latest = task.tw
    arrival = max(earliest, travel)
    if arrival > latest + EPS:
        return None
    if task.kind == "d" and (
        task.w > problem.e2_weight_cap + EPS
        or task.v > problem.e2_volume_cap + EPS
    ):
        return None
    if task.kind == "p" and (
        task.w > problem.e2_weight_cap + EPS
        or task.v > problem.e2_volume_cap + EPS
    ):
        return None
    return E2RouteColumn(
        sat_id=sat_id,
        path=path,
        tasks=(task.id,),
        cost=_route_cost(problem, task_map, path),
        source="singleton",
    )


def _columns_from_paths(problem, sat_id, tasks, paths):
    task_map = {t.id: t for t in tasks}
    task_ids = set(task_map)
    cols = []
    for path in paths or []:
        if not path or path[0] != sat_id or path[-1] != sat_id:
            continue
        covered = tuple(n for n in path if n in task_ids)
        if not covered:
            continue
        cols.append(E2RouteColumn(
            sat_id=sat_id,
            path=tuple(path),
            tasks=covered,
            cost=_route_cost(problem, task_map, path),
            source="initial",
        ))
    return cols


def solve_satellite_route_cg(problem, sat_id, tasks, vehicles_per_satellite,
                             initial_paths=None, max_iterations=50,
                             pricing_time_limit=10.0, pricing_pool_solutions=1,
                             pricing_method="mip", label_beam_width=250,
                             label_max_expansions=200000,
                             integer_master=True,
                             return_columns=False,
                             output_flag=False):
    """Run route-column generation for one satellite.

    The LP is a relaxation of the true E2 problem because route-level
    cross-vehicle pickup-after-delivery timing is not enforced in the master.
    Therefore its objective is a valid lower-bound candidate when all pricing
    problems are solved to optimality.
    """
    started = time.time()
    task_ids = [t.id for t in tasks]
    if not task_ids:
        result = {
            "satellite": sat_id,
            "task_count": 0,
            "lp_obj": 0.0,
            "integer_obj": 0.0,
            "columns": 0,
            "iterations": 0,
            "pricing_statuses": [],
            "pricing_exact": True,
            "elapsed": 0.0,
        }
        if return_columns:
            result["columns_data"] = []
        return result

    columns = []
    seen_paths = set()
    for col in _columns_from_paths(problem, sat_id, tasks, initial_paths):
        if col.path not in seen_paths:
            columns.append(col)
            seen_paths.add(col.path)
    for task in tasks:
        col = _singleton_column(problem, sat_id, task)
        if col is not None and col.path not in seen_paths:
            columns.append(col)
            seen_paths.add(col.path)

    if len(columns) < len(task_ids):
        missing = sorted(set(task_ids) - {t for c in columns for t in c.tasks})
        result = {
            "satellite": sat_id,
            "task_count": len(task_ids),
            "status": "NO_INITIAL_COVER",
            "missing": missing,
            "elapsed": time.time() - started,
        }
        if return_columns:
            result["columns_data"] = []
        return result

    lp_obj = None
    last_master = None
    pricing_statuses = []
    added = 0

    for iteration in range(max_iterations):
        master, lambdas, artificials, cover_constrs, vehicle_constr, _ = _build_master(
            sat_id, task_ids, columns, vehicles_per_satellite,
            relax=True, output_flag=output_flag,
        )
        master.optimize()
        last_master = master
        if master.status != GRB.OPTIMAL:
            result = {
                "satellite": sat_id,
                "task_count": len(task_ids),
                "status": f"MASTER_{_status_name(master.status)}",
                "columns": len(columns),
                "iterations": iteration,
                "elapsed": time.time() - started,
            }
            if return_columns:
                result["columns_data"] = _serialize_columns(columns)
            return result

        lp_obj = float(master.ObjVal)
        cover_duals = {tid: cover_constrs[tid].Pi for tid in task_ids}
        vehicle_dual = vehicle_constr.Pi
        if pricing_method == "label":
            pricing_cols, pricing_info = _solve_pricing_labeling(
                problem, sat_id, tasks, cover_duals, vehicle_dual,
                pool_solutions=pricing_pool_solutions,
                beam_width=label_beam_width,
                max_expansions=label_max_expansions,
            )
        else:
            pricing_cols, pricing_info = _solve_pricing_mip(
                problem, sat_id, tasks, cover_duals, vehicle_dual,
                pricing_time_limit=pricing_time_limit,
                pool_solutions=pricing_pool_solutions,
                output_flag=output_flag,
            )
        pricing_statuses.append(pricing_info)

        if not pricing_cols or pricing_info["reduced_cost_bound"] >= -EPS:
            break
        new_count = 0
        for pricing_col in pricing_cols:
            if pricing_col.path in seen_paths:
                continue
            columns.append(pricing_col)
            seen_paths.add(pricing_col.path)
            new_count += 1
        if new_count == 0:
            break
        added += new_count

    int_obj = None
    int_status = None
    int_gap = None
    artificial_sum = None
    if integer_master:
        int_master, _, int_artificials, _, _, _ = _build_master(
            sat_id, task_ids, columns, vehicles_per_satellite,
            relax=False, output_flag=output_flag,
        )
        int_master.optimize()
        int_status = _status_name(int_master.status)
        if int_master.SolCount:
            int_obj = float(int_master.ObjVal)
            int_gap = float(int_master.MIPGap)
            artificial_sum = sum(var.X for var in int_artificials.values())

    lp_artificial_sum = None
    if last_master is not None and last_master.SolCount:
        lp_artificial_sum = sum(var.X for var in artificials.values())
    final_pricing_exact = (
        bool(pricing_statuses)
        and pricing_statuses[-1].get("status") == "OPTIMAL"
        and pricing_statuses[-1].get("reduced_cost_bound", float("-inf")) >= -EPS
    )

    result = {
        "satellite": sat_id,
        "task_count": len(task_ids),
        "status": "OK",
        "lp_obj": lp_obj,
        "integer_obj": int_obj,
        "integer_status": int_status,
        "integer_gap": int_gap,
        "lp_artificial_sum": lp_artificial_sum,
        "integer_artificial_sum": artificial_sum,
        "lp_valid": (
            lp_artificial_sum is not None
            and lp_artificial_sum <= 1e-7
            and final_pricing_exact
        ),
        "columns": len(columns),
        "columns_added": added,
        "iterations": len(pricing_statuses),
        "pricing_statuses": pricing_statuses,
        "pricing_exact": final_pricing_exact,
        "elapsed": time.time() - started,
    }
    if return_columns:
        result["columns_data"] = _serialize_columns(columns)
    return result


def _serialize_columns(columns):
    return [
        {
            "sat_id": col.sat_id,
            "path": list(col.path),
            "tasks": list(col.tasks),
            "cost": col.cost,
            "source": col.source,
        }
        for col in columns
    ]


def _sri_column_coeff(col, cut_tasks):
    return len(set(col.tasks) & cut_tasks) // 2


def _build_master(sat_id, task_ids, columns, vehicles_per_satellite,
                  relax=True, output_flag=False, gurobi_params=None,
                  min_routes=None, cuts=None):
    m = gp.Model(f"E2_RouteMaster_{sat_id}")
    m.setParam("OutputFlag", 1 if output_flag else 0)
    for key, value in (gurobi_params or {}).items():
        m.setParam(key, value)
    vtype = GRB.CONTINUOUS if relax else GRB.BINARY
    if relax:
        lambdas = m.addVars(range(len(columns)), lb=0.0, vtype=vtype,
                            name="lambda")
    else:
        lambdas = m.addVars(range(len(columns)), vtype=vtype, name="lambda")
    artificials = m.addVars(task_ids, lb=0.0, ub=1.0, vtype=vtype,
                            name="artificial")

    cover_constrs = {}
    for task_id in task_ids:
        cover_constrs[task_id] = m.addConstr(
            gp.quicksum(
                lambdas[idx]
                for idx, col in enumerate(columns)
                if task_id in col.tasks
            ) + artificials[task_id] == 1.0,
            name=f"cover_{task_id}",
        )
    vehicle_constr = m.addConstr(
        gp.quicksum(lambdas[idx] for idx in range(len(columns)))
        <= vehicles_per_satellite,
        name="vehicle_limit",
    )
    if min_routes is not None and min_routes > 0:
        m.addConstr(
            gp.quicksum(lambdas[idx] for idx in range(len(columns)))
            >= min_routes,
            name="vehicle_minimum",
        )
    cut_constrs = {}
    for cut_idx, cut in enumerate(cuts or []):
        if cut.get("type") != "sri":
            continue
        cut_tasks = set(cut["tasks"])
        rhs = int(cut.get("rhs", len(cut_tasks) // 2))
        terms = [
            _sri_column_coeff(col, cut_tasks) * lambdas[idx]
            for idx, col in enumerate(columns)
            if _sri_column_coeff(col, cut_tasks) > 0
        ]
        cut_constrs[cut_idx] = m.addConstr(
            gp.quicksum(terms) <= rhs,
            name=f"sri_{cut_idx}",
        )
    m.setObjective(
        gp.quicksum(col.cost * lambdas[idx]
                    for idx, col in enumerate(columns))
        + ARTIFICIAL_COST * gp.quicksum(artificials[tid] for tid in task_ids),
        GRB.MINIMIZE,
    )
    return m, lambdas, artificials, cover_constrs, vehicle_constr, cut_constrs


def _solve_pricing_mip(problem, sat_id, tasks, cover_duals, vehicle_dual,
                       pricing_time_limit=10.0, pool_solutions=1,
                       output_flag=False, same_pairs=None, diff_pairs=None,
                       gurobi_params=None, diverse_task_sets=False,
                       proof_as_feasibility=False, forbidden_paths=None,
                       cut_duals=None, s_max_floor=None):
    started = time.time()
    task_ids = [t.id for t in tasks]
    task_map = {t.id: t for t in tasks}
    nodes = [sat_id] + task_ids
    forbidden_path_set = {tuple(path) for path in (forbidden_paths or [])}

    m = gp.Model(f"E2_RoutePricing_{sat_id}")
    m.setParam("OutputFlag", 1 if output_flag else 0)
    m.setParam("TimeLimit", pricing_time_limit)
    m.setParam("MIPGap", 0.0)
    if proof_as_feasibility:
        m.setParam("FeasibilityTol", 1e-9)
    for key, value in (gurobi_params or {}).items():
        m.setParam(key, value)
    if pool_solutions and pool_solutions > 1 and not diverse_task_sets and not proof_as_feasibility:
        m.setParam("PoolSearchMode", 2)
        m.setParam("PoolSolutions", int(pool_solutions))

    max_latest = max(
        [t.tw[1] for t in tasks if t.tw[1] < float("inf")] + [0.0]
    )
    m_time = max(100000.0, max_latest + 10000.0)
    # s_max_floor: free the satellite departure A[sat] into [floor, m_time]
    # instead of pinning it to 0. A route is then feasible iff it admits a
    # schedule departing at >= floor while meeting every time window, which is
    # exactly s_max >= floor (the route's latest feasible departure). The
    # reduced-cost objective is independent of A[sat], so this is a sound and
    # complete restriction to the floor-feasible route family.
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

    y = m.addVars(task_ids, vtype=GRB.BINARY, name="y")
    z = m.addVars(
        [(i, j) for i in nodes for j in nodes if i != j],
        vtype=GRB.BINARY,
        name="z",
    )
    A = {
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
                lb=0.0,
                ub=len(cut_tasks) // 2,
                vtype=GRB.INTEGER,
                name=f"sri_coeff[{cut_idx}]",
            )
            m.addConstr(2 * h <= selected_count, name=f"sri_coeff_ub_{cut_idx}")
            m.addConstr(2 * h >= selected_count - 1, name=f"sri_coeff_lb_{cut_idx}")
        cut_aux[cut_idx] = h

    m.addConstr(gp.quicksum(z[sat_id, j] for j in task_ids) == 1,
                name="start_once")
    m.addConstr(gp.quicksum(z[i, sat_id] for i in task_ids) == 1,
                name="return_once")

    for h in task_ids:
        incoming = gp.quicksum(z[i, h] for i in nodes if i != h)
        outgoing = gp.quicksum(z[h, j] for j in nodes if j != h)
        m.addConstr(incoming == y[h], name=f"in_{h}")
        m.addConstr(outgoing == y[h], name=f"out_{h}")
        m.addConstr(A[h] >= time_lb[h] * y[h], name=f"tw_lb_{h}")
        m.addConstr(A[h] <= time_ub[h] * y[h], name=f"tw_ub_{h}")
        m.addConstr(order[h] >= y[h], name=f"ord_lb_{h}")
        m.addConstr(order[h] <= len(task_ids) * y[h], name=f"ord_used_{h}")
        m.addConstr(Q[h] <= problem.e2_weight_cap * y[h],
                    name=f"w_used_{h}")
        m.addConstr(Vol[h] <= problem.e2_volume_cap * y[h],
                    name=f"vol_used_{h}")

    for a, b in same_pairs or []:
        if a in y and b in y:
            m.addConstr(y[a] == y[b], name=f"branch_same_{a}_{b}")
    for a, b in diff_pairs or []:
        if a in y and b in y:
            m.addConstr(y[a] + y[b] <= 1, name=f"branch_diff_{a}_{b}")

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
        gp.quicksum(t.w * y[t.id] for t in pick_tasks)
        <= problem.e2_weight_cap,
        name="total_pickup_weight_cap",
    )
    m.addConstr(
        gp.quicksum(t.v * y[t.id] for t in pick_tasks)
        <= problem.e2_volume_cap,
        name="total_pickup_volume_cap",
    )
    if s_max_floor is None:
        m.addConstr(A[sat_id] == 0.0, name="sat_time")
    else:
        # A[sat] is the free departure variable bounded to [floor, m_time];
        # the lower bound already enforces departure >= floor.
        m.addConstr(A[sat_id] >= float(s_max_floor), name="sat_depart_floor")

    for i in nodes:
        s_i = _service_time(problem, task_map, i)
        for j in task_ids:
            if i == j:
                continue
            travel = problem.travel_time(_phys(task_map, i), _phys(task_map, j))
            if time_lb[i] + s_i + travel > time_ub[j] + EPS:
                z[i, j].UB = 0.0
            arc_m_time = max(
                0.0,
                time_ub[i] + s_i + travel,
            )
            m.addConstr(
                A[j] >= A[i] + s_i + travel - arc_m_time * (1 - z[i, j]),
                name=f"time_{i}_{j}",
            )
            task_j = task_map[j]
            q_j = _signed_q(task_j)
            v_j = _signed_v(task_j)
            m_w = problem.e2_weight_cap + abs(q_j)
            m_v = problem.e2_volume_cap + abs(v_j)
            m.addConstr(
                Q[j] <= Q[i] - q_j + m_w * (1 - z[i, j]),
                name=f"w_up_{i}_{j}",
            )
            m.addConstr(
                Q[j] >= Q[i] - q_j - m_w * (1 - z[i, j]),
                name=f"w_lo_{i}_{j}",
            )
            m.addConstr(
                Vol[j] <= Vol[i] - v_j + m_v * (1 - z[i, j]),
                name=f"vol_up_{i}_{j}",
            )
            m.addConstr(
                Vol[j] >= Vol[i] - v_j - m_v * (1 - z[i, j]),
                name=f"vol_lo_{i}_{j}",
            )

    for cust_id in sorted({t.cust for t in tasks}):
        d_id = f"{cust_id}d"
        p_id = f"{cust_id}p"
        if d_id in task_map and p_id in task_map:
            service_d = _service_time(problem, task_map, d_id)
            prec_m_time = max(
                0.0,
                time_ub[d_id] + service_d,
            )
            m.addConstr(
                A[p_id] >= A[d_id] + service_d
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

    route_cost = gp.quicksum(
        problem.distance(_phys(task_map, i), _phys(task_map, j)) * z[i, j]
        for (i, j) in z.keys()
    )
    dual_reward = gp.quicksum(cover_duals[tid] * y[tid] for tid in task_ids)
    cut_reward = gp.quicksum(
        cut.get("dual", 0.0) * cut_aux[cut_idx]
        for cut_idx, cut in enumerate(cut_duals or [])
        if cut_idx in cut_aux
    )
    reduced_expr = route_cost - dual_reward - vehicle_dual - cut_reward
    if proof_as_feasibility:
        m.addConstr(reduced_expr <= -PRICING_PROOF_TOL, name="negative_reduced_cost")
        m.setObjective(0.0, GRB.MINIMIZE)
    else:
        m.setObjective(reduced_expr, GRB.MINIMIZE)
    m.optimize()

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
            reduced_cost = sum(
                problem.distance(_phys(task_map, i), _phys(task_map, j)) * z[i, j].X
                for (i, j) in z.keys()
            ) - sum(cover_duals[tid] * y[tid].X for tid in task_ids) - vehicle_dual - sum(
                cut.get("dual", 0.0) * cut_aux[idx].X
                for idx, cut in enumerate(cut_duals or [])
                if idx in cut_aux
            )
        else:
            reduced_cost = float(m.ObjVal)
    info = {
        "status": _status_name(m.status),
        "status_code": int(m.status),
        "reduced_cost": reduced_cost,
        "reduced_cost_bound": reduced_cost_bound,
        "pool_solution_count": int(m.SolCount),
        "runtime": float(m.Runtime),
        "proof_as_feasibility": proof_as_feasibility,
        "lazy_forbidden_count": lazy_forbidden_count,
    }
    if not m.SolCount:
        return [], info

    cols = []
    seen = set()
    excluded_task_sets = set()
    sol_count = min(int(m.SolCount), int(pool_solutions or 1))
    for sol_idx in range(sol_count):
        if sol_idx:
            m.setParam("SolutionNumber", sol_idx)
            sol_z = {key: var.Xn for key, var in z.items()}
            sol_y = {key: var.Xn for key, var in y.items()}
            obj = (
                sum(
                    problem.distance(_phys(task_map, i), _phys(task_map, j)) * value
                    for (i, j), value in sol_z.items()
                )
                - sum(cover_duals[tid] * value for tid, value in sol_y.items())
                - vehicle_dual
                - sum(
                    cut.get("dual", 0.0) * cut_aux[idx].Xn
                    for idx, cut in enumerate(cut_duals or [])
                    if idx in cut_aux
                )
            ) if proof_as_feasibility else float(m.PoolObjVal)
        else:
            sol_z = {key: var.X for key, var in z.items()}
            sol_y = {key: var.X for key, var in y.items()}
            obj = (
                sum(
                    problem.distance(_phys(task_map, i), _phys(task_map, j)) * value
                    for (i, j), value in sol_z.items()
                )
                - sum(cover_duals[tid] * value for tid, value in sol_y.items())
                - vehicle_dual
                - sum(
                    cut.get("dual", 0.0) * cut_aux[idx].X
                    for idx, cut in enumerate(cut_duals or [])
                    if idx in cut_aux
                )
            ) if proof_as_feasibility else float(m.ObjVal)
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
            source="pricing",
        ))
    if diverse_task_sets and len(cols) < int(pool_solutions or 1):
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
            proof_obj = None
            if m.SolCount:
                proof_obj = sum(
                    problem.distance(_phys(task_map, i), _phys(task_map, j)) * z[i, j].X
                    for (i, j) in z.keys()
                ) - sum(cover_duals[tid] * y[tid].X for tid in task_ids) - vehicle_dual - sum(
                    cut.get("dual", 0.0) * cut_aux[idx].X
                    for idx, cut in enumerate(cut_duals or [])
                    if idx in cut_aux
                )
            info.update({
                "status": _status_name(m.status),
                "status_code": int(m.status),
                "reduced_cost": proof_obj if proof_as_feasibility else (
                    float(m.ObjVal) if m.SolCount else None
                ),
                "reduced_cost_bound": float("-inf"),
                "pool_solution_count": int(m.SolCount),
                "runtime": time.time() - started,
                "diverse_task_sets": True,
            })
            if not m.SolCount:
                break
            if proof_as_feasibility:
                if proof_obj is None or proof_obj >= -EPS:
                    break
            elif float(m.ObjVal) >= -EPS:
                break
            sol_y = {key: var.X for key, var in y.items()}
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
                source="pricing_diverse_tasks",
            ))
    return cols, info


def _solve_pricing_labeling(problem, sat_id, tasks, cover_duals, vehicle_dual,
                            pool_solutions=10, beam_width=250,
                            max_expansions=200000, time_limit=None):
    """Heuristic ESPPRC-style label pricing.

    It respects time windows, elementarity, capacity, and same-route
    pickup-after-delivery ordering. It does not yet provide a proof that no
    negative reduced-cost route exists.
    """
    started = time.time()
    task_ids = [t.id for t in tasks]
    task_map = {t.id: t for t in tasks}
    index = {tid: idx for idx, tid in enumerate(task_ids)}
    service = {
        node: _service_time(problem, task_map, node)
        for node in [sat_id] + task_ids
    }
    candidates = []
    by_node = {sat_id: []}

    start = {
        "node": sat_id,
        "path": (sat_id,),
        "visited": 0,
        "time": 0.0,
        "cost": 0.0,
        "reward": 0.0,
        "total_del_w": 0.0,
        "total_del_v": 0.0,
        "delta_w": 0.0,
        "delta_v": 0.0,
        "max_delta_w": 0.0,
        "max_delta_v": 0.0,
        "picked_mask": 0,
    }
    queue = [start]
    expansions = 0

    while queue and expansions < max_expansions:
        if time_limit is not None and time.time() - started >= time_limit:
            break
        label = queue.pop()
        expansions += 1
        current = label["node"]
        for task in tasks:
            tid = task.id
            bit = 1 << index[tid]
            if label["visited"] & bit:
                continue
            if task.kind == "d":
                pick_id = f"{task.cust}p"
                if pick_id in index and (label["picked_mask"] & (1 << index[pick_id])):
                    continue

            travel = problem.travel_time(_phys(task_map, current), task.cust)
            arr = max(task.tw[0], label["time"] + service[current] + travel)
            if arr > task.tw[1] + EPS:
                continue

            total_del_w = label["total_del_w"]
            total_del_v = label["total_del_v"]
            delta_w = label["delta_w"]
            delta_v = label["delta_v"]
            picked_mask = label["picked_mask"]
            if task.kind == "d":
                total_del_w += task.w
                total_del_v += task.v
                delta_w -= task.w
                delta_v -= task.v
            else:
                delta_w += task.w
                delta_v += task.v
                picked_mask |= bit
            max_delta_w = max(label["max_delta_w"], delta_w)
            max_delta_v = max(label["max_delta_v"], delta_v)
            if total_del_w > problem.e2_weight_cap + EPS:
                continue
            if total_del_v > problem.e2_volume_cap + EPS:
                continue
            if total_del_w + max_delta_w > problem.e2_weight_cap + EPS:
                continue
            if total_del_v + max_delta_v > problem.e2_volume_cap + EPS:
                continue

            arc_cost = problem.distance(_phys(task_map, current), task.cust)
            new_label = {
                "node": tid,
                "path": label["path"] + (tid,),
                "visited": label["visited"] | bit,
                "time": arr,
                "cost": label["cost"] + arc_cost,
                "reward": label["reward"] + cover_duals[tid],
                "total_del_w": total_del_w,
                "total_del_v": total_del_v,
                "delta_w": delta_w,
                "delta_v": delta_v,
                "max_delta_w": max_delta_w,
                "max_delta_v": max_delta_v,
                "picked_mask": picked_mask,
            }
            return_cost = problem.distance(task.cust, sat_id)
            reduced = (
                new_label["cost"] + return_cost
                - new_label["reward"] - vehicle_dual
            )
            if reduced < -EPS:
                path = new_label["path"] + (sat_id,)
                candidates.append((reduced, E2RouteColumn(
                    sat_id=sat_id,
                    path=path,
                    tasks=tuple(n for n in path if n in task_map),
                    cost=new_label["cost"] + return_cost,
                    source="label_pricing",
                )))

            node_labels = by_node.setdefault(tid, [])
            if _is_dominated(new_label, node_labels):
                continue
            _remove_labels_dominated_by(new_label, node_labels)
            node_labels.append(new_label)
            if len(node_labels) > beam_width:
                node_labels.sort(key=_label_rank)
                del node_labels[beam_width:]
            queue.append(new_label)

        if len(candidates) > max(50, pool_solutions * 5):
            candidates.sort(key=lambda item: item[0])
            del candidates[max(50, pool_solutions * 5):]

    candidates.sort(key=lambda item: item[0])
    cols = []
    seen = set()
    for _, col in candidates:
        if col.path in seen:
            continue
        seen.add(col.path)
        cols.append(col)
        if len(cols) >= pool_solutions:
            break
    best_reduced = candidates[0][0] if candidates else None
    info = {
        "status": "LABEL_HEURISTIC",
        "status_code": -1,
        "reduced_cost": best_reduced,
        "reduced_cost_bound": float("-inf") if cols else 0.0,
        "pool_solution_count": len(cols),
        "runtime": time.time() - started,
        "expansions": expansions,
        "labels_kept": sum(len(v) for v in by_node.values()),
    }
    return cols, info


def _solve_pricing_labeling_exact(problem, sat_id, tasks, cover_duals, vehicle_dual,
                                  pool_solutions=10, max_expansions=2000000,
                                  time_limit=None, stop_after_columns=True,
                                  dominance_mode="same_visited", s_max_floor=None,
                                  cut_duals=None):
    """Exact elementary label pricing with conservative dominance.

    This is intended as a proof-capable alternative to the MIP pricing model.
    It only certifies the pricing problem when the label queue is exhausted.
    Dominance is deliberately conservative: labels are compared only at the
    same node with exactly the same visited set, so no pickup/delivery
    precedence state is lost by dominance.

    ``s_max_floor``: if not None, restrict pricing to routes whose latest
    feasible departure from the satellite (s_max = min over visited tasks of
    ``l_task - raw_cum``, where ``raw_cum`` is the no-wait cumulative
    travel+service from the satellite) is ``>= s_max_floor``. Labels whose
    partial ``s_max_so_far`` already drops below the floor are pruned, which is
    a sound and complete restriction (s_max only decreases as tasks are added,
    so such labels can never reach the floor). This makes the pricing exact for
    the floor-restricted route family, matching ``full_route_s_max``.
    """
    started = time.time()
    task_ids = [t.id for t in tasks]
    task_map = {t.id: t for t in tasks}
    index = {tid: idx for idx, tid in enumerate(task_ids)}
    service = {
        node: _service_time(problem, task_map, node)
        for node in [sat_id] + task_ids
    }
    positive_dual_total = sum(max(0.0, cover_duals[tid]) for tid in task_ids)
    # SRI cut duals: coefficient of a route = floor(|route ∩ cut| / 2), which
    # jumps by +1 whenever the visited-set intersection reaches an even count
    # (2nd, 4th, ... member).  The incremental contribution is added to the
    # label's cut_reward; since it is a deterministic function of the visited
    # mask, same-visited dominance stays valid.
    cut_duals = list(cut_duals or [])
    cut_masks = []
    for cut in cut_duals:
        if cut.get("type") not in (None, "sri"):
            continue
        mask = 0
        for tid in cut.get("tasks", []):
            if tid in index:
                mask |= 1 << index[tid]
        if mask and cut.get("dual", 0.0) != 0.0:
            cut_masks.append((mask, float(cut["dual"])))
    if cut_duals:
        dominance_mode = "same_visited"  # subset dominance is not cut-aware
    candidates = []
    seen_candidate_paths = set()
    labels_by_state = {}
    expansions = 0
    generated = 0
    pruned_by_bound = 0
    counter = 0

    start = {
        "node": sat_id,
        "path": (sat_id,),
        "visited": 0,
        "time": 0.0,
        "raw_cum": 0.0,
        "s_max_so_far": float("inf"),
        "cost": 0.0,
        "reward": 0.0,
        "total_del_w": 0.0,
        "total_del_v": 0.0,
        "delta_w": 0.0,
        "delta_v": 0.0,
        "max_delta_w": 0.0,
        "max_delta_v": 0.0,
        "picked_mask": 0,
        "pos_reward_visited": 0.0,
        "cut_reward": 0.0,
    }
    queue = [(0.0, counter, start)]

    while queue and expansions < max_expansions:
        if time_limit is not None and time.time() - started >= time_limit:
            break
        _, _, label = heapq.heappop(queue)
        expansions += 1
        current = label["node"]

        for task in tasks:
            tid = task.id
            bit = 1 << index[tid]
            if label["visited"] & bit:
                continue
            if task.kind == "d":
                pick_id = f"{task.cust}p"
                if pick_id in index and (label["picked_mask"] & (1 << index[pick_id])):
                    continue

            travel = problem.travel_time(_phys(task_map, current), task.cust)
            arr = max(task.tw[0], label["time"] + service[current] + travel)
            if arr > task.tw[1] + EPS:
                continue

            # Latest-feasible-departure (s_max) tracking for the optional floor
            # restriction. raw_cum = no-wait cumulative travel+service from the
            # satellite (matches full_route_s_max). s_max only decreases as tasks
            # are added, so a partial label already below the floor can never
            # recover -> prune (sound and complete for the floor-restricted family).
            raw_cum = label["raw_cum"] + service[current] + travel
            s_max_so_far = label["s_max_so_far"]
            l_task = task.tw[1]
            if l_task < float("inf"):
                s_max_so_far = min(s_max_so_far, l_task - raw_cum)
            if s_max_floor is not None and s_max_so_far < s_max_floor - EPS:
                continue

            total_del_w = label["total_del_w"]
            total_del_v = label["total_del_v"]
            delta_w = label["delta_w"]
            delta_v = label["delta_v"]
            picked_mask = label["picked_mask"]
            if task.kind == "d":
                total_del_w += task.w
                total_del_v += task.v
                delta_w -= task.w
                delta_v -= task.v
            else:
                delta_w += task.w
                delta_v += task.v
                picked_mask |= bit
            max_delta_w = max(label["max_delta_w"], delta_w)
            max_delta_v = max(label["max_delta_v"], delta_v)
            if total_del_w > problem.e2_weight_cap + EPS:
                continue
            if total_del_v > problem.e2_volume_cap + EPS:
                continue
            if total_del_w + max_delta_w > problem.e2_weight_cap + EPS:
                continue
            if total_del_v + max_delta_v > problem.e2_volume_cap + EPS:
                continue

            arc_cost = problem.distance(_phys(task_map, current), task.cust)
            visited = label["visited"] | bit
            # incremental SRI cut contribution (jump at even intersection counts)
            cut_add = 0.0
            if cut_masks:
                for c_mask, c_dual in cut_masks:
                    if not (c_mask & bit):
                        continue
                    new_count = _popcount(visited & c_mask)
                    old_count = new_count - 1
                    if new_count > 0 and new_count % 2 == 0 and old_count % 2 == 1:
                        cut_add += c_dual
            new_label = {
                "node": tid,
                "path": label["path"] + (tid,),
                "visited": visited,
                "time": arr,
                "raw_cum": raw_cum,
                "s_max_so_far": s_max_so_far,
                "cost": label["cost"] + arc_cost,
                "reward": label["reward"] + cover_duals[tid],
                "total_del_w": total_del_w,
                "total_del_v": total_del_v,
                "delta_w": delta_w,
                "delta_v": delta_v,
                "max_delta_w": max_delta_w,
                "max_delta_v": max_delta_v,
                "picked_mask": picked_mask,
                "pos_reward_visited": (
                    label["pos_reward_visited"] + max(0.0, cover_duals[tid])
                ),
                "cut_reward": label["cut_reward"] + cut_add,
            }

            return_cost = problem.distance(task.cust, sat_id)
            reduced = (
                new_label["cost"] + return_cost
                - new_label["reward"] - vehicle_dual
                - new_label["cut_reward"]
            )
            if reduced < -EPS:
                path = new_label["path"] + (sat_id,)
                if path not in seen_candidate_paths:
                    seen_candidate_paths.add(path)
                    candidates.append((reduced, E2RouteColumn(
                        sat_id=sat_id,
                        path=path,
                        tasks=tuple(n for n in path if n in task_map),
                        cost=new_label["cost"] + return_cost,
                        source="exact_label_pricing",
                    )))
                    if stop_after_columns and len(candidates) >= pool_solutions:
                        return _exact_label_result(
                            candidates, pool_solutions, started, expansions,
                            generated, labels_by_state, "EXACT_LABEL_COLUMNS",
                            exhausted=False,
                            pruned_by_bound=pruned_by_bound,
                        )

            remaining_positive_dual = (
                positive_dual_total - new_label["pos_reward_visited"]
            )
            optimistic_completion_bound = (
                new_label["cost"] - new_label["reward"]
                - vehicle_dual - remaining_positive_dual
            )
            if optimistic_completion_bound >= -EPS:
                pruned_by_bound += 1
                continue

            if dominance_mode == "subset_bucket":
                state = tid
                state_labels = labels_by_state.setdefault(state, {})
                if _is_dominated_subset_bucket(new_label, state_labels):
                    continue
                _add_subset_bucket_label(new_label, state_labels)
            elif dominance_mode == "subset":
                state = tid
                state_labels = labels_by_state.setdefault(state, [])
                if _is_dominated_subset(new_label, state_labels):
                    continue
                _remove_subset_dominated_by(new_label, state_labels)
                state_labels.append(new_label)
            else:
                state = (tid, visited)
                state_labels = labels_by_state.setdefault(state, [])
                if _is_dominated_same_visited(new_label, state_labels):
                    continue
                _remove_same_visited_dominated_by(new_label, state_labels)
                state_labels.append(new_label)
            generated += 1
            rank = _label_rank(new_label)
            counter += 1
            heapq.heappush(queue, (rank, counter, new_label))

    exhausted = not queue
    if exhausted:
        status = "EXACT_LABEL_OPTIMAL"
    elif expansions >= max_expansions:
        status = "EXACT_LABEL_LIMIT"
    else:
        status = "EXACT_LABEL_TIME_LIMIT"
    return _exact_label_result(
        candidates, pool_solutions, started, expansions, generated,
        labels_by_state, status, exhausted=exhausted,
        pruned_by_bound=pruned_by_bound,
    )


def _exact_label_result(candidates, pool_solutions, started, expansions,
                        generated, labels_by_state, status, exhausted,
                        pruned_by_bound=0):
    candidates.sort(key=lambda item: item[0])
    cols = []
    seen = set()
    for _, col in candidates:
        if col.path in seen:
            continue
        seen.add(col.path)
        cols.append(col)
        if len(cols) >= pool_solutions:
            break
    best_reduced = candidates[0][0] if candidates else None
    if exhausted:
        reduced_bound = best_reduced if best_reduced is not None else 0.0
    else:
        reduced_bound = float("-inf")
    info = {
        "status": status,
        "status_code": -2,
        "reduced_cost": best_reduced,
        "reduced_cost_bound": reduced_bound,
        "pool_solution_count": len(cols),
        "runtime": time.time() - started,
        "expansions": expansions,
        "labels_generated": generated,
        "labels_kept": _labels_kept_count(labels_by_state),
        "pruned_by_bound": pruned_by_bound,
        "exhausted": exhausted,
    }
    return cols, info


def _labels_kept_count(labels_by_state):
    total = 0
    for value in labels_by_state.values():
        if isinstance(value, dict):
            for by_count in value.values():
                total += sum(len(labels) for labels in by_count.values())
        else:
            total += len(value)
    return total


def _popcount(mask):
    return bin(mask).count("1")


def _label_resource_tuple(label):
    # -s_max_so_far: a later feasible departure (larger s_max) can complete any
    # suffix an earlier one can (and more), so it dominates; negated so the
    # standard "all(<=)" dominance test is correct. Required for completeness of
    # the floor-restricted exact pricing; sound (only weakens dominance) for the
    # unrestricted case.
    return (
        label["time"],
        label["cost"] - label["reward"],
        label["total_del_w"],
        label["total_del_v"],
        label["delta_w"],
        label["delta_v"],
        label["max_delta_w"],
        label["max_delta_v"],
        -label["s_max_so_far"],
    )


def _is_dominated_same_visited(label, labels):
    """Safe dominance for exact pricing: same node and same visited set only."""
    target = _label_resource_tuple(label)
    for other in labels:
        if other["picked_mask"] != label["picked_mask"]:
            continue
        other_values = _label_resource_tuple(other)
        if all(a <= b + EPS for a, b in zip(other_values, target)):
            return True
    return False


def _remove_same_visited_dominated_by(label, labels):
    source = _label_resource_tuple(label)
    keep = []
    for other in labels:
        if other["picked_mask"] != label["picked_mask"]:
            keep.append(other)
            continue
        other_values = _label_resource_tuple(other)
        if all(a <= b + EPS for a, b in zip(source, other_values)):
            continue
        keep.append(other)
    labels[:] = keep


def _is_dominated_subset(label, labels):
    target = _label_resource_tuple(label)
    for other in labels:
        if other["visited"] & ~label["visited"]:
            continue
        if other["picked_mask"] & ~label["picked_mask"]:
            continue
        other_values = _label_resource_tuple(other)
        if all(a <= b + EPS for a, b in zip(other_values, target)):
            return True
    return False


def _remove_subset_dominated_by(label, labels):
    source = _label_resource_tuple(label)
    keep = []
    for other in labels:
        if label["visited"] & ~other["visited"]:
            keep.append(other)
            continue
        if label["picked_mask"] & ~other["picked_mask"]:
            keep.append(other)
            continue
        other_values = _label_resource_tuple(other)
        if all(a <= b + EPS for a, b in zip(source, other_values)):
            continue
        keep.append(other)
    labels[:] = keep


def _iter_submasks(mask):
    submask = mask
    while True:
        yield submask
        if submask == 0:
            break
        submask = (submask - 1) & mask


def _is_dominated_subset_bucket(label, buckets):
    target = _label_resource_tuple(label)
    visited_count = _popcount(label["visited"])
    for picked_mask in _iter_submasks(label["picked_mask"]):
        by_count = buckets.get(picked_mask)
        if not by_count:
            continue
        for count, labels in by_count.items():
            if count > visited_count:
                continue
            for other in labels:
                if other["visited"] & ~label["visited"]:
                    continue
                other_values = _label_resource_tuple(other)
                if all(a <= b + EPS for a, b in zip(other_values, target)):
                    return True
    return False


def _add_subset_bucket_label(label, buckets):
    picked_bucket = buckets.setdefault(label["picked_mask"], {})
    count_bucket = picked_bucket.setdefault(_popcount(label["visited"]), [])
    source = _label_resource_tuple(label)
    keep = []
    for other in count_bucket:
        if label["visited"] & ~other["visited"]:
            keep.append(other)
            continue
        other_values = _label_resource_tuple(other)
        if all(a <= b + EPS for a, b in zip(source, other_values)):
            continue
        keep.append(other)
    keep.append(label)
    count_bucket[:] = keep


def _label_rank(label):
    return (
        label["cost"] - label["reward"],
        label["time"],
        label["total_del_w"] + label["max_delta_w"],
        label["total_del_v"] + label["max_delta_v"],
        -bin(label["visited"]).count("1"),
    )


def _is_dominated(label, labels):
    """Dominance for labels ending at the same node."""
    for other in labels:
        # A label that has visited a subset of tasks retains at least as many
        # extension options as the current one.
        if other["visited"] & ~label["visited"]:
            continue
        if (
            other["time"] <= label["time"] + EPS
            and other["cost"] - other["reward"] <= label["cost"] - label["reward"] + EPS
            and other["total_del_w"] <= label["total_del_w"] + EPS
            and other["total_del_v"] <= label["total_del_v"] + EPS
            and other["max_delta_w"] <= label["max_delta_w"] + EPS
            and other["max_delta_v"] <= label["max_delta_v"] + EPS
        ):
            return True
    return False


def _remove_labels_dominated_by(label, labels):
    keep = []
    for other in labels:
        if label["visited"] & ~other["visited"]:
            keep.append(other)
            continue
        if (
            label["time"] <= other["time"] + EPS
            and label["cost"] - label["reward"] <= other["cost"] - other["reward"] + EPS
            and label["total_del_w"] <= other["total_del_w"] + EPS
            and label["total_del_v"] <= other["total_del_v"] + EPS
            and label["max_delta_w"] <= other["max_delta_w"] + EPS
            and label["max_delta_v"] <= other["max_delta_v"] + EPS
        ):
            continue
        keep.append(other)
    labels[:] = keep


def _extract_pricing_path(sat_id, task_ids, sol_z):
    path = [sat_id]
    current = sat_id
    seen = {sat_id}
    while True:
        nxt = None
        for j in [sat_id] + task_ids:
            if j != current and sol_z.get((current, j), 0.0) > 0.5:
                nxt = j
                break
        if nxt is None:
            break
        path.append(nxt)
        if nxt == sat_id:
            break
        if nxt in seen:
            break
        seen.add(nxt)
        current = nxt
    return path
