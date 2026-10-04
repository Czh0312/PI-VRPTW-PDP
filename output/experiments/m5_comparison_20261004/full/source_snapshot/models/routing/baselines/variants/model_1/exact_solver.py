"""Exact and time-limited MIP entry points for Model 1."""
# -*- coding: utf-8 -*-
from gurobipy import GRB
from .model import build_model


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
    if solution_type == "proven_optimal" and float(m.Params.MIPGap) != 0.0:
        solution_type = "feasible_not_proven"
    info = {
        "status_code": m.status,
        "status": _status_name(m.status),
        "solution_type": solution_type,
        # A feasible incumbent and a certified optimum are fundamentally
        # different results.  Downstream experiments must use this field,
        # rather than inferring optimality from the presence of routes.
        "proven_optimal": solution_type == "proven_optimal",
        "proof_scope": "full_arc_flow",
        "formulation": "complete_fixed_assignment_delivery_arc_flow",
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
            print(f"[OK] Gurobi 参数 {name} = {value}")


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

    get_phys = ctx["get_phys"]
    K = ctx["K"]
    vehicles_per_satellite = ctx["vehicles_per_satellite"]
    started_x = 0
    skipped_x = 0
    started_z = 0
    skipped_z = 0

    for k, route in zip(K, e1_routes or []):
        phys_route = [get_phys(nid) for nid in route]
        for i, j in zip(phys_route, phys_route[1:]):
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
                verbose=True, time_limit=300, mip_gap=0.0,
                return_incumbent_on_time_limit=False, return_info=False,
                warm_start_routes=None, gurobi_params=None, backend="indexed"):
    """
    运行精确求解器并解析输出
    """
    from models.routing.common.operating_policy import policy_for
    policy_for(problem).require_model(1)
    if backend == "commodity_flow":
        from models.routing.common.commodity_flow import solve_commodity_flow
        return solve_commodity_flow(problem, 1, vehicles_per_satellite,
            e1_tasks=e1_tasks, e2_tasks_by_sat=e2_tasks_by_sat,
            verbose=verbose, time_limit=time_limit, mip_gap=mip_gap,
            return_incumbent_on_time_limit=return_incumbent_on_time_limit,
            return_info=return_info, warm_start_routes=warm_start_routes,
            gurobi_params=gurobi_params)
    if backend != "indexed":
        raise ValueError(f"Unknown exact backend: {backend}")
    m, vars_dict, ctx = build_model(problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite)
    
    if not verbose:
        m.setParam('OutputFlag', 0)

    if verbose:
        print("\n" + "=" * 60)
        print("求解器配置: 精确最优解模式 (Model 1 纯配送)")
        print("=" * 60)
        print(f"[OK] MIPGap = {mip_gap} (要求精确最优解)")
        if time_limit is None:
            print("[OK] 无时间限制")
        else:
            print(f"[OK] 时间限制 {time_limit} 秒")
        print("=" * 60)

    m.setParam('MIPGap', mip_gap)
    # ``None`` deliberately leaves Gurobi's TimeLimit at its default infinity.
    # This is different from supplying an arbitrarily large finite limit and is
    # needed by resumable proof campaigns that must run until OPTIMAL/INFEASIBLE.
    if time_limit is not None:
        m.setParam('TimeLimit', time_limit)
    m.setParam('IntFeasTol', 1e-9)
    m.setParam('FeasibilityTol', 1e-9)
    _apply_gurobi_params(m, gurobi_params, verbose)
    _apply_route_warm_start(vars_dict, ctx, warm_start_routes, verbose)
    m.optimize()

    if m.status == GRB.OPTIMAL:
        # 修正：捕获 _print_solution 返回的路径数据并 return
        e1_routes, e2_routes_out = _print_solution(
            m, problem, vars_dict, ctx, e2_tasks_by_sat, verbose=verbose
        )
        return _maybe_with_info(
            (e1_routes, e2_routes_out),
            _solve_info(m, "proven_optimal"),
            return_info,
        )

    if m.SolCount > 0 and return_incumbent_on_time_limit:
        if verbose:
            print(f"\n求解未证明最优 ({_status_name(m.status)})，返回可行解: "
                  f"{m.ObjVal:.2f}  (Gap: {m.MIPGap:.2%})")
        e1_routes, e2_routes_out = _print_solution(
            m, problem, vars_dict, ctx, e2_tasks_by_sat, verbose=verbose
        )
        return _maybe_with_info(
            (e1_routes, e2_routes_out),
            _solve_info(m, "feasible_not_proven"),
            return_info,
        )
    else:
        if verbose:
            print("\n[FAILED] 未找到证明最优的解")
        if m.status == GRB.INFEASIBLE:
            if verbose:
                print("模型不可行，正在计算 IIS（可能需要几秒到几十秒）...")
            m.computeIIS()
            from pathlib import Path
            iis_dir = Path(__file__).resolve().parent.parent.parent / "output" / "debug_iis" / "model1_exact"
            iis_dir.mkdir(parents=True, exist_ok=True)
            iis_path = iis_dir / "debug_iis_model01_exact_infeasible.ilp"
            m.write(str(iis_path))
            if verbose:
                print(f"[OK] IIS 已写入 {iis_path}")
        return _maybe_with_info((None, None), _solve_info(m, "none"), return_info)


def solve_mip(problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite,
              **kwargs):
    """Run the Model 1 MIP and allow a time-limited incumbent explicitly.

    ``solve_exact`` is intentionally strict: its default output is certified
    only.  Callers that need a usable bound/incumbent for diagnostics or warm
    starts should opt into this separate entry point instead.
    """
    kwargs["return_incumbent_on_time_limit"] = True
    return solve_exact(
        problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite, **kwargs
    )

def _print_solution(m, problem, vars_dict, ctx, e2_tasks_by_sat, *, verbose=True):
    """Extract routes, optionally printing the human-readable solution."""
    if verbose:
        print("\n" + "=" * 60)
        print("[OK] 求解成功! 已证明最优性")
        print(f"最优目标值: {m.ObjVal:.2f}")
        print(f"求解时间: {m.Runtime:.2f} 秒")
        print("=" * 60)
    
    x = vars_dict['x']
    z = vars_dict['z']
    sol_x = m.getAttr('X', x)
    sol_z = m.getAttr('X', z)
    
    depot_id = problem.depot.id
    F = ctx['F']
    N1E = ctx['N1E']
    K = ctx['K']
    vehicles_per_satellite = ctx['vehicles_per_satellite']

    # 用于返回给外部绘图的数据结构
    out_e1_routes = []
    out_e2_routes = {f: [] for f in F}

    # 1. 提取 E1 路线
    if verbose:
        print("\n--- E1 路线 ---")
    for k in K:
        route = [depot_id]
        cur = next((j for j in F if sol_x.get((depot_id, j, k), 0) > 0.5), None)
        visited = {depot_id}
        
        while cur and cur not in visited:
            route.append(cur)
            visited.add(cur)
            next_node = next((j for j in N1E if cur != j and sol_x.get((cur, j, k), 0) > 0.5), None)
            if next_node == depot_id or not next_node:
                route.append(depot_id)
                break
            cur = next_node
        
        if len(route) > 2:
            if verbose:
                print(f"卡车{k}: {' → '.join(route)}")
            out_e1_routes.append(route)

    # 2. 提取 E2 路线
    if verbose:
        print("\n" + "=" * 60)
        print("E2 前置仓独立车队统计")
        print("=" * 60)
    
    total_e2_used = 0
    for f in F:
        tasks_f = e2_tasks_by_sat.get(f, [])
        task_ids = [t.id for t in tasks_f]
        used_v = []
        
        for v_idx in range(vehicles_per_satellite):
            v_key = (f, v_idx)
            if any(sol_z.get((f, j, v_key), 0) > 0.5 for j in task_ids):
                used_v.append(v_idx)
        
        if verbose:
            print(f"\n【前置仓 {f}】")
            print(f"  配置车辆数: {vehicles_per_satellite} 辆")
            print(f"  实际使用数: {len(used_v)} 辆")
        
        if used_v:
            if verbose:
                print(f"  使用率: {len(used_v) / vehicles_per_satellite * 100:.1f}%")
            for v_idx in used_v:
                v_key = (f, v_idx)
                route = [f]
                cur = next((j for j in task_ids if sol_z.get((f, j, v_key), 0) > 0.5), None)
                visited = {f}
                
                while cur and cur not in visited:
                    route.append(cur)
                    visited.add(cur)
                    next_node = next((j for j in [f] + task_ids if cur != j and sol_z.get((cur, j, v_key), 0) > 0.5), None)
                    if next_node == f or not next_node:
                        route.append(f)
                        break
                    cur = next_node
                
                if verbose:
                    print(f"  车辆{v_idx}: {' → '.join(route)}")
                out_e2_routes[f].append(route)
                
        total_e2_used += len(used_v)
    
    # 3. 统计汇总
    total_e2_veh = len(F) * vehicles_per_satellite
    if verbose:
        print("\n" + "-" * 60)
        print(f"E2总体利用率: {total_e2_used / total_e2_veh * 100:.1f}%")
        print("=" * 60)

    # 返回提取好的路径
    return out_e1_routes, out_e2_routes
