# -*- coding: utf-8 -*-
"""
Model 4 (自由分配) vs Model 3 (固定分配) ALNS 对比。

口径：同一数据集、同一 seed、同一迭代/时间预算。M3 用 strong initial；
M4 从固定分配种子出发，允许跨前置仓搬移顾客。成本统一用 M4 求值器
（E1+E2 距离）计算，确保两种解用同一把尺子量。

注意：ALNS 降温速率依赖 max_iterations，因此公平对比应给两边**相同的迭代数**
和宽松的时间上限（如 --iterations 500 --time 60），而不是把迭代数设很大
（那会让温度几乎不降，两边都变成高温随机游走）。

输出：markdown + csv 汇总（分规模 better/equal/worse、M3 不可行但 M4 可行、
平均 gap%），并保存每个算例的分配变化（搬移顾客数）。
"""
import argparse
import csv
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from models.routing.common.domain.instance import ProblemInstance
from models.routing.alns.types import Route, Solution
from models.routing.variants.model_3.alns_solver import solve_alns as solve_m3
from models.routing.variants.model_4.alns_solver import Evaluator as M4Evaluator
from models.routing.variants.model_4.alns_solver import solve_alns as solve_m4
from models.routing.common.data_loader import parse_dataset
from models.routing.common.task_builder import build_tasks_for_model

DATA_ROOT = Path(__file__).resolve().parents[2] / "data"
OUT_ROOT = Path(__file__).resolve().parents[2] / "output" / "model4_vs_model3"
FLEET = json.loads(
    (Path(__file__).resolve().parents[2] / "config" / "routing" /
     "validation" / "zhou_small.json")
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
    e1_tasks, e2_tasks_by_sat = build_tasks_for_model(problem, 3)
    fixed = fixed_assignment(problem)

    t0 = time.time()
    r2 = solve_m3(problem, e1_tasks, e2_tasks_by_sat, vps,
                  max_iterations=iterations, max_time=time_limit, seed=seed,
                  verbose=False, strong_initial_solution=True)
    t_m3 = time.time() - t0

    t0 = time.time()
    r6 = solve_m4(problem, vps, max_iterations=iterations, max_time=time_limit,
                  seed=seed, verbose=False)
    t_m4 = time.time() - t0

    ev = M4Evaluator(problem, 9999.0, vps)
    row = {
        "dataset": path.name,
        "scale": path.parent.name,
        "customers": len(problem.customers),
        "satellites": len(problem.satellites),
        "m3_feasible": r2[0] is not None,
        "m4_feasible": r6[0] is not None,
        "m3_cost": None,
        "m4_cost": None,
        "m3_time": round(t_m3, 2),
        "m4_time": round(t_m4, 2),
        "m4_moved": None,
    }
    if r2[0]:
        row["m3_cost"] = ev.total_cost(
            solution_from_routes(problem, r2[0], r2[1], vps))
    if r6[0]:
        row["m4_cost"] = ev.total_cost(
            solution_from_routes(problem, r6[0], r6[1], vps))
        moved = sum(1 for c, s in r6[2].items() if fixed.get(c) != s)
        row["m4_moved"] = moved
    return row


def summarize(rows):
    scales = {}
    for r in rows:
        scale = r["scale"]
        s = scales.setdefault(scale, {
            "n": 0, "both_feasible": 0, "m4_better": 0, "m4_equal": 0,
            "m4_worse": 0, "m3_infeas_m4_feas": 0, "m4_infeas": 0,
            "gap_sum": 0.0, "moved_sum": 0,
        })
        s["n"] += 1
        if r["m3_feasible"] and r["m4_feasible"]:
            s["both_feasible"] += 1
            delta = (r["m4_cost"] - r["m3_cost"]) / r["m3_cost"] * 100
            s["gap_sum"] += delta
            if delta < -1e-9:
                s["m4_better"] += 1
            elif delta > 1e-9:
                s["m4_worse"] += 1
            else:
                s["m4_equal"] += 1
        elif not r["m3_feasible"] and r["m4_feasible"]:
            s["m3_infeas_m4_feas"] += 1
        elif not r["m4_feasible"]:
            s["m4_infeas"] += 1
        if r["m4_moved"] is not None:
            s["moved_sum"] += r["m4_moved"]
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
        m3 = f"{r['m3_cost']:.2f}" if r["m3_cost"] else "INF"
        m4 = f"{r['m4_cost']:.2f}" if r["m4_cost"] else "INF"
        print(f"[{i + 1}/{len(datasets)}] {r['scale']}/{r['dataset']} "
              f"M3={m3} M4={m4} moved={r['m4_moved']} "
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
        f"# Model 4 (自由分配) vs Model 3 (固定分配) — {args.tag}",
        "",
        f"预算：iterations={args.iterations}, time={args.time}s/求解器, "
        f"seed={args.seed}；成本 = E1+E2 距离。",
        "",
        "| 规模 | 算例数 | 双方可行 | M4更好 | 持平 | M4更差 | "
        "M3不可行但M4可行 | M4不可行 | 平均gap% | 平均搬移顾客 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for scale in args.scales:
        s = scales.get(scale)
        if not s:
            continue
        avg_gap = s["gap_sum"] / max(s["both_feasible"], 1)
        avg_moved = s["moved_sum"] / max(s["n"], 1)
        md.append(f"| {scale} | {s['n']} | {s['both_feasible']} | "
                  f"{s['m4_better']} | {s['m4_equal']} | {s['m4_worse']} | "
                  f"{s['m3_infeas_m4_feas']} | {s['m4_infeas']} | "
                  f"{avg_gap:.2f}% | {avg_moved:.1f} |")
    md.append("")
    md_path = out_dir / "summary.md"
    md_path.write_text("\n".join(md), encoding="utf-8")
    print(f"\n汇总: {md_path}")
    print("\n".join(md))


if __name__ == "__main__":
    main()
