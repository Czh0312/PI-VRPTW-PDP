# -*- coding: utf-8 -*-
"""Prototype Ryan-Foster branch-price for one E2 satellite."""

import argparse
import heapq
import json
import math
import sys
import time
from itertools import combinations
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from models.routing.common.domain.instance import ProblemInstance
from models.routing.variants.model_3.e2_route_cg import (
    _build_master,
    _columns_from_paths,
    _singleton_column,
    _solve_pricing_labeling,
    _solve_pricing_labeling_exact,
    _solve_pricing_mip,
)
from models.routing.common.data_loader import parse_dataset
from models.routing.common.task_builder import build_tasks_for_model


CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"
EPS = 1e-6


def load_fleet_config():
    return json.loads((CONFIG_DIR / "routing" / "validation" / "zhou_small.json").read_text(encoding="utf-8"))


def build_problem(data_path, fleet_cfg):
    nodes_dict, _ = parse_dataset(str(data_path))
    problem = ProblemInstance(nodes_dict, fleet_cfg, speed=fleet_cfg.get("speed", 1.0))
    problem.aggregate_demands_from_pre_assignment()
    return problem


def read_routes(path):
    if not path:
        return {}
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return payload.get("routes", payload).get("e2", {})


def read_columns(path, sat_id):
    if not path:
        return []
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return [
        col for col in payload.get("columns", [])
        if col.get("sat_id") == sat_id
    ]


def read_cuts(path):
    if not path:
        return []
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if "cuts" in payload:
        return payload["cuts"]
    node_results = payload.get("node_results", [])
    if node_results:
        return node_results[-1].get("cuts", [])
    return []


def solve_node(problem, sat_id, tasks, vehicles_per_satellite, base_columns,
               same_pairs, diff_pairs, args, node_deadline=None,
               min_routes=None, initial_cuts=None):
    task_ids = [t.id for t in tasks]
    columns = [
        c for c in base_columns
        if _column_respects(c, same_pairs, diff_pairs)
    ]
    seen = {tuple(c.path) for c in columns}
    for task in tasks:
        col = _singleton_column(problem, sat_id, task)
        if col is not None and _column_respects(col, same_pairs, diff_pairs):
            if tuple(col.path) not in seen:
                columns.append(col)
                seen.add(tuple(col.path))

    pricing_statuses = []
    iteration_history = []
    active_cuts = list(initial_cuts or [])
    cut_history = []
    last_lp = None
    last_art_sum = None
    last_pricing = None
    for iteration in range(args.cg_iterations):
        if node_deadline is not None and time.time() >= node_deadline:
            return {
                "status": "NODE_TIME_LIMIT",
                "lp": last_lp,
                "artificial": last_art_sum,
                "columns": columns,
                "pricing": last_pricing,
                "pricing_statuses": pricing_statuses,
                "iteration_history": iteration_history,
                "cuts": active_cuts,
                "cut_history": cut_history,
            }
        master, lambdas, artificials, cover, vehicle, cut_constrs = _build_master(
            sat_id, task_ids, columns, vehicles_per_satellite,
            relax=True, output_flag=False,
            gurobi_params=_master_gurobi_params(args),
            min_routes=min_routes,
            cuts=active_cuts,
        )
        master.optimize()
        if master.status != 2:
            return {"status": f"MASTER_{master.status}", "columns": columns}
        art_sum = sum(v.X for v in artificials.values())
        last_lp = float(master.ObjVal)
        last_art_sum = art_sum
        cover_duals = {tid: cover[tid].Pi for tid in task_ids}
        vehicle_dual = vehicle.Pi
        cut_duals = [
            {**cut, "dual": cut_constrs[idx].Pi}
            for idx, cut in enumerate(active_cuts)
            if idx in cut_constrs
        ]
        if args.label_before_mip:
            label_time_limit = args.label_time_limit
            if node_deadline is not None:
                remaining = node_deadline - time.time()
                if remaining <= 1.0:
                    return {
                        "status": "NODE_TIME_LIMIT",
                        "lp": float(master.ObjVal),
                        "artificial": art_sum,
                        "columns": columns,
                        "master": master,
                        "lambdas": lambdas,
                        "pricing_statuses": pricing_statuses,
                        "iteration_history": iteration_history,
                        "cuts": active_cuts,
                        "cut_history": cut_history,
                    }
                label_time_limit = min(label_time_limit, max(1.0, remaining * 0.25))
            label_cols, label_pricing = _solve_pricing_labeling(
                problem, sat_id, tasks, cover_duals, vehicle_dual,
                pool_solutions=args.label_pool_solutions,
                beam_width=args.label_beam_width,
                max_expansions=args.label_max_expansions,
                time_limit=label_time_limit,
            )
            pricing_statuses.append(label_pricing)
            last_pricing = label_pricing
            added = 0
            for col in label_cols:
                if _column_respects(col, same_pairs, diff_pairs) and tuple(col.path) not in seen:
                    columns.append(col)
                    seen.add(tuple(col.path))
                    added += 1
            if added:
                continue
        pricing_time_limit = args.pricing_time_limit
        if node_deadline is not None:
            remaining = node_deadline - time.time()
            if remaining <= 1.0:
                return {
                    "status": "NODE_TIME_LIMIT",
                    "lp": float(master.ObjVal),
                    "artificial": art_sum,
                    "columns": columns,
                    "master": master,
                        "lambdas": lambdas,
                        "pricing_statuses": pricing_statuses,
                        "iteration_history": iteration_history,
                        "cuts": active_cuts,
                        "cut_history": cut_history,
                    }
            pricing_time_limit = min(pricing_time_limit, remaining)
        if args.pricing_method == "exact_label":
            new_cols, pricing = _solve_pricing_labeling_exact(
                problem, sat_id, tasks, cover_duals, vehicle_dual,
                pool_solutions=args.pricing_pool_solutions,
                max_expansions=args.exact_label_max_expansions,
                time_limit=pricing_time_limit,
                stop_after_columns=not args.exact_label_exhaust_each_iteration,
                dominance_mode=args.exact_label_dominance,
                cut_duals=cut_duals,
            )
        else:
            new_cols, pricing = _solve_pricing_mip(
                problem, sat_id, tasks, cover_duals, vehicle_dual,
                pricing_time_limit=pricing_time_limit,
                pool_solutions=args.pricing_pool_solutions,
                same_pairs=same_pairs,
                diff_pairs=diff_pairs,
                gurobi_params=_pricing_gurobi_params(args),
                diverse_task_sets=args.pricing_diverse_task_sets,
                proof_as_feasibility=args.pricing_feasibility_proof,
                forbidden_paths=seen if args.pricing_forbid_existing_paths else None,
                cut_duals=cut_duals,
                output_flag=False,
            )
        pricing_statuses.append(pricing)
        last_pricing = pricing
        added = 0
        if pricing["reduced_cost_bound"] >= -args.pricing_tol or not new_cols:
            iteration_history.append({
                "iteration": iteration + 1,
                "lp": float(master.ObjVal),
                "artificial": art_sum,
                "columns_before": len(columns),
                "columns_added": 0,
                "pricing": pricing,
            })
            if not _pricing_is_proved(pricing, args.pricing_tol):
                return {
                    "status": "PRICING_UNPROVED",
                    "lp": float(master.ObjVal),
                    "artificial": art_sum,
                    "columns": columns,
                    "master": master,
                    "lambdas": lambdas,
                    "pricing": pricing,
                    "pricing_statuses": pricing_statuses,
                    "iteration_history": iteration_history,
                    "cuts": active_cuts,
                    "cut_history": cut_history,
                }
            if art_sum > 1e-7:
                return {
                    "status": "INFEASIBLE_PROVED",
                    "lp": float(master.ObjVal),
                    "artificial": art_sum,
                    "columns": columns,
                    "master": master,
                    "lambdas": lambdas,
                    "pricing": pricing,
                    "pricing_statuses": pricing_statuses,
                    "iteration_history": iteration_history,
                    "cuts": active_cuts,
                    "cut_history": cut_history,
                }
            new_cuts = _separate_sri_cuts(
                task_ids, columns, lambdas, active_cuts, args)
            if new_cuts:
                active_cuts.extend(new_cuts)
                cut_history.append({
                    "iteration": iteration + 1,
                    "added": len(new_cuts),
                    "total": len(active_cuts),
                    "cuts": new_cuts,
                })
                iteration_history.append({
                    "iteration": iteration + 1,
                    "lp": float(master.ObjVal),
                    "artificial": art_sum,
                    "columns_before": len(columns),
                    "columns_added": 0,
                    "cuts_added": len(new_cuts),
                    "cuts_total": len(active_cuts),
                    "pricing": pricing,
                })
                continue
            return {
                "status": "LP_PROVED",
                "lp": float(master.ObjVal),
                "artificial": art_sum,
                "columns": columns,
                "master": master,
                "lambdas": lambdas,
                "pricing": pricing,
                "pricing_statuses": pricing_statuses,
                "iteration_history": iteration_history,
                "cuts": active_cuts,
                "cut_history": cut_history,
            }
        for col in new_cols:
            if _column_respects(col, same_pairs, diff_pairs) and tuple(col.path) not in seen:
                columns.append(col)
                seen.add(tuple(col.path))
                added += 1
        iteration_history.append({
            "iteration": iteration + 1,
            "lp": float(master.ObjVal),
            "artificial": art_sum,
            "columns_before": len(columns) - added,
            "columns_added": added,
            "pricing": pricing,
        })
        if added == 0:
            return {
                "status": (
                    "DUPLICATE_COLUMNS" if _pricing_is_proved(pricing, args.pricing_tol)
                    else "PRICING_UNPROVED"
                ),
                "lp": float(master.ObjVal),
                "artificial": art_sum,
                "columns": columns,
                "master": master,
                "lambdas": lambdas,
                "pricing": pricing,
                "pricing_statuses": pricing_statuses,
                "iteration_history": iteration_history,
                "cuts": active_cuts,
                "cut_history": cut_history,
            }
    return {
        "status": "CG_LIMIT",
        "lp": last_lp,
        "artificial": last_art_sum,
        "columns": columns,
        "master": master,
        "lambdas": lambdas,
        "pricing": last_pricing,
        "pricing_statuses": pricing_statuses,
        "iteration_history": iteration_history,
        "cuts": active_cuts,
        "cut_history": cut_history,
    }


def merge_columns(*column_sets):
    unique = {}
    for columns in column_sets:
        for col in columns or []:
            unique[col.path] = col
    return list(unique.values())


def solve_integer_pool(sat_id, tasks, columns, vehicles_per_satellite,
                       min_routes=None, cuts=None):
    task_ids = [t.id for t in tasks]
    master, lambdas, artificials, _, _, _ = _build_master(
        sat_id, task_ids, columns, vehicles_per_satellite,
        relax=False, output_flag=False, min_routes=min_routes, cuts=cuts,
    )
    master.optimize()
    if master.SolCount:
        art_sum = sum(v.X for v in artificials.values())
        if art_sum <= 1e-7:
            return float(master.ObjVal)
    return None


def choose_ryan_foster_pair(
    sat_id,
    tasks,
    columns,
    lambdas,
    vehicles_per_satellite,
    args,
    min_routes=None,
    cuts=None,
):
    task_ids = [t.id for t in tasks]
    candidates = []
    for i, a in enumerate(task_ids):
        for b in task_ids[i + 1:]:
            together = 0.0
            for idx, col in enumerate(columns):
                if a in col.tasks and b in col.tasks:
                    together += lambdas[idx].X
            if EPS < together < 1.0 - EPS:
                score = abs(together - 0.5)
                candidates.append((score, a, b, together))
    if not candidates:
        return None
    candidates.sort()
    if args.branch_strategy != "strong":
        _, a, b, together = candidates[0]
        return (a, b, together, None)

    best = None
    best_score = (-math.inf, -math.inf, -math.inf)
    for frac_score, a, b, together in candidates[:args.branch_strong_candidates]:
        child_lps = []
        for child_same, child_diff in ((((a, b),), tuple()), (tuple(), ((a, b),))):
            child_columns = [
                col for col in columns
                if _column_respects(col, child_same, child_diff)
            ]
            if not child_columns:
                child_lps.append(math.inf)
                continue
            master, _, artificials, _, _, _ = _build_master(
                sat_id,
                task_ids,
                child_columns,
                vehicles_per_satellite,
                relax=True,
                output_flag=False,
                gurobi_params=_master_gurobi_params(args),
                min_routes=min_routes,
                cuts=cuts,
            )
            master.optimize()
            if master.status == 2:
                art_sum = sum(v.X for v in artificials.values())
                child_lps.append(float(master.ObjVal) if art_sum <= 1e-7 else math.inf)
            else:
                child_lps.append(math.inf)
        min_child = min(child_lps)
        max_child = max(child_lps)
        score = (min_child, max_child, -frac_score)
        if score > best_score:
            best = (a, b, together, {"same_lp": child_lps[0], "diff_lp": child_lps[1]})
            best_score = score
    if best is not None:
        return best
    _, a, b, together = candidates[0]
    return (a, b, together, None)


def _column_respects(col, same_pairs, diff_pairs):
    tasks = set(col.tasks if hasattr(col, "tasks") else col.get("tasks", []))
    for a, b in same_pairs:
        if (a in tasks) != (b in tasks):
            return False
    for a, b in diff_pairs:
        if a in tasks and b in tasks:
            return False
    return True


def _as_column_dicts(problem, sat_id, tasks, route_paths, column_dicts):
    from models.routing.variants.model_3.e2_route_cg import E2RouteColumn

    task_map = {t.id: t for t in tasks}
    cols = []
    for col in column_dicts:
        cols.append(E2RouteColumn(
            sat_id=sat_id,
            path=tuple(col["path"]),
            tasks=tuple(col["tasks"]),
            cost=float(col["cost"]),
            source=col.get("source", "json"),
        ))
    for col in _columns_from_paths(problem, sat_id, tasks, route_paths):
        cols.append(col)
    unique = {}
    for col in cols:
        unique[col.path] = col
    return list(unique.values())


def _pairs_from_json(pairs):
    return tuple(tuple(pair) for pair in pairs or [])


def _node_from_json(item):
    return (
        float(item.get("lower_bound", 0.0)),
        int(item.get("counter", 0)),
        _pairs_from_json(item.get("same")),
        _pairs_from_json(item.get("diff")),
    )


def _node_to_json(node):
    lower_bound, counter, same_pairs, diff_pairs = node
    return {
        "lower_bound": lower_bound,
        "counter": counter,
        "same": [list(pair) for pair in same_pairs],
        "diff": [list(pair) for pair in diff_pairs],
    }


def _load_resume_state(path):
    if not path:
        return None
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    nodes = [_node_from_json(item) for item in payload.get("remaining_nodes", [])]
    unresolved = [
        _node_from_json(item)
        for item in payload.get("unresolved_nodes", [])
    ]
    return {
        "incumbent": payload.get("incumbent"),
        "nodes": nodes + unresolved,
        "counter": max([node[1] for node in nodes + unresolved] + [0]),
        "node_results": payload.get("node_results", []),
    }


def _pricing_gurobi_params(args):
    params = {}
    if args.pricing_mip_focus is not None:
        params["MIPFocus"] = args.pricing_mip_focus
    if args.pricing_cuts is not None:
        params["Cuts"] = args.pricing_cuts
    if args.pricing_heuristics is not None:
        params["Heuristics"] = args.pricing_heuristics
    if args.pricing_presolve is not None:
        params["Presolve"] = args.pricing_presolve
    return params


def _master_gurobi_params(args):
    params = {}
    if args.master_method is not None:
        params["Method"] = args.master_method
    if args.master_perturb_value is not None:
        params["PerturbValue"] = args.master_perturb_value
    if args.master_numeric_focus is not None:
        params["NumericFocus"] = args.master_numeric_focus
    return params


def _pricing_is_proved(pricing, tol=EPS):
    status = pricing.get("status")
    if status in {"OPTIMAL", "EXACT_LABEL_OPTIMAL"}:
        return pricing.get("reduced_cost_bound", float("-inf")) >= -tol
    if status == "INFEASIBLE" and pricing.get("proof_as_feasibility"):
        return pricing.get("reduced_cost_bound", float("-inf")) >= -tol
    return False


def _separate_sri_cuts(task_ids, columns, lambdas, active_cuts, args):
    if not args.sri_cuts:
        return []
    if args.sri_max_total_cuts is not None and len(active_cuts) >= args.sri_max_total_cuts:
        return []
    size = args.sri_cut_size
    if size < 3 or size > len(task_ids):
        return []
    existing = {tuple(sorted(cut["tasks"])) for cut in active_cuts}
    active_task_sets = [set(cut["tasks"]) for cut in active_cuts]
    lambda_values = [lambdas[idx].X for idx in range(len(columns))]
    column_task_sets = [set(col.tasks) for col in columns]
    candidates = []
    for subset in combinations(task_ids, size):
        key = tuple(sorted(subset))
        if key in existing:
            continue
        subset_set = set(subset)
        rhs = size // 2
        lhs = 0.0
        for value, col_tasks in zip(lambda_values, column_task_sets):
            if value <= EPS:
                continue
            coeff = len(col_tasks & subset_set) // 2
            if coeff:
                lhs += coeff * value
        violation = lhs - rhs
        if violation > args.sri_cut_tol:
            candidates.append((violation, {
                "type": "sri",
                "tasks": list(subset),
                "rhs": rhs,
                "violation": violation,
                "lhs": lhs,
            }))
    candidates.sort(key=lambda item: item[0], reverse=True)
    selected = []
    selected_task_sets = []
    remaining_slots = args.sri_max_cuts_per_round
    if args.sri_max_total_cuts is not None:
        remaining_slots = min(remaining_slots, args.sri_max_total_cuts - len(active_cuts))
    max_shared = args.sri_max_shared_tasks
    for _, cut in candidates:
        if len(selected) >= remaining_slots:
            break
        cut_tasks = set(cut["tasks"])
        if max_shared >= 0:
            too_close = any(
                len(cut_tasks & other) > max_shared
                for other in active_task_sets + selected_task_sets
            )
            if too_close:
                continue
        selected.append(cut)
        selected_task_sets.append(cut_tasks)
    return selected


def _capacity_min_routes(problem, tasks):
    delivery_tasks = [t for t in tasks if t.kind == "d"]
    pickup_tasks = [t for t in tasks if t.kind == "p"]
    lower_bounds = [
        sum(t.w for t in delivery_tasks) / problem.e2_weight_cap,
        sum(t.w for t in pickup_tasks) / problem.e2_weight_cap,
        sum(t.v for t in delivery_tasks) / problem.e2_volume_cap,
        sum(t.v for t in pickup_tasks) / problem.e2_volume_cap,
    ]
    return max(math.ceil(value - EPS) for value in lower_bounds)


def _write_outputs(args, payload, columns):
    args.output_dir.mkdir(parents=True, exist_ok=True)
    columns_out = args.output_dir / "satellite_branch_price_columns.json"
    columns_out.write_text(
        json.dumps({
            "scale": args.scale,
            "instance": args.instance,
            "columns": [
                {
                    "sat_id": col.sat_id,
                    "path": list(col.path),
                    "tasks": list(col.tasks),
                    "cost": col.cost,
                    "source": col.source,
                }
                for col in columns
            ],
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    payload["columns_json"] = str(columns_out)
    out = args.output_dir / "satellite_branch_price_probe.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    checkpoint = args.output_dir / "satellite_branch_price_checkpoint.json"
    checkpoint.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def run(args):
    fleet_cfg = load_fleet_config()
    data_path = DATA_DIR / args.scale / f"{args.instance}.csv"
    problem = build_problem(data_path, fleet_cfg)
    _, e2_tasks = build_tasks_for_model(problem, 3)
    tasks = e2_tasks[args.satellite]
    min_routes = _capacity_min_routes(problem, tasks) if args.capacity_route_minimum else None
    seed_routes = read_routes(args.seed_routes_json).get(args.satellite, [])
    seed_cols = read_columns(args.columns_json, args.satellite)
    initial_cuts = read_cuts(args.initial_cuts_json)
    base_columns = _as_column_dicts(problem, args.satellite, tasks, seed_routes, seed_cols)

    resume = _load_resume_state(args.resume_json)
    incumbent = args.incumbent
    if resume and resume.get("incumbent") is not None:
        incumbent = min(incumbent, float(resume["incumbent"]))
    best_explored_lp = -math.inf
    nodes = []
    counter = 0
    if resume and resume["nodes"]:
        nodes.extend(resume["nodes"])
        heapq.heapify(nodes)
        counter = resume["counter"]
    else:
        heapq.heappush(nodes, (0.0, counter, tuple(), tuple()))
    started = time.time()
    explored = 0
    unresolved_nodes = []
    results = list(resume.get("node_results", [])) if resume else []
    last_columns = base_columns

    def current_payload():
        remaining_nodes = sorted(nodes)
        all_open = remaining_nodes + unresolved_nodes
        complete = not all_open
        if complete:
            global_lower_bound = incumbent
        elif all_open:
            global_lower_bound = min(node[0] for node in all_open)
        else:
            global_lower_bound = -math.inf
        return {
            "scale": args.scale,
            "instance": args.instance,
            "satellite": args.satellite,
            "incumbent": incumbent,
            "best_bound": global_lower_bound,
            "global_lower_bound": global_lower_bound,
            "best_explored_lp": best_explored_lp,
            "gap": (
                None if incumbent == 0 or global_lower_bound == -math.inf
                else (incumbent - global_lower_bound) / incumbent
            ),
            "complete": complete,
            "explored": explored,
            "remaining_queue": len(remaining_nodes),
            "open_unproved_nodes": len(unresolved_nodes),
            "remaining_nodes": [_node_to_json(node) for node in remaining_nodes],
            "unresolved_nodes": [_node_to_json(node) for node in unresolved_nodes],
            "elapsed": time.time() - started,
            "node_results": results,
            "capacity_min_routes": min_routes,
            "sri_cuts_enabled": bool(args.sri_cuts),
            "initial_cut_count": len(initial_cuts),
        }

    while nodes and explored < args.node_limit and time.time() - started < args.time_limit:
        inherited_lb, _, same_pairs_t, diff_pairs_t = heapq.heappop(nodes)
        same_pairs = list(same_pairs_t)
        diff_pairs = list(diff_pairs_t)
        explored += 1
        node_deadline = None
        if args.node_time_limit:
            node_deadline = time.time() + args.node_time_limit
        if args.time_limit:
            run_deadline = started + args.time_limit
            node_deadline = (
                run_deadline if node_deadline is None
                else min(node_deadline, run_deadline)
            )
        result = solve_node(
            problem, args.satellite, tasks, problem.fleet.e2_per_sat_count,
            base_columns, same_pairs, diff_pairs, args,
            node_deadline=node_deadline,
            min_routes=min_routes,
            initial_cuts=initial_cuts,
        )
        result_summary = {
            "same": same_pairs,
            "diff": diff_pairs,
            "status": result["status"],
            "lp": result.get("lp"),
            "artificial": result.get("artificial"),
            "columns": len(result.get("columns", [])),
            "pricing": result.get("pricing"),
            "pricing_status_count": len(result.get("pricing_statuses", [])),
            "pricing_statuses": result.get("pricing_statuses", []),
            "iteration_history": result.get("iteration_history", []),
            "inherited_lower_bound": inherited_lb,
            "cuts": result.get("cuts", []),
            "cut_count": len(result.get("cuts", [])),
            "cut_history": result.get("cut_history", []),
        }
        results.append(result_summary)
        base_columns = merge_columns(base_columns, result.get("columns", []))
        last_columns = base_columns
        if result["status"] == "LP_PROVED" and "lp" in result:
            best_explored_lp = max(best_explored_lp, min(result["lp"], incumbent))
        if result.get("lp") is None:
            unresolved_nodes.append((inherited_lb, counter, same_pairs_t, diff_pairs_t))
            _write_outputs(args, current_payload(), last_columns)
            continue
        if result["status"] == "INFEASIBLE_PROVED":
            _write_outputs(args, current_payload(), last_columns)
            continue
        if result["status"] != "LP_PROVED":
            unresolved_nodes.append((inherited_lb, counter, same_pairs_t, diff_pairs_t))
            _write_outputs(args, current_payload(), last_columns)
            continue
        if result["lp"] >= incumbent - args.bound_tol:
            _write_outputs(args, current_payload(), last_columns)
            continue
        int_obj = solve_integer_pool(
            args.satellite, tasks, result["columns"],
            problem.fleet.e2_per_sat_count, min_routes=min_routes,
            cuts=result.get("cuts"))
        if int_obj is not None and int_obj < incumbent:
            incumbent = int_obj
        pair = choose_ryan_foster_pair(
            args.satellite,
            tasks,
            result["columns"],
            result["lambdas"],
            problem.fleet.e2_per_sat_count,
            args,
            min_routes=min_routes,
            cuts=result.get("cuts"),
        )
        if pair is None:
            incumbent = min(incumbent, result["lp"])
            _write_outputs(args, current_payload(), last_columns)
            continue
        a, b, together, branch_probe = pair
        result_summary["branch_pair"] = [a, b]
        result_summary["branch_together"] = together
        result_summary["branch_probe"] = branch_probe
        counter += 1
        heapq.heappush(
            nodes,
            (result["lp"], counter, tuple(same_pairs + [(a, b)]), tuple(diff_pairs)),
        )
        counter += 1
        heapq.heappush(
            nodes,
            (result["lp"], counter, tuple(same_pairs), tuple(diff_pairs + [(a, b)])),
        )
        _write_outputs(args, current_payload(), last_columns)

    payload = current_payload()
    _write_outputs(args, payload, last_columns)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scale", default="50_customers")
    parser.add_argument("--instance", default="C101")
    parser.add_argument("--satellite", required=True)
    parser.add_argument("--seed-routes-json")
    parser.add_argument("--columns-json")
    parser.add_argument("--initial-cuts-json")
    parser.add_argument("--resume-json")
    parser.add_argument("--incumbent", type=float, required=True)
    parser.add_argument("--time-limit", type=float, default=600.0)
    parser.add_argument("--node-limit", type=int, default=20)
    parser.add_argument("--node-time-limit", type=float)
    parser.add_argument("--cg-iterations", type=int, default=80)
    parser.add_argument("--pricing-method", choices=["mip", "exact_label"], default="mip")
    parser.add_argument("--pricing-time-limit", type=float, default=10.0)
    parser.add_argument("--pricing-pool-solutions", type=int, default=10)
    parser.add_argument("--pricing-diverse-task-sets", action="store_true")
    parser.add_argument("--pricing-feasibility-proof", action="store_true")
    parser.add_argument("--pricing-forbid-existing-paths", action="store_true")
    parser.add_argument("--exact-label-max-expansions", type=int, default=2000000)
    parser.add_argument("--exact-label-exhaust-each-iteration", action="store_true")
    parser.add_argument(
        "--exact-label-dominance",
        choices=["same_visited", "subset", "subset_bucket"],
        default="same_visited",
    )
    parser.add_argument("--label-before-mip", action="store_true")
    parser.add_argument("--label-pool-solutions", type=int, default=30)
    parser.add_argument("--label-beam-width", type=int, default=500)
    parser.add_argument("--label-max-expansions", type=int, default=500000)
    parser.add_argument("--label-time-limit", type=float, default=20.0)
    parser.add_argument("--pricing-mip-focus", type=int)
    parser.add_argument("--pricing-cuts", type=int)
    parser.add_argument("--pricing-heuristics", type=float)
    parser.add_argument("--pricing-presolve", type=int)
    parser.add_argument("--pricing-tol", type=float, default=1e-6)
    parser.add_argument("--master-method", type=int)
    parser.add_argument("--master-perturb-value", type=float)
    parser.add_argument("--master-numeric-focus", type=int)
    parser.add_argument("--branch-strategy", choices=["fractional", "strong"], default="fractional")
    parser.add_argument("--branch-strong-candidates", type=int, default=12)
    parser.add_argument("--capacity-route-minimum", action="store_true")
    parser.add_argument("--sri-cuts", action="store_true")
    parser.add_argument("--sri-cut-size", type=int, default=3)
    parser.add_argument("--sri-max-cuts-per-round", type=int, default=25)
    parser.add_argument("--sri-max-total-cuts", type=int)
    parser.add_argument("--sri-max-shared-tasks", type=int, default=-1)
    parser.add_argument("--sri-cut-tol", type=float, default=1e-5)
    parser.add_argument("--bound-tol", type=float, default=1e-5)
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "output" / "satellite_branch_price_probe")
    return parser.parse_args()


if __name__ == "__main__":
    run(parse_args())
