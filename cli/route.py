# -*- coding: utf-8 -*-
"""
2E-VRPTW-PDD 求解引擎入口
配置驱动：选择 config/cases/ 下的案例清单后运行。
"""
import argparse
import io
import json
import time
from contextlib import redirect_stdout
from pathlib import Path

from configuration.loader import (
    PROJECT_ROOT,
    load_json,
    load_routing_case,
    resolve_project_path,
)
from models.routing.common.domain.instance import ProblemInstance
from models.routing.catalog import require_model_number
from models.routing.service import solve_routing
from models.routing.common.data_loader import parse_dataset
from models.routing.common.task_builder import build_tasks_for_model
from models.routing.common.solution_audit import audit_exact_solution
from reporting.route_plot import plot_vrp_solution


DEFAULT_CASE = PROJECT_ROOT / "config" / "cases" / "zhou_small_demo.json"
OUTPUT_DIR = PROJECT_ROOT / "output"


def load_config(case_path=DEFAULT_CASE):
    return load_routing_case(case_path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--case",
        default=str(DEFAULT_CASE),
        help="config/cases/ 下的案例配置文件",
    )
    parser.add_argument("--model", type=int, choices=range(1, 8))
    parser.add_argument("--solver", choices=("alns", "exact"))
    parser.add_argument("--exact-algorithm", choices=("default", "benders"),
                        help="精确算法；benders 当前支持 Model 7")
    parser.add_argument("--exact-time", type=float, help="精确求解总时间限制（秒）")
    parser.add_argument("--benders-subproblem-time", type=float,
                        help="Benders 每个分仓子问题的初始时间片（秒）")
    parser.add_argument("--data", help="覆盖案例中的数据文件")
    parser.add_argument("--fleet-config", help="覆盖案例中的车辆参数文件")
    parser.add_argument("--time", type=float, help="覆盖 ALNS 时间限制（秒）")
    parser.add_argument("--iterations", type=int, help="覆盖 ALNS 迭代次数")
    parser.add_argument("--seed", type=int, help="覆盖随机种子")
    args = parser.parse_args(argv)
    app_cfg, fleet_cfg = load_config(args.case)

    if args.model is not None:
        app_cfg["model"] = args.model
    if args.solver is not None:
        app_cfg["solver"] = args.solver
    if args.exact_algorithm is not None:
        app_cfg["exact_algorithm"] = args.exact_algorithm
    if args.exact_time is not None:
        app_cfg["exact_time"] = args.exact_time
    if args.benders_subproblem_time is not None:
        benders_options = dict(app_cfg.get("benders_options") or {})
        benders_options["subproblem_time_limit"] = args.benders_subproblem_time
        app_cfg["benders_options"] = benders_options
    if args.data is not None:
        app_cfg["data"] = args.data
    if args.time is not None:
        app_cfg["alns_time"] = args.time
    if args.iterations is not None:
        app_cfg["alns_iter"] = args.iterations
    if args.seed is not None:
        app_cfg["seed"] = args.seed
    if args.fleet_config is not None:
        fleet_cfg = load_json(args.fleet_config)

    model = require_model_number(app_cfg["model"])
    if model == 7:
        # Model 7 needs a meaningful relay phase even when the shared M6
        # configuration allocates almost all time to its first stage.
        stage1_time_ratio = app_cfg.get("model7_stage1_time_ratio", 0.75)
    data_file = app_cfg["data"]
    solver = app_cfg["solver"]
    exact_algorithm = app_cfg.get("exact_algorithm", "default")
    if exact_algorithm == "benders" and (model != 7 or solver != "exact"):
        parser.error("Benders 当前支持 --model 7 --solver exact")
    solver_tag = "exact_benders" if solver == "exact" and exact_algorithm == "benders" else solver
    alns_iter = app_cfg.get("alns_iter", 5000)
    alns_time = app_cfg.get("alns_time", 120)
    seed = app_cfg.get("seed", 42)
    stage1_time_ratio = app_cfg.get("stage1_time_ratio", 0.95)
    transshipment_min_net_saving = app_cfg.get(
        "transshipment_min_net_saving", 0.01)
    transshipment_max_total = app_cfg.get("transshipment_max_total")
    transshipment_max_per_satellite = app_cfg.get(
        "transshipment_max_per_satellite")

    data_path = resolve_project_path(data_file)
    if not data_path.exists():
        print(f"[错误] 找不到数据文件: {data_path.absolute()}")
        return

    data_stem = data_path.stem
    # Organized single-run output:
    # output/single_runs/model4_alns/C101/{routes,figures}/...
    sub_dir = OUTPUT_DIR / "single_runs" / f"model{model}_{solver_tag}" / data_stem
    routes_dir = sub_dir / "routes"
    figures_dir = sub_dir / "figures"
    routes_dir.mkdir(parents=True, exist_ok=True)
    figures_dir.mkdir(parents=True, exist_ok=True)

    run_prefix = f"run_model{model:02d}_{solver_tag}_{data_stem}"
    log_path = routes_dir / f"{run_prefix}_routes_detail.txt"
    img_path = figures_dir / f"{run_prefix}_solution_route_map.png"
    certificate_path = routes_dir / f"{run_prefix}_certificate.json"

    print("=" * 60)
    print("启动 2E-VRP 求解引擎")
    print(f"数据文件: {data_path.name}")
    print(f"激活模型: Model {model}")
    print(f"求解方法: {solver_tag.upper()}")
    print(f"输出目录: {sub_dir}")
    print("=" * 60)

    nodes_dict, _ = parse_dataset(str(data_path))

    problem = ProblemInstance(nodes_dict, fleet_cfg, speed=fleet_cfg.get("speed", 1.0))
    problem.aggregate_demands_from_pre_assignment()
    e1_tasks, e2_tasks_by_sat = build_tasks_for_model(problem, model)

    # 重定向 stdout：求解器详细输出写入日志文件，终端只显示简要信息
    log_buf = io.StringIO()

    start_time = time.time()
    with redirect_stdout(log_buf):
        solver_result = solve_routing(
            model=model,
            method=solver,
            problem=problem,
            e1_tasks=e1_tasks,
            e2_tasks_by_sat=e2_tasks_by_sat,
            alns_iter=alns_iter,
            alns_time=alns_time,
            seed=seed,
            stage1_time_ratio=stage1_time_ratio,
            transshipment_min_net_saving=transshipment_min_net_saving,
            transshipment_max_total=transshipment_max_total,
            transshipment_max_per_satellite=transshipment_max_per_satellite,
            TFp=app_cfg.get("TFp", 9999.0),
            exact_algorithm=exact_algorithm,
            exact_time=app_cfg.get("exact_time"),
            benders_options=app_cfg.get("benders_options"),
        )
    e1_routes, e2_routes_by_sat_res = solver_result[0], solver_result[1]
    exact_info = None
    exact_assignment = None
    route_audit = None
    if solver == "exact":
        if solver_result and isinstance(solver_result[-1], dict):
            exact_info = solver_result[-1]
        if model in (2, 4, 7) and len(solver_result) >= 4:
            exact_assignment = solver_result[2]
        if (
            model in (1, 2, 3, 4)
            and e1_routes is not None
            and e2_routes_by_sat_res is not None
        ):
            route_audit = audit_exact_solution(
                problem, model, e1_routes, e2_routes_by_sat_res,
                assignment=exact_assignment,
                objective=(exact_info or {}).get("objective"),
                tfp=app_cfg.get("TFp", 9999.0),
            )
    run_time = time.time() - start_time

    # 写入日志文件
    log_content = log_buf.getvalue()
    with open(log_path, "w", encoding="utf-8") as f:
        f.write(f"2E-VRP 求解结果 | Model {model} | {solver.upper()}\n")
        f.write(f"数据文件: {data_path.name}\n")
        f.write(f"求解耗时: {run_time:.2f} 秒\n")
        f.write("=" * 60 + "\n\n")
        f.write(log_content)

    if solver == "exact":
        exact_info = exact_info or {}
        certificate = {
            "model": model,
            "data_file": str(data_path),
            "status": exact_info.get("status"),
            "status_code": exact_info.get("status_code"),
            "solution_type": exact_info.get("solution_type"),
            "proven_optimal": bool(exact_info.get("proven_optimal")),
            "objective": exact_info.get("objective"),
            "best_bound": exact_info.get("best_bound"),
            "mip_gap": exact_info.get("mip_gap"),
            "runtime": exact_info.get("runtime"),
            "wall_time": run_time,
            "sol_count": exact_info.get("sol_count"),
            "proof_scope": exact_info.get("proof_scope"),
            "formulation": exact_info.get("formulation"),
            "assignment": exact_assignment,
            "route_audit": route_audit,
            "exact_algorithm": exact_algorithm,
        }
        if exact_algorithm == "benders":
            certificate["benders"] = {
                field: exact_info.get(field) for field in (
                    "iterations", "optimality_cuts", "feasibility_cuts",
                    "assignment_partitions_total", "assignment_partitions_evaluated",
                    "assignment_partition_retries", "master_runtime", "subproblem_runtime",
                    "master_statistics", "master_relaxation", "iteration_history", "absolute_tolerance",
                )
            }
            certificate["transshipments"] = exact_info.get("transshipments", [])
        with open(certificate_path, "w", encoding="utf-8") as f:
            json.dump(certificate, f, ensure_ascii=False, indent=2)

    # 终端简要输出
    exact_certified = bool(
        exact_info
        and exact_info.get("proven_optimal")
        and (route_audit is None or route_audit.get("valid"))
    )
    if solver == "exact" and exact_certified:
        print(
            f"[OK] 已证明全局最优 | 耗时: {run_time:.2f}s | "
            f"目标值: {exact_info.get('objective'):.6f} | "
            f"Gap: {exact_info.get('mip_gap'):.3%}"
        )
    elif solver == "exact":
        print(
            f"[FAILED] 精确证明未闭合 | 耗时: {run_time:.2f}s | "
            f"状态: {(exact_info or {}).get('status')} | "
            f"路线审计: {None if route_audit is None else route_audit.get('valid')}"
        )
    elif e1_routes:
        print(f"[OK] 求解成功 | 耗时: {run_time:.2f}s | E1 路线: {len(e1_routes)} 条")
    else:
        print(f"[FAILED] 未找到可行解 | 耗时: {run_time:.2f}s")

    print(f"[OK] 路径日志已保存: {log_path}")
    if solver == "exact":
        print(f"[OK] 精确证书已保存: {certificate_path}")

    # 绘制路径图
    if e1_routes and e2_routes_by_sat_res:
        plot_vrp_solution(
            problem=problem,
            e1_routes=e1_routes,
            e2_routes_by_sat=e2_routes_by_sat_res,
            title=f"2E-VRP Solution - Model {model} ({solver_tag.upper()}) - {data_stem}",
            save_dir=f"single_runs/model{model}_{solver_tag}/{data_stem}/figures",
            filename=img_path.name,
        )
    else:
        print("[提示] 求解器未返回有效路径，跳过绘图。")

    print(f"{'=' * 60}")
    print(f"所有输出已保存至: {sub_dir}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
