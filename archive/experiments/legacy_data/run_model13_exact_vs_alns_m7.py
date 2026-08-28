# -*- coding: utf-8 -*-
"""
Diagnose Model 1/3 exact Gurobi runs and compare them with ALNS for
Models 1/3/7.

Default usage intentionally runs one representative instance per scale so a
small probe finishes quickly:

    python3 experiments/run_model13_exact_vs_alns_m7.py

Use --all-instances after the small probe looks healthy.
"""
import argparse
import csv
import io
import json
import sys
import time
import traceback
from contextlib import redirect_stdout
from pathlib import Path
from statistics import mean

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from models.routing.common.domain.instance import ProblemInstance
from models.routing.variants.model_1.solver import solve_exact as exact_m1
from models.routing.variants.model_3.solver import solve_exact as exact_m3
from models.routing.variants.model_1.alns_solver import solve_alns as alns_m1
from models.routing.variants.model_3.alns_solver import solve_alns as alns_m3
from models.routing.variants.model_7.alns_solver import solve_alns as alns_m7
from models.routing.common.data_loader import parse_dataset
from models.routing.common.task_builder import build_tasks_for_model

CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"
OUTPUT_DIR = PROJECT_ROOT / "output" / "model13_exact_vs_alns_m7"

EXACT_SOLVERS = {1: exact_m1, 3: exact_m3}
ALNS_SOLVERS = {1: alns_m1, 3: alns_m3, 7: alns_m7}

GUROBI_PARAM_PRESETS = {
    "default": {},
    "feasible": {
        "MIPFocus": 1,
        "Heuristics": 0.5,
        "NoRelHeurTime": 20,
        "ImproveStartTime": 30,
        "StartNodeLimit": 100000,
    },
    "balanced": {
        "MIPFocus": 1,
        "Heuristics": 0.25,
        "NoRelHeurTime": 10,
        "ImproveStartTime": 60,
        "Cuts": 2,
        "Presolve": 2,
        "Symmetry": 2,
        "StartNodeLimit": 100000,
    },
    "bound": {
        "MIPFocus": 2,
        "Cuts": 2,
        "Presolve": 2,
        "Symmetry": 2,
        "StartNodeLimit": 100000,
    },
    "incumbent": {
        "MIPFocus": 1,
        "Heuristics": 0.8,
        "NoRelHeurTime": 30,
        "ImproveStartTime": 20,
        "RINS": 10,
        "StartNodeLimit": 100000,
    },
}


def load_fleet_config():
    with open(CONFIG_DIR / "routing" / "validation" / "zhou_small.json", "r", encoding="utf-8") as f:
        return json.load(f)


def _parse_param_value(value):
    lowered = value.lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    try:
        if "." not in value and "e" not in lowered:
            return int(value)
        return float(value)
    except ValueError:
        return value


def gurobi_params_from_args(args):
    params = dict(GUROBI_PARAM_PRESETS[args.gurobi_preset])
    for item in args.gurobi_param:
        if "=" not in item:
            raise ValueError(f"--gurobi-param 需要 Name=Value 格式，收到: {item}")
        name, value = item.split("=", 1)
        params[name] = _parse_param_value(value)
    return params


def _to_phys(nid):
    if nid.startswith("TRANS_PICK_") or nid.startswith("TRANS_DROP_"):
        payload = nid.split("_", 2)[-1]
        return payload.rsplit("@", 1)[-1]
    if nid.endswith(("d", "p")) and len(nid) > 1 and not nid.startswith("TRANS"):
        return nid[:-1]
    return nid


def route_distance(problem, route):
    return sum(
        problem.distance(_to_phys(route[i]), _to_phys(route[i + 1]))
        for i in range(len(route) - 1)
    )


def total_cost(problem, e1_routes, e2_routes_by_sat):
    if not e1_routes or not e2_routes_by_sat:
        return None
    cost = sum(route_distance(problem, route) for route in e1_routes)
    for routes in e2_routes_by_sat.values():
        cost += sum(route_distance(problem, route) for route in routes)
    return cost


def count_e2(e2_routes_by_sat):
    return sum(len(routes) for routes in (e2_routes_by_sat or {}).values())


def build_problem(data_path, fleet_cfg):
    nodes_dict, _ = parse_dataset(str(data_path))
    problem = ProblemInstance(
        nodes_dict,
        fleet_cfg,
        speed=fleet_cfg.get("speed", 1.0),
    )
    problem.aggregate_demands_from_pre_assignment()
    return problem


def build_alns_warm_start(model, data_path, fleet_cfg, args):
    if model not in (1, 3):
        return None

    problem = build_problem(data_path, fleet_cfg)
    e1_tasks, e2_tasks_by_sat = build_tasks_for_model(problem, model)
    kwargs = {
        "problem": problem,
        "e1_tasks": e1_tasks,
        "e2_tasks_by_sat": e2_tasks_by_sat,
        "vehicles_per_satellite": problem.fleet.e2_per_sat_count,
        "max_iterations": args.warm_start_alns_iterations,
        "max_time": args.warm_start_alns_time,
        "seed": args.seed,
        "verbose": False,
    }
    if model == 3:
        kwargs["TFp"] = args.tfp
        kwargs["strong_initial_solution"] = True

    log_buf = io.StringIO()
    started = time.time()
    with redirect_stdout(log_buf):
        e1_routes, e2_routes = ALNS_SOLVERS[model](**kwargs)
    elapsed = time.time() - started
    cost = total_cost(problem, e1_routes, e2_routes)
    if cost is None:
        return {
            "routes": None,
            "cost": None,
            "elapsed": elapsed,
            "status": "NO_SOLUTION",
        }
    return {
        "routes": (e1_routes, e2_routes),
        "cost": cost,
        "elapsed": elapsed,
        "status": "OK",
    }


def run_exact(model, data_path, fleet_cfg, args, logs_dir):
    problem = build_problem(data_path, fleet_cfg)
    e1_tasks, e2_tasks_by_sat = build_tasks_for_model(problem, model)
    gurobi_params = gurobi_params_from_args(args)
    warm_start = None
    if args.warm_start_exact and model in args.warm_start_models:
        warm_start = build_alns_warm_start(
            model, data_path, fleet_cfg, args
        )
    kwargs = {
        "problem": problem,
        "e1_tasks": e1_tasks,
        "e2_tasks_by_sat": e2_tasks_by_sat,
        "vehicles_per_satellite": problem.fleet.e2_per_sat_count,
        "verbose": args.verbose,
        "time_limit": args.exact_time_limit,
        "mip_gap": 0.0,
        "return_incumbent_on_time_limit": True,
        "return_info": True,
        "warm_start_routes": warm_start["routes"] if warm_start else None,
        "gurobi_params": gurobi_params,
    }
    if model == 3:
        kwargs["TFp"] = args.tfp

    log_buf = io.StringIO()
    started = time.time()
    try:
        with redirect_stdout(log_buf):
            e1_routes, e2_routes, info = EXACT_SOLVERS[model](**kwargs)
        elapsed = time.time() - started
        cost = total_cost(problem, e1_routes, e2_routes)
        status = "OK" if cost is not None else "NO_SOLUTION"
        if info.get("solution_type") == "time_limit_incumbent":
            status = "INCUMBENT_NOT_PROVEN"
        result = {
            "scale": data_path.parent.name,
            "instance": data_path.stem,
            "model": model,
            "solver": "exact",
            "status": status,
            "cost": cost,
            "elapsed": elapsed,
            "e1_routes": len(e1_routes or []),
            "e2_routes": count_e2(e2_routes),
            "gurobi_preset": args.gurobi_preset,
            "gurobi_params": json.dumps(
                gurobi_params,
                ensure_ascii=False,
                sort_keys=True,
            ),
            **{f"gurobi_{k}": v for k, v in info.items()},
        }
        if warm_start:
            result.update({
                "warm_start_status": warm_start["status"],
                "warm_start_cost": warm_start["cost"],
                "warm_start_elapsed": warm_start["elapsed"],
            })
    except Exception as exc:
        elapsed = time.time() - started
        result = {
            "scale": data_path.parent.name,
            "instance": data_path.stem,
            "model": model,
            "solver": "exact",
            "status": f"ERROR: {exc}",
            "cost": None,
            "elapsed": elapsed,
            "e1_routes": 0,
            "e2_routes": 0,
            "gurobi_preset": args.gurobi_preset,
            "gurobi_params": json.dumps(
                gurobi_params,
                ensure_ascii=False,
                sort_keys=True,
            ),
        }
        if warm_start:
            result.update({
                "warm_start_status": warm_start["status"],
                "warm_start_cost": warm_start["cost"],
                "warm_start_elapsed": warm_start["elapsed"],
            })
        log_buf.write("\n\n[TRACEBACK]\n")
        log_buf.write(traceback.format_exc())

    write_log(logs_dir, result, log_buf.getvalue())
    return result


def run_alns(model, data_path, fleet_cfg, args, logs_dir):
    problem = build_problem(data_path, fleet_cfg)
    e1_tasks, e2_tasks_by_sat = build_tasks_for_model(problem, model)
    kwargs = {
        "problem": problem,
        "e1_tasks": e1_tasks,
        "e2_tasks_by_sat": e2_tasks_by_sat,
        "vehicles_per_satellite": problem.fleet.e2_per_sat_count,
        "max_iterations": args.alns_iterations,
        "max_time": args.alns_time,
        "seed": args.seed,
        "verbose": args.verbose,
    }
    if model == 3:
        kwargs["TFp"] = args.tfp
        kwargs["strong_initial_solution"] = True
    if model == 7:
        kwargs.update({
            "transshipment_min_net_saving": args.transshipment_min_net_saving,
            "transshipment_time_budget": args.transshipment_time_budget,
            "transshipment_probe_depth": args.transshipment_probe_depth,
            "transshipment_final_refine": args.model7_final_refine,
            "transshipment_baseline_guard": args.model7_baseline_guard,
        })

    log_buf = io.StringIO()
    started = time.time()
    try:
        with redirect_stdout(log_buf):
            e1_routes, e2_routes = ALNS_SOLVERS[model](**kwargs)
        elapsed = time.time() - started
        cost = total_cost(problem, e1_routes, e2_routes)
        result = {
            "scale": data_path.parent.name,
            "instance": data_path.stem,
            "model": model,
            "solver": "alns",
            "status": "OK" if cost is not None else "NO_SOLUTION",
            "cost": cost,
            "elapsed": elapsed,
            "e1_routes": len(e1_routes or []),
            "e2_routes": count_e2(e2_routes),
        }
    except Exception as exc:
        elapsed = time.time() - started
        result = {
            "scale": data_path.parent.name,
            "instance": data_path.stem,
            "model": model,
            "solver": "alns",
            "status": f"ERROR: {exc}",
            "cost": None,
            "elapsed": elapsed,
            "e1_routes": 0,
            "e2_routes": 0,
        }
        log_buf.write("\n\n[TRACEBACK]\n")
        log_buf.write(traceback.format_exc())

    write_log(logs_dir, result, log_buf.getvalue())
    return result


def write_log(logs_dir, result, content):
    logs_dir.mkdir(parents=True, exist_ok=True)
    path = logs_dir / (
        f"{result['scale']}__{result['instance']}__"
        f"M{result['model']}__{result['solver']}.log"
    )
    header = json.dumps(result, ensure_ascii=False, indent=2)
    path.write_text(header + "\n\n" + content, encoding="utf-8")


def select_instances(scale, args):
    csvs = sorted((DATA_DIR / scale).glob("*.csv"))
    if args.instances:
        wanted = {name[:-4] if name.endswith(".csv") else name for name in args.instances}
        csvs = [path for path in csvs if path.stem in wanted]
    if not args.all_instances:
        csvs = csvs[:args.max_instances]
    return csvs


def write_outputs(results, output_dir):
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "model13_exact_vs_alns_m7_raw.json"
    json_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    csv_path = output_dir / "model13_exact_vs_alns_m7_raw.csv"
    fieldnames = sorted({key for row in results for key in row})
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    summary = {}
    for row in results:
        key = (row["scale"], row["model"], row["solver"])
        summary.setdefault(key, {"costs": [], "times": [], "ok": 0, "total": 0})
        summary[key]["total"] += 1
        if row["cost"] is not None:
            summary[key]["ok"] += 1
            summary[key]["costs"].append(row["cost"])
            summary[key]["times"].append(row["elapsed"])

    summary_rows = []
    for (scale, model, solver), vals in sorted(summary.items()):
        summary_rows.append({
            "scale": scale,
            "model": model,
            "solver": solver,
            "n_ok": vals["ok"],
            "n_total": vals["total"],
            "cost_mean": mean(vals["costs"]) if vals["costs"] else None,
            "time_mean": mean(vals["times"]) if vals["times"] else None,
        })

    summary_path = output_dir / "model13_exact_vs_alns_m7_summary.csv"
    with open(summary_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "scale", "model", "solver", "n_ok", "n_total",
                "cost_mean", "time_mean",
            ],
        )
        writer.writeheader()
        writer.writerows(summary_rows)
    return json_path, csv_path, summary_path


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scales",
        nargs="*",
        default=["5_customers", "10_customers", "50_customers", "100_customers"],
    )
    parser.add_argument("--models", nargs="*", type=int, default=[1, 3, 7])
    parser.add_argument("--instances", nargs="*", default=["C101"])
    parser.add_argument("--all-instances", action="store_true")
    parser.add_argument("--max-instances", type=int, default=1)
    parser.add_argument("--exact-time-limit", type=float, default=600.0)
    parser.add_argument("--alns-time", type=float, default=60.0)
    parser.add_argument("--alns-iterations", type=int, default=5000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--tfp", type=float, default=9999.0)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--skip-exact", action="store_true")
    parser.add_argument("--skip-alns", action="store_true")
    parser.add_argument("--warm-start-exact", action="store_true")
    parser.add_argument("--warm-start-models", nargs="*", type=int, default=[3])
    parser.add_argument("--warm-start-alns-time", type=float, default=30.0)
    parser.add_argument("--warm-start-alns-iterations", type=int, default=5000)
    parser.add_argument(
        "--gurobi-preset",
        choices=sorted(GUROBI_PARAM_PRESETS),
        default="default",
    )
    parser.add_argument("--gurobi-param", action="append", default=[])
    parser.add_argument("--transshipment-min-net-saving", type=float, default=0.01)
    parser.add_argument("--transshipment-time-budget", type=float, default=0.05)
    parser.add_argument("--transshipment-probe-depth", type=int, default=1)
    parser.add_argument("--model7-final-refine", action="store_true")
    parser.add_argument("--model7-baseline-guard", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    return parser.parse_args()


def main():
    args = parse_args()
    fleet_cfg = load_fleet_config()
    logs_dir = args.output_dir / "logs"
    results = []

    for scale in args.scales:
        csvs = select_instances(scale, args)
        if not csvs:
            print(f"[WARN] {scale}: no matching instances")
            continue
        print(f"\n=== {scale}: {len(csvs)} instance(s) ===")
        for data_path in csvs:
            print(f"\n[{data_path.parent.name}/{data_path.stem}]")
            if not args.skip_exact:
                for model in (1, 3):
                    if model not in args.models:
                        continue
                    print(f"  M{model} exact ... ", end="", flush=True)
                    row = run_exact(model, data_path, fleet_cfg, args, logs_dir)
                    results.append(row)
                    cost = f"{row['cost']:.2f}" if row["cost"] is not None else "N/A"
                    gap = row.get("gurobi_mip_gap")
                    gap_s = f", gap={gap:.2%}" if gap is not None else ""
                    print(f"{row['status']} cost={cost} t={row['elapsed']:.1f}s{gap_s}")
            if not args.skip_alns:
                for model in (1, 3, 7):
                    if model not in args.models:
                        continue
                    print(f"  M{model} ALNS  ... ", end="", flush=True)
                    row = run_alns(model, data_path, fleet_cfg, args, logs_dir)
                    results.append(row)
                    cost = f"{row['cost']:.2f}" if row["cost"] is not None else "N/A"
                    print(f"{row['status']} cost={cost} t={row['elapsed']:.1f}s")

    json_path, csv_path, summary_path = write_outputs(results, args.output_dir)
    print("\nSaved:")
    print(f"  raw json: {json_path}")
    print(f"  raw csv : {csv_path}")
    print(f"  summary : {summary_path}")


if __name__ == "__main__":
    main()
