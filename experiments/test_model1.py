# experiments/test_model1.py
# -*- coding: utf-8 -*-
import sys
import os
import time
from pathlib import Path

# 动态将项目根目录加入系统路径
project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.append(project_root)

from core.instance import ProblemInstance
from core.node import Task
from models.model_1.solver import solve_exact
from utils.data_loader import parse_dataset

def run_experiment(data_file: str):
    print(f"[{time.strftime('%H:%M:%S')}] 开始测试 Model 1 (纯配送 - 2E-VRP)")
    print(f"数据集: {data_file}")

    # 1. 加载数据与初始化
    nodes_dict, fleet_config = parse_dataset(data_file)
    # 强制覆盖部分车队参数（可按需修改）
    fleet_config["vehicles_per_satellite"] = 6

    problem = ProblemInstance(nodes_dict, fleet_config)
    problem.aggregate_demands_from_pre_assignment()

    # 2. 构建纯配送任务 (剔除回收任务)
    e1_tasks = []
    for s in problem.satellites:
        if s.delivery_weight > 1e-6:
            # E1 只需要把货物送到前置仓
            e1_tasks.append(
                Task(
                    f"{s.id}d",
                    s.id,
                    "d",
                    s.delivery_weight,
                    s.delivery_volume,
                    (0.0, s.e1_delivery_due_date),
                )
            )

    e2_tasks_by_sat = {s.id: [] for s in problem.satellites}
    for c in problem.customers:
        if c.assigned_sat and c.demand > 1e-6:
            e2_tasks_by_sat[c.assigned_sat].append(
                Task(f"{c.id}d", c.id, "d", c.demand, c.vol_del, c.tw_del)
            )

    # 3. 调用求解器
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
    # 默认测试路径，支持通过命令行传入路径
    test_data = (
        sys.argv[1]
        if len(sys.argv) > 1
        else os.path.join(project_root, "data", "small", "test2.csv")
    )
    if os.path.exists(test_data):
        run_experiment(test_data)
    else:
        print(f"[错误] 找不到测试文件: {test_data}")