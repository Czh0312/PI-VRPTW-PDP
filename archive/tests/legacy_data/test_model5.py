# experiments/test_model5.py
# -*- coding: utf-8 -*-
import sys
import os
import json
import time
from pathlib import Path

project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.append(project_root)

from models.routing.common.domain.instance import ProblemInstance
from models.routing.common.data_loader import parse_dataset
from models.routing.common.task_builder import build_tasks_for_model
from models.routing.variants.model_5.solver import solve_exact
from models.routing.variants.model_5.alns_solver import solve_alns
from configuration.loader import load_routing_case

CONFIG_DIR = Path(project_root) / "config"
DEFAULT_TEST_DATA = os.path.join(project_root, "data", "5_customers", "C101.csv")


def load_fleet_config():
    with open(CONFIG_DIR / "routing" / "validation" / "zhou_small.json", "r", encoding="utf-8") as f:
        return json.load(f)


def load_app_config():
    app, _ = load_routing_case(CONFIG_DIR / "cases" / "zhou_small_demo.json")
    return app


def run_experiment(data_file: str, solver: str = "exact",
                   transshipment_candidate_mode: str = "all_sampled",
                   max_relocation_candidates=None,
                   max_transshipments=None,
                   relay_fixes=None,
                   handoff_fixes=None,
                   objective_cutoff=None,
                   exact_time: float = 300.0,
                   transshipment_time_budget: float = 2.0,
                   transshipment_interval: int = 20,
                   alns_iter=None,
                   alns_time=None,
                   seed=None):
    print(f"[{time.strftime('%H:%M:%S')}] 开始测试 Model 5 "
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

    e1_tasks, e2_tasks_by_sat = build_tasks_for_model(problem, 5)

    start_time = time.time()

    if solver == "exact":
        e1_routes, e2_routes, info = solve_exact(
            problem=problem,
            e1_tasks=e1_tasks,
            e2_tasks_by_sat=e2_tasks_by_sat,
            vehicles_per_satellite=problem.fleet.e2_per_sat_count,
            verbose=True,
            max_relocation_candidates=max_relocation_candidates,
            max_transshipments=max_transshipments,
            relay_fixes=relay_fixes,
            handoff_fixes=handoff_fixes,
            objective_cutoff=objective_cutoff,
            time_limit=exact_time,
            return_incumbent_on_time_limit=True,
            return_info=True,
        )
        print(
            "\n精确状态: "
            f"{info['status']} | proven_optimal={info['proven_optimal']} | "
            f"objective={info['objective']} | bound={info['best_bound']} | "
            f"gap={info['mip_gap']} | relays={info['transshipment_count']}"
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
    if solver == "exact" and e1_routes:
        if info["status"] == "OPTIMAL":
            msg = (
                "规范M5已证明最优" if info["proven_optimal"]
                else "受限模型已证明最优（非规范M5证明）"
            )
        else:
            msg = "限时可行解（未证明）"
    else:
        msg = "可行解" if e1_routes else "未找到可行解"
    print(f"\n[{time.strftime('%H:%M:%S')}] 测试完成 | 耗时: {run_time:.2f}s | {msg}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Model 5 实验")
    parser.add_argument("data", nargs="?", type=str,
                        default=DEFAULT_TEST_DATA)
    parser.add_argument("--solver", "-s", type=str, choices=["exact", "alns"],
                        default="exact")
    parser.add_argument("--trans-mode", type=str,
                        choices=["nearest_k", "all_sampled", "all_exhaustive"],
                        default="all_sampled")
    parser.add_argument("--max-reloc", type=int, default=None,
                        help="每个 pickup 的交接点候选数；exact 默认 None 表示同仓全候选")
    parser.add_argument("--max-total", type=int, default=None,
                        help="全局接力数上限；0 用于验证 M5 退化为 M3")
    parser.add_argument("--force-relay", action="append", default=[],
                        help="强制指定 pickup 使用接力；可重复")
    parser.add_argument("--forbid-relay", action="append", default=[],
                        help="禁止指定 pickup 使用接力；可重复")
    parser.add_argument("--force-handoff", action="append", default=[],
                        metavar="PICKUP=CUSTOMER",
                        help="强制具体交接点，例如 C1p=C2；可重复")
    parser.add_argument("--cutoff", type=float, default=None,
                        help="只搜索严格优于该目标阈值的方案")
    parser.add_argument("--exact-time", type=float, default=300.0,
                        help="exact 求解时间上限，单位秒")
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
            max_transshipments=args.max_total,
            relay_fixes={
                **{task: 0 for task in args.forbid_relay},
                **{task: 1 for task in args.force_relay},
            },
            handoff_fixes={
                spec.split("=", 1)[0]: spec.split("=", 1)[1]
                for spec in args.force_handoff if "=" in spec
            },
            objective_cutoff=args.cutoff,
            exact_time=args.exact_time,
            transshipment_time_budget=args.trans_budget,
            transshipment_interval=args.trans_interval,
        )
    else:
        print(f"[错误] 找不到测试文件: {args.data}")
