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


def _compute_warm_start_continuous(ctx, e1_routes, e2_routes_by_sat):
    """Replay ALNS routes to (a) drop arcs the strengthened M2 fixed to 0 and
    (b) compute complete continuous .Start values, so Gurobi accepts the start.

    The strengthened M2 fixes time-window-infeasible arcs to 0 by an
    *earliest-departure* test (model.py `_time_infeasible`), but ALNS routes use
    *earliest-arrival-with-waiting* feasibility, so an ALNS route can traverse an
    arc the MIP forbids. Setting x/z.Start=1 on such an arc makes the start
    infeasible and Gurobi rejects it. This helper drops any route containing a
    forbidden arc, and additionally derives the continuous time/load variables
    (which the old warm-start left UNDEFINED) so Gurobi gets a complete start.

    Mirrors constraints.py timing: earliest-arrival with waiting, TFp pickup
    lower bound when TFp < 9000. E1/E2 routes are sorted by descending delivery
    weight so the E1_LoadSym / E2_LoadSym ordering constraints hold.

    Returns dict with binary arc sets + continuous value dicts (var-key -> float)
    + '_stats'. Returns None on a fatal error (caller then skips warm-start).
    """
    import math

    depot_id = ctx['depot_id']
    K = ctx['K']
    F_set = set(ctx['F'])
    e1_task_map = ctx['e1_task_map']
    e2_task_map = ctx['e2_task_map']
    e2_tasks_by_sat = ctx['e2_tasks_by_sat']
    time_map = ctx['time_map']
    e1_svc = ctx['e1_service_time_map']
    e2_svc = ctx['e2_service_time_map']
    e1_bad = ctx['e1_infeasible_arcs']
    e2_bad = ctx['e2_infeasible_arcs']
    TFp = ctx['TFp']
    tfp_active = TFp < 9000.0
    vehicles_per_satellite = ctx['vehicles_per_satellite']

    # Same-satellite E1 (Sp->Sd) and same-customer E2 (Cp->Cd) forbidden arcs
    # (mirror constraints.py E1_ForbidArc / E2_ForbidSameCustPickBeforeDel).
    e1_forbidden = set()
    for s_id in ctx['F']:
        pid, did = f"{s_id}p", f"{s_id}d"
        if pid in e1_task_map and did in e1_task_map:
            e1_forbidden.add((pid, did))
    e2_forbidden = set()
    for _sat, tasks in e2_tasks_by_sat.items():
        for customer in {t.cust for t in tasks}:
            d_id, p_id = f"{customer}d", f"{customer}p"
            if d_id in e2_task_map and p_id in e2_task_map:
                e2_forbidden.add((p_id, d_id))

    def sq(t):  # signed weight: delivery consumes (+w), pickup returns (-w)
        return t.w if t.kind == 'd' else -t.w

    def sv(t):
        return t.v if t.kind == 'd' else -t.v

    def _del_w(r, task_map):
        return sum(t.w for nid in (r or [])
                   for t in [task_map.get(nid)] if t and t.kind == 'd')

    out = {'e1_arcs': set(), 'e2_arcs': set(), 'A_e1': {}, 'Q_k': {}, 'Vol_k': {},
           'A_e2': {}, 'Q_r': {}, 'Vol_r': {}, 'A_start': {}, 'A_return': {},
           '_stats': {'e1_dropped': 0, 'e2_dropped': 0}}

    # ---- E1 forward pass (sorted by descending delivery weight -> E1_LoadSym) ----
    sat_departure = {}  # sat_id -> E2 start time
    for k, route in zip(K, sorted((e1_routes or []), key=lambda r: _del_w(r, e1_task_map), reverse=True)):
        if not route or len(route) < 2:
            continue
        arcs = list(zip(route, route[1:]))
        if any((i, j) in e1_bad or (i, j) in e1_forbidden for i, j in arcs):
            out['_stats']['e1_dropped'] += 1
            continue
        cur = 0.0
        q = _del_w(route, e1_task_map)
        v = sum(t.v for nid in route for t in [e1_task_map.get(nid)]
                if t and t.kind == 'd')
        out['A_e1'][depot_id, k] = 0.0
        out['Q_k'][depot_id, k] = q
        out['Vol_k'][depot_id, k] = v
        feasible = True
        for i, j in arcs:
            out['e1_arcs'].add((i, j, k))
            t = e1_task_map.get(j)
            cur += time_map[i, j]
            if t is not None:
                e_j, l_j = t.tw
                if cur < e_j:
                    cur = e_j
                if l_j < math.inf and cur > l_j + 1e-6:
                    feasible = False
                    break
                if t.kind == 'p' and tfp_active and cur < TFp - 1e-6:
                    cur = TFp
                q -= sq(t)
                v -= sv(t)
                if q < -1e-6 or v < -1e-6:
                    feasible = False
                    break
                out['A_e1'][j, k] = cur
                out['Q_k'][j, k] = q
                out['Vol_k'][j, k] = v
                cur += e1_svc.get(j, 0.0)
                if t.kind == 'd' and t.cust in F_set:
                    sat_departure[t.cust] = cur  # arrival + service = E2 start
        if not feasible:
            for (i, j) in arcs:
                out['e1_arcs'].discard((i, j, k))
            out['_stats']['e1_dropped'] += 1

    # ---- E2 per-satellite pass (sorted by descending init delivery weight) ----
    for sat_id, routes in (e2_routes_by_sat or {}).items():
        if not routes or not e2_tasks_by_sat.get(sat_id):
            continue
        if sat_id not in sat_departure:
            continue  # E2 unanchored: its satellite delivery was dropped on E1
        start_time = sat_departure[sat_id]
        e2_sorted = sorted(routes[:vehicles_per_satellite],
                           key=lambda r: _del_w(r, e2_task_map), reverse=True)
        for v_idx, route in enumerate(e2_sorted):
            if not route or len(route) < 2:
                continue
            arcs = list(zip(route, route[1:]))
            if any((i, j) in e2_bad or (i, j) in e2_forbidden for i, j in arcs):
                out['_stats']['e2_dropped'] += 1
                continue
            init_w = _del_w(route, e2_task_map)
            init_v = sum(t.v for nid in route for t in [e2_task_map.get(nid)]
                         if t and t.kind == 'd')
            q, vol, rel, prev = init_w, init_v, 0.0, sat_id
            v_key = (sat_id, v_idx)
            arrivals = {}
            feasible = True
            for nid in route:
                if nid == sat_id:
                    continue
                t = e2_task_map.get(nid)
                rel += time_map[prev, nid]
                abs_arr = start_time + rel
                if t is not None:
                    e_j, l_j = t.tw
                    if abs_arr < e_j:
                        rel = e_j - start_time
                        abs_arr = e_j
                    if l_j < math.inf and abs_arr > l_j + 1e-6:
                        feasible = False
                        break
                    q -= sq(t)
                    vol -= sv(t)
                    if q < -1e-6 or vol < -1e-6:
                        feasible = False
                        break
                    arrivals[nid] = abs_arr - start_time  # A_e2 is RELATIVE
                    rel += e2_svc.get(nid, 0.0)
                prev = nid
            if not feasible:
                out['_stats']['e2_dropped'] += 1
                continue
            for (i, j) in arcs:
                if i == sat_id and j == sat_id:
                    continue
                out['e2_arcs'].add((i, j, v_key))
            out['A_start'][sat_id, v_idx] = start_time
            out['A_return'][sat_id, v_idx] = start_time + rel + time_map[prev, sat_id]
            out['A_e2'][sat_id, v_key] = 0.0
            out['Q_r'][sat_id, v_key] = init_w
            out['Vol_r'][sat_id, v_key] = init_v
            q2, vol2 = init_w, init_v
            for nid in route:
                t = e2_task_map.get(nid)
                if t is None or nid == sat_id:
                    continue
                q2 -= sq(t)
                vol2 -= sv(t)
                out['A_e2'][nid, v_key] = arrivals.get(nid, 0.0)
                out['Q_r'][nid, v_key] = q2
                out['Vol_r'][nid, v_key] = vol2
    return out


def _apply_route_warm_start(vars_dict, ctx, warm_start_routes, verbose):
    if not warm_start_routes:
        return

    e1_routes, e2_routes_by_sat = warm_start_routes

    # Reset all warm-started var groups (binaries + continuous) first.
    var_groups = [vars_dict["x"], vars_dict["z"], vars_dict["A_e1"],
                  vars_dict["A_e2"], vars_dict["Q_k"], vars_dict["Vol_k"],
                  vars_dict["Q_r"], vars_dict["Vol_r"], vars_dict["A_start"],
                  vars_dict["A_return"]]
    for vd in var_groups:
        for var in vd.values():
            var.Start = GRB.UNDEFINED

    try:
        cont = _compute_warm_start_continuous(ctx, e1_routes, e2_routes_by_sat)
    except Exception as exc:  # best-effort warm start: never crash the solve
        if verbose:
            print(f"[MIPStart] warm-start 计算失败，跳过: {exc}")
        return
    if cont is None:
        return

    x = vars_dict["x"]
    z = vars_dict["z"]

    def _assign_bin(var_dict, arc_set):
        started = skipped = 0
        for key in arc_set:
            if key in var_dict:
                var_dict[key].Start = 1.0
                started += 1
            else:
                skipped += 1
        return started, skipped

    def _assign_cont(var_dict, value_dict):
        n = 0
        for key, val in value_dict.items():
            if key in var_dict:
                var_dict[key].Start = val
                n += 1
        return n

    sx, skx = _assign_bin(x, cont["e1_arcs"])
    sz, skz = _assign_bin(z, cont["e2_arcs"])
    n = {name: _assign_cont(vars_dict[name], cont[name])
         for name in ("A_e1", "Q_k", "Vol_k", "A_e2", "Q_r", "Vol_r",
                      "A_start", "A_return")}

    if verbose:
        st = cont["_stats"]
        print(
            "[MIPStart] 路线 warm start: "
            f"E1 arcs={sx}(skip={skx},dropped={st['e1_dropped']}), "
            f"E2 arcs={sz}(skip={skz},dropped={st['e2_dropped']}); "
            f"连续 A_e1={n['A_e1']} Q_k={n['Q_k']} Vol_k={n['Vol_k']} "
            f"A_start={n['A_start']} A_return={n['A_return']} "
            f"A_e2={n['A_e2']} Q_r={n['Q_r']} Vol_r={n['Vol_r']}"
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
