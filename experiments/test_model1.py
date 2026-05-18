# experiments/test_model1.py
# -*- coding: utf-8 -*-
import sys
import os
import time
from pathlib import Path

project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.append(project_root)

from core.instance import ProblemInstance
from utils.data_loader import parse_dataset
from utils.task_builder import build_tasks_for_model
from models.model_1.solver import solve_exact

def run_experiment(data_file: str):
    print(f"[{time.strftime('%H:%M:%S')}] 开始测试 Model 1 (纯配送 - 2E-VRP)")
    print(f"数据集: {data_file}")

    nodes_dict, fleet_config = parse_dataset(data_file)
    fleet_config["vehicles_per_satellite"] = fleet_config.get("vehicles_per_satellite", 6)

    problem = ProblemInstance(nodes_dict, fleet_config)
    problem.aggregate_demands_from_pre_assignment()

    e1_tasks, e2_tasks_by_sat = build_tasks_for_model(problem, 1)

    start_time = time.time()

    e1_routes, e2_routes = solve_exact(
        problem=problem,
        e1_tasks=e1_tasks,
        e2_tasks_by_sat=e2_tasks_by_sat,
        vehicles_per_satellite=problem.fleet.e2_per_sat_count,
        verbose=True,
    )

    run_time = time.time() - start_time
    msg = "最优解" if e1_routes else "未找到可行解"
    print(f"\n[{time.strftime('%H:%M:%S')}] 测试完成 | 耗时: {run_time:.2f}s | {msg}")

if __name__ == "__main__":
    test_data = (
        sys.argv[1]
        if len(sys.argv) > 1
        else os.path.join(project_root, "data", "small", "test2.csv")
    )
    if os.path.exists(test_data):
        run_experiment(test_data)
    else:
        print(f"[错误] 找不到测试文件: {test_data}")
