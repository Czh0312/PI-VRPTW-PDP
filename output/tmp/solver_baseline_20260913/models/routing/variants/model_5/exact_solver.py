# -*- coding: utf-8 -*-
"""Model 5 精确求解：证书、模式完备分解与联合 MIP。"""
import itertools
import time

from gurobipy import GRB

from models.routing.common.domain.node import Task

from .model import (
    apply_route_warm_start,
    build_model,
    extract_solution,
)
from models.routing.variants.model_3.exact_solver import solve_exact as solve_m3_exact


def _status_name(status_code):
    names = {
        GRB.OPTIMAL: "OPTIMAL", GRB.INFEASIBLE: "INFEASIBLE",
        GRB.TIME_LIMIT: "TIME_LIMIT", GRB.INTERRUPTED: "INTERRUPTED",
        GRB.NODE_LIMIT: "NODE_LIMIT", GRB.SUBOPTIMAL: "SUBOPTIMAL",
        GRB.NUMERIC: "NUMERIC", GRB.CUTOFF: "CUTOFF",
    }
    return names.get(status_code, str(status_code))


def _solve_info(m, solution_type, max_relocation_candidates,
                max_transshipments, transshipments=None, strengthen=True,
                relay_fixes=None, handoff_fixes=None):
    canonical_scope = (
        max_relocation_candidates is None and max_transshipments is None
        and not relay_fixes and not handoff_fixes
    )
    info = {
        "status_code": int(m.status),
        "status": _status_name(m.status),
        "solution_type": solution_type,
        # A restricted relocation neighbourhood is not the original Model 5.
        "proven_optimal": (
            solution_type == "proven_optimal"
            and canonical_scope
        ),
        "relocation_neighborhood": (
            "full" if max_relocation_candidates is None
            else f"nearest_{max_relocation_candidates}"
        ),
        "runtime": float(getattr(m, "Runtime", 0.0)),
        "relay_policy": "single_handoff_indivisible_same_satellite",
        "formulation": "strengthened_arc_flow" if strengthen else "baseline_arc_flow",
        "max_transshipments": max_transshipments,
        "relay_fixes": dict(relay_fixes or {}),
        "handoff_fixes": dict(handoff_fixes or {}),
        "proof_scope": (
            "full_single_handoff_model5" if canonical_scope
            else "restricted_handoff_model"
        ),
        "transshipments": list(transshipments or []),
        "transshipment_count": len(transshipments or []),
        "sol_count": int(getattr(m, "SolCount", 0)),
        "objective": None,
        "best_bound": None,
        "mip_gap": None,
    }
    if m.SolCount:
        info["objective"] = float(m.ObjVal)
        info["mip_gap"] = float(m.MIPGap)
    try:
        info["best_bound"] = float(m.ObjBound)
    except Exception:
        pass
    return info


def _with_info(routes, info, return_info):
    if return_info:
        return *routes, info
    return routes


def _solve_joint_exact(problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite,
                verbose=True, max_relocation_candidates=None,
                max_transshipments=None,
                relay_fixes=None, objective_cutoff=None,
                handoff_fixes=None,
                strengthen=True,
                warm_start_with_m3=True, time_limit=300, mip_gap=0.0,
                return_incumbent_on_time_limit=False, return_info=False,
                warm_start_routes=None, gurobi_params=None,
                compute_iis_on_infeasible=True):
    """Solve the full Model 5 MIP.

    ``max_relocation_candidates=None`` is deliberately the default: limiting
    handoff locations changes the model and cannot certify canonical Model 5.
    ``max_transshipments=0`` is useful for the M3-degeneration regression; any
    finite global cap is likewise labelled as a restricted model.
    """
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

    m, vars_dict, ctx = build_model(
        problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite,
        max_relocation_candidates=max_relocation_candidates,
        max_transshipments=max_transshipments,
        relay_fixes=relay_fixes,
        handoff_fixes=handoff_fixes,
        strengthen=strengthen,
    )
    if warm_start_routes is not None:
        # External incumbent (e.g. ALNS routes from the comparison harness):
        # seed the joint MIP directly and skip the internal Model 3 solve.
        # Without this, the 100-customer joint MIP finds no feasible solution
        # in the time budget (the same arc-flow weakness as Model 3), and the
        # internal Model 3 warm-start solve would hit the same wall.
        try:
            ws_e1, ws_e2 = warm_start_routes
            if ws_e1 and ws_e2:
                if verbose:
                    print("\n[INFO] 使用外部路线作为 Model 5 联合 MIP warm start")
                apply_route_warm_start(vars_dict, ctx, ws_e1, ws_e2)
        except Exception as exc:
            if verbose:
                print(f"[WARN] 外部 warm start 构造失败，继续使用默认初始解: {exc}")
    elif (warm_start_with_m3
          and not any((relay_fixes or {}).values())
          and not handoff_fixes):
        try:
            if verbose:
                print("\n[INFO] 使用 Model 3 exact 解作为 Model 5 联合 MIP warm start")
            m3_e1, m3_e2 = solve_m3_exact(
                problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite,
                TFp=9999.0, verbose=False, time_limit=time_limit,
                return_incumbent_on_time_limit=True,
            )
            if m3_e1 and m3_e2:
                apply_route_warm_start(vars_dict, ctx, m3_e1, m3_e2)
        except Exception as exc:
            if verbose:
                print(f"[WARN] Model 3 warm start 构造失败，继续使用默认初始解: {exc}")
    if not verbose:
        m.setParam("OutputFlag", 0)

    m.setParam("MIPGap", mip_gap)
    m.setParam("TimeLimit", time_limit)
    # A no-relay M3 incumbent is already supplied.  Spend the exact budget on
    # bounds and proof, and leave the relay-mode branch priorities in control.
    m.setParam("MIPFocus", 3)
    m.setParam("Heuristics", 0.05)
    m.setParam("Symmetry", 2)
    m.setParam("Presolve", 2)
    m.setParam("PreSparsify", 1)
    m.setParam("Cuts", 2)
    m.setParam("CutPasses", 3)
    if objective_cutoff is not None:
        m.setParam("Cutoff", float(objective_cutoff))
    if gurobi_params:
        for name, value in gurobi_params.items():
            m.setParam(name, value)
    m.optimize()

    if m.status == GRB.OPTIMAL:
        e1_routes, e2_routes, transshipments = _print_joint_solution(
            m, vars_dict, ctx, verbose=verbose
        )
        if verbose:
            _print_joint_routes(e1_routes, e2_routes, transshipments)
        solution_type = (
            "proven_optimal"
            if (max_relocation_candidates is None
                and max_transshipments is None
                and not relay_fixes and not handoff_fixes)
            else "restricted_optimal"
        )
        info = _solve_info(
            m, solution_type, max_relocation_candidates,
            max_transshipments, transshipments, strengthen,
            relay_fixes,
            handoff_fixes,
        )
        return _with_info((e1_routes, e2_routes), info, return_info)

    if m.SolCount > 0 and return_incumbent_on_time_limit:
        if verbose:
            print(f"\n求解未证明最优 ({_status_name(m.status)})，返回可行解: "
                  f"{m.ObjVal:.2f}  (Gap: {m.MIPGap:.2%})")
        e1_routes, e2_routes, transshipments = _print_joint_solution(
            m, vars_dict, ctx, verbose=verbose
        )
        if verbose:
            _print_joint_routes(e1_routes, e2_routes, transshipments)
        info = _solve_info(
            m, "feasible_not_proven", max_relocation_candidates,
            max_transshipments, transshipments, strengthen,
            relay_fixes,
            handoff_fixes,
        )
        return _with_info((e1_routes, e2_routes), info, return_info)

    if m.status == GRB.INFEASIBLE and compute_iis_on_infeasible:
        if verbose:
            print("\n模型不可行，正在计算 IIS...")
        m.computeIIS()
        from pathlib import Path
        iis_dir = Path(__file__).resolve().parent.parent.parent / "output" / "debug_iis" / "model5_exact"
        iis_dir.mkdir(parents=True, exist_ok=True)
        iis_path = iis_dir / "debug_iis_model03_exact_infeasible.ilp"
        m.write(str(iis_path))
        if verbose:
            print(f"[OK] IIS 已写入 {iis_path}")

    if verbose:
        print("\n未找到可行解")
    return _with_info(
        (None, None), _solve_info(
            m, "none", max_relocation_candidates, max_transshipments,
            strengthen=strengthen, relay_fixes=relay_fixes,
            handoff_fixes=handoff_fixes,
        ),
        return_info,
    )


def _has_metric_distances(problem, e1_tasks, e2_tasks_by_sat, tol=1e-7):
    """Check the triangle inequality required by the shortcut certificate."""
    physical = {problem.depot.id}
    physical.update(s.id for s in problem.satellites)
    physical.update(t.cust for t in e1_tasks)
    physical.update(
        t.cust for tasks in e2_tasks_by_sat.values() for t in tasks
    )
    physical = sorted(physical)
    for i in physical:
        for j in physical:
            dij = problem.distance(i, j)
            for h in physical:
                if dij > problem.distance(i, h) + problem.distance(h, j) + tol:
                    return False
    return True


def _zero_pickup_load_relaxation(e2_tasks_by_sat):
    """Keep every original pickup visit/TW but remove its container load.

    Any one-handoff Model 5 route maps to this relaxation by replacing TP with
    the original pickup task, deleting TD/RP, and shortcutting the route.  The
    delivery load is unchanged and pickup load is removed, so capacity can only
    become easier.  Under metric travel distances the mapped objective cannot
    increase.
    """
    return {
        sat_id: [
            Task(
                task.id, task.cust, task.kind,
                task.w if task.kind == "d" else 0.0,
                task.v if task.kind == "d" else 0.0,
                task.tw,
            )
            for task in tasks
        ]
        for sat_id, tasks in e2_tasks_by_sat.items()
    }


def _pickup_mode_patterns(e2_tasks_by_sat):
    """Enumerate the complete direct/relay mode partition.

    A relay branch retains every same-satellite physical handoff candidate;
    the fixed branch MIP chooses the best candidate exactly.
    """
    pickup_rows = []
    for sat_id in sorted(e2_tasks_by_sat):
        tasks = e2_tasks_by_sat[sat_id]
        same_sat_customers = {task.cust for task in tasks}
        for task in sorted(
                (item for item in tasks if item.kind == "p"),
                key=lambda item: item.id):
            has_handoff = any(
                customer != task.cust for customer in same_sat_customers
            )
            pickup_rows.append((task.id, (0, 1) if has_handoff else (0,)))

    patterns = []
    choice_sets = [choices for _task_id, choices in pickup_rows]
    for selected in itertools.product(*choice_sets):
        pattern = tuple(
            (task_id, relay)
            for (task_id, _choices), relay in zip(pickup_rows, selected)
        )
        relay_count = sum(selected)
        patterns.append((relay_count, pattern))
    # Single-relay patterns tend to yield useful incumbents cheaply.
    patterns.sort(key=lambda item: (item[0], item[1]))
    return patterns


def _concrete_handoff_patterns(binary_pattern, e2_tasks_by_sat):
    """Refine one direct/relay mode into exact physical handoff choices."""
    task_rows = {}
    for sat_id in sorted(e2_tasks_by_sat):
        tasks = e2_tasks_by_sat[sat_id]
        same_sat_customers = sorted({task.cust for task in tasks})
        for task in tasks:
            if task.kind == "p":
                task_rows[task.id] = (
                    task.cust,
                    [customer for customer in same_sat_customers
                     if customer != task.cust],
                )
    relay_tasks = [task_id for task_id, relay in binary_pattern if relay]
    choices = [task_rows[task_id][1] for task_id in relay_tasks]
    for selected in itertools.product(*choices):
        yield dict(zip(relay_tasks, selected))


def _solve_mode_partition(
        problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite,
        *, direct_routes, direct_info, lower_bound, remaining_time,
        verbose, strengthen, return_info, gurobi_params,
        branch_time_limit):
    """Prove canonical Model 5 by closing every fixed pickup-mode MIP."""
    partition_started = time.perf_counter()
    patterns = _pickup_mode_patterns(e2_tasks_by_sat)
    total_patterns = len(patterns)
    best_e1, best_e2 = direct_routes
    best_info = direct_info if best_e1 and best_e2 else None
    best_objective = (
        best_info.get("objective") if best_info is not None else None
    )
    closed = 0
    solved = 0
    unresolved = []
    branch_status_counts = {}

    direct_pattern = patterns[0][1] if patterns else tuple()
    direct_is_proven = bool(
        best_info and best_info.get("proven_optimal")
    )
    if direct_is_proven:
        closed += 1
        branch_status_counts["DIRECT_OPTIMAL"] = 1

    candidates = [
        item for item in patterns
        if not (direct_is_proven and item[1] == direct_pattern)
    ]
    proof_tol = 1e-7 * max(1.0, abs(best_objective or 0.0))

    if verbose:
        print(
            "[INFO] 启动完备模式分解："
            f"{total_patterns} 个直接/单次交接组合"
        )

    refined_solved = 0

    def solve_fixed(relay_fixes, handoff_fixes, limit, status_prefix):
        nonlocal solved, best_e1, best_e2, best_info
        nonlocal best_objective, proof_tol
        cutoff = (
            None if best_objective is None
            else float(best_objective) - proof_tol
        )
        branch_e1, branch_e2, branch_info = _solve_joint_exact(
            problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite,
            verbose=False,
            max_relocation_candidates=None,
            max_transshipments=None,
            relay_fixes=relay_fixes,
            handoff_fixes=handoff_fixes,
            objective_cutoff=cutoff,
            strengthen=strengthen,
            warm_start_with_m3=False,
            time_limit=limit,
            mip_gap=0.0,
            return_incumbent_on_time_limit=True,
            return_info=True,
            warm_start_routes=None,
            gurobi_params=gurobi_params,
            compute_iis_on_infeasible=False,
        )
        solved += 1
        status = branch_info["status"]
        status_key = f"{status_prefix}_{status}"
        branch_status_counts[status_key] = (
            branch_status_counts.get(status_key, 0) + 1
        )
        branch_objective = branch_info.get("objective")
        if (branch_objective is not None
                and (best_objective is None
                     or branch_objective < best_objective - proof_tol)):
            best_e1, best_e2 = branch_e1, branch_e2
            best_info = branch_info
            best_objective = float(branch_objective)
            proof_tol = 1e-7 * max(1.0, abs(best_objective))
            if verbose:
                print(
                    f"[INCUMBENT] 第 {solved} 个固定 MIP 找到更优解 "
                    f"{best_objective:.6f}，"
                    f"中转 {branch_info.get('transshipment_count', 0)} 个"
                )
        return status in {"OPTIMAL", "INFEASIBLE", "CUTOFF"}

    for index, (_relay_count, pattern) in enumerate(candidates, start=1):
        remaining = remaining_time()
        if remaining is not None and remaining <= 0.02:
            unresolved.extend(item[1] for item in candidates[index - 1:])
            break

        relay_fixes = dict(pattern)
        per_branch = remaining
        if branch_time_limit is not None:
            # A broad relay branch can contain many physical handoff chains.
            # Give it a short chance, then refine only this hard branch.
            broad_limit = min(5.0, float(branch_time_limit))
            per_branch = (
                broad_limit if remaining is None
                else min(broad_limit, remaining)
            )
        # With one relay the broad MIP is compact and can optimize all handoff
        # customers at once.  With multiple relays its candidate-chain product
        # makes the relaxation much larger; go straight to the equivalent
        # complete concrete-handoff disjunction instead of spending a doomed
        # broad-branch time slice first.
        broad_closed = False
        if _relay_count <= 1:
            broad_closed = solve_fixed(
                relay_fixes, None, per_branch, "BROAD"
            )
        if broad_closed:
            closed += 1
        else:
            # This is an exact disjunction of the broad branch: direct tasks
            # remain fixed, and every relayed container receives exactly one
            # concrete same-satellite handoff customer.
            refinements_closed = True
            refinements = list(
                _concrete_handoff_patterns(pattern, e2_tasks_by_sat)
            )
            for handoff_fixes in refinements:
                remaining = remaining_time()
                if remaining is not None and remaining <= 0.02:
                    refinements_closed = False
                    break
                refined_limit = remaining
                if branch_time_limit is not None:
                    refined_cap = min(10.0, float(branch_time_limit))
                    refined_limit = (
                        refined_cap if remaining is None
                        else min(refined_cap, remaining)
                    )
                concrete_closed = solve_fixed(
                    relay_fixes, handoff_fixes, refined_limit, "REFINED"
                )
                refined_solved += 1
                if not concrete_closed:
                    remaining = remaining_time()
                    if remaining is None or remaining > 0.02:
                        retry_limit = (
                            30.0 if remaining is None
                            else min(30.0, remaining)
                        )
                        concrete_closed = solve_fixed(
                            relay_fixes, handoff_fixes,
                            retry_limit, "REFINED_RETRY",
                        )
                        refined_solved += 1
                if not concrete_closed:
                    refinements_closed = False
            if refinements_closed:
                closed += 1
            else:
                unresolved.append(pattern)

        if verbose and (solved % 25 == 0 or closed == total_patterns):
            print(
                f"[PROOF] 已关闭 {closed}/{total_patterns} 个模式，"
                f"未关闭 {len(unresolved)} 个"
            )

    proven = closed == total_patterns and best_objective is not None
    runtime = time.perf_counter() - partition_started
    if proven:
        transshipments = list(best_info.get("transshipments", []))
        info = {
            "status_code": int(GRB.OPTIMAL),
            "status": "OPTIMAL",
            "solution_type": "proven_optimal",
            "proven_optimal": True,
            "relocation_neighborhood": "full",
            "runtime": runtime,
            "relay_policy": "single_handoff_indivisible_same_satellite",
            "formulation": "exhaustive_pickup_mode_partition",
            "max_transshipments": None,
            "relay_fixes": {},
            "handoff_fixes": {},
            "proof_scope": "full_single_handoff_model5",
            "transshipments": transshipments,
            "transshipment_count": len(transshipments),
            "sol_count": 1,
            "objective": float(best_objective),
            "best_bound": float(best_objective),
            "mip_gap": 0.0,
            "certificate": {
                "type": "complete_direct_or_single_handoff_partition",
                "total_patterns": total_patterns,
                "closed_patterns": closed,
                "solved_fixed_patterns": solved,
                "solved_refined_patterns": refined_solved,
                "branch_status_counts": branch_status_counts,
                "relaxation_lower_bound": lower_bound,
                "objective_tolerance": proof_tol,
            },
        }
        if verbose:
            print(
                "[CERTIFIED] 所有固定模式均已关闭，规范 Model 5 全局最优："
                f"{best_objective:.6f}"
            )
            _print_joint_routes(best_e1, best_e2, transshipments)
        return _with_info((best_e1, best_e2), info, return_info)

    info = dict(best_info or {})
    info.update({
        "status_code": int(GRB.TIME_LIMIT),
        "status": "TIME_LIMIT",
        "solution_type": "feasible_not_proven" if best_info else "none",
        "proven_optimal": False,
        "runtime": runtime,
        "formulation": "incomplete_pickup_mode_partition",
        "proof_scope": "full_single_handoff_model5",
        "objective": best_objective,
        "best_bound": lower_bound,
        "certificate": {
            "type": "incomplete_direct_or_single_handoff_partition",
            "total_patterns": total_patterns,
            "closed_patterns": closed,
            "solved_fixed_patterns": solved,
            "solved_refined_patterns": refined_solved,
            "unresolved_patterns": len(unresolved),
            "unresolved_modes": [dict(pattern) for pattern in unresolved],
            "branch_status_counts": branch_status_counts,
            "relaxation_lower_bound": lower_bound,
        },
    })
    if verbose:
        print(
            f"[WARN] 模式分解未闭合：{closed}/{total_patterns}，"
            "当前解不能称为精确最优"
        )
    return _with_info((best_e1, best_e2), info, return_info)


def solve_exact(problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite,
                verbose=True, max_relocation_candidates=None,
                max_transshipments=None,
                relay_fixes=None, objective_cutoff=None,
                handoff_fixes=None,
                strengthen=True,
                warm_start_with_m3=True, time_limit=300, mip_gap=0.0,
                return_incumbent_on_time_limit=False, return_info=False,
                warm_start_routes=None, gurobi_params=None,
                use_relaxation_certificate=True,
                use_mode_partition=True,
                mode_partition_max_patterns=512,
                mode_partition_branch_time_limit=30.0):
    """Solve canonical Model 5 exactly, using a compact proof when possible.

    The certificate first obtains a feasible no-relay incumbent, then solves a
    compact Model 3 relaxation in which all original pickup visits and time
    windows remain but pickup container loads are set to zero.  Equality of its
    proven lower bound and the incumbent certifies the full Model 5, including
    every same-satellite handoff candidate and any number of relayed containers.
    If equality is not obtained and the complete direct/single-handoff mode
    partition is small enough, every fixed mode is solved and closed exactly.
    Otherwise the remaining time is passed to the monolithic joint MIP.
    """
    canonical = (
        max_relocation_candidates is None
        and max_transshipments is None
        and not relay_fixes and not handoff_fixes
        and objective_cutoff is None
        and mip_gap == 0.0
    )
    if not (use_relaxation_certificate and canonical
            and _has_metric_distances(
                problem, e1_tasks, e2_tasks_by_sat
            )):
        return _solve_joint_exact(
            problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite,
            verbose=verbose,
            max_relocation_candidates=max_relocation_candidates,
            max_transshipments=max_transshipments,
            relay_fixes=relay_fixes,
            objective_cutoff=objective_cutoff,
            handoff_fixes=handoff_fixes,
            strengthen=strengthen,
            warm_start_with_m3=warm_start_with_m3,
            time_limit=time_limit, mip_gap=mip_gap,
            return_incumbent_on_time_limit=return_incumbent_on_time_limit,
            return_info=return_info,
            warm_start_routes=warm_start_routes,
            gurobi_params=gurobi_params,
        )

    started = time.perf_counter()

    def remaining_time():
        if time_limit is None:
            return None
        return max(0.01, float(time_limit) - (time.perf_counter() - started))

    if verbose:
        print("\n[INFO] Model 5 精确证书：先求无接力可行上界")
    direct_e1, direct_e2, direct_info = solve_m3_exact(
        problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite,
        TFp=9999.0, verbose=False, time_limit=remaining_time(),
        mip_gap=0.0, return_incumbent_on_time_limit=True,
        return_info=True, warm_start_routes=warm_start_routes,
        gurobi_params=gurobi_params,
    )

    relaxation_info = None
    if direct_e1 and direct_e2:
        if verbose:
            print(
                "[INFO] 求解零回收载荷松弛：保留原 pickup 访问、时间窗和同步"
            )
        relaxed_tasks = _zero_pickup_load_relaxation(e2_tasks_by_sat)
        _lb_e1, _lb_e2, relaxation_info = solve_m3_exact(
            problem, e1_tasks, relaxed_tasks, vehicles_per_satellite,
            TFp=9999.0, verbose=False, time_limit=remaining_time(),
            mip_gap=0.0, return_incumbent_on_time_limit=True,
            return_info=True,
            warm_start_routes=(direct_e1, direct_e2),
            gurobi_params=gurobi_params,
        )

    incumbent = direct_info.get("objective") if direct_info else None
    lower_bound = (
        relaxation_info.get("objective")
        if relaxation_info and relaxation_info.get("proven_optimal")
        else None
    )
    proof_tol = 1e-6 * max(1.0, abs(incumbent or 0.0))
    if (incumbent is not None and lower_bound is not None
            and lower_bound >= incumbent - proof_tol):
        runtime = time.perf_counter() - started
        info = {
            "status_code": int(GRB.OPTIMAL),
            "status": "OPTIMAL",
            "solution_type": "proven_optimal",
            "proven_optimal": True,
            "relocation_neighborhood": "full",
            "runtime": runtime,
            "relay_policy": "single_handoff_indivisible_same_satellite",
            "formulation": "zero_pickup_load_relaxation_certificate",
            "max_transshipments": None,
            "relay_fixes": {},
            "handoff_fixes": {},
            "proof_scope": "full_single_handoff_model5",
            "transshipments": [],
            "transshipment_count": 0,
            "sol_count": 1,
            "objective": float(incumbent),
            "best_bound": float(lower_bound),
            "mip_gap": 0.0,
            "certificate": {
                "type": "metric_route_shortcut_zero_pickup_load",
                "direct_status": direct_info["status"],
                "relaxation_status": relaxation_info["status"],
                "lower_bound": float(lower_bound),
            },
        }
        if verbose:
            print(
                "[CERTIFIED] 松弛下界与无接力可行解相等："
                f"{incumbent:.6f}，规范 Model 5 已证明最优"
            )
            _print_joint_routes(direct_e1, direct_e2, [])
        return _with_info((direct_e1, direct_e2), info, return_info)

    if verbose:
        lb_text = "无" if lower_bound is None else f"{lower_bound:.6f}"
        ub_text = "无" if incumbent is None else f"{incumbent:.6f}"
        print(
            f"[INFO] 证书未闭合（LB={lb_text}, UB={ub_text}），"
            "检查完备模式分解"
        )

    mode_patterns = _pickup_mode_patterns(e2_tasks_by_sat)
    if (use_mode_partition
            and len(mode_patterns) <= int(mode_partition_max_patterns)):
        return _solve_mode_partition(
            problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite,
            direct_routes=(direct_e1, direct_e2),
            direct_info=direct_info,
            lower_bound=lower_bound,
            remaining_time=remaining_time,
            verbose=verbose,
            strengthen=strengthen,
            return_info=return_info,
            gurobi_params=gurobi_params,
            branch_time_limit=mode_partition_branch_time_limit,
        )

    if verbose:
        print(
            f"[INFO] 模式数 {len(mode_patterns)} 超过阈值 "
            f"{mode_partition_max_patterns}，转入联合 MIP"
        )
    return _solve_joint_exact(
        problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite,
        verbose=verbose,
        max_relocation_candidates=max_relocation_candidates,
        max_transshipments=max_transshipments,
        relay_fixes=relay_fixes,
        objective_cutoff=objective_cutoff,
        handoff_fixes=handoff_fixes,
        strengthen=strengthen,
        warm_start_with_m3=False,
        time_limit=remaining_time(), mip_gap=mip_gap,
        return_incumbent_on_time_limit=return_incumbent_on_time_limit,
        return_info=return_info,
        warm_start_routes=(direct_e1, direct_e2)
        if direct_e1 and direct_e2 else warm_start_routes,
        gurobi_params=gurobi_params,
    )


def solve_restricted_mip(problem, e1_tasks, e2_tasks_by_sat,
                         vehicles_per_satellite, **kwargs):
    """Run a deliberately restricted Model 5 MIP and permit incumbents."""
    kwargs.setdefault("max_relocation_candidates", 1)
    kwargs["return_incumbent_on_time_limit"] = True
    return solve_exact(
        problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite, **kwargs
    )


def _print_joint_solution(m, vars_dict, ctx, *, verbose=True):
    if verbose:
        print("\n" + "=" * 60)
        print(f"Model 5 联合 MIP 求解完成!  目标值: {m.ObjVal:.2f}")
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
    for relay in transshipments:
        print(
            f"  {relay['pickup_task']}: {relay['origin']} "
            f"--车辆{relay['carrier_vehicle']}--> {relay['handoff']} "
            f"--交接--> 车辆{relay['receiver_vehicle']} --回仓"
        )
