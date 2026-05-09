# main.py
# -*- coding: utf-8 -*-
import argparse
import time
from pathlib import Path

from core.instance import ProblemInstance
from core.node import Task
from models.model_1.solver import solve_exact as solve_m1
from models.model_2.solver import solve_exact as solve_m2
from models.model_3.solver import solve_exact as solve_m3
from utils.data_loader import parse_dataset
# 1. 导入绘图函数
from utils.plot import plot_vrp_solution


def build_tasks_for_model(problem: ProblemInstance, model_type: int):
    # ... (保持你原有的逻辑不变) ...
    e1_tasks = []
    e2_tasks_by_sat = {s.id: [] for s in problem.satellites}

    if model_type == 1:
        for s in problem.satellites:
            if s.delivery_volume > 1e-9:
                e1_tasks.append(
                    Task(f"{s.id}d", s.id, "d", s.delivery_weight, s.delivery_volume, (0.0, s.e1_delivery_due_date))
                )
        for c in problem.customers:
            if c.assigned_sat and c.demand > 1e-9:
                e2_tasks_by_sat[c.assigned_sat].append(
                    Task(f"{c.id}d", c.id, "d", c.demand, c.vol_del, c.tw_del)
                )
    elif model_type in (2, 3):
        for s in problem.satellites:
            if s.delivery_volume > 1e-9:
                e1_tasks.append(
                    Task(f"{s.id}d", s.id, "d", s.delivery_weight, s.delivery_volume, (0.0, s.e1_delivery_due_date))
                )
            if s.pickup_volume > 1e-9 or s.pickup_weight > 1e-9:
                e1_tasks.append(
                    Task(f"{s.id}p", s.id, "p", max(s.pickup_weight, 0.0), max(s.pickup_volume, 0.0),
                         (s.e1_pickup_ready_time, float('inf')))
                )
        for c in problem.customers:
            if not c.assigned_sat:
                continue
            if c.demand > 1e-9 or c.vol_del > 1e-9:
                e2_tasks_by_sat[c.assigned_sat].append(
                    Task(f"{c.id}d", c.id, "d", c.demand, c.vol_del, c.tw_del)
                )
            if c.weight_pick > 1e-9 or c.vol_pick > 1e-9:
                e2_tasks_by_sat[c.assigned_sat].append(
                    Task(f"{c.id}p", c.id, "p", c.weight_pick, c.vol_pick, c.tw_pick)
                )
    else:
        raise ValueError(f"当前仅支持 model 1/2/3，收到: {model_type}")

    return e1_tasks, e2_tasks_by_sat


def main():
    parser = argparse.ArgumentParser(description="2E-VRP 递进数学模型总控台")
    parser.add_argument("-m", "--model", type=int, choices=[1, 2, 3], default=2)
    parser.add_argument("-d", "--data", type=str, default="data/small/test2.csv")
    parser.add_argument("-v", "--vehicles", type=int, default=6)
    args = parser.parse_args()

    data_path = Path(args.data)
    if not data_path.exists():
        print(f"[错误] 找不到数据文件: {data_path.absolute()}")
        return

    print("=" * 60)
    print("启动 2E-VRP 求解引擎")
    print(f"数据文件: {data_path.name}")
    print(f"激活模型: Model {args.model}")
    print("=" * 60)

    nodes_dict, fleet_config = parse_dataset(str(data_path))
    fleet_config["vehicles_per_satellite"] = args.vehicles

    problem = ProblemInstance(nodes_dict, fleet_config)
    problem.aggregate_demands_from_pre_assignment()
    e1_tasks, e2_tasks_by_sat = build_tasks_for_model(problem, args.model)

    start_time = time.time()
    
    # 2. 捕获求解器返回的路径数据
    if args.model == 1:
        e1_routes, e2_routes_by_sat_res = solve_m1(
            problem, e1_tasks, e2_tasks_by_sat, problem.fleet.e2_per_sat_count, verbose=True
        )
    elif args.model == 2:
        e1_routes, e2_routes_by_sat_res = solve_m2(
            problem, e1_tasks, e2_tasks_by_sat, problem.fleet.e2_per_sat_count, TFp=9999.0, verbose=True
        )
    else:
        e1_routes, e2_routes_by_sat_res = solve_m3(
            problem, e1_tasks, e2_tasks_by_sat, problem.fleet.e2_per_sat_count, verbose=True
        )

    run_time = time.time() - start_time
    print(f"\n总耗时: {run_time:.2f} 秒")

    # 3. 调用绘图功能
    if e1_routes and e2_routes_by_sat_res:
        print("正在生成路径图...")
        plot_vrp_solution(
            problem=problem, 
            e1_routes=e1_routes, 
            e2_routes_by_sat=e2_routes_by_sat_res, 
            title=f"2E-VRP Solution - Model {args.model}"
        )
    else:
        print("[提示] 求解器未返回有效路径，跳过绘图。")


if __name__ == "__main__":
    main()