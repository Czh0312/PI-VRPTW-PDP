# models/model_3/solver.py
# -*- coding: utf-8 -*-
"""
Model 3 精确求解：路线与中转联合 MIP。
"""
from gurobipy import GRB

from .exact_joint import (
    apply_route_warm_start,
    build_model3_joint_exact,
    extract_solution,
)
from models.model_2.solver import solve_exact as solve_m2_exact


def solve_exact(problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite,
                verbose=True, max_relocation_candidates=1,
                warm_start_with_m2=True):
    if verbose:
        total_w_d = sum(t.w for t in e1_tasks if t.kind == 'd')
        total_w_p = sum(t.w for t in e1_tasks if t.kind == 'p')
        total_v_d = sum(t.v for t in e1_tasks if t.kind == 'd')
        total_v_p = sum(t.v for t in e1_tasks if t.kind == 'p')
        print(f"\n[DEBUG] E1 任务总览:")
        print(f"  卡车数 K = {problem.e1_vehicle_count}")
        print(f"  单车 weight cap = {problem.e1_weight_cap}")
        print(f"  单车 volume cap = {problem.e1_volume_cap}")
        print(f"  total delivery weight = {total_w_d}, volume = {total_v_d}")
        print(f"  total pickup  weight = {total_w_p}, volume = {total_v_p}")
        for t in e1_tasks:
            print(f"    {t.id}: kind={t.kind}, w={t.w}, v={t.v}, tw={t.tw}")

        e2_total_d = sum(
            t.w for tasks in e2_tasks_by_sat.values()
            for t in tasks if t.kind == 'd')
        e2_total_p = sum(
            t.w for tasks in e2_tasks_by_sat.values()
            for t in tasks if t.kind == 'p')
        print(f"\n[DEBUG] E2 任务总览:")
        print(f"  每卫星车辆数 = {vehicles_per_satellite}")
        print(f"  单车 weight cap = {problem.e2_weight_cap}")
        print(f"  单车 volume cap = {problem.e2_volume_cap}")
        print(f"  total delivery weight = {e2_total_d}, pickup weight = {e2_total_p}")
        for sat_id, tasks in e2_tasks_by_sat.items():
            print(f"  satellite {sat_id}: {len(tasks)} tasks")
            for t in tasks:
                print(f"    {t.id}: kind={t.kind}, w={t.w}, v={t.v}")

    m, vars_dict, ctx = build_model3_joint_exact(
        problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite,
        max_relocation_candidates=max_relocation_candidates,
    )
    if warm_start_with_m2:
        try:
            if verbose:
                print("\n[INFO] 使用 Model 2 exact 解作为 Model 3 联合 MIP warm start")
            m2_e1, m2_e2 = solve_m2_exact(
                problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite,
                TFp=9999.0, verbose=False,
            )
            if m2_e1 and m2_e2:
                apply_route_warm_start(vars_dict, ctx, m2_e1, m2_e2)
        except Exception as exc:
            if verbose:
                print(f"[WARN] Model 2 warm start 构造失败，继续使用默认初始解: {exc}")
    if not verbose:
        m.setParam("OutputFlag", 0)

    m.setParam("MIPGap", 0.0)
    m.setParam("TimeLimit", 300)
    m.setParam("MIPFocus", 1)
    m.setParam("Heuristics", 0.5)
    m.optimize()

    if m.status == GRB.OPTIMAL:
        e1_routes, e2_routes, transshipments = _print_joint_solution(
            m, vars_dict, ctx
        )
        if verbose:
            _print_joint_routes(e1_routes, e2_routes, transshipments)
        return e1_routes, e2_routes

    if m.status == GRB.TIME_LIMIT and m.SolCount > 0:
        print(f"\n达到时间限制, 当前最优解: {m.ObjVal:.2f}  (Gap: {m.MIPGap:.2%})")
        e1_routes, e2_routes, transshipments = _print_joint_solution(
            m, vars_dict, ctx
        )
        if verbose:
            _print_joint_routes(e1_routes, e2_routes, transshipments)
        return e1_routes, e2_routes

    if m.status == GRB.INFEASIBLE:
        print("\n模型不可行，正在计算 IIS...")
        m.computeIIS()
        from pathlib import Path
        iis_dir = Path(__file__).resolve().parent.parent.parent / "output" / "debug_iis" / "model3_exact"
        iis_dir.mkdir(parents=True, exist_ok=True)
        iis_path = iis_dir / "debug_iis_model03_exact_infeasible.ilp"
        m.write(str(iis_path))
        print(f"✓ IIS 已写入 {iis_path}")

    print("\n未找到可行解")
    return None, None


def _print_joint_solution(m, vars_dict, ctx):
    print("\n" + "=" * 60)
    print(f"Model 3 联合 MIP 求解完成!  目标值: {m.ObjVal:.2f}")
    print("=" * 60)
    return extract_solution(m, vars_dict, ctx)


def _print_joint_routes(e1_routes, e2_routes, transshipments):
    print("\n--- E1 路线 ---")
    for idx, route in enumerate(e1_routes):
        print(f"  卡车 {idx}: {' -> '.join(route)}")

    print("\n--- E2 路线 ---")
    for sat_id, routes in e2_routes.items():
        if not routes:
            continue
        print(f"  前置仓 {sat_id}:")
        for idx, route in enumerate(routes):
            print(f"    车辆 {idx}: {' -> '.join(route)}")

    print("\n--- 联合中转决策 ---")
    if not transshipments:
        print("  无中转")
    for pick_task, old_phys, new_phys in transshipments:
        print(f"  {pick_task}: {old_phys} -> {new_phys}")
