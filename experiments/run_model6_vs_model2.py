# -*- coding: utf-8 -*-
"""
Model 6 (自由分配) vs Model 2 (固定分配) ALNS 对比。

口径：同一数据集、同一 seed、同一迭代/时间预算。M2 用 strong initial；
M6 从固定分配种子出发，允许跨前置仓搬移顾客。成本统一用 M6 求值器
（E1+E2 距离）计算，确保两种解用同一把尺子量。

注意：ALNS 降温速率依赖 max_iterations，因此公平对比应给两边**相同的迭代数**
和宽松的时间上限（如 --iterations 500 --time 60），而不是把迭代数设很大
（那会让温度几乎不降，两边都变成高温随机游走）。

输出：markdown + csv 汇总（分规模 better/equal/worse、M2 不可行但 M6 可行、
平均 gap%），并保存每个算例的分配变化（搬移顾客数）。
"""
import argparse
import csv
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.instance import ProblemInstance
from models.alns_base import Route, Solution
from models.model_2.alns_solver import solve_alns as solve_m2
from models.model_6.alns_solver import Evaluator as M6Evaluator
from models.model_6.alns_solver import solve_alns as solve_m6
from utils.data_loader import parse_dataset
from utils.task_builder import build_tasks_for_model

DATA_ROOT = Path(__file__).resolve().parent.parent / "data"
OUT_ROOT = Path(__file__).resolve().parent.parent / "output" / "model6_vs_model2"
FLEET = json.loads(
    (Path(__file__).resolve().parent.parent / "config" / "fleet_config.json")
    .read_text(encoding="utf-8"))


def load_problem(path):
    nodes, _ = parse_dataset(str(path))
    problem = ProblemInstance(nodes, FLEET, speed=FLEET.get("speed", 1.0))
    problem.aggregate_demands_from_pre_assignment()
    return problem


def solution_from_routes(problem, e1_routes, e2_routes_by_sat, vps):
    sol = Solution()
    depot = problem.depot.id
    sol.e1_routes = [Route(depot, list(r[1:-1])) for r in e1_routes]
    for sat, routes in e2_routes_by_sat.items():
        sol.e2_routes[sat] = [Route(sat, list(r[1:-1])) for r in routes]
    return sol


def fixed_assignment(problem):
    return {c.id: c.assigned_sat for c in problem.customers}


def run_one(path, iterations, time_limit, seed):
    problem = load_problem(path)
    vps = problem.fleet.e2_per_sat_count
    e1_tasks, e2_tasks_by_sat = build_tasks_for_model(problem, 2)
    fixed = fixed_assignment(problem)

    t0 = time.time()
    r2 = solve_m2(problem, e1_tasks, e2_tasks_by_sat, vps,
                  max_iterations=iterations, max_time=time_limit, seed=seed,
                  verbose=False, strong_initial_solution=True)
    t_m2 = time.time() - t0

    t0 = time.time()
    r6 = solve_m6(problem, vps, max_iterations=iterations, max_time=time_limit,
                  seed=seed, verbose=False)
    t_m6 = time.time() - t0

    ev = M6Evaluator(problem, 9999.0, vps)
    row = {
        "dataset": path.name,
        "scale": path.parent.name,
        "customers": len(problem.customers),
        "satellites": len(problem.satellites),
        "m2_feasible": r2[0] is not None,
        "m6_feasible": r6[0] is not None,
        "m2_cost": None,
        "m6_cost": None,
        "m2_time": round(t_m2, 2),
        "m6_time": round(t_m6, 2),
        "m6_moved": None,
    }
    if r2[0]:
        row["m2_cost"] = ev.total_cost(
            solution_from_routes(problem, r2[0], r2[1], vps))
    if r6[0]:
        row["m6_cost"] = ev.total_cost(
            solution_from_routes(problem, r6[0], r6[1], vps))
        moved = sum(1 for c, s in r6[2].items() if fixed.get(c) != s)
        row["m6_moved"] = moved
    return row


def summarize(rows):
    scales = {}
    for r in rows:
        scale = r["scale"]
        s = scales.setdefault(scale, {
            "n": 0, "both_feasible": 0, "m6_better": 0, "m6_equal": 0,
            "m6_worse": 0, "m2_infeas_m6_feas": 0, "m6_infeas": 0,
            "gap_sum": 0.0, "moved_sum": 0,
        })
        s["n"] += 1
        if r["m2_feasible"] and r["m6_feasible"]:
            s["both_feasible"] += 1
            delta = (r["m6_cost"] - r["m2_cost"]) / r["m2_cost"] * 100
            s["gap_sum"] += delta
            if delta < -1e-9:
                s["m6_better"] += 1
            elif delta > 1e-9:
                s["m6_worse"] += 1
            else:
                s["m6_equal"] += 1
        elif not r["m2_feasible"] and r["m6_feasible"]:
            s["m2_infeas_m6_feas"] += 1
        elif not r["m6_feasible"]:
            s["m6_infeas"] += 1
        if r["m6_moved"] is not None:
            s["moved_sum"] += r["m6_moved"]
    return scales


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scales", nargs="*", default=[
        "5_customers", "10_customers", "20_customers", "50_customers",
        "100_customers"])
    ap.add_argument("--instances", nargs="*", default=None,
                    help="实例名（不含扩展名）；默认该规模全部")
    ap.add_argument("--iterations", type=int, default=200)
    ap.add_argument("--time", type=float, default=15.0)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--tag", default="default")
    ap.add_argument("--output-dir", default=None)
    args = ap.parse_args()

    datasets = []
    for scale in args.scales:
        d = DATA_ROOT / scale
        if not d.exists():
            print(f"[skip] 不存在 {d}")
            continue
        files = sorted(d.glob("*.csv"))
        if args.instances:
            wanted = {i if i.endswith(".csv") else i + ".csv"
                      for i in args.instances}
            files = [f for f in files if f.name in wanted]
        datasets.extend(files)

    print(f"共 {len(datasets)} 个算例，iterations={args.iterations} "
          f"time={args.time}s seed={args.seed} tag={args.tag}")

    rows = []
    for i, path in enumerate(datasets):
        t0 = time.time()
        row = run_one(path, args.iterations, args.time, args.seed)
        rows.append(row)
        r = row
        m2 = f"{r['m2_cost']:.2f}" if r["m2_cost"] else "INF"
        m6 = f"{r['m6_cost']:.2f}" if r["m6_cost"] else "INF"
        print(f"[{i + 1}/{len(datasets)}] {r['scale']}/{r['dataset']} "
              f"M2={m2} M6={m6} moved={r['m6_moved']} "
              f"({time.time() - t0:.1f}s)")

    out_dir = Path(args.output_dir) if args.output_dir else (OUT_ROOT / args.tag)
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "summary.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    scales = summarize(rows)
    md = [
        f"# Model 6 (自由分配) vs Model 2 (固定分配) — {args.tag}",
        "",
        f"预算：iterations={args.iterations}, time={args.time}s/求解器, "
        f"seed={args.seed}；成本 = E1+E2 距离。",
        "",
        "| 规模 | 算例数 | 双方可行 | M6更好 | 持平 | M6更差 | "
        "M2不可行但M6可行 | M6不可行 | 平均gap% | 平均搬移顾客 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for scale in args.scales:
        s = scales.get(scale)
        if not s:
            continue
        avg_gap = s["gap_sum"] / max(s["both_feasible"], 1)
        avg_moved = s["moved_sum"] / max(s["n"], 1)
        md.append(f"| {scale} | {s['n']} | {s['both_feasible']} | "
                  f"{s['m6_better']} | {s['m6_equal']} | {s['m6_worse']} | "
                  f"{s['m2_infeas_m6_feas']} | {s['m6_infeas']} | "
                  f"{avg_gap:.2f}% | {avg_moved:.1f} |")
    md.append("")
    md_path = out_dir / "summary.md"
    md_path.write_text("\n".join(md), encoding="utf-8")
    print(f"\n汇总: {md_path}")
    print("\n".join(md))


if __name__ == "__main__":
    main()
