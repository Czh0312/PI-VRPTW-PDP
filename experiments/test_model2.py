# experiments/test_model2.py
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
from models.model_2.solver import solve_exact

def run_experiment(data_file: str):
    print(f"[{time.strftime('%H:%M:%S')}] 开始测试 Model 2 (统一取送货 - 2E-VRP-PDP)")
    print(f"数据集: {data_file}")
    
    nodes_dict, fleet_config = parse_dataset(data_file)
    fleet_config["vehicles_per_satellite"] = 6
    
    problem = ProblemInstance(nodes_dict, fleet_config)
    problem.aggregate_demands_from_pre_assignment()
    
    # 2. 构建 E1 取送货任务
    e1_tasks = []
    for s in problem.satellites:
        if s.delivery_volume > 1e-9:
            e1_tasks.append(Task(f"{s.id}d", s.id, 'd', s.delivery_weight, s.delivery_volume, (0.0, s.e1_delivery_due_date)))
        if s.pickup_volume > 1e-9:
            # E1 取空箱的时间通常设置在很晚
            e1_tasks.append(Task(f"{s.id}p", s.id, 'p', 0.0, s.pickup_volume, (s.e1_pickup_ready_time, float('inf'))))

    # 3. 构建 E2 取送货任务
    e2_tasks_by_sat = {s.id: [] for s in problem.satellites}
    for c in problem.customers:
        if not c.assigned_sat: continue
        
        if c.demand > 1e-9 or c.vol_del > 1e-9:
            e2_tasks_by_sat[c.assigned_sat].append(
                Task(f"{c.id}d", c.id, 'd', c.demand, c.vol_del, c.tw_del)
            )
        if c.vol_pick > 1e-9:
            e2_tasks_by_sat[c.assigned_sat].append(
                Task(f"{c.id}p", c.id, 'p', 0.0, c.vol_pick, c.tw_pick)
            )
            
    # 4. 调用求解器
    start_time = time.time()
    
    obj_val = solve_exact(
        problem=problem, 
        e1_tasks=e1_tasks, 
        e2_tasks_by_sat=e2_tasks_by_sat, 
        vehicles_per_satellite=problem.fleet.e2_per_sat_count,
        verbose=True
    )
    
    run_time = time.time() - start_time
    print(f"\n[{time.strftime('%H:%M:%S')}] 测试完成 | 耗时: {run_time:.2f}s | 目标值: {obj_val if obj_val else '未找到最优解'}")

if __name__ == "__main__":
    test_data = sys.argv[1] if len(sys.argv) > 1 else os.path.join(project_root, "data", "C101_C10x.txt")
    if os.path.exists(test_data):
        run_experiment(test_data)
    else:
        print(f"[错误] 找不到测试文件: {test_data}")