# main.py
# -*- coding: utf-8 -*-
"""
2E-VRPTW-PDD 求解引擎入口
配置驱动：修改 config/app_config.json 和 config/fleet_config.json 后运行
"""
import io
import json
import time
from contextlib import redirect_stdout
from pathlib import Path

from core.instance import ProblemInstance
from models.model_1.solver import solve_exact as solve_m1
from models.model_2.solver import solve_exact as solve_m2
from models.model_3.solver import solve_exact as solve_m3
from models.model_4.solver import solve_exact as solve_m4
from models.model_1.alns_solver import solve_alns as alns_m1
from models.model_2.alns_solver import solve_alns as alns_m2
from models.model_3.alns_solver import solve_alns as alns_m3
from models.model_4.alns_solver import solve_alns as alns_m4
from utils.data_loader import parse_dataset
from utils.task_builder import build_tasks_for_model
from utils.plot import plot_vrp_solution


CONFIG_DIR = Path(__file__).resolve().parent / "config"
OUTPUT_DIR = Path(__file__).resolve().parent / "output"


def load_config():
    with open(CONFIG_DIR / "app_config.json", "r", encoding="utf-8") as f:
        app = json.load(f)
    with open(CONFIG_DIR / "fleet_config.json", "r", encoding="utf-8") as f:
        fleet = json.load(f)
    return app, fleet


def _run_solver(model, solver, problem, e1_tasks, e2_tasks_by_sat,
                alns_iter, alns_time, seed, TFp=9999.0):
    if solver == "exact":
        solvers = {1: solve_m1, 2: solve_m2, 3: solve_m3, 4: solve_m4}
        kwargs = dict(
            problem=problem,
            e1_tasks=e1_tasks,
            e2_tasks_by_sat=e2_tasks_by_sat,
            vehicles_per_satellite=problem.fleet.e2_per_sat_count,
            verbose=True,
        )
        if model in (2, 4):
            kwargs["TFp"] = TFp
        return solvers[model](**kwargs)
    else:
        alns_solvers = {1: alns_m1, 2: alns_m2, 3: alns_m3, 4: alns_m4}
        kwargs = dict(
            problem=problem,
            e1_tasks=e1_tasks,
            e2_tasks_by_sat=e2_tasks_by_sat,
            vehicles_per_satellite=problem.fleet.e2_per_sat_count,
            max_iterations=alns_iter,
            max_time=alns_time,
            seed=seed,
        )
        if model in (2, 4):
            kwargs["TFp"] = TFp
        return alns_solvers[model](**kwargs)


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

    # 输出子目录: output/model{X}_{solver}/
    sub_dir = OUTPUT_DIR / f"model{model}_{solver}"
    sub_dir.mkdir(parents=True, exist_ok=True)

    data_stem = data_path.stem
    log_path = sub_dir / f"{data_stem}_m{model}_{solver}_routes.txt"
    img_path = sub_dir / f"{data_stem}_m{model}_{solver}_solution.png"

    print("=" * 60)
    print("启动 2E-VRP 求解引擎")
    print(f"数据文件: {data_path.name}")
    print(f"激活模型: Model {model}")
    print(f"求解方法: {solver.upper()}")
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
        e1_routes, e2_routes_by_sat_res = _run_solver(
            model, solver, problem, e1_tasks, e2_tasks_by_sat,
            alns_iter, alns_time, seed,
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

    # 终端简要输出
    if e1_routes:
        print(f"✓ 求解成功 | 耗时: {run_time:.2f}s | E1 路线: {len(e1_routes)} 条")
    else:
        print(f"✗ 未找到可行解 | 耗时: {run_time:.2f}s")

    print(f"✓ 路径日志已保存: {log_path}")

    # 绘制路径图
    if e1_routes and e2_routes_by_sat_res:
        plot_vrp_solution(
            problem=problem,
            e1_routes=e1_routes,
            e2_routes_by_sat=e2_routes_by_sat_res,
            title=f"2E-VRP Solution - Model {model} ({solver.upper()}) - {data_stem}",
            save_dir=f"model{model}_{solver}",
            filename=img_path.name,
        )
    else:
        print("[提示] 求解器未返回有效路径，跳过绘图。")

    print(f"{'=' * 60}")
    print(f"所有输出已保存至: {sub_dir}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
