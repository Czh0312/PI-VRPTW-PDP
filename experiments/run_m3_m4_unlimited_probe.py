# -*- coding: utf-8 -*-
"""Run Model 3 / Model 4 exact MIPs with an optional Gurobi TimeLimit.

This diagnostic keeps the formulation complete and writes final metadata under
``results/unlimited_exact_probe`` by default.  Omitting ``--time-limit`` makes
the run unlimited; supplying it enables fair before/after solver comparisons.
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUROBI_PY = Path(r"C:\gurobi\win64\python311\lib")
if GUROBI_PY.exists():
    sys.path.insert(0, str(GUROBI_PY))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gurobipy import GRB
from gurobipy import GurobiError

from core.instance import ProblemInstance
from models.model_3.model import build_unified_pdp_model
from models.model_3.solver import _solve_info as m3_solve_info
from models.model_4.alns_solver import solve_alns as solve_m4_alns
from models.model_4.exact import solve_exact as solve_m4
from utils.data_loader import parse_dataset
from utils.task_builder import build_tasks_for_model


def _problem(scale, instance):
    fleet = json.loads((ROOT / "config" / "fleet_config.json").read_text(encoding="utf-8"))
    nodes, _ = parse_dataset(str(ROOT / "data" / scale / f"{instance}.csv"))
    problem = ProblemInstance(nodes, fleet, speed=fleet.get("speed", 1.0))
    problem.aggregate_demands_from_pre_assignment()
    return problem


def _status_name(code):
    names = {
        GRB.OPTIMAL: "OPTIMAL",
        GRB.INFEASIBLE: "INFEASIBLE",
        GRB.INF_OR_UNBD: "INF_OR_UNBD",
        GRB.UNBOUNDED: "UNBOUNDED",
        GRB.TIME_LIMIT: "TIME_LIMIT",
        GRB.INTERRUPTED: "INTERRUPTED",
        GRB.NUMERIC: "NUMERIC",
        GRB.SUBOPTIMAL: "SUBOPTIMAL",
        GRB.WORK_LIMIT: "WORK_LIMIT",
        GRB.MEM_LIMIT: "MEM_LIMIT",
    }
    return names.get(code, str(code))


def run_m3(problem, out_dir, scale, instance, time_limit, gurobi_params):
    e1_tasks, e2_tasks_by_sat = build_tasks_for_model(problem, 3)
    m, _vars, _ctx = build_unified_pdp_model(
        problem,
        e1_tasks,
        e2_tasks_by_sat,
        problem.fleet.e2_per_sat_count,
        TFp=9999.0,
    )
    m.setParam("MIPGap", 0.0)
    m.setParam("IntFeasTol", 1e-9)
    m.setParam("FeasibilityTol", 1e-9)
    if time_limit is not None:
        m.setParam("TimeLimit", time_limit)
    for name, value in gurobi_params.items():
        m.setParam(name, value)
    try:
        m.setParam("LogFile", str(out_dir / f"m3_{scale}_{instance}.log"))
    except GurobiError as exc:
        print(f"[warn] Gurobi LogFile disabled: {exc}")
    started = time.perf_counter()
    m.optimize()
    solution_type = "proven_optimal" if m.status == GRB.OPTIMAL else (
        "feasible_not_proven" if getattr(m, "SolCount", 0) else "none"
    )
    info = m3_solve_info(m, solution_type)
    info["wall_time"] = time.perf_counter() - started
    info["scale"] = scale
    info["instance"] = instance
    info["model"] = 3
    return info


def run_m4(problem, out_dir, scale, instance, time_limit, gurobi_params,
           warm_start_alns_time=None, warm_start_alns_seed=42):
    warm_start_routes = None
    warm_start_info = None
    if warm_start_alns_time is not None and warm_start_alns_time > 0:
        started_alns = time.perf_counter()
        e1_routes, e2_routes, assignment = solve_m4_alns(
            problem,
            problem.fleet.e2_per_sat_count,
            max_time=warm_start_alns_time,
            seed=warm_start_alns_seed,
            verbose=False,
        )
        warm_start_info = {
            "source": "m4_alns",
            "time_limit": warm_start_alns_time,
            "seed": warm_start_alns_seed,
            "wall_time": time.perf_counter() - started_alns,
            "available": bool(e1_routes and e2_routes and assignment),
        }
        if warm_start_info["available"]:
            warm_start_routes = (e1_routes, e2_routes)
    started = time.perf_counter()
    _e1, _e2, _assign, info = solve_m4(
        problem,
        problem.fleet.e2_per_sat_count,
        time_limit=time_limit,
        gurobi_params=gurobi_params,
        verbose=True,
        return_info=True,
        warm_start_routes=warm_start_routes,
    )
    info["wall_time"] = time.perf_counter() - started
    info["scale"] = scale
    info["instance"] = instance
    info["model"] = 4
    info["status"] = _status_name(info.get("status_code", info.get("status")))
    info["warm_start"] = warm_start_info
    return info


def _parse_gurobi_params(items):
    """Parse repeatable ``Name=JSON-value`` command-line parameters."""
    params = {}
    for item in items:
        if "=" not in item:
            raise ValueError(f"--gurobi-param must be Name=Value, got {item!r}")
        name, raw_value = item.split("=", 1)
        try:
            params[name] = json.loads(raw_value)
        except json.JSONDecodeError:
            params[name] = raw_value
    return params


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=int, choices=[3, 4], required=True)
    parser.add_argument("--scale", default="10_customers")
    parser.add_argument("--instance", required=True)
    parser.add_argument("--output-dir", default="results/unlimited_exact_probe")
    parser.add_argument(
        "--time-limit", type=float, default=None,
        help="Optional Gurobi TimeLimit in seconds; omit for an unlimited probe.",
    )
    parser.add_argument(
        "--gurobi-param", action="append", default=[], metavar="NAME=VALUE",
        help="Repeatable Gurobi parameter, with VALUE parsed as JSON when possible.",
    )
    parser.add_argument(
        "--warm-start-alns-time", type=float, default=None,
        help="For M4, run ALNS for this many seconds and inject its solution as a MIP start.",
    )
    parser.add_argument("--warm-start-alns-seed", type=int, default=42)
    args = parser.parse_args()

    out_dir = ROOT / args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    problem = _problem(args.scale, args.instance)
    gurobi_params = _parse_gurobi_params(args.gurobi_param)

    if args.model == 3:
        info = run_m3(problem, out_dir, args.scale, args.instance,
                      args.time_limit, gurobi_params)
    else:
        info = run_m4(problem, out_dir, args.scale, args.instance,
                      args.time_limit, gurobi_params,
                      args.warm_start_alns_time, args.warm_start_alns_seed)

    stamp = time.strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"m{args.model}_{args.scale}_{args.instance}_{stamp}.json"
    info["time_limit"] = args.time_limit
    info["gurobi_params"] = gurobi_params
    out_path.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(info, ensure_ascii=False, indent=2))
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
