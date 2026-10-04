# -*- coding: utf-8 -*-
"""Generate M1/M2/M3/M4/M6/M7 routing-aware container cost comparisons."""
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

from configuration.loader import PROJECT_ROOT, load_routing_case, resolve_project_path
from models.routing.common.domain.instance import ProblemInstance
from models.routing.alns.types import Route, Solution
from models.routing.alns.variants.model_1 import solve_alns as solve_m1
from models.routing.alns.variants.model_2 import solve_alns as solve_m2
from models.routing.alns.variants.model_3 import solve_alns as solve_m3
from models.routing.alns.variants.model_4 import solve_alns as solve_m4
from models.routing.alns.variants.model_6 import solve_alns as solve_m6
from models.routing.alns.variants.model_7 import solve_alns as solve_m7
from models.routing.common.data_loader import parse_dataset
from models.cost.lifecycle import (
    EconomicParams,
    RouteMetrics,
    period_sweep,
    route_period_cost,
    value_decomposition,
)
from models.routing.common.matrix_loader import load_square_matrix
from models.routing.common.task_builder import build_tasks_for_model


ROOT = PROJECT_ROOT
CONFIG_DIR = ROOT / "config"
OUTPUT_DIR = ROOT / "output" / "economic_analysis"
DEFAULT_CASE = CONFIG_DIR / "cases" / "zhou_small_demo.json"


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


def _as_solution(e1_paths: list[list[str]], e2_paths: dict[str, list[list[str]]]) -> Solution:
    """Convert public route paths into an incumbent accepted by nested ALNS models."""
    solution = Solution()
    solution.e1_routes = [Route(path[0], list(path[1:-1])) for path in e1_paths]
    solution.e2_routes = {
        sat_id: [Route(path[0], list(path[1:-1])) for path in paths]
        for sat_id, paths in e2_paths.items()
    }
    return solution


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
    if model in (2, 4):
        # Free-assignment models construct tasks dynamically because the
        # customer-satellite assignment is itself a decision variable.
        kwargs = {
            "problem": problem,
            "vehicles_per_satellite": problem.fleet.e2_per_sat_count,
            "max_iterations": 5000,
            "max_time": alns_time,
            "seed": seed,
            "verbose": False,
            "return_info": True,
        }
        if model == 4:
            kwargs["TFp"] = 9999.0
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
            "return_info": True,
        }
        if model in (3, 6, 7):
            kwargs["TFp"] = 9999.0
    if solver_kwargs:
        kwargs.update(solver_kwargs)

    buffer = io.StringIO()
    started = time.time()
    with redirect_stdout(buffer):
        solver_result = solver_fn(**kwargs)
    elapsed = time.time() - started
    e1_routes, e2_routes = solver_result[:2]
    assignment = solver_result[2] if model in (2, 4, 7) else None
    solver_info = (
        solver_result[3]
        if model in (2, 4, 7) and len(solver_result) >= 4
        else solver_result[2]
        if model not in (2, 4, 7) and len(solver_result) >= 3
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
        "solution": _as_solution(e1_routes, e2_routes),
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
    """Select the observed route with the lowest E1/E2 transport cost."""
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
    selected_single_use_model: int,
    reference_reusable_model: int,
    selected_reusable_model: int,
    path: Path,
) -> None:
    periods = [row["period"] for row in rows]
    plt.figure(figsize=(10, 5.8))
    plt.plot(periods, [r["single_use_total"] for r in rows], linewidth=2.2,
             label=f"Single-use / selected Model {selected_single_use_model}", color="#4C78A8")
    plt.plot(periods, [r["reusable_baseline_total"] for r in rows], linewidth=2.0,
             label=f"Reusable reference / Model {reference_reusable_model}", color="#F58518", linestyle="--")
    plt.plot(periods, [r["reusable_optimized_total"] for r in rows], linewidth=2.4,
             label=f"Reusable / selected Model {selected_reusable_model}", color="#54A24B")
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
    selected_single_model = summary["selected_single_use_model"]
    selected_reusable_model = summary["selected_reusable_model"]
    reference_reusable_model = summary["reference_reusable_model"]
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
        "| 方案 | 搜索预算 | E1距离/km | E2距离/km | 总距离/km | 运输成本/元 | E1车辆 | E2车辆 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for key, label in [
        ("single_use_m1", "一次性包装固定分仓 / Model 1"),
        ("single_use_free_assignment_m2", "一次性包装自由分仓 / Model 2"),
        ("reusable_baseline_m3", "循环箱固定分仓 / Model 3"),
        ("reusable_free_assignment_m4", "循环箱自由分配 / Model 4"),
        ("reusable_relay_m6", "循环箱固定分仓 + 异步交接 / Model 6"),
        ("reusable_hybrid_m7", "循环箱自由分仓 + 异步交接 / Model 7"),
    ]:
        row = metrics[key]
        selected = (
            "（一次性侧采用）" if row.get("model") == selected_single_model
            else "（循环箱侧采用）" if row.get("model") == selected_reusable_model
            else ""
        )
        budget = (
            f"{row['chain_time_budget_seconds']:g}s"
            if row.get("inherited_from_model") is None
            else f"继承 M{row['inherited_from_model']} + {row['own_time_budget_seconds']:g}s"
        )
        lines.append(f"| {label} | {budget} | {fmt(row['e1_distance_km'])} | {fmt(row['e2_distance_km'])} | "
                     f"{fmt(row['total_distance_km'])}{selected} | {fmt(row['transport_cost_per_period'])} | "
                     f"{row['e1_vehicle_count']} | {row['e2_vehicle_count']} |")
    lines += [
        "", f"同一数据、{shared_distance_basis}、seed={summary['seed']} 下，基模型 M1/M3 各运行 "
        f"{summary['base_alns_time_seconds']:g} 秒，扩展模型继承其基模型可行解并追加 "
        f"{summary['extension_time_seconds']:g} 秒机制搜索。一次性侧在 Model 1/2 中采用 "
        f"Model {selected_single_model}，循环箱侧在 Model 3/4/6/7 中采用 Model "
        f"{selected_reusable_model}。这是限时 ALNS 的阶段性结果，不代表各模型的全局最优。",
        "", "## 二、扩展机制的经济价值", "",
        f"- 每期配送容器：**{d['container_uses_per_period']} 个**；回收容器：**{d['returned_container_uses_per_period']} 个**。",
        f"- 包装替代毛收益：**{fmt(d['gross_packaging_saving_per_period'])} 元/期**。",
        f"- M1 → M2 一次性侧自由分仓收益：**{fmt(comparison['single_use_free_assignment_saving_m1_to_m2'])} 元/期**。",
        f"- 固定分仓 M1 → M3 闭环回收运输增量：**{fmt(comparison['closed_loop_transport_penalty_m1_to_m3'])} 元/期**。",
        f"- 自由分仓 M2 → M4 闭环回收运输增量：**{fmt(comparison['closed_loop_transport_penalty_m2_to_m4'])} 元/期**。",
        f"- M3 → M4 循环箱侧自由分仓收益：**{fmt(comparison['reusable_free_assignment_saving_m3_to_m4'])} 元/期**。",
        f"- M3 → M6 固定分仓异步交接收益：**{fmt(comparison['m3_minus_m6_transport_cost'])} 元/期**。M6 直接继承本次 M3 解，因此该差额与同次运行中转收益一致。",
        f"- M4 → M7 自由分仓异步交接收益：**{fmt(comparison['m4_minus_m7_transport_cost'])} 元/期**。M7 直接继承本次 M4 解，因此该差额与同次运行中转收益一致。",
        f"- M6 同次运行中转直接收益：**{fmt(comparison['m6_internal_transshipment_transport_saving'])} 元/期**，中转 **{comparison['m6_internal_transshipment_count'] or 0} 次**。",
        f"- M7 同次运行中转直接收益：**{fmt(comparison['m7_internal_transshipment_transport_saving'])} 元/期**，中转 **{comparison['m7_internal_transshipment_count'] or 0} 次**。",
        f"- 采用 Model {selected_reusable_model} 后净边际收益：**{fmt(d['net_variable_saving_per_period'])} 元/期**。",
        f"- 初始容器池投资：**{fmt(d['fixed_pool_investment'])} 元**。",
        f"- 回本周期：**{fmt(d['break_even_periods'])} 期**；累计使用约 **{fmt(d['break_even_container_uses'], 0)} 次**。",
        f"- 临界一次性包装购置价：**{fmt(d['critical_single_use_package_price_yuan'])} 元/个**。",
        f"- 临界业务密度：**{fmt(d['critical_container_uses_per_period'])} 个容器/期**（路线不变且车辆容量可行时）。",
        "", f"价值分解采用与一次性侧相同分仓自由度的循环基准 Model {reference_reusable_model}。恒等式："
        f"`净边际收益 = 包装替代毛收益 − 所选一次性Model {selected_single_model}→参考循环Model "
        f"{reference_reusable_model}运输增量 + 参考循环Model {reference_reusable_model}→所选循环Model "
        f"{selected_reusable_model}路径收益`。",
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
        "Model 2/4 分别测量一次性侧和循环箱侧的自由分仓价值；Model 6 是 Model 3 的固定分仓异步交接扩展；Model 7 是 Model 4 的自由分仓与异步交接联合扩展。纯中转收益只能取同一次 M6/M7 运行内部的阶段一与最终值之差，不能用独立 ALNS 结果相减。",
        "", "## 五、口径限制", "",
        f"- 距离口径：{summary['distance_assumption']['description']}；换算系数为 {summary['distance_assumption']['distance_scale_to_km']}。",
        "- 成本曲线把同一代表性路线按期重复，用于回本分析，不代表扩展客户规模。",
        "- 清洗、损耗率和容器池规模是运营假设，需结合敏感性结果报告。",
    ]
    if not summary["distance_assumption"].get("is_road_matrix", False):
        lines.append("- 若坐标并非真实公里，绝对金额仅是情景测算；相对路径收益仍然有效。")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", default=str(DEFAULT_CASE), help="Composable case manifest")
    parser.add_argument("--data", help="Dataset path; defaults to the selected case")
    parser.add_argument("--time", type=float, help="ALNS seconds per route model")
    parser.add_argument(
        "--extension-time", type=float,
        help="Additional seconds for each incumbent-preserving model extension",
    )
    parser.add_argument("--seed", type=int, help="Random seed")
    parser.add_argument("--output", help="Output directory")
    parser.add_argument("--fleet-config", help="Override routing/fleet config JSON")
    parser.add_argument("--economic-config", help="Override economic config JSON")
    parser.add_argument("--route-result", help="多批次成本：已保存且需要重新审计的 result.json")
    parser.add_argument("--distance-matrix", help="Labelled square road-distance matrix in km")
    parser.add_argument("--duration-matrix", help="Labelled square road-duration matrix in minutes")
    parser.add_argument(
        "--optimized-model", type=int, choices=[6, 7],
        help="Deprecated compatibility option; all six current models are evaluated",
    )
    args = parser.parse_args(argv)

    app_cfg, fleet_cfg = load_routing_case(args.case)
    if fleet_cfg.get('operating_policy', {}).get('policy_version') == 'multi_batch_cutoff_v1':
        from cli.multibatch_cost import run_cost
        return run_cost(app_cfg, fleet_cfg, args)
    if args.route_result:
        raise ValueError('--route-result currently requires a multibatch case')
    if fleet_cfg.get("operating_policy"):
        raise ValueError("单日版本目前请用 route 入口；旧 cost 入口比较 M1/M2/M3/M4/M6/M7，尚未支持单日同口径费用比较")
    if args.fleet_config:
        fleet_cfg = load_json(resolve_project_path(args.fleet_config))
    econ_path = resolve_project_path(
        args.economic_config
        or app_cfg.get("cost_config")
        or "config/cost/cases/theoretical_validation.json"
    )
    econ_cfg = load_json(econ_path)
    params = EconomicParams.from_config(econ_cfg)
    analysis_cfg = econ_cfg.get("analysis", {})
    data_path = resolve_project_path(args.data or app_cfg["data"])
    out_dir = Path(args.output) if args.output else OUTPUT_DIR
    if not out_dir.is_absolute():
        out_dir = ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    alns_time = float(args.time if args.time is not None else app_cfg.get("alns_time", 30))
    extension_time = float(
        args.extension_time
        if args.extension_time is not None
        else analysis_cfg.get("extension_time_seconds", 2.0)
    )
    if extension_time < 0:
        raise ValueError("--extension-time cannot be negative")
    seed = int(args.seed if args.seed is not None else app_cfg.get("seed", 42))
    distance_value = args.distance_matrix or app_cfg.get("distance_matrix")
    duration_value = args.duration_matrix or app_cfg.get("duration_matrix")
    distance_matrix_path = resolve_project_path(distance_value) if distance_value else None
    duration_matrix_path = resolve_project_path(duration_value) if duration_value else None
    distance_matrix = load_square_matrix(distance_matrix_path) if distance_matrix_path else None
    duration_matrix = load_square_matrix(duration_matrix_path) if duration_matrix_path else None
    if duration_matrix is not None and distance_matrix is None:
        raise ValueError("--duration-matrix requires --distance-matrix")
    km_scale = 1.0 if distance_matrix is not None else float(
        analysis_cfg.get("distance_scale_to_km", 1.0)
    )
    uses, returned_uses, customers, count_basis = count_container_flows(data_path)

    extension_kwargs = {
        "return_info": True,
        "stage1_time_ratio": 0.0,
        "transshipment_time_budget": extension_time,
        "transshipment_max_total": 2,
        "transshipment_max_per_satellite": 1,
    }
    m1_run = solve_route_model(
        1, solve_m1, data_path, fleet_cfg, alns_time, seed,
        {"return_info": False},
        distance_matrix=distance_matrix, duration_matrix=duration_matrix,
    )
    m2_run = solve_route_model(
        2, solve_m2, data_path, fleet_cfg, extension_time, seed,
        {"initial_solution": m1_run["solution"], "return_info": False},
        distance_matrix=distance_matrix, duration_matrix=duration_matrix,
    )
    m3_run = solve_route_model(
        3, solve_m3, data_path, fleet_cfg, alns_time, seed,
        {"strong_initial_solution": True, "return_info": False},
        distance_matrix=distance_matrix, duration_matrix=duration_matrix,
    )
    m4_run = solve_route_model(
        4, solve_m4, data_path, fleet_cfg, extension_time, seed,
        {"initial_solution": m3_run["solution"], "return_info": False},
        distance_matrix=distance_matrix, duration_matrix=duration_matrix,
    )
    m6_run = solve_route_model(
        6, solve_m6, data_path, fleet_cfg, extension_time, seed,
        {
            **extension_kwargs,
            "strong_initial_solution": True,
            "initial_solution": m3_run["solution"],
            "refill_unused_time": False,
        },
        distance_matrix=distance_matrix, duration_matrix=duration_matrix,
    )
    m7_run = solve_route_model(
        7, solve_m7, data_path, fleet_cfg, extension_time, seed,
        {**extension_kwargs, "initial_solution": m4_run["solution"]},
        distance_matrix=distance_matrix, duration_matrix=duration_matrix,
    )
    route_runs = {
        "single_use_m1": m1_run,
        "single_use_free_assignment_m2": m2_run,
        "reusable_baseline_m3": m3_run,
        "reusable_free_assignment_m4": m4_run,
        "reusable_relay_m6": m6_run,
        "reusable_hybrid_m7": m7_run,
    }
    run_design = {
        "single_use_m1": (None, alns_time, alns_time),
        "single_use_free_assignment_m2": (1, extension_time, alns_time + extension_time),
        "reusable_baseline_m3": (None, alns_time, alns_time),
        "reusable_free_assignment_m4": (3, extension_time, alns_time + extension_time),
        "reusable_relay_m6": (3, extension_time, alns_time + extension_time),
        "reusable_hybrid_m7": (4, extension_time, alns_time + 2 * extension_time),
    }
    for key, (base_model, own_budget, chain_budget) in run_design.items():
        route_runs[key]["inherited_from_model"] = base_model
        route_runs[key]["own_time_budget_seconds"] = own_budget
        route_runs[key]["chain_time_budget_seconds"] = chain_budget
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
    model_by_key = {
        "single_use_m1": 1,
        "single_use_free_assignment_m2": 2,
        "reusable_baseline_m3": 3,
        "reusable_free_assignment_m4": 4,
        "reusable_relay_m6": 6,
        "reusable_hybrid_m7": 7,
    }
    selected_single_key = select_reusable_route(
        route_metrics, params,
        ["single_use_m1", "single_use_free_assignment_m2"],
    )
    selected_single = route_metrics[selected_single_key]
    m3 = route_metrics["reusable_baseline_m3"]
    m4 = route_metrics["reusable_free_assignment_m4"]
    m6 = route_metrics["reusable_relay_m6"]
    m7 = route_metrics["reusable_hybrid_m7"]
    selected_reusable_key = select_reusable_route(
        route_metrics,
        params,
        ["reusable_baseline_m3", "reusable_free_assignment_m4",
         "reusable_relay_m6", "reusable_hybrid_m7"],
    )
    selected_reusable = route_metrics[selected_reusable_key]
    selected_single_model = model_by_key[selected_single_key]
    selected_reusable_model = model_by_key[selected_reusable_key]
    reference_reusable_key = (
        "reusable_free_assignment_m4"
        if selected_single_model == 2
        else "reusable_baseline_m3"
    )
    reference_reusable = route_metrics[reference_reusable_key]
    reference_reusable_model = model_by_key[reference_reusable_key]

    transport_costs = {
        key: route_period_cost(route_metrics[key], params) for key in model_by_key
    }
    m6_info = route_runs["reusable_relay_m6"].get("solver_info", {}) or {}
    m7_info = route_runs["reusable_hybrid_m7"].get("solver_info", {}) or {}
    mechanism_comparison = {
        "closed_loop_transport_penalty_m1_to_m3": (
            transport_costs["reusable_baseline_m3"] - transport_costs["single_use_m1"]
        ),
        "closed_loop_transport_penalty_m2_to_m4": (
            transport_costs["reusable_free_assignment_m4"]
            - transport_costs["single_use_free_assignment_m2"]
        ),
        "single_use_free_assignment_saving_m1_to_m2": (
            transport_costs["single_use_m1"]
            - transport_costs["single_use_free_assignment_m2"]
        ),
        "reusable_free_assignment_saving_m3_to_m4": (
            transport_costs["reusable_baseline_m3"]
            - transport_costs["reusable_free_assignment_m4"]
        ),
        "m3_minus_m6_transport_cost": (
            transport_costs["reusable_baseline_m3"]
            - transport_costs["reusable_relay_m6"]
        ),
        "m4_minus_m7_transport_cost": (
            transport_costs["reusable_free_assignment_m4"]
            - transport_costs["reusable_hybrid_m7"]
        ),
        "m6_minus_m7_transport_cost": (
            transport_costs["reusable_relay_m6"]
            - transport_costs["reusable_hybrid_m7"]
        ),
        "m1_to_m2_distance_saving_km": (
            route_metrics["single_use_m1"].total_distance_km
            - route_metrics["single_use_free_assignment_m2"].total_distance_km
        ),
        "m3_to_m4_distance_saving_km": m3.total_distance_km - m4.total_distance_km,
        "m3_minus_m6_distance_km": m3.total_distance_km - m6.total_distance_km,
        "m4_minus_m7_distance_km": m4.total_distance_km - m7.total_distance_km,
        "m6_internal_stage1_distance": m6_info.get("stage1_objective"),
        "m6_internal_transshipment_distance_saving": m6_info.get("transshipment_improvement"),
        "m6_internal_transshipment_transport_saving": (
            m6_info.get("transshipment_improvement") * params.e2_cost_per_km
            if m6_info.get("transshipment_improvement") is not None else None
        ),
        "m6_internal_transshipment_count": m6_info.get("transshipment_count"),
        "m7_internal_stage1_distance": m7_info.get("free_assignment_objective"),
        "m7_internal_transshipment_distance_saving": m7_info.get("transshipment_improvement"),
        "m7_internal_transshipment_transport_saving": (
            m7_info.get("transshipment_improvement") * params.e2_cost_per_km
            if m7_info.get("transshipment_improvement") is not None else None
        ),
        "m7_internal_transshipment_count": m7_info.get("transshipment_count"),
        "selected_single_use_model": selected_single_model,
        "selected_reusable_model": selected_reusable_model,
        "selection_criterion": "minimum observed E1/E2 transport cost within M1/M2 and M3/M4/M6/M7",
    }

    horizon = int(analysis_cfg.get("horizon_periods", 300))
    baseline = value_decomposition(
        selected_single, reference_reusable, selected_reusable,
        params, uses, horizon,
        returned_container_uses_per_period=returned_uses,
    )
    max_periods = int(analysis_cfg.get("curve_max_periods", max(600, horizon)))
    periods = period_axis(max_periods, int(analysis_cfg.get("curve_period_step", 10)),
                          [horizon, baseline["break_even_periods"]])
    curve = period_sweep(
        selected_single, reference_reusable, selected_reusable,
        params, uses, periods,
        returned_container_uses_per_period=returned_uses,
    )
    write_csv(out_dir / "horizon_cost_curve.csv", curve)
    write_csv(out_dir / "value_decomposition.csv", [{**baseline, **mechanism_comparison}])
    write_csv(out_dir / "mechanism_comparison.csv", [mechanism_comparison])
    write_csv(
        out_dir / "route_model_comparison.csv",
        [{
            "model": model_by_key[key],
            "route_key": key,
            "packaging_side": "single_use" if model_by_key[key] in (1, 2) else "reusable",
            "selected": key in (selected_single_key, selected_reusable_key),
            "e1_distance_km": route_metrics[key].e1_distance_km,
            "e2_distance_km": route_metrics[key].e2_distance_km,
            "total_distance_km": route_metrics[key].total_distance_km,
            "transport_cost_per_period": transport_costs[key],
            "runtime_seconds": route_runs[key]["time"],
            "inherited_from_model": route_runs[key]["inherited_from_model"],
            "own_time_budget_seconds": route_runs[key]["own_time_budget_seconds"],
            "chain_time_budget_seconds": route_runs[key]["chain_time_budget_seconds"],
            "moved_customers": route_runs[key].get("moved_customers"),
        } for key in model_by_key],
    )
    plot_horizon_curve(
        curve,
        baseline["break_even_periods"],
        selected_single_model,
        reference_reusable_model,
        selected_reusable_model,
        out_dir / "horizon_cost_curve.png",
    )
    plot_value_decomposition(baseline, out_dir / "value_decomposition.png")

    scenario_rows = []
    for scenario in econ_cfg.get("scenarios", []):
        scenario_params = params_for_scenario(params, scenario)
        row = value_decomposition(
            selected_single, reference_reusable, selected_reusable,
            scenario_params, uses, horizon,
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
                              selected_single, reference_reusable,
                              selected_reusable, rate_params, uses, horizon,
                              returned_container_uses_per_period=returned_uses,
                          )})
    write_csv(out_dir / "loss_rate_sensitivity.csv", loss_rows)

    metrics_dict = {}
    for name, metric in route_metrics.items():
        model = model_by_key[name]
        metrics_dict[name] = {
            **metric.__dict__,
            "model": model,
            "total_distance_km": metric.total_distance_km,
            "transport_cost_per_period": route_period_cost(metric, params),
            "raw_solver_objective_distance": route_runs[name]["cost"],
            "runtime_seconds": route_runs[name]["time"],
            "inherited_from_model": route_runs[name]["inherited_from_model"],
            "own_time_budget_seconds": route_runs[name]["own_time_budget_seconds"],
            "chain_time_budget_seconds": route_runs[name]["chain_time_budget_seconds"],
            "moved_customers": route_runs[name].get("moved_customers"),
            "solver_info": route_runs[name].get("solver_info"),
        }
    metrics_dict["single_use_selected"] = {
        **metrics_dict[selected_single_key],
        "selected_from": selected_single_key,
    }
    metrics_dict["reusable_selected"] = {
        **metrics_dict[selected_reusable_key],
        "selected_from": selected_reusable_key,
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
        "base_alns_time_seconds": alns_time,
        "extension_time_seconds": extension_time,
        "search_design": "incumbent-preserving nested extensions: M2<-M1, M4/M6<-M3, M7<-M4",
        "evaluated_models": [1, 2, 3, 4, 6, 7],
        "selected_single_use_model": selected_single_model,
        "selected_single_use_route_key": selected_single_key,
        "selected_single_use_route_source": "Lowest observed transport cost among Models 1 and 2",
        "reference_reusable_model": reference_reusable_model,
        "reference_reusable_route_key": reference_reusable_key,
        "selected_reusable_model": selected_reusable_model,
        "selected_reusable_route_key": selected_reusable_key,
        "selected_reusable_route_source": "Lowest observed transport cost among Models 3, 4, 6 and 7",
        "parallel_route_model": 7,
        "parallel_route_key": "reusable_hybrid_m7",
        "optimized_route_model": selected_reusable_model,
        "optimized_route_source": f"Selected Model {selected_reusable_model} observed route",
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
