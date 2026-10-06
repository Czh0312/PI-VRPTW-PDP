# -*- coding: utf-8 -*-
"""
2E-VRPTW-PDD 求解引擎入口
配置驱动：选择 config/baselines/ 下的案例清单后运行。
"""
import argparse
import io
import json
import math
import time
import hashlib
import subprocess
from datetime import datetime
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
from models.routing.common.matrix_loader import load_square_matrix
from reporting.route_plot import plot_vrp_solution


DEFAULT_CASE = PROJECT_ROOT / "config" / "baselines" / "zhou_small_demo.json"
OUTPUT_DIR = PROJECT_ROOT / "output"


def load_config(case_path=DEFAULT_CASE):
    return load_routing_case(case_path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--case",
        default=str(DEFAULT_CASE),
        help="config/baselines/ 下的案例配置文件",
    )
    parser.add_argument("--model", type=int, choices=range(1, 5))
    parser.add_argument("--solver", choices=("alns", "exact"))
    parser.add_argument("--exact-time", type=float, help="精确求解总时间限制（秒）")
    parser.add_argument("--data", help="覆盖案例中的数据文件")
    parser.add_argument("--fleet-config", help="覆盖案例中的车辆参数文件")
    parser.add_argument("--time", type=float, help="覆盖 ALNS 时间限制（秒）")
    parser.add_argument("--iterations", type=int, help="覆盖 ALNS 迭代次数")
    parser.add_argument("--seed", type=int, help="覆盖随机种子")
    parser.add_argument("--day-end", type=float, help="覆盖单日案例的日终期限（分钟）")
    parser.add_argument("--exact-backend", choices=("commodity_flow", "indexed", "multibatch_indexed"))
    parser.add_argument("--orders", help="覆盖多批次案例中的独立订单文件")
    parser.add_argument("--output", help="多批次结果输出到新的或空目录")
    args = parser.parse_args(argv)
    app_cfg, fleet_cfg = load_config(args.case)

    if args.model is not None:
        app_cfg["model"] = args.model
    if args.solver is not None:
        app_cfg["solver"] = args.solver
    if args.exact_time is not None:
        app_cfg["exact_time"] = args.exact_time
    if args.data is not None:
        app_cfg["data"] = args.data
    if args.time is not None:
        app_cfg["alns_time"] = args.time
    if args.iterations is not None:
        app_cfg["alns_iter"] = args.iterations
    if args.seed is not None:
        app_cfg["seed"] = args.seed
    if args.fleet_config is not None:
        policy_config = fleet_cfg.get("operating_policy")
        fleet_cfg = load_json(args.fleet_config)
        if policy_config is not None:
            fleet_cfg["operating_policy"] = policy_config
    if args.day_end is not None:
        if (fleet_cfg.get('operating_policy') or {}).get('policy_version') == 'multi_batch_cutoff_v1':
            raise ValueError('--day-end is single-day only; edit the multibatch calendar/cycle_end explicitly')
        if not fleet_cfg.get("operating_policy"):
            raise ValueError("--day-end requires a case with operating_policy")
        fleet_cfg["operating_policy"] = {**fleet_cfg["operating_policy"], "day_end": args.day_end}

    if (fleet_cfg.get('operating_policy') or {}).get('policy_version') == 'multi_batch_cutoff_v1':
        if args.orders:
            app_cfg['orders'] = args.orders
        if args.exact_backend:
            app_cfg['exact_backend'] = args.exact_backend
        from cli.multibatch import run_case
        return run_case(app_cfg, fleet_cfg, case_path=args.case, output=args.output)
    if args.orders or args.output:
        raise ValueError('--orders/--output currently require a multibatch case')

    model = require_model_number(app_cfg["model"])
    data_file = app_cfg["data"]
    solver = app_cfg["solver"]
    alns_iter = app_cfg.get("alns_iter", 5000)
    alns_time = app_cfg.get("alns_time", 120)
    seed = app_cfg.get("seed", 42)
    data_path = resolve_project_path(data_file)
    if not data_path.exists():
        print(f"[错误] 找不到数据文件: {data_path.absolute()}")
        return

    nodes_dict, _ = parse_dataset(str(data_path))
    distance_path = resolve_project_path(app_cfg["distance_matrix"]) if app_cfg.get("distance_matrix") else None
    duration_path = resolve_project_path(app_cfg["duration_matrix"]) if app_cfg.get("duration_matrix") else None
    if duration_path and not distance_path:
        raise ValueError("duration_matrix requires distance_matrix")
    problem = ProblemInstance(
        nodes_dict, fleet_cfg, speed=fleet_cfg.get("speed", 1.0),
        distance_matrix=load_square_matrix(distance_path) if distance_path else None,
        travel_time_matrix=load_square_matrix(duration_path) if duration_path else None)
    problem.operating_policy.require_model(model)
    problem.aggregate_demands_from_pre_assignment()
    e1_tasks, e2_tasks_by_sat = build_tasks_for_model(problem, model)

    data_stem = data_path.stem
    # Organized single-run output:
    # output/single_runs/model4_alns/C101/{routes,figures}/...
    model_folder = f"model{model}_{solver}"
    sub_dir = OUTPUT_DIR / "baselines" / model_folder / data_stem
    if problem.operating_policy.single_day:
        model_folder = f"single_day_backhaul_v1/model{model}_{solver}"
        data_stem = f"{data_stem}/{datetime.now().strftime('%Y%m%dT%H%M%S_%f')}_seed{seed}"
        sub_dir = OUTPUT_DIR / "baselines" / model_folder / data_stem
    routes_dir = sub_dir / "routes"
    figures_dir = sub_dir / "figures"
    routes_dir.mkdir(parents=True, exist_ok=True)
    figures_dir.mkdir(parents=True, exist_ok=True)

    run_prefix = f"run_model{model:02d}_{solver}_{data_path.stem}"
    log_path = routes_dir / f"{run_prefix}_routes_detail.txt"
    img_path = figures_dir / f"{run_prefix}_solution_route_map.png"
    certificate_path = routes_dir / f"{run_prefix}_certificate.json"

    print("=" * 60)
    print("历史基线：不代表新版单批次对照")
    print(f"数据文件: {data_path.name}")
    print(f"激活模型: Model {model}")
    print(f"求解方法: {solver.upper()}")
    print(f"输出目录: {sub_dir}")
    print("=" * 60)

    # 重定向 stdout：求解器详细输出写入日志文件，终端只显示简要信息
    log_buf = io.StringIO()

    start_time = time.time()
    with redirect_stdout(log_buf):
        solver_result = solve_routing(
            model=model, allow_baseline=True,
            method=solver,
            problem=problem,
            e1_tasks=e1_tasks,
            e2_tasks_by_sat=e2_tasks_by_sat,
            alns_iter=alns_iter,
            alns_time=alns_time,
            seed=seed,
            TFp=app_cfg.get("TFp", 9999.0),
            exact_time=app_cfg.get("exact_time", app_cfg.get("time_limit_seconds", 300)),
            exact_backend=args.exact_backend or app_cfg.get("exact_backend"),
        )
    e1_routes, e2_routes_by_sat_res = solver_result[0], solver_result[1]
    exact_info = None
    exact_assignment = None
    route_audit = None
    if solver == "exact":
        if solver_result and isinstance(solver_result[-1], dict):
            exact_info = solver_result[-1]
        if model in (2, 4) and len(solver_result) >= 4:
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
    if problem.operating_policy.single_day and e1_routes is not None and e2_routes_by_sat_res is not None:
        route_audit = audit_exact_solution(
            problem, model, e1_routes, e2_routes_by_sat_res,
            objective=(solver_result[-1].get("objective") if isinstance(solver_result[-1], dict) else None),
            tfp=app_cfg.get("TFp", 9999.0))
    run_time = time.time() - start_time

    if problem.operating_policy.single_day:
        info = solver_result[-1] if isinstance(solver_result[-1], dict) else {}
        files = [resolve_project_path(args.case), data_path,
                 resolve_project_path(args.fleet_config or app_cfg["routing_config"])]
        files.extend(p for p in (distance_path, duration_path) if p)
        if isinstance(app_cfg.get("operating_policy"), str):
            files.append(resolve_project_path(app_cfg["operating_policy"]))
        revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT,
                                  capture_output=True, text=True, check=False).stdout.strip()
        result_path = sub_dir / "result.json"
        artifact = dict(model=model, solver=solver,
                        exact_backend=(args.exact_backend or app_cfg.get("exact_backend", "commodity_flow")) if solver == 'exact' else None,
                        operating_policy=problem.operating_policy.as_dict(problem),
                        seed=seed, runtime=run_time, run_config=app_cfg, fleet_config=fleet_cfg,
                        git_revision=revision,
                        input_sha256={str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
                        e1_routes=e1_routes, e2_routes=e2_routes_by_sat_res,
                        info=info, route_audit=route_audit,
                        valid=bool(route_audit and route_audit['valid']))
        result_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f"单日路线与时间表: {result_path}")

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
            "operating_policy": problem.operating_policy.as_dict(problem),
        }
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
    elif (solver == "exact" and e1_routes is not None
          and e2_routes_by_sat_res is not None
          and (exact_info or {}).get("objective") is not None
          and (route_audit is None or route_audit.get("valid"))):
        bound = exact_info.get("best_bound")
        gap = exact_info.get("mip_gap")
        bound_text = f"{bound:.6f}" if bound is not None and math.isfinite(bound) else "未知"
        gap_text = f"{gap:.3%}" if gap is not None and math.isfinite(gap) else "未知"
        print(
            f"[FEASIBLE] 已找到可行解，最优证明未闭合 | 耗时: {run_time:.2f}s | "
            f"目标值: {exact_info['objective']:.6f} | 下界: {bound_text} | Gap: {gap_text}"
        )
    elif solver == "exact":
        print(
            f"[FAILED] 未返回有效可行解 | 耗时: {run_time:.2f}s | "
            f"状态: {(exact_info or {}).get('status')} | "
            f"路线审计: {None if route_audit is None else route_audit.get('valid')}"
        )
    elif e1_routes and (route_audit is None or route_audit.get('valid')):
        print(f"[OK] 求解成功 | 耗时: {run_time:.2f}s | E1 路线: {len(e1_routes)} 条")
    else:
        print(f"[FAILED] 未找到可行解 | 耗时: {run_time:.2f}s")

    print(f"[OK] 路径日志已保存: {log_path}")
    if solver == "exact":
        print(f"[OK] 精确证书已保存: {certificate_path}")

    # 绘制路径图
    if e1_routes and e2_routes_by_sat_res and (route_audit is None or route_audit.get('valid')):
        plot_vrp_solution(
            problem=problem,
            e1_routes=e1_routes,
            e2_routes_by_sat=e2_routes_by_sat_res,
            title=f"2E-VRP Solution - Model {model} ({solver.upper()}) - {data_stem}",
            save_dir=f"baselines/{model_folder}/{data_stem}/figures",
            filename=img_path.name,
        )
    else:
        print("[提示] 求解器未返回有效路径，跳过绘图。")

    print(f"{'=' * 60}")
    print(f"所有输出已保存至: {sub_dir}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
