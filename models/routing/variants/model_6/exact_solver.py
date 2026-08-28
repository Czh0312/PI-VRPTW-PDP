# -*- coding: utf-8 -*-
"""Model 6 compact homogeneous-container globally exact solver."""
from __future__ import annotations

import time
from pathlib import Path

from gurobipy import GRB

from models.routing.variants.model_3.exact_solver import solve_exact as solve_m3_exact
from models.routing.variants.model_5.exact_solver import (
    _has_metric_distances,
    _zero_pickup_load_relaxation,
)

from .model import (
    apply_no_transfer_warm_start,
    build_model,
    complete_no_transfer_warm_start,
    extract_compact_solution,
    model_statistics,
)


def _status_name(status_code):
    names = {
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
        GRB.USER_OBJ_LIMIT: "USER_OBJ_LIMIT",
        GRB.WORK_LIMIT: "WORK_LIMIT",
        GRB.MEM_LIMIT: "MEM_LIMIT",
    }
    return names.get(status_code, str(status_code))


def _initial_routes(problem, e1_tasks, e2_tasks_by_sat,
                    vehicles_per_satellite):
    """Build a cheap feasible no-transfer incumbent without another MIP."""
    try:
        from models.routing.alns.variants.model_5 import Evaluator, build_initial_solution

        evaluator = Evaluator(
            problem, e1_tasks, e2_tasks_by_sat,
            vehicles_per_satellite=vehicles_per_satellite,
        )
        solution = build_initial_solution(
            problem, e1_tasks, e2_tasks_by_sat, evaluator
        )
        if not evaluator.is_feasible(solution):
            return None
        e1_routes = [route.full_path() for route in solution.e1_routes]
        e2_routes = {
            sat_id: [route.full_path() for route in routes]
            for sat_id, routes in solution.e2_routes.items()
        }
        return e1_routes, e2_routes
    except Exception:
        return None


def _solve_info(model, solution_type, statistics, transshipments=None,
                transferred_boxes=0):
    sol_count = int(getattr(model, "SolCount", 0))
    info = {
        "status_code": int(model.status),
        "status": _status_name(model.status),
        "solution_type": solution_type,
        "proven_optimal": solution_type == "proven_optimal",
        "runtime": float(getattr(model, "Runtime", 0.0)),
        "sol_count": sol_count,
        "objective": None,
        "best_bound": None,
        "mip_gap": None,
        "formulation": "compact_homogeneous_async_event_arc_flow",
        "proof_scope": "compact_async_event_handoff_model6",
        "relay_policy": (
            "homogeneous_integer_single_async_handoff_at_existing_service_location"
        ),
        "transshipments": list(transshipments or []),
        "transshipment_count": len(transshipments or []),
        "transferred_container_count": int(transferred_boxes),
        "model_statistics": dict(statistics),
    }
    if sol_count:
        info["objective"] = float(model.ObjVal)
        info["mip_gap"] = float(model.MIPGap)
    try:
        info["best_bound"] = float(model.ObjBound)
    except Exception:
        pass
    return info


def solve_exact(problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite,
                TFp=9999.0, verbose=True, time_limit=300, mip_gap=0.0,
                return_incumbent_on_time_limit=False, return_info=False,
                warm_start_routes=None, gurobi_params=None,
                strengthen=True, compute_iis_on_infeasible=True,
                use_relaxation_certificate=True):
    """Solve the complete compact Model 6 MILP.

    Unlike the historical two-stage implementation, routes and homogeneous
    container handoffs are optimized jointly. ``proven_optimal`` is true only
    when Gurobi closes this full compact event-handoff model.
    """
    started = time.perf_counter()

    def remaining_time():
        if time_limit is None:
            return None
        return max(0.01, float(time_limit) - (time.perf_counter() - started))

    # Exact shortcut certificate.  Removing every empty-container load while
    # retaining all original pickup visits, time windows and E1/E2 timing is a
    # valid metric-distance relaxation of the O/R handoff model: delete the
    # extra meet-only visits and shortcut the resulting route.  If this lower
    # bound equals a no-transfer Model 3 solution, no relay can improve it.
    direct_e1 = direct_e2 = None
    direct_info = relaxation_info = None
    if (use_relaxation_certificate
            and _has_metric_distances(problem, e1_tasks, e2_tasks_by_sat)):
        if verbose:
            print("[CERT] 先求无中转可行上界与零回收载荷严格下界")
        direct_e1, direct_e2, direct_info = solve_m3_exact(
            problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite,
            TFp=TFp, verbose=False, time_limit=remaining_time(), mip_gap=0.0,
            return_incumbent_on_time_limit=True, return_info=True,
            warm_start_routes=warm_start_routes, gurobi_params=gurobi_params,
        )
        if direct_e1 and direct_e2:
            relaxed_tasks = _zero_pickup_load_relaxation(e2_tasks_by_sat)
            _lb_e1, _lb_e2, relaxation_info = solve_m3_exact(
                problem, e1_tasks, relaxed_tasks, vehicles_per_satellite,
                TFp=TFp, verbose=False, time_limit=remaining_time(), mip_gap=0.0,
                return_incumbent_on_time_limit=True, return_info=True,
                warm_start_routes=(direct_e1, direct_e2),
                gurobi_params=gurobi_params,
            )
        incumbent = direct_info.get("objective") if direct_info else None
        lower_bound = (
            relaxation_info.get("objective")
            if relaxation_info and relaxation_info.get("proven_optimal")
            else None
        )
        tolerance = 1e-6 * max(1.0, abs(incumbent or 0.0))
        if (incumbent is not None and lower_bound is not None
                and lower_bound >= incumbent - tolerance):
            runtime = time.perf_counter() - started
            info = {
                "status_code": int(GRB.OPTIMAL),
                "status": "OPTIMAL",
                "solution_type": "proven_optimal",
                "proven_optimal": True,
                "runtime": runtime,
                "sol_count": 1,
                "objective": float(incumbent),
                "best_bound": float(lower_bound),
                "mip_gap": 0.0,
                "formulation": "zero_pickup_load_relaxation_certificate",
                "proof_scope": "compact_async_event_handoff_model6",
                "relay_policy": (
                    "homogeneous_integer_single_async_handoff_at_"
                    "existing_service_location"
                ),
                "transshipments": [],
                "transshipment_count": 0,
                "transferred_container_count": 0,
                "model_statistics": {},
                "certificate": {
                    "type": "metric_route_shortcut_zero_pickup_load",
                    "direct_status": direct_info["status"],
                    "relaxation_status": relaxation_info["status"],
                    "lower_bound": float(lower_bound),
                },
            }
            if verbose:
                print(
                    f"[CERTIFIED] LB=UB={incumbent:.6f}，"
                    "Model 6 已严格证明最优"
                )
            if return_info:
                return direct_e1, direct_e2, info
            return direct_e1, direct_e2

        if direct_e1 and direct_e2:
            warm_start_routes = (direct_e1, direct_e2)

    if verbose:
        print("\n" + "=" * 60)
        print("Model 6 紧凑同质空箱联合精确求解")
        print("交接口径：仅在配送/回收服务事件；接力空箱不得再次中转")
        print("=" * 60)

    model, variables, ctx = build_model(
        problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite,
        TFp=TFp, strengthen=strengthen,
    )
    statistics = model_statistics(model, variables, ctx)
    if verbose:
        print(
            "模型规模: "
            f"vars={statistics['variables']}, "
            f"binary={statistics['binary_variables']}, "
            f"integer={statistics['integer_variables']}, "
            f"constraints={statistics['constraints']}, "
            f"E2 arcs={statistics['e2_route_arcs']}, "
            f"handoff vars={statistics['handoff_quantity_variables']}"
        )

    seed_routes = warm_start_routes
    if seed_routes is None:
        seed_routes = _initial_routes(
            problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite
        )
    if seed_routes:
        apply_no_transfer_warm_start(variables, ctx, seed_routes)
        completed = complete_no_transfer_warm_start(model, variables)
        if verbose:
            state = "完整可行解" if completed else "部分路线初值"
            print(f"[MIPStart] 已加入无中转{state}")

    if not verbose:
        model.setParam("OutputFlag", 0)
    model.setParam("MIPGap", mip_gap)
    if time_limit is not None:
        model.setParam("TimeLimit", remaining_time())
    model.setParam("IntFeasTol", 1e-9)
    model.setParam("FeasibilityTol", 1e-9)
    model.setParam("Presolve", 2)
    model.setParam("PreSparsify", 1)
    model.setParam("Symmetry", 2)
    if gurobi_params:
        for name, value in gurobi_params.items():
            model.setParam(name, value)
            if verbose:
                print(f"Gurobi 参数 {name} = {value}")

    model.optimize()

    can_return = model.status == GRB.OPTIMAL or (
        model.SolCount > 0 and return_incumbent_on_time_limit
    )
    if can_return:
        e1_routes, e2_routes, route_vehicles, transshipments, boxes = (
            extract_compact_solution(model, variables, ctx)
        )
        solution_type = (
            "proven_optimal"
            if model.status == GRB.OPTIMAL and float(mip_gap) == 0.0
            else "feasible_not_proven"
        )
        info = _solve_info(
            model, solution_type, statistics, transshipments, boxes
        )
        info["compact_mip_runtime"] = info["runtime"]
        info["runtime"] = time.perf_counter() - started
        info["route_vehicle_indices"] = route_vehicles
        if verbose:
            print(
                f"\n状态={info['status']}  objective={info['objective']:.6f}  "
                f"bound={info['best_bound']:.6f}  gap={info['mip_gap']:.4%}"
            )
            print("\n--- E1 路线 ---")
            for idx, route in enumerate(e1_routes):
                print(f"  车辆{idx}: {' -> '.join(route)}")
            print("\n--- E2 路线 ---")
            for sat_id, routes in e2_routes.items():
                if not routes:
                    continue
                print(f"  前置仓 {sat_id}:")
                vehicle_indices = route_vehicles.get(sat_id, [])
                for pos, route in enumerate(routes):
                    vehicle_idx = vehicle_indices[pos]
                    print(f"    车辆{vehicle_idx}: {' -> '.join(route)}")
            print("\n--- 同质空箱交接 ---")
            if not transshipments:
                print("  无中转")
            for item in transshipments:
                print(
                    f"  {item['event']}@{item['physical_location']}: "
                    f"车辆{item['sender_vehicle']} -> 车辆{item['receiver_vehicle']}, "
                    f"数量={item['quantity']}, "
                    f"可接收={item['handoff_ready_time']:.2f}, "
                    f"实际接收={item['receiver_pickup_time']:.2f}"
                )
        if return_info:
            return e1_routes, e2_routes, info
        return e1_routes, e2_routes

    if model.status == GRB.INFEASIBLE and compute_iis_on_infeasible:
        if verbose:
            print("模型不可行，正在计算 IIS...")
        model.computeIIS()
        iis_dir = (
            Path(__file__).resolve().parents[2]
            / "output" / "debug_iis" / "model6_exact"
        )
        iis_dir.mkdir(parents=True, exist_ok=True)
        iis_path = iis_dir / "compact_model6_infeasible.ilp"
        model.write(str(iis_path))
        if verbose:
            print(f"IIS 已写入 {iis_path}")

    info = _solve_info(model, "none", statistics)
    info["compact_mip_runtime"] = info["runtime"]
    info["runtime"] = time.perf_counter() - started
    if verbose:
        print(f"\n未找到可返回解，状态={info['status']}")
    if return_info:
        return None, None, info
    return None, None


def solve_mip(problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite,
              **kwargs):
    """Time-limited non-certifying counterpart that may return an incumbent."""
    kwargs["return_incumbent_on_time_limit"] = True
    return solve_exact(
        problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite, **kwargs
    )
