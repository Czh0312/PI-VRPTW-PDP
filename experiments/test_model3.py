# experiments/test_model3.py
# -*- coding: utf-8 -*-
import sys
import os
import time
from pathlib import Path

project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.append(project_root)

from core.instance import ProblemInstance
from core.node import Task
from utils.data_loader import parse_dataset
from models.model_3.solver import solve_exact
from models.model_3.alns_solver import solve_alns


def run_experiment(data_file: str, solver: str = "exact"):
    print(f"[{time.strftime('%H:%M:%S')}] 开始测试 Model 3 "
          f"(取送货+横向中转 - 2E-VRP-PDP-TR)")
    print(f"数据集: {data_file}")
    print(f"求解器: {solver}")

    nodes_dict, fleet_config = parse_dataset(data_file)
    fleet_config["vehicles_per_satellite"] = 2

    problem = ProblemInstance(nodes_dict, fleet_config)
    problem.aggregate_demands_from_pre_assignment()

    # 构建 E1 任务 (配送 + 回收)
    e1_tasks = []
    for s in problem.satellites:
        if s.delivery_volume > 1e-9:
            e1_tasks.append(Task(
                f"{s.id}d", s.id, 'd', s.delivery_weight,
                s.delivery_volume, (0.0, s.e1_delivery_due_date)))
        if s.pickup_volume > 1e-9 or s.pickup_weight > 1e-9:
            e1_tasks.append(Task(
                f"{s.id}p", s.id, 'p', max(s.pickup_weight, 0.0),
                max(s.pickup_volume, 0.0),
                (s.e1_pickup_ready_time, float('inf'))))

    # 构建 E2 任务 (配送 + 回收)
    e2_tasks_by_sat = {s.id: [] for s in problem.satellites}
    cw = problem.fleet.container_weight
    cv = problem.fleet.container_volume
    for c in problem.customers:
        if not c.assigned_sat:
            continue
        if c.demand > 1e-9:
            e2_tasks_by_sat[c.assigned_sat].append(
                Task(f"{c.id}d", c.id, 'd', c.demand + cw, cv, c.tw_del))
        if cw > 1e-9:
            e2_tasks_by_sat[c.assigned_sat].append(
                Task(f"{c.id}p", c.id, 'p', cw, cv, c.tw_pick))

    start_time = time.time()

    if solver == "exact":
        e1_routes, e2_routes = solve_exact(
            problem=problem,
            e1_tasks=e1_tasks,
            e2_tasks_by_sat=e2_tasks_by_sat,
            vehicles_per_satellite=problem.fleet.e2_per_sat_count,
            verbose=True
        )
    else:
        e1_routes, e2_routes = solve_alns(
            problem=problem,
            e1_tasks=e1_tasks,
            e2_tasks_by_sat=e2_tasks_by_sat,
            vehicles_per_satellite=problem.fleet.e2_per_sat_count,
            max_iterations=5000, max_time=120, seed=42
        )

    run_time = time.time() - start_time
    msg = "最优解" if e1_routes else "未找到可行解"
    print(f"\n[{time.strftime('%H:%M:%S')}] 测试完成 | 耗时: {run_time:.2f}s | {msg}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Model 3 实验")
    parser.add_argument("data", nargs="?", type=str,
                        default=os.path.join(project_root, "data", "small", "test2.csv"))
    parser.add_argument("--solver", "-s", type=str, choices=["exact", "alns"],
                        default="exact")
    args = parser.parse_args()

    if os.path.exists(args.data):
        run_experiment(args.data, args.solver)
    else:
        print(f"[错误] 找不到测试文件: {args.data}")
