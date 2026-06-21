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
DEFAULT_TEST_DATA = os.path.join(project_root, "data", "5_customers", "C101.csv")


def load_fleet_config():
    with open(CONFIG_DIR / "fleet_config.json", "r", encoding="utf-8") as f:
        return json.load(f)


def load_app_config():
    with open(CONFIG_DIR / "app_config.json", "r", encoding="utf-8") as f:
        return json.load(f)


def run_experiment(data_file: str, solver: str = "exact",
                   transshipment_candidate_mode: str = "all_sampled",
                   max_relocation_candidates=None,
                   transshipment_time_budget: float = 2.0,
                   transshipment_interval: int = 20,
                   alns_iter=None,
                   alns_time=None,
                   seed=None):
    print(f"[{time.strftime('%H:%M:%S')}] 开始测试 Model 3 "
          f"(取送货+横向中转 - 2E-VRP-PDP-TR)")
    print(f"数据集: {data_file}")
    print(f"求解器: {solver}")

    nodes_dict, _ = parse_dataset(data_file)
    fleet_config = load_fleet_config()
    app_config = load_app_config()
    alns_iter = app_config.get("alns_iter", 1000) if alns_iter is None else alns_iter
    alns_time = app_config.get("alns_time", 30) if alns_time is None else alns_time
    seed = app_config.get("seed", 42) if seed is None else seed

    problem = ProblemInstance(
        nodes_dict,
        fleet_config,
        speed=fleet_config.get("speed", 1.0),
    )
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
            max_iterations=alns_iter,
            max_time=alns_time,
            seed=seed,
            transshipment_candidate_mode=transshipment_candidate_mode,
            max_relocation_candidates=max_relocation_candidates,
            transshipment_time_budget=transshipment_time_budget,
            transshipment_interval=transshipment_interval,
        )

    run_time = time.time() - start_time
    msg = "最优解" if e1_routes else "未找到可行解"
    print(f"\n[{time.strftime('%H:%M:%S')}] 测试完成 | 耗时: {run_time:.2f}s | {msg}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Model 3 实验")
    parser.add_argument("data", nargs="?", type=str,
                        default=DEFAULT_TEST_DATA)
    parser.add_argument("--solver", "-s", type=str, choices=["exact", "alns"],
                        default="exact")
    parser.add_argument("--trans-mode", type=str,
                        choices=["nearest_k", "all_sampled", "all_exhaustive"],
                        default="all_sampled")
    parser.add_argument("--max-reloc", type=int, default=None,
                        help="nearest_k 模式下每个 pickup 的候选中转点数量")
    parser.add_argument("--trans-budget", type=float, default=2.0,
                        help="每次中转搜索的时间预算，单位秒")
    parser.add_argument("--trans-interval", type=int, default=20,
                        help="ALNS 每隔多少轮主动触发一次中转搜索")
    args = parser.parse_args()

    if os.path.exists(args.data):
        run_experiment(
            args.data, args.solver,
            transshipment_candidate_mode=args.trans_mode,
            max_relocation_candidates=args.max_reloc,
            transshipment_time_budget=args.trans_budget,
            transshipment_interval=args.trans_interval,
        )
    else:
        print(f"[错误] 找不到测试文件: {args.data}")
