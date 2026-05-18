# experiments/test_model3.py
# -*- coding: utf-8 -*-
import sys
import os
import json
import time
from pathlib import Path

project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.append(project_root)

from core.instance import ProblemInstance
from utils.data_loader import parse_dataset
from utils.task_builder import build_tasks_for_model
from models.model_3.solver import solve_exact
from models.model_3.alns_solver import solve_alns

CONFIG_DIR = Path(project_root) / "config"


def load_fleet_config():
    with open(CONFIG_DIR / "fleet_config.json", "r", encoding="utf-8") as f:
        return json.load(f)


def run_experiment(data_file: str, solver: str = "exact"):
    print(f"[{time.strftime('%H:%M:%S')}] 开始测试 Model 3 "
          f"(取送货+横向中转 - 2E-VRP-PDP-TR)")
    print(f"数据集: {data_file}")
    print(f"求解器: {solver}")

    nodes_dict, _ = parse_dataset(data_file)
    fleet_config = load_fleet_config()

    problem = ProblemInstance(nodes_dict, fleet_config)
    problem.aggregate_demands_from_pre_assignment()

    e1_tasks, e2_tasks_by_sat = build_tasks_for_model(problem, 3)

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
