# models/model_2/solver.py
# -*- coding: utf-8 -*-
"""
适配文档2变量命名: Q_k, Vol_k, Q_r, Vol_r
"""
from gurobipy import GRB

from .model import build_unified_pdp_model
from ..solver_utils import extract_routes_e1, extract_routes_e2, print_e1_loads, print_e2_loads


def _status_name(status_code):
    status_names = {
        GRB.LOADED: "LOADED",
        GRB.OPTIMAL: "OPTIMAL",
        GRB.INFEASIBLE: "INFEASIBLE",
        GRB.INF_OR_UNBD: "INF_OR_UNBD",
        GRB.UNBOUNDED: "UNBOUNDED",
        GRB.CUTOFF: "CUTOFF",
        GRB.ITERATION_LIMIT: "ITERATION_LIMIT",
        GRB.NODE_LIMIT: "NODE_LIMIT",
        GRB.TIME_LIMIT: "TIME_LIMIT",
        GRB.SOLUTION_LIMIT: "SOLUTION_LIMIT",
        GRB.INTERRUPTED: "INTERRUPTED",
        GRB.NUMERIC: "NUMERIC",
        GRB.SUBOPTIMAL: "SUBOPTIMAL",
        GRB.INPROGRESS: "INPROGRESS",
        GRB.USER_OBJ_LIMIT: "USER_OBJ_LIMIT",
        GRB.WORK_LIMIT: "WORK_LIMIT",
        GRB.MEM_LIMIT: "MEM_LIMIT",
    }
    if status_code in status_names:
        return status_names[status_code]
    return str(status_code)


def _solve_info(m, solution_type):
    info = {
        "status_code": m.status,
        "status": _status_name(m.status),
        "solution_type": solution_type,
        # Do not let callers mistake a time-limited incumbent for an exact
        # result merely because routes were returned.
        "proven_optimal": solution_type == "proven_optimal",
        "runtime": getattr(m, "Runtime", None),
        "sol_count": getattr(m, "SolCount", 0),
        "objective": None,
        "best_bound": None,
        "mip_gap": None,
    }
    if info["sol_count"] and info["sol_count"] > 0:
        info["objective"] = m.ObjVal
        info["mip_gap"] = m.MIPGap
    try:
        info["best_bound"] = m.ObjBound
    except Exception:
        pass
    return info


def _maybe_with_info(routes, info, return_info):
    e1_routes, e2_routes = routes
    if return_info:
        return e1_routes, e2_routes, info
    return e1_routes, e2_routes


def _apply_gurobi_params(m, gurobi_params, verbose):
    if not gurobi_params:
        return
    for name, value in gurobi_params.items():
        m.setParam(name, value)
        if verbose:
            print(f"✓ Gurobi 参数 {name} = {value}")


def _apply_route_warm_start(vars_dict, ctx, warm_start_routes, verbose):
    if not warm_start_routes:
        return

    e1_routes, e2_routes_by_sat = warm_start_routes
    x = vars_dict["x"]
    z = vars_dict["z"]
    for var in x.values():
        var.Start = GRB.UNDEFINED
    for var in z.values():
        var.Start = GRB.UNDEFINED

    K = ctx["K"]
    vehicles_per_satellite = ctx["vehicles_per_satellite"]
    started_x = 0
    skipped_x = 0
    started_z = 0
    skipped_z = 0

    for k, route in zip(K, e1_routes or []):
        for i, j in zip(route, route[1:]):
            key = (i, j, k)
            if key in x:
                x[key].Start = 1.0
                started_x += 1
            else:
                skipped_x += 1

    for sat_id, routes in (e2_routes_by_sat or {}).items():
        for v_idx, route in enumerate(routes[:vehicles_per_satellite]):
            v_key = (sat_id, v_idx)
            for i, j in zip(route, route[1:]):
                key = (i, j, v_key)
                if key in z:
                    z[key].Start = 1.0
                    started_z += 1
                else:
                    skipped_z += 1

    if verbose:
        print(
            "[MIPStart] 已写入路线 warm start: "
            f"E1 arcs={started_x} (skip={skipped_x}), "
            f"E2 arcs={started_z} (skip={skipped_z})"
        )


def solve_exact(problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite,
                TFp=9999.0, verbose=True, time_limit=300, mip_gap=0.0,
                return_incumbent_on_time_limit=False, return_info=False,
                warm_start_routes=None, gurobi_params=None,
                e2_cost_lower_bounds=None, e1_cost_lower_bound=None):
    if verbose:
        total_w = sum(t.w for t in e1_tasks if t.kind == 'd')
        total_v = sum(t.v for t in e1_tasks if t.kind == 'd')
        print(f"\n[DEBUG] E1 任务总览:")
        print(f"  卡车数 K = {problem.e1_vehicle_count}")
        print(f"  单车 weight cap = {problem.e1_weight_cap}")
        print(f"  单车 volume cap = {problem.e1_volume_cap}")
        print(f"  total delivery weight = {total_w}")
        print(f"  total delivery volume = {total_v}")
        for t in e1_tasks:
            print(f"    {t.id}: kind={t.kind}, w={t.w}, v={t.v}")

    m, vars_dict, ctx = build_unified_pdp_model(
        problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite, TFp=TFp,
        e2_cost_lower_bounds=e2_cost_lower_bounds,
        e1_cost_lower_bound=e1_cost_lower_bound,
    )
    if not verbose:
        m.setParam("OutputFlag", 0)

    m.setParam("MIPGap", mip_gap)
    m.setParam("TimeLimit", time_limit)
    _apply_gurobi_params(m, gurobi_params, verbose)
    _apply_route_warm_start(vars_dict, ctx, warm_start_routes, verbose)
    m.optimize()

    if m.status == GRB.OPTIMAL:
        return _maybe_with_info(
            _print_solution(m, vars_dict, ctx, e2_tasks_by_sat, problem,
                            verbose=verbose),
            _solve_info(m, "proven_optimal"),
            return_info,
        )

    if m.SolCount > 0 and return_incumbent_on_time_limit:
        if verbose:
            print(f"\n求解未证明最优 ({_status_name(m.status)})，返回可行解: "
                  f"{m.ObjVal:.2f}  (Gap: {m.MIPGap:.2%})")
        return _maybe_with_info(
            _print_solution(m, vars_dict, ctx, e2_tasks_by_sat, problem,
                            verbose=verbose),
            _solve_info(m, "feasible_not_proven"),
            return_info,
        )

    if m.status == GRB.INFEASIBLE:
        if verbose:
            print("\n模型不可行，正在计算 IIS...")
        m.computeIIS()
        from pathlib import Path
        iis_dir = Path(__file__).resolve().parent.parent.parent / "output" / "debug_iis" / "model2_exact"
        iis_dir.mkdir(parents=True, exist_ok=True)
        iis_path = iis_dir / "debug_iis_model02_exact_infeasible.ilp"
        m.write(str(iis_path))
        if verbose:
            print(f"✓ IIS 已写入 {iis_path}")

    if verbose:
        print("\n未找到可行解")
    return _maybe_with_info((None, None), _solve_info(m, "none"), return_info)


def solve_mip(problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite,
              **kwargs):
    """Run Model 2 as a limited MIP and explicitly allow an incumbent.

    This is the non-certifying counterpart to :func:`solve_exact`; it exists
    for warm starts and diagnostics, not for exact-solver comparisons.
    """
    kwargs["return_incumbent_on_time_limit"] = True
    return solve_exact(
        problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite, **kwargs
    )


def _print_solution(m, vars_dict, ctx, e2_tasks_by_sat, problem, *, verbose=True):
    if verbose:
        print("\n" + "=" * 60)
        print(f"求解完成!  目标值: {m.ObjVal:.2f}")
        print("=" * 60)

    # 先把 ctx 里要用的东西解包出来
    depot_id = ctx["depot_id"]
    K = ctx["K"]
    F = ctx["F"]
    N_E1_Tasks = ctx["N_E1_Tasks"]

    sol_x = m.getAttr("X", vars_dict["x"])
    sol_z = {k: v.X for k, v in vars_dict["z"].items()}

    if verbose:
        print("\n[DEBUG] 所有被激活的 E1 弧 (x > 0.5):")
        for (i, j, k), val in sol_x.items():
            if val > 0.5:
                print(f"  x[{i:>6}, {j:>6}, k={k}] = {val:.2f}")
        print("[DEBUG] E1 弧总数:", sum(1 for v in sol_x.values() if v > 0.5))

        print("\n[DEBUG] 所有卡车的 D0 初始载量 (只显示非零):")
        Q_k_var = vars_dict["Q_k"]
        Vol_k_var = vars_dict["Vol_k"]
        any_loaded = False
        for k in K:
            q = Q_k_var[depot_id, k].X
            v = Vol_k_var[depot_id, k].X
            if q > 0.01 or v > 0.01:
                print(f"  卡车 {k}: Q={q:.1f}, V={v:.1f}")
                any_loaded = True
        if not any_loaded:
            print("  (所有卡车初始载量都是 0 —— 这肯定有问题!)")

    # ============ E1 路线提取与打印 ============
    if verbose:
        print("\n--- E1 路线 ---")
    e1_routes_with_k = extract_routes_e1(sol_x, depot_id, N_E1_Tasks, K)
    if verbose:
        for k, route in e1_routes_with_k:
            print(f"  卡车 {k}: {' -> '.join(route)}")
        print_e1_loads(vars_dict, e1_routes_with_k)

    # ============ E2 路线提取与打印 ============
    out_e2_routes = {f: [] for f in F}
    if verbose:
        print("\n--- E2 路线 ---")
    for sat_id in F:
        vehs = ctx["V_by_sat"][sat_id]
        t_ids = [t.id for t in e2_tasks_by_sat.get(sat_id, [])]
        used_v = [
            v for v in vehs
            if sum(sol_z.get((sat_id, j, v), 0) for j in t_ids) > 0.5
        ]
        if not used_v:
            continue
        if verbose:
            print(f"  前置仓 {sat_id}:")
        routes = extract_routes_e2(sol_z, sat_id, t_ids, used_v)
        out_e2_routes[sat_id] = routes

        if verbose:
            for v_key, route in zip(used_v, routes):
                print(f"    车辆{v_key[1]}: {' -> '.join(route)}")
            print_e2_loads(vars_dict, used_v, routes)

    # 返回纯 route 列表以兼容 main.py 的绘图
    e1_routes = [r for _, r in e1_routes_with_k]
    return e1_routes, out_e2_routes
