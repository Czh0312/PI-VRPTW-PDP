# -*- coding: utf-8 -*-
"""Generate parallel M1/M3/M4/M6 reusable-container cost comparisons."""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import time
from contextlib import redirect_stdout
from pathlib import Path
from typing import Iterable

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from core.instance import ProblemInstance
from models.model_1.alns_solver import solve_alns as solve_m1
from models.model_3.alns_solver import solve_alns as solve_m3
from models.model_4.alns_solver import solve_alns as solve_m4
from models.model_6.alns_solver import solve_alns as solve_m6
from models.model_7.alns_solver import solve_alns as solve_m7
from utils.data_loader import parse_dataset
from utils.economic_cost import (
    EconomicParams,
    RouteMetrics,
    period_sweep,
    route_period_cost,
    value_decomposition,
)
from utils.matrix_loader import load_square_matrix
from utils.task_builder import build_tasks_for_model


ROOT = Path(__file__).resolve().parent
CONFIG_DIR = ROOT / "config"
OUTPUT_DIR = ROOT / "output" / "economic_analysis"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def count_container_uses(data_path: Path) -> tuple[int, int, str]:
    """Count actual container uses; fall back to one container per customer."""
    customers = uses = 0
    has_container_count = False
    with data_path.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("node_type", "").strip().lower() != "c":
                continue
            customers += 1
            raw = row.get("container_count", "").strip()
            if raw:
                has_container_count = True
                uses += max(0, int(math.ceil(float(raw))))
            else:
                uses += 1
    if customers <= 0 or uses <= 0:
        raise ValueError(f"No customer/container demand found in {data_path}")
    basis = "container_count column" if has_container_count else "1 container/customer fallback"
    return uses, customers, basis


def count_container_flows(data_path: Path) -> tuple[int, int, int, str]:
    """Count outbound and returned containers while preserving legacy CSVs."""
    deliveries = pickups = customers = 0
    has_directional_counts = False
    with data_path.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("node_type", "").strip().lower() != "c":
                continue
            customers += 1
            fallback = row.get("container_count", "").strip()
            delivery_raw = row.get("delivery_container_count", "").strip()
            pickup_raw = row.get("pickup_container_count", "").strip()
            if delivery_raw or pickup_raw:
                has_directional_counts = True
            if not delivery_raw:
                delivery_raw = fallback or "1"
            if not pickup_raw:
                pickup_raw = fallback or delivery_raw
            deliveries += max(0, int(math.ceil(float(delivery_raw))))
            pickups += max(0, int(math.ceil(float(pickup_raw))))
    if customers <= 0 or deliveries <= 0:
        raise ValueError(f"No customer/container demand found in {data_path}")
    basis = (
        "delivery_container_count + pickup_container_count columns"
        if has_directional_counts
        else "container_count/1 container per customer fallback"
    )
    return deliveries, pickups, customers, basis


def route_metrics_from_result(result: dict, uses: int, km_scale: float) -> RouteMetrics:
    if result.get("e1_distance") is None or result.get("e2_distance") is None:
        raise ValueError("Routing result does not contain echelon distance metrics")
    return RouteMetrics(
        orders=uses,
        e1_distance_km=float(result["e1_distance"]) * km_scale,
        e2_distance_km=float(result["e2_distance"]) * km_scale,
        e1_vehicle_count=int(result.get("e1", 0)),
        e2_vehicle_count=int(result.get("e2", 0)),
    )


def _to_physical_node(node_id: str) -> str:
    if node_id.startswith("MEET@"):
        node_id = node_id.split("@", 1)[1]
    if node_id.startswith("TRANS_PICK_") or node_id.startswith("TRANS_DROP_"):
        return node_id.split("_", 2)[-1].rsplit("@", 1)[-1]
    if node_id.endswith(("d", "p")) and len(node_id) > 1 and not node_id.startswith("TRANS"):
        return node_id[:-1]
    return node_id


def _route_distance(problem: ProblemInstance, route: list[str]) -> float:
    return sum(
        problem.distance(_to_physical_node(route[i]), _to_physical_node(route[i + 1]))
        for i in range(len(route) - 1)
    )


def solve_route_model(
    model: int,
    solver_fn,
    data_path: Path,
    fleet_cfg: dict,
    alns_time: float,
    seed: int,
    solver_kwargs: dict | None = None,
    distance_matrix: dict | None = None,
    duration_matrix: dict | None = None,
) -> dict:
    """Run one ALNS model without importing the optional Gurobi stack."""
    nodes, _ = parse_dataset(str(data_path))
    problem = ProblemInstance(
        nodes,
        fleet_cfg,
        speed=fleet_cfg.get("speed", 1.0),
        distance_matrix=distance_matrix,
        travel_time_matrix=duration_matrix,
    )
    problem.aggregate_demands_from_pre_assignment()
    if model == 4:
        # M4 constructs customer delivery/pickup tasks dynamically because the
        # customer-satellite assignment is itself a decision variable.
        kwargs = {
            "problem": problem,
            "vehicles_per_satellite": problem.fleet.e2_per_sat_count,
            "TFp": 9999.0,
            "max_iterations": 5000,
            "max_time": alns_time,
            "seed": seed,
            "verbose": False,
        }
    else:
        e1_tasks, e2_tasks = build_tasks_for_model(problem, model)
        kwargs = {
            "problem": problem,
            "e1_tasks": e1_tasks,
            "e2_tasks_by_sat": e2_tasks,
            "vehicles_per_satellite": problem.fleet.e2_per_sat_count,
            "max_iterations": 5000,
            "max_time": alns_time,
            "seed": seed,
            "verbose": False,
        }
        if model in (3, 6):
            kwargs["TFp"] = 9999.0
    if solver_kwargs:
        kwargs.update(solver_kwargs)

    buffer = io.StringIO()
    started = time.time()
    with redirect_stdout(buffer):
        solver_result = solver_fn(**kwargs)
    elapsed = time.time() - started
    e1_routes, e2_routes = solver_result[:2]
    assignment = solver_result[2] if model == 4 else None
    solver_info = (
        solver_result[3]
        if model == 4 and len(solver_result) >= 4
        else solver_result[2]
        if model != 4 and len(solver_result) >= 3
        and isinstance(solver_result[2], dict)
        else None
    )
    if not e1_routes or not e2_routes:
        return {
            "status": "INFEASIBLE",
            "cost": None,
            "time": elapsed,
            "e1": 0,
            "e2": 0,
            "e1_distance": None,
            "e2_distance": None,
            "assignment": assignment,
            "solver_info": solver_info,
            "log": buffer.getvalue(),
        }
    e1_distance = sum(_route_distance(problem, route) for route in e1_routes)
    e2_distance = sum(
        _route_distance(problem, route)
        for routes in e2_routes.values()
        for route in routes
    )
    return {
        "status": "OK",
        "cost": e1_distance + e2_distance,
        "time": elapsed,
        "e1": len(e1_routes),
        "e2": sum(len(routes) for routes in e2_routes.values()),
        "e1_distance": e1_distance,
        "e2_distance": e2_distance,
        "assignment": assignment,
        "solver_info": solver_info,
        "moved_customers": (
            sum(
                1 for customer in problem.customers
                if assignment.get(customer.id) != customer.assigned_sat
            )
            if assignment is not None
            else None
        ),
        "log": buffer.getvalue(),
    }


def select_reusable_route(
    route_metrics: dict[str, RouteMetrics],
    params: EconomicParams,
    candidate_keys: list[str],
) -> str:
    """Select the observed reusable route with the lowest transport cost.

    M4 and M6 are parallel extensions of M3, so selection is made directly
    across all evaluated reusable candidates rather than as an M3->M4->M6
    improvement chain.
    """
    available = [key for key in candidate_keys if key in route_metrics]
    if not available:
        raise ValueError("No feasible reusable route candidate is available")
    return min(
        available,
        key=lambda key: (
            route_period_cost(route_metrics[key], params),
            route_metrics[key].total_distance_km,
            key,
        ),
    )


def params_for_scenario(base: EconomicParams, scenario: dict) -> EconomicParams:
    """Apply generic config overrides for sensitivity scenarios."""
    return EconomicParams(
        single_use_package_price=float(scenario.get("single_use_package_price_yuan", base.single_use_package_price)),
        single_use_eol_cost=float(scenario.get("single_use_eol_cost_yuan", base.single_use_eol_cost)),
        reusable_container_price=float(scenario.get("reusable_container_price_yuan", base.reusable_container_price)),
        reusable_pool_size=int(scenario.get("reusable_pool_size", base.reusable_pool_size)),
        reusable_cleaning_sorting_cost=float(scenario.get("reusable_cleaning_sorting_cost_yuan", base.reusable_cleaning_sorting_cost)),
        reusable_loss_rate=float(scenario.get("reusable_loss_rate_per_cycle", base.reusable_loss_rate)),
        reusable_tracking_scan_cost=float(scenario.get("reusable_tracking_scan_cost_yuan", base.reusable_tracking_scan_cost)),
        e1_cost_per_km=float(scenario.get("e1_cost_yuan_per_km", base.e1_cost_per_km)),
        e2_cost_per_km=float(scenario.get("e2_cost_yuan_per_km", base.e2_cost_per_km)),
    )


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def period_axis(max_periods: int, step: int, anchors: Iterable[float | int | None]) -> list[int]:
    values = set(range(1, max_periods + 1, max(1, step)))
    values.add(max_periods)
    for anchor in anchors:
        if anchor is not None and math.isfinite(float(anchor)):
            rounded = max(1, int(math.ceil(float(anchor))))
            if rounded <= max_periods:
                values.add(rounded)
    return sorted(values)


def plot_horizon_curve(
    rows: list[dict],
    break_even: float | None,
    selected_model: int,
    path: Path,
) -> None:
    periods = [row["period"] for row in rows]
    plt.figure(figsize=(10, 5.8))
    plt.plot(periods, [r["single_use_total"] for r in rows], linewidth=2.2,
             label="Single-use / Model 1", color="#4C78A8")
    plt.plot(periods, [r["reusable_baseline_total"] for r in rows], linewidth=2.0,
             label="Reusable baseline / Model 3", color="#F58518", linestyle="--")
    plt.plot(periods, [r["reusable_optimized_total"] for r in rows], linewidth=2.4,
             label=f"Reusable / selected Model {selected_model}", color="#54A24B")
    if all(
        abs(r["reusable_baseline_total"] - r["reusable_optimized_total"]) <= 1e-9
        for r in rows
    ):
        plt.text(
            0.98, 0.05,
            "Reusable baseline and optimized route overlap",
            transform=plt.gca().transAxes,
            ha="right", va="bottom", fontsize=9, color="#555555",
        )
    if break_even is not None and break_even <= max(periods):
        plt.axvline(break_even, color="#555555", linestyle=":", linewidth=1.4)
        plt.text(break_even, plt.ylim()[0], f" break-even {break_even:.1f}",
                 rotation=90, va="bottom", ha="left", fontsize=9)
    plt.xlabel("Repeated operating periods")
    plt.ylabel("Cumulative total cost (yuan)")
    plt.title("Single-use vs reusable-container cumulative cost")
    plt.grid(alpha=0.25)
    plt.legend()
    plt.tight_layout()
    plt.savefig(path, dpi=250)
    plt.close()


def plot_value_decomposition(decomp: dict, path: Path) -> None:
    labels = ["Packaging\nsubstitution", "Closed-loop\ntransport",
              "Route\noptimization", "Net variable\nvalue"]
    values = [
        decomp["gross_packaging_saving_per_period"],
        -decomp["closed_loop_transport_penalty_per_period"],
        decomp["route_optimization_saving_per_period"],
        decomp["net_variable_saving_per_period"],
    ]
    colors = ["#54A24B" if value >= 0 else "#E45756" for value in values]
    plt.figure(figsize=(9.2, 5.3))
    bars = plt.bar(labels, values, color=colors, alpha=0.86)
    plt.axhline(0, color="#333333", linewidth=1.0)
    offset = max(1.0, max(abs(v) for v in values) * 0.02)
    for bar, value in zip(bars, values):
        plt.text(bar.get_x() + bar.get_width() / 2,
                 value + (offset if value >= 0 else -offset), f"{value:,.1f}",
                 ha="center", va="bottom" if value >= 0 else "top", fontsize=9)
    plt.ylabel("Value per operating period (yuan)")
    plt.title("Where reusable-container economic value comes from")
    plt.grid(axis="y", alpha=0.22)
    plt.tight_layout()
    plt.savefig(path, dpi=250)
    plt.close()


def fmt(value: object, digits: int = 2) -> str:
    if value is None:
        return "无（当前参数下边际收益≤0）"
    if isinstance(value, (int, float)):
        return f"{value:,.{digits}f}"
    return str(value)


def write_markdown_report(path: Path, summary: dict) -> None:
    d = summary["baseline_decomposition"]
    metrics = summary["route_metrics"]
    comparison = summary["mechanism_comparison"]
    selected_model = summary["selected_reusable_model"]
    parallel_model = summary["parallel_route_model"]
    shared_distance_basis = (
        "同一 OSRM 道路距离/时间矩阵"
        if summary["distance_assumption"].get("is_road_matrix", False)
        else "同一距离与时间口径"
    )
    verdict = ("具备回本条件" if d["break_even_periods"] is not None
               else "在基准参数下尚不具备回本条件")
    lines = [
        "# 可循环容器经济性与路径优化价值报告", "",
        f"**结论：可循环容器{verdict}。**", "",
        "## 一、路线结果", "",
        "| 方案 | E1距离/km | E2距离/km | 总距离/km | E1车辆 | E2车辆 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for key, label in [
        ("single_use_m1", "一次性包装 / Model 1"),
        ("reusable_baseline_m3", "循环箱常规回收 / Model 3"),
        ("reusable_free_assignment_m4", "循环箱自由分配 / Model 4"),
        (summary["parallel_route_key"], f"循环箱并列优化 / Model {parallel_model}"),
    ]:
        row = metrics[key]
        selected = "（经济测算采用）" if row.get("model") == selected_model else ""
        lines.append(f"| {label} | {fmt(row['e1_distance_km'])} | {fmt(row['e2_distance_km'])} | "
                     f"{fmt(row['total_distance_km'])}{selected} | {row['e1_vehicle_count']} | {row['e2_vehicle_count']} |")
    lines += [
        "", f"同一数据、{shared_distance_basis}、seed={summary['seed']}、每模型 "
        f"{summary['alns_time_per_model_seconds']:g} 秒预算下，在已测试的 "
        f"Model 3、Model 4 和 Model {parallel_model} 路线中，经济测算采用运输成本最低的 "
        f"Model {selected_model}。这是限时 ALNS 的阶段性结果，不代表所有模型的全局最优。",
        "", "## 二、并列机制的经济价值", "",
        f"- 每期配送容器：**{d['container_uses_per_period']} 个**；回收容器：**{d['returned_container_uses_per_period']} 个**。",
        f"- 包装替代毛收益：**{fmt(d['gross_packaging_saving_per_period'])} 元/期**。",
        f"- M1 → M3 闭环回收运输增量：**{fmt(comparison['closed_loop_transport_penalty_m1_to_m3'])} 元/期**。",
        f"- M3 → M4 自由分配收益：**{fmt(comparison['free_assignment_saving_m3_to_m4'])} 元/期**。",
        f"- 独立运行 M3 − M{parallel_model} 路线成本差：**{fmt(comparison['independent_route_cost_difference_m3_minus_parallel'])} 元/期**；该值混有第一阶段普通 ALNS 差异，不作为中转收益。",
        f"- M{parallel_model} 同次运行的中转前后直接收益：**{fmt(comparison['parallel_internal_transshipment_transport_saving'])} 元/期**，中转 **{comparison['parallel_internal_transshipment_count'] or 0} 次**。",
        f"- M4 − M{parallel_model} 运输成本差：**{fmt(comparison['m4_minus_parallel_transport_cost'])} 元/期**（正值表示 M{parallel_model} 更低）。",
        f"- 采用 Model {selected_model} 后净边际收益：**{fmt(d['net_variable_saving_per_period'])} 元/期**。",
        f"- 初始容器池投资：**{fmt(d['fixed_pool_investment'])} 元**。",
        f"- 回本周期：**{fmt(d['break_even_periods'])} 期**；累计使用约 **{fmt(d['break_even_container_uses'], 0)} 次**。",
        f"- 临界一次性包装购置价：**{fmt(d['critical_single_use_package_price_yuan'])} 元/个**。",
        f"- 临界业务密度：**{fmt(d['critical_container_uses_per_period'])} 个容器/期**（路线不变且车辆容量可行时）。",
        "", f"恒等式：`净边际收益 = 包装替代毛收益 − M1→M3闭环运输增量 + M3→所选Model {selected_model}路径收益`。",
        "", "## 三、情景对比", "",
        "| 情景 | 净边际收益/期 | 回本期数 | 规划期净节省 | 节省率 |",
        "|---|---:|---:|---:|---:|",
    ]
    for scenario in summary.get("scenario_summaries", []):
        saving_rate = scenario.get("saving_rate_at_horizon")
        lines.append(
            f"| {scenario['scenario']} | {fmt(scenario['net_variable_saving_per_period'])} | "
            f"{fmt(scenario['break_even_periods'])} | {fmt(scenario['net_saving_at_horizon'])} | "
            f"{fmt(saving_rate * 100 if saving_rate is not None else None)}% |"
        )
    if any("fuel_only" in row["scenario"] for row in summary.get("scenario_summaries", [])):
        lines += [
            "", "基准情景采用运输全成本；`fuel_only` 情景仅用于增量成本归属敏感性，不替代主结论。",
        ]
    lines += [
        "", "## 四、论文解释", "",
        f"Model 4 与 Model {parallel_model} 都是从 Model 3 扩展出的并列优化机制，不能解释为连续的 M3→M4→M{parallel_model} 改进链。结果应分别报告自由分配与异步交接/路径机制的观测收益；限时 ALNS 值是同预算可行解，不是模型全局最优证明。",
        "", "## 五、口径限制", "",
        f"- 距离口径：{summary['distance_assumption']['description']}；换算系数为 {summary['distance_assumption']['distance_scale_to_km']}。",
        "- 成本曲线把同一代表性路线按期重复，用于回本分析，不代表扩展客户规模。",
        "- 清洗、损耗率和容器池规模是运营假设，需结合敏感性结果报告。",
    ]
    if not summary["distance_assumption"].get("is_road_matrix", False):
        lines.append("- 若坐标并非真实公里，绝对金额仅是情景测算；相对路径收益仍然有效。")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", help="Dataset path; defaults to app_config.json")
    parser.add_argument("--time", type=float, help="ALNS seconds per route model")
    parser.add_argument("--seed", type=int, help="Random seed")
    parser.add_argument("--output", help="Output directory")
    parser.add_argument("--fleet-config", help="Fleet config JSON; defaults to config/fleet_config.json")
    parser.add_argument("--economic-config", help="Economic config JSON; defaults to config/economic_cost_config.json")
    parser.add_argument("--distance-matrix", help="Labelled square road-distance matrix in km")
    parser.add_argument("--duration-matrix", help="Labelled square road-duration matrix in minutes")
    parser.add_argument("--optimized-model", type=int, choices=[6, 7], help="Override optimized route model")
    args = parser.parse_args()

    app_cfg = load_json(CONFIG_DIR / "app_config.json")
    fleet_path = Path(args.fleet_config) if args.fleet_config else CONFIG_DIR / "fleet_config.json"
    if not fleet_path.is_absolute():
        fleet_path = ROOT / fleet_path
    fleet_cfg = load_json(fleet_path)
    econ_path = Path(args.economic_config) if args.economic_config else CONFIG_DIR / "economic_cost_config.json"
    if not econ_path.is_absolute():
        econ_path = ROOT / econ_path
    econ_cfg = load_json(econ_path)
    params = EconomicParams.from_config(econ_cfg)
    analysis_cfg = econ_cfg.get("analysis", {})
    data_path = Path(args.data) if args.data else Path(app_cfg["data"])
    if not data_path.is_absolute():
        data_path = ROOT / data_path
    out_dir = Path(args.output) if args.output else OUTPUT_DIR
    if not out_dir.is_absolute():
        out_dir = ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    alns_time = float(args.time if args.time is not None else app_cfg.get("alns_time", 30))
    seed = int(args.seed if args.seed is not None else app_cfg.get("seed", 42))
    distance_matrix_path = Path(args.distance_matrix) if args.distance_matrix else None
    duration_matrix_path = Path(args.duration_matrix) if args.duration_matrix else None
    if distance_matrix_path is not None and not distance_matrix_path.is_absolute():
        distance_matrix_path = ROOT / distance_matrix_path
    if duration_matrix_path is not None and not duration_matrix_path.is_absolute():
        duration_matrix_path = ROOT / duration_matrix_path
    distance_matrix = load_square_matrix(distance_matrix_path) if distance_matrix_path else None
    duration_matrix = load_square_matrix(duration_matrix_path) if duration_matrix_path else None
    if duration_matrix is not None and distance_matrix is None:
        raise ValueError("--duration-matrix requires --distance-matrix")
    km_scale = 1.0 if distance_matrix is not None else float(
        analysis_cfg.get("distance_scale_to_km", 1.0)
    )
    uses, returned_uses, customers, count_basis = count_container_flows(data_path)

    model7_kwargs = {
        "strong_initial_solution": True,
        "transshipment_baseline_guard": True,
        "transshipment_max_total": 2,
        "transshipment_max_per_satellite": 1,
        "route_local_refine": True,
        "inter_route_rebalance": True,
        "inter_route_rebalance_rounds": 2,
        "inter_route_rebalance_max_moves": 10,
        "inter_route_rebalance_max_pairs": 30,
        "iterative_rebalance": True,
        "iterative_rebalance_trigger": "fixed",
        "iterative_rebalance_interval": 10,
        "iterative_rebalance_max_moves": 2,
        "iterative_rebalance_max_pairs": 16,
    }
    optimized_model = int(
        args.optimized_model
        if args.optimized_model is not None
        else analysis_cfg.get("optimized_route_model", app_cfg.get("model", 6))
    )
    if optimized_model not in (6, 7):
        raise ValueError("analysis.optimized_route_model must be Model 6 or Model 7")
    optimized_solver = solve_m6 if optimized_model == 6 else solve_m7
    optimized_kwargs = (
        {"strong_initial_solution": True, "return_info": True}
        if optimized_model == 6
        else model7_kwargs
    )
    parallel_route_key = f"reusable_parallel_m{optimized_model}"
    route_runs = {
        "single_use_m1": solve_route_model(
            1, solve_m1, data_path, fleet_cfg, alns_time, seed,
            distance_matrix=distance_matrix, duration_matrix=duration_matrix,
        ),
        "reusable_baseline_m3": solve_route_model(
            3, solve_m3, data_path, fleet_cfg, alns_time, seed,
            {"strong_initial_solution": True},
            distance_matrix=distance_matrix, duration_matrix=duration_matrix,
        ),
        "reusable_free_assignment_m4": solve_route_model(
            4, solve_m4, data_path, fleet_cfg, alns_time, seed,
            distance_matrix=distance_matrix, duration_matrix=duration_matrix,
        ),
        parallel_route_key: solve_route_model(
            optimized_model, optimized_solver, data_path, fleet_cfg,
            alns_time, seed, optimized_kwargs,
            distance_matrix=distance_matrix, duration_matrix=duration_matrix,
        ),
    }
    mandatory_failures = {
        name: result["status"]
        for name, result in route_runs.items()
        if result.get("status") != "OK"
    }
    if mandatory_failures:
        raise RuntimeError(f"Routing failed: {mandatory_failures}")

    route_metrics = {
        name: route_metrics_from_result(result, uses, km_scale)
        for name, result in route_runs.items()
        if result.get("status") == "OK"
    }
    su = route_metrics["single_use_m1"]
    m3 = route_metrics["reusable_baseline_m3"]
    m4 = route_metrics["reusable_free_assignment_m4"]
    parallel = route_metrics[parallel_route_key]
    selected_key = select_reusable_route(
        route_metrics,
        params,
        ["reusable_baseline_m3", "reusable_free_assignment_m4", parallel_route_key],
    )
    selected = route_metrics[selected_key]
    model_by_key = {
        "reusable_baseline_m3": 3,
        "reusable_free_assignment_m4": 4,
        parallel_route_key: optimized_model,
    }
    selected_model = model_by_key[selected_key]

    transport_costs = {
        key: route_period_cost(route_metrics[key], params)
        for key in model_by_key
    }
    mechanism_comparison = {
        "closed_loop_transport_penalty_m1_to_m3": (
            transport_costs["reusable_baseline_m3"] - route_period_cost(su, params)
        ),
        "free_assignment_saving_m3_to_m4": (
            transport_costs["reusable_baseline_m3"]
            - transport_costs["reusable_free_assignment_m4"]
        ),
        "independent_route_cost_difference_m3_minus_parallel": (
            transport_costs["reusable_baseline_m3"]
            - transport_costs[parallel_route_key]
        ),
        "m4_minus_parallel_transport_cost": (
            transport_costs["reusable_free_assignment_m4"]
            - transport_costs[parallel_route_key]
        ),
        "m3_to_m4_distance_saving_km": m3.total_distance_km - m4.total_distance_km,
        "m3_to_parallel_distance_saving_km": m3.total_distance_km - parallel.total_distance_km,
        "m4_minus_parallel_distance_km": m4.total_distance_km - parallel.total_distance_km,
        "parallel_internal_stage1_distance": (
            route_runs[parallel_route_key].get("solver_info", {}) or {}
        ).get("stage1_objective"),
        "parallel_internal_transshipment_distance_saving": (
            route_runs[parallel_route_key].get("solver_info", {}) or {}
        ).get("transshipment_improvement"),
        "parallel_internal_transshipment_transport_saving": (
            (
                route_runs[parallel_route_key].get("solver_info", {}) or {}
            ).get("transshipment_improvement") * params.e2_cost_per_km
            if (
                route_runs[parallel_route_key].get("solver_info", {}) or {}
            ).get("transshipment_improvement") is not None
            else None
        ),
        "parallel_internal_transshipment_count": (
            route_runs[parallel_route_key].get("solver_info", {}) or {}
        ).get("transshipment_count"),
        "selected_reusable_model": selected_model,
        "selection_criterion": "minimum observed E1/E2 transport cost among M3, M4 and the configured parallel model",
    }

    horizon = int(analysis_cfg.get("horizon_periods", 300))
    baseline = value_decomposition(
        su, m3, selected, params, uses, horizon,
        returned_container_uses_per_period=returned_uses,
    )
    max_periods = int(analysis_cfg.get("curve_max_periods", max(600, horizon)))
    periods = period_axis(max_periods, int(analysis_cfg.get("curve_period_step", 10)),
                          [horizon, baseline["break_even_periods"]])
    curve = period_sweep(
        su, m3, selected, params, uses, periods,
        returned_container_uses_per_period=returned_uses,
    )
    write_csv(out_dir / "horizon_cost_curve.csv", curve)
    write_csv(out_dir / "value_decomposition.csv", [{**baseline, **mechanism_comparison}])
    write_csv(out_dir / "mechanism_comparison.csv", [mechanism_comparison])
    plot_horizon_curve(
        curve,
        baseline["break_even_periods"],
        selected_model,
        out_dir / "horizon_cost_curve.png",
    )
    plot_value_decomposition(baseline, out_dir / "value_decomposition.png")

    scenario_rows = []
    for scenario in econ_cfg.get("scenarios", []):
        scenario_params = params_for_scenario(params, scenario)
        row = value_decomposition(
            su, m3, selected, scenario_params, uses, horizon,
            returned_container_uses_per_period=returned_uses,
        )
        scenario_rows.append({"scenario": scenario["name"], "description": scenario.get("description", ""),
                              "single_use_package_price_yuan": scenario_params.single_use_package_price, **row})
    write_csv(out_dir / "scenario_summary.csv", scenario_rows)

    loss_rows = []
    for rate in econ_cfg.get("loss_rate_sensitivity", {}).get("rates", []):
        rate_params = params_for_scenario(params, {"reusable_loss_rate_per_cycle": float(rate)})
        loss_rows.append({"loss_rate": float(rate),
                          **value_decomposition(
                              su, m3, selected, rate_params, uses, horizon,
                              returned_container_uses_per_period=returned_uses,
                          )})
    write_csv(out_dir / "loss_rate_sensitivity.csv", loss_rows)

    metrics_dict = {}
    for name, metric in route_metrics.items():
        model = model_by_key.get(name, 1 if name == "single_use_m1" else None)
        metrics_dict[name] = {
            **metric.__dict__,
            "model": model,
            "total_distance_km": metric.total_distance_km,
            "transport_cost_per_period": route_period_cost(metric, params),
            "raw_solver_objective_distance": route_runs[name]["cost"],
            "runtime_seconds": route_runs[name]["time"],
            "moved_customers": route_runs[name].get("moved_customers"),
            "solver_info": route_runs[name].get("solver_info"),
        }
    metrics_dict["reusable_selected"] = {
        **metrics_dict[selected_key],
        "selected_from": selected_key,
    }
    # Backward-compatible alias for earlier readers of summary.json.
    metrics_dict["reusable_optimized"] = dict(metrics_dict["reusable_selected"])
    summary = {
        "data": str(data_path.relative_to(ROOT)) if data_path.is_relative_to(ROOT) else str(data_path),
        "customers": customers,
        "container_uses_per_period": uses,
        "returned_container_uses_per_period": returned_uses,
        "container_count_basis": count_basis,
        "operating_period": analysis_cfg.get("operating_period", "one representative service day"),
        "horizon_periods": horizon,
        "seed": seed,
        "alns_time_per_model_seconds": alns_time,
        "evaluated_models": [1, 3, 4, optimized_model],
        "parallel_route_model": optimized_model,
        "parallel_route_key": parallel_route_key,
        "selected_reusable_model": selected_model,
        "selected_reusable_route_key": selected_key,
        "selected_reusable_route_source": (
            f"Lowest observed transport cost among Models 3, 4 and {optimized_model}"
        ),
        "optimized_route_model": selected_model,
        "optimized_route_source": f"Selected Model {selected_model} observed route",
        "route_metrics": metrics_dict,
        "mechanism_comparison": mechanism_comparison,
        "economic_params": params.__dict__,
        "baseline_decomposition": baseline,
        "scenario_summaries": scenario_rows,
        "loss_rate_sensitivity": loss_rows,
        "distance_assumption": {
            "distance_scale_to_km": km_scale,
            "is_road_matrix": distance_matrix is not None,
            "description": (
                f"Road distances loaded from {distance_matrix_path}; travel times loaded from {duration_matrix_path}"
                if distance_matrix is not None
                else analysis_cfg.get("distance_scale_basis", "Model coordinate units are treated as kilometers")
            ),
        },
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown_report(out_dir / "economic_report.md", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
