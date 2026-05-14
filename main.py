# main.py
# -*- coding: utf-8 -*-
"""
2E-VRPTW-PDD 求解引擎入口
配置驱动：修改 config/app_config.json 和 config/fleet_config.json 后运行
"""
import json
import time
from pathlib import Path

from core.instance import ProblemInstance
from models.model_1.solver import solve_exact as solve_m1
from models.model_2.solver import solve_exact as solve_m2
from models.model_3.solver import solve_exact as solve_m3
from models.model_1.alns_solver import solve_alns as alns_m1
from models.model_2.alns_solver import solve_alns as alns_m2
from models.model_3.alns_solver import solve_alns as alns_m3
from utils.data_loader import parse_dataset
from utils.task_builder import build_tasks_for_model
from utils.plot import plot_vrp_solution


CONFIG_DIR = Path(__file__).resolve().parent / "config"


def load_config():
    with open(CONFIG_DIR / "app_config.json", "r", encoding="utf-8") as f:
        app = json.load(f)
    with open(CONFIG_DIR / "fleet_config.json", "r", encoding="utf-8") as f:
        fleet = json.load(f)
    return app, fleet


def main():
    app_cfg, fleet_cfg = load_config()

    model = app_cfg["model"]
    data_file = app_cfg["data"]
    solver = app_cfg["solver"]
    alns_iter = app_cfg.get("alns_iter", 5000)
    alns_time = app_cfg.get("alns_time", 120)
    seed = app_cfg.get("seed", 42)

    data_path = Path(data_file)
    if not data_path.exists():
        print(f"[错误] 找不到数据文件: {data_path.absolute()}")
        return

    print("=" * 60)
    print("启动 2E-VRP 求解引擎")
    print(f"配置文件: app_config.json / fleet_config.json")
    print(f"数据文件: {data_path.name}")
    print(f"激活模型: Model {model}")
    print(f"求解方法: {solver.upper()}")
    print("=" * 60)

    nodes_dict, _ = parse_dataset(str(data_path))

    problem = ProblemInstance(nodes_dict, fleet_cfg)
    problem.aggregate_demands_from_pre_assignment()
    e1_tasks, e2_tasks_by_sat = build_tasks_for_model(problem, model)

    start_time = time.time()

    if solver == "exact":
        if model == 1:
            e1_routes, e2_routes_by_sat_res = solve_m1(
                problem, e1_tasks, e2_tasks_by_sat, problem.fleet.e2_per_sat_count, verbose=True
            )
        elif model == 2:
            e1_routes, e2_routes_by_sat_res = solve_m2(
                problem, e1_tasks, e2_tasks_by_sat, problem.fleet.e2_per_sat_count, TFp=9999.0, verbose=True
            )
        else:
            e1_routes, e2_routes_by_sat_res = solve_m3(
                problem, e1_tasks, e2_tasks_by_sat, problem.fleet.e2_per_sat_count, verbose=True
            )
    else:
        if model == 1:
            e1_routes, e2_routes_by_sat_res = alns_m1(
                problem, e1_tasks, e2_tasks_by_sat, problem.fleet.e2_per_sat_count,
                max_iterations=alns_iter, max_time=alns_time, seed=seed
            )
        elif model == 2:
            e1_routes, e2_routes_by_sat_res = alns_m2(
                problem, e1_tasks, e2_tasks_by_sat, problem.fleet.e2_per_sat_count,
                TFp=9999.0, max_iterations=alns_iter, max_time=alns_time, seed=seed
            )
        else:
            e1_routes, e2_routes_by_sat_res = alns_m3(
                problem, e1_tasks, e2_tasks_by_sat, problem.fleet.e2_per_sat_count,
                max_iterations=alns_iter, max_time=alns_time, seed=seed
            )

    run_time = time.time() - start_time
    print(f"\n总耗时: {run_time:.2f} 秒")

    if e1_routes and e2_routes_by_sat_res:
        print("正在生成路径图...")
        plot_vrp_solution(
            problem=problem,
            e1_routes=e1_routes,
            e2_routes_by_sat=e2_routes_by_sat_res,
            title=f"2E-VRP Solution - Model {model} ({solver.upper()})"
        )
    else:
        print("[提示] 求解器未返回有效路径，跳过绘图。")


if __name__ == "__main__":
    main()
