# -*- coding: utf-8 -*-
"""Generate economic comparison outputs from routing results.

The script runs Model 1 as the single-use baseline and Model 4 as the reusable
closed-loop scheme, then converts route distances into monetary costs.
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

import matplotlib.pyplot as plt

from run_comparison import run_one
from utils.data_loader import parse_dataset
from utils.economic_cost import (
    EconomicParams,
    RouteMetrics,
    break_even_demand,
    demand_sweep,
    marginal_saving_per_order,
    reusable_cost,
    single_use_cost,
)


ROOT = Path(__file__).resolve().parent
CONFIG_DIR = ROOT / "config"
OUTPUT_DIR = ROOT / "output" / "economic_analysis"


def load_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def count_customers(data_path: Path) -> int:
    nodes, _ = parse_dataset(str(data_path))
    return sum(1 for n in nodes.values() if getattr(n, "type", None) == "c")


def extract_route_metrics(log: str, orders: int) -> RouteMetrics:
    e1_distance = 0.0
    e2_distance = 0.0
    e1_routes = 0
    e2_routes = 0
    section = None

    for line in log.splitlines():
        if line.startswith("--- E1"):
            section = "e1"
            continue
        if line.startswith("--- E2"):
            section = "e2"
            continue

        match = re.search(r"距离=([0-9]+(?:\.[0-9]+)?)", line)
        if not match:
            continue
        distance = float(match.group(1))
        if section == "e1":
            e1_distance += distance
            e1_routes += 1
        elif section == "e2":
            e2_distance += distance
            e2_routes += 1

    if e1_distance <= 0 and e2_distance <= 0:
        raise ValueError("No route distances found in solver log")

    return RouteMetrics(
        orders=orders,
        e1_distance_km=e1_distance,
        e2_distance_km=e2_distance,
        e1_vehicle_count=e1_routes,
        e2_vehicle_count=e2_routes,
    )


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def cost_components(breakdown: dict) -> list[tuple[str, float]]:
    labels = {
        "transport_cost": "Transport",
        "package_purchase_cost": "Package purchase",
        "end_of_life_cost": "Disposal",
        "fixed_pool_cost": "Container pool",
        "cleaning_sorting_cost": "Cleaning/sorting",
        "loss_replacement_cost": "Loss replacement",
        "tracking_scan_cost": "Tracking/scanning",
    }
    components = [
        (label, float(breakdown.get(key, 0.0)))
        for key, label in labels.items()
        if float(breakdown.get(key, 0.0)) > 1e-9
    ]
    components.sort(key=lambda x: x[1], reverse=True)
    return components


def plot_component_pareto(
    single_use_breakdown: dict,
    reusable_breakdown: dict,
    out_path: Path,
    title: str,
) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(12, 5.2), sharey=False)
    schemes = [
        ("Single-use", cost_components(single_use_breakdown), axes[0], "#4C78A8"),
        ("Reusable", cost_components(reusable_breakdown), axes[1], "#E45756"),
    ]

    for scheme_name, components, ax, color in schemes:
        names = [name for name, _ in components]
        values = [value for _, value in components]
        total = sum(values)
        cumulative = []
        running = 0.0
        for value in values:
            running += value
            cumulative.append(running / total * 100.0 if total else 0.0)

        ax.bar(names, values, color=color, alpha=0.82)
        ax.set_title(scheme_name)
        ax.set_ylabel("Cost (yuan)")
        ax.tick_params(axis="x", rotation=35, labelsize=8)
        ax.grid(axis="y", alpha=0.22)

        ax2 = ax.twinx()
        ax2.plot(names, cumulative, color="#333333", marker="o", linewidth=1.8)
        ax2.set_ylim(0, 105)
        ax2.set_ylabel("Cumulative share (%)")
        ax2.axhline(80, color="#777777", linestyle="--", linewidth=1.0)

    fig.suptitle(title)
    fig.tight_layout()
    fig.savefig(out_path, dpi=250)
    plt.close(fig)


def _sample_rows_for_bars(
    rows: list[dict],
    max_bars: int = 45,
    min_demand: int = 1,
) -> list[dict]:
    rows = [r for r in rows if r["demand"] >= min_demand]
    if not rows:
        return []
    if len(rows) <= max_bars:
        return rows
    stride = max(1, len(rows) // max_bars)
    sampled = rows[::stride]
    if sampled[-1]["demand"] != rows[-1]["demand"]:
        sampled.append(rows[-1])
    return sampled


def plot_advantage_combo(
    rows: list[dict],
    break_even: float | None,
    out_path: Path,
    title: str,
    min_demand: int = 1,
) -> None:
    sampled = _sample_rows_for_bars(rows, min_demand=min_demand)
    if not sampled:
        return
    demands = [r["demand"] for r in sampled]
    saving_rates = [r["saving_rate"] * 100.0 for r in sampled]
    ratios = [r["cost_ratio_ru_to_su"] for r in sampled]
    bar_colors = ["#54A24B" if v >= 0 else "#E45756" for v in saving_rates]

    fig, ax = plt.subplots(figsize=(10, 5.4))
    ax.bar(demands, saving_rates, width=18, color=bar_colors, alpha=0.78)
    ax.axhline(0, color="#333333", linestyle="-", linewidth=1.0)
    ax.set_xlabel("Cumulative orders / container cycles")
    ax.set_ylabel("Saving rate (%)")
    ax.grid(axis="y", alpha=0.25)
    y_low = min(-10.0, min(saving_rates) * 1.15)
    y_high = max(10.0, max(saving_rates) * 1.15)
    ax.set_ylim(y_low, y_high)

    ax2 = ax.twinx()
    ax2.plot(demands, ratios, color="#F58518", marker="o", linewidth=1.8)
    ax2.axhline(1.0, color="#333333", linestyle="--", linewidth=1.0)
    ax2.set_ylabel("Cost ratio (reusable / single-use)")
    ax2.set_ylim(0.5, 2.0)

    if break_even is not None:
        ax.axvline(break_even, color="#555555", linestyle=":", linewidth=1.2)
        ax.text(
            break_even,
            min(saving_rates) if saving_rates else 0,
            f"break-even {break_even:.0f}",
            rotation=90,
            va="bottom",
            ha="right",
            fontsize=8,
            color="#555555",
        )

    ax.set_title(title)
    fig.tight_layout()
    fig.savefig(out_path, dpi=250)
    plt.close(fig)


def plot_comparison(rows: list[dict], break_even: float | None, out_path: Path) -> None:
    demands = [r["demand"] for r in rows]
    su_total = [r["single_use_total"] for r in rows]
    ru_total = [r["reusable_total"] for r in rows]

    plt.figure(figsize=(9, 5.4))
    plt.plot(demands, su_total, label="Single-use packaging", linewidth=2.2)
    plt.plot(demands, ru_total, label="Reusable container", linewidth=2.2)
    if break_even is not None:
        plt.axvline(break_even, color="#555555", linestyle="--", linewidth=1.4)
        plt.text(
            break_even,
            min(max(su_total), max(ru_total)) * 0.08,
            f"break-even: {break_even:.0f} orders",
            rotation=90,
            va="bottom",
            ha="right",
            fontsize=9,
        )
    plt.xlabel("Cumulative orders / container cycles")
    plt.ylabel("Total cost (yuan)")
    plt.title("Economic comparison: single-use vs reusable containers")
    plt.grid(True, alpha=0.25)
    plt.legend()
    plt.tight_layout()
    plt.savefig(out_path, dpi=250)
    plt.close()


def plot_scenarios(
    scenario_rows: dict[str, list[dict]],
    scenario_break_evens: dict[str, float | None],
    out_path: Path,
) -> None:
    plt.figure(figsize=(9.6, 5.8))
    colors = {
        "baseline_standard_carton": ("#4C78A8", "#F58518"),
        "large_or_reinforced_package": ("#54A24B", "#E45756"),
    }
    for name, rows in scenario_rows.items():
        demands = [r["demand"] for r in rows]
        su_total = [r["single_use_total"] for r in rows]
        ru_total = [r["reusable_total"] for r in rows]
        c_su, c_ru = colors.get(name, (None, None))
        label_prefix = name.replace("_", " ")
        plt.plot(
            demands,
            su_total,
            label=f"{label_prefix}: single-use",
            linewidth=2.0,
            color=c_su,
        )
        plt.plot(
            demands,
            ru_total,
            label=f"{label_prefix}: reusable",
            linewidth=2.0,
            linestyle="--",
            color=c_ru,
        )
        be = scenario_break_evens.get(name)
        if be is not None:
            plt.axvline(be, color=c_ru, linestyle=":", linewidth=1.2)
            plt.text(
                be,
                min(max(su_total), max(ru_total)) * 0.05,
                f"{name}: {be:.0f}",
                rotation=90,
                va="bottom",
                ha="right",
                fontsize=8,
                color=c_ru,
            )

    plt.xlabel("Cumulative orders / container cycles")
    plt.ylabel("Total cost (yuan)")
    plt.title("Cost sensitivity by single-use packaging price")
    plt.grid(True, alpha=0.25)
    plt.legend(fontsize=8)
    plt.tight_layout()
    plt.savefig(out_path, dpi=250)
    plt.close()


def plot_ratio_scenarios(
    scenario_rows: dict[str, list[dict]],
    scenario_break_evens: dict[str, float | None],
    out_path: Path,
    y_min: float = 0.5,
    y_max: float = 2.0,
) -> None:
    plt.figure(figsize=(9.6, 5.2))
    colors = {
        "baseline_standard_carton": "#F58518",
        "large_or_reinforced_package": "#E45756",
    }
    for name, rows in scenario_rows.items():
        demands = [r["demand"] for r in rows]
        ratios = [r["cost_ratio_ru_to_su"] for r in rows]
        label = name.replace("_", " ")
        color = colors.get(name)
        plt.plot(
            demands,
            ratios,
            label=f"{label}: TC_RU / TC_SU",
            linewidth=2.1,
            color=color,
        )
        be = scenario_break_evens.get(name)
        if be is not None:
            plt.axvline(be, color=color, linestyle=":", linewidth=1.2)
            plt.text(
                be,
                0.52,
                f"break-even {be:.0f}",
                rotation=90,
                va="bottom",
                ha="right",
                fontsize=8,
                color=color,
            )

    plt.axhline(1.0, color="#333333", linestyle="--", linewidth=1.2)
    plt.text(
        0.99,
        1.01,
        "ratio = 1",
        ha="right",
        va="bottom",
        transform=plt.gca().get_yaxis_transform(),
        fontsize=9,
        color="#333333",
    )
    plt.xlabel("Cumulative orders / container cycles")
    plt.ylabel("Cost ratio (reusable / single-use)")
    plt.title("Reusable-container cost ratio by demand scale")
    plt.ylim(bottom=y_min, top=y_max)
    plt.grid(True, alpha=0.25)
    plt.legend(fontsize=8)
    plt.tight_layout()
    plt.savefig(out_path, dpi=250)
    plt.close()


def params_for_scenario(base: EconomicParams, scenario: dict) -> EconomicParams:
    """Generic override: any field present in the scenario replaces the base value.

    This lets a single mechanism sweep any parameter (package price, loss rate,
    cleaning cost, transport rate, ...) without bespoke code per parameter.
    """
    return EconomicParams(
        single_use_package_price=float(
            scenario.get("single_use_package_price_yuan", base.single_use_package_price)
        ),
        single_use_eol_cost=float(
            scenario.get("single_use_eol_cost_yuan", base.single_use_eol_cost)
        ),
        reusable_container_price=float(
            scenario.get("reusable_container_price_yuan", base.reusable_container_price)
        ),
        reusable_pool_size=int(
            scenario.get("reusable_pool_size", base.reusable_pool_size)
        ),
        reusable_cleaning_sorting_cost=float(
            scenario.get(
                "reusable_cleaning_sorting_cost_yuan", base.reusable_cleaning_sorting_cost
            )
        ),
        reusable_loss_rate=float(
            scenario.get("reusable_loss_rate_per_cycle", base.reusable_loss_rate)
        ),
        reusable_tracking_scan_cost=float(
            scenario.get(
                "reusable_tracking_scan_cost_yuan", base.reusable_tracking_scan_cost
            )
        ),
        e1_cost_per_km=float(
            scenario.get("e1_cost_yuan_per_km", base.e1_cost_per_km)
        ),
        e2_cost_per_km=float(
            scenario.get("e2_cost_yuan_per_km", base.e2_cost_per_km)
        ),
    )


def loss_rate_sensitivity(
    su_per_order: RouteMetrics,
    ru_per_order: RouteMetrics,
    base_params: EconomicParams,
    rates: list[float],
    fixed_demand: int,
) -> list[dict]:
    """Break-even demand and reusable cost vs reusable container loss rate.

    Loss rate only affects the reusable scheme, so single-use total is constant
    across rates and computed once. break_even_demand is None when the marginal
    saving is non-positive (reusable never pays off at that loss rate).
    """
    su_total = single_use_cost(su_per_order, base_params, fixed_demand).total_cost
    rows = []
    for rate in rates:
        params_r = params_for_scenario(
            base_params, {"reusable_loss_rate_per_cycle": rate}
        )
        be = break_even_demand(su_per_order, ru_per_order, params_r)
        ru_total = reusable_cost(ru_per_order, params_r, fixed_demand).total_cost
        rows.append(
            {
                "loss_rate": rate,
                "break_even_demand": be,
                "single_use_total": su_total,
                "reusable_total": ru_total,
                "saving_rate": (su_total - ru_total) / su_total if su_total else float("-inf"),
                "cost_ratio_ru_to_su": ru_total / su_total if su_total else float("inf"),
            }
        )
    return rows


def plot_loss_rate_sensitivity(
    rows: list[dict], out_path: Path, fixed_demand: int
) -> None:
    if not rows:
        return
    rates = [r["loss_rate"] * 100.0 for r in rows]
    valid_be = [(rt, r["break_even_demand"]) for rt, r in zip(rates, rows)
                if r["break_even_demand"] is not None]

    fig, ax = plt.subplots(figsize=(9.5, 5.4))
    if valid_be:
        v_rates, v_be = zip(*valid_be)
        ax.plot(list(v_rates), list(v_be), color="#4C78A8", marker="o",
                linewidth=2.2, label="Break-even demand")
        ax.set_ylim(bottom=0)
    ax.set_xlabel("Reusable container loss rate (%)")
    ax.set_ylabel("Break-even demand (orders)", color="#4C78A8")
    ax.tick_params(axis="y", labelcolor="#4C78A8")
    ax.grid(True, alpha=0.25)

    ax2 = ax.twinx()
    ru_totals = [r["reusable_total"] for r in rows]
    ax2.plot(rates, ru_totals, color="#E45756", marker="s", linewidth=2.0,
             linestyle="--", label="Reusable total cost")
    su_total = rows[0]["single_use_total"]
    if su_total:
        ax2.axhline(su_total, color="#54A24B", linewidth=1.4, linestyle=":",
                    label=f"Single-use total ({su_total:.0f})")
    ax2.set_ylabel(f"Total cost at D={fixed_demand} (yuan)", color="#E45756")
    ax2.tick_params(axis="y", labelcolor="#E45756")

    ax.set_title("Break-even demand & reusable cost vs container loss rate")
    fig.tight_layout()
    fig.savefig(out_path, dpi=250)
    plt.close(fig)


def main() -> None:
    app_cfg = load_json(CONFIG_DIR / "app_config.json")
    fleet_cfg = load_json(CONFIG_DIR / "fleet_config.json")
    econ_cfg = load_json(CONFIG_DIR / "economic_cost_config.json")
    params = EconomicParams.from_config(econ_cfg)

    data_path = ROOT / app_cfg["data"]
    orders = count_customers(data_path)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    su_result = run_one(
        1,
        "alns",
        str(data_path),
        fleet_cfg,
        alns_time=app_cfg.get("alns_time", 30),
        seed=app_cfg.get("seed", 42),
    )
    ru_result = run_one(
        4,
        "alns",
        str(data_path),
        fleet_cfg,
        alns_time=app_cfg.get("alns_time", 30),
        seed=app_cfg.get("seed", 42),
    )

    if su_result["status"] != "OK" or ru_result["status"] != "OK":
        raise RuntimeError(f"Routing failed: SU={su_result['status']} RU={ru_result['status']}")

    su_metrics = extract_route_metrics(su_result["log"], orders)
    ru_metrics = extract_route_metrics(ru_result["log"], orders)
    su_per_order = su_metrics.per_order()
    ru_per_order = ru_metrics.per_order()

    demand_min = int(econ_cfg["plot"].get("demand_min", 1))
    demand_max = int(econ_cfg["plot"].get("demand_max", 1000))
    demand_step = int(econ_cfg["plot"].get("demand_step", 10))
    demands = list(range(demand_min, demand_max + 1, demand_step))
    if orders not in demands:
        demands.append(orders)
        demands.sort()

    rows = demand_sweep(su_per_order, ru_per_order, params, demands)
    be = break_even_demand(su_per_order, ru_per_order, params)

    write_csv(OUTPUT_DIR / "cost_curve.csv", rows)
    plot_comparison(rows, be, OUTPUT_DIR / "cost_comparison.png")

    scenario_summaries = []
    scenario_rows = {}
    scenario_break_evens = {}
    for scenario in econ_cfg.get("scenarios", []):
        scenario_name = scenario["name"]
        scenario_params = params_for_scenario(params, scenario)
        scenario_curve = demand_sweep(
            su_per_order, ru_per_order, scenario_params, demands
        )
        scenario_be = break_even_demand(
            su_per_order, ru_per_order, scenario_params
        )
        scenario_rows[scenario_name] = scenario_curve
        scenario_break_evens[scenario_name] = scenario_be
        write_csv(OUTPUT_DIR / f"cost_curve_{scenario_name}.csv", scenario_curve)
        pareto_demand = int(round(scenario_be)) if scenario_be else demand_max
        su_pareto = single_use_cost(su_per_order, scenario_params, pareto_demand)
        ru_pareto = reusable_cost(ru_per_order, scenario_params, pareto_demand)
        component_pareto_path = OUTPUT_DIR / f"pareto_components_{scenario_name}.png"
        advantage_combo_path = OUTPUT_DIR / f"pareto_advantage_{scenario_name}.png"
        plot_component_pareto(
            su_pareto.as_dict(),
            ru_pareto.as_dict(),
            component_pareto_path,
            title=(
                f"Cost component Pareto at D={pareto_demand} "
                f"({scenario_name.replace('_', ' ')})"
            ),
        )
        plot_advantage_combo(
            scenario_curve,
            scenario_be,
            advantage_combo_path,
            title=f"Cost advantage combo ({scenario_name.replace('_', ' ')})",
            min_demand=int(econ_cfg["plot"].get("advantage_combo_min_demand", 1)),
        )
        scenario_summaries.append(
            {
                "name": scenario_name,
                "description": scenario.get("description", ""),
                "single_use_package_price_yuan": scenario_params.single_use_package_price,
                "marginal_saving_per_order_without_fixed_pool": marginal_saving_per_order(
                    su_per_order, ru_per_order, scenario_params
                ),
                "break_even_demand": scenario_be,
                "pareto_demand": pareto_demand,
                "component_pareto_plot": str(component_pareto_path.relative_to(ROOT)),
                "advantage_combo_plot": str(advantage_combo_path.relative_to(ROOT)),
            }
        )

    if scenario_rows:
        plot_scenarios(
            scenario_rows,
            scenario_break_evens,
            OUTPUT_DIR / "cost_comparison_scenarios.png",
        )
        plot_ratio_scenarios(
            scenario_rows,
            scenario_break_evens,
            OUTPUT_DIR / "cost_ratio_scenarios.png",
            y_min=float(econ_cfg["plot"].get("ratio_y_min", 0.5)),
            y_max=float(econ_cfg["plot"].get("ratio_y_max", 2.0)),
        )

    # Loss-rate sensitivity sweep (break-even demand & reusable cost vs loss rate)
    lr_cfg = econ_cfg.get("loss_rate_sensitivity")
    loss_rate_rows: list[dict] = []
    loss_rate_fixed_demand = orders
    if lr_cfg:
        rates = [float(r) for r in lr_cfg.get("rates", [])]
        fd = lr_cfg.get("fixed_demand")
        loss_rate_fixed_demand = int(fd) if fd else orders
        loss_rate_rows = loss_rate_sensitivity(
            su_per_order, ru_per_order, params, rates, loss_rate_fixed_demand
        )
        write_csv(OUTPUT_DIR / "loss_rate_sensitivity.csv", loss_rate_rows)
        plot_loss_rate_sensitivity(
            loss_rate_rows,
            OUTPUT_DIR / "loss_rate_sensitivity.png",
            loss_rate_fixed_demand,
        )

    su_now = single_use_cost(su_per_order, params, orders)
    ru_now = reusable_cost(ru_per_order, params, orders)
    summary = {
        "data": str(data_path.relative_to(ROOT)),
        "orders_in_instance": orders,
        "single_use_route_metrics": su_metrics.__dict__,
        "reusable_route_metrics": ru_metrics.__dict__,
        "derived_transport_cost_yuan_per_km": {
            "e1": params.e1_cost_per_km,
            "e2": params.e2_cost_per_km,
        },
        "single_use_cost_at_instance_orders": su_now.as_dict(),
        "reusable_cost_at_instance_orders": ru_now.as_dict(),
        "marginal_saving_per_order_without_fixed_pool": marginal_saving_per_order(
            su_per_order, ru_per_order, params
        ),
        "break_even_demand": be,
        "scenario_summaries": scenario_summaries,
        "loss_rate_sensitivity": {
            "fixed_demand": loss_rate_fixed_demand,
            "rows": loss_rate_rows,
            "plot": str((OUTPUT_DIR / "loss_rate_sensitivity.png").relative_to(ROOT))
            if lr_cfg
            else None,
            "csv": str((OUTPUT_DIR / "loss_rate_sensitivity.csv").relative_to(ROOT))
            if lr_cfg
            else None,
        },
        "outputs": {
            "csv": str((OUTPUT_DIR / "cost_curve.csv").relative_to(ROOT)),
            "plot": str((OUTPUT_DIR / "cost_comparison.png").relative_to(ROOT)),
            "scenario_plot": str(
                (OUTPUT_DIR / "cost_comparison_scenarios.png").relative_to(ROOT)
            ),
            "ratio_plot": str(
                (OUTPUT_DIR / "cost_ratio_scenarios.png").relative_to(ROOT)
            ),
            "loss_rate_plot": str(
                (OUTPUT_DIR / "loss_rate_sensitivity.png").relative_to(ROOT)
            )
            if lr_cfg
            else None,
        },
    }
    with open(OUTPUT_DIR / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
