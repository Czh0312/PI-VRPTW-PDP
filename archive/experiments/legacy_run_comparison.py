# cli/compare.py
# -*- coding: utf-8 -*-
"""
批量对比实验：4 规模 × 4 模型 × 2 求解器 (certified exact / ALNS)
跑完每个规模下全部实例，按 (规模, 模型, 求解器) 聚合求均值，
最终输出对比图 + 对比表格。
"""
import json
import time
import io
import traceback
from contextlib import redirect_stdout
from pathlib import Path
from statistics import mean, stdev

from models.routing.common.domain.instance import ProblemInstance
from models.routing.variants.model_1.solver import solve_exact as exact_m1
from models.routing.variants.model_3.solver import solve_exact as exact_m3
from models.routing.variants.model_5.solver import solve_exact as exact_m5
from models.routing.variants.model_6.solver import solve_exact as exact_m6
from models.routing.variants.model_1.alns_solver import solve_alns as alns_m1
from models.routing.variants.model_3.alns_solver import solve_alns as alns_m3
from models.routing.variants.model_5.alns_solver import solve_alns as alns_m5
from models.routing.variants.model_6.alns_solver import solve_alns as alns_m6
from models.routing.variants.model_7.alns_solver import solve_alns as alns_m7
from models.routing.common.data_loader import parse_dataset
from models.routing.common.task_builder import build_tasks_for_model

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = PROJECT_ROOT / "config"
OUTPUT_DIR = PROJECT_ROOT / "output"
DATA_DIR = PROJECT_ROOT / "data"

EXACT_SOLVERS = {1: exact_m1, 3: exact_m3, 5: exact_m5, 6: exact_m6}
ALNS_SOLVERS = {1: alns_m1, 3: alns_m3, 5: alns_m5, 6: alns_m6, 7: alns_m7}


def load_fleet_config():
    with open(CONFIG_DIR / "routing" / "validation" / "zhou_small.json", "r", encoding="utf-8") as f:
        return json.load(f)


# ─── 工具函数 ───

def _to_phys(nid):
    if nid.startswith("MEET@"):
        nid = nid.split("@", 1)[1]
    if nid.startswith("TRANS_PICK_") or nid.startswith("TRANS_DROP_"):
        payload = nid.split("_", 2)[-1]
        return payload.rsplit("@", 1)[-1]
    if nid.endswith(('d', 'p')) and len(nid) > 1 and not nid.startswith("TRANS"):
        return nid[:-1]
    return nid


def _route_distance(problem, route):
    return sum(problem.distance(_to_phys(route[i]), _to_phys(route[i + 1]))
               for i in range(len(route) - 1))


def _total_cost(problem, e1_routes, e2_routes_by_sat):
    cost = sum(_route_distance(problem, r) for r in e1_routes)
    for routes in e2_routes_by_sat.values():
        cost += sum(_route_distance(problem, r) for r in routes)
    return cost


def _distance_breakdown(problem, e1_routes, e2_routes_by_sat):
    """Return route distance by echelon without scraping human-readable logs."""
    e1_distance = sum(_route_distance(problem, route) for route in e1_routes)
    e2_distance = sum(
        _route_distance(problem, route)
        for routes in e2_routes_by_sat.values()
        for route in routes
    )
    return e1_distance, e2_distance


def _count_e2(e2_by_sat):
    return sum(len(v) for v in e2_by_sat.values())


# ─── 单次求解 ───

def run_one(model, solver, data_path, fleet_cfg, alns_time=120,
            exact_time=300, seed=42, solver_kwargs=None):
    """
    运行单次实验。
    solver: "exact" | "alns"
    Exact rows are counted only when the solver has proved global optimality.
    A time-limited MIP incumbent is retained as diagnostic data, but is never
    folded into the Exact means, tables, or success rates.
    """
    nodes_dict, _ = parse_dataset(str(data_path))
    problem = ProblemInstance(nodes_dict, fleet_cfg, speed=fleet_cfg.get("speed", 1.0))
    problem.aggregate_demands_from_pre_assignment()
    e1_tasks, e2_tasks_by_sat = build_tasks_for_model(problem, model)
    n_customers = len({t.cust for sat_tasks in e2_tasks_by_sat.values()
                       for t in sat_tasks})

    # Model 5's joint relocation MIP does not scale: with the full relocation
    # candidate set (required for proven_optimal) model construction hangs at
    # 50/100 customers, and a restricted candidate set yields a degenerate
    # empty model. Skip M5 exact at large scale rather than hang the batch
    # (see backlog task on build_model5_joint_exact).
    if solver == "exact" and model == 5 and n_customers >= 50:
        return dict(
            cost=None, time=0.0, e1=0, e2=0,
            e1_distance=None, e2_distance=None,
            status="UNAVAILABLE_M5_NOT_SCALABLE",
            solver_info={
                "solution_type": "none",
                "proven_optimal": False,
            },
            log=f"Model 5 exact skipped at {n_customers} customers: joint MIP "
                "construction does not scale.\n",
        )

    if solver == "exact":
        if model not in EXACT_SOLVERS:
            return dict(
                cost=None, time=0.0, e1=0, e2=0,
                e1_distance=None, e2_distance=None,
                status=f"UNAVAILABLE_EXACT_MODEL_{model}",
                solver_info={"solution_type": "none", "proven_optimal": False},
                log=f"Model {model} has no exact solver.\n",
            )
        fn = EXACT_SOLVERS[model]
        kwargs = dict(
            problem=problem, e1_tasks=e1_tasks,
            e2_tasks_by_sat=e2_tasks_by_sat,
            vehicles_per_satellite=problem.fleet.e2_per_sat_count,
            time_limit=exact_time,
            verbose=True,
            return_info=True,
            # Capture time-limited incumbents as FEASIBLE_NOT_PROVEN diagnostic
            # data (excluded from Exact aggregates, which require proven_optimal).
            # Without this, an arc-flow MIP that only reaches a time-limited
            # incumbent returns no routes at all and looks like infeasibility.
            return_incumbent_on_time_limit=True,
        )
        if model in (3, 6):
            kwargs["TFp"] = 9999.0
        # ALNS warm start (models 1/2/3). Without it the 50/100-customer
        # arc-flow MIP finds no feasible solution within the exact time budget;
        # seeding Gurobi with an ALNS incumbent turns "no solution" into a real
        # solve (incumbent + bound), recorded as FEASIBLE_NOT_PROVEN, never as
        # proven. Gated to large instances: small ones prove optimal in seconds
        # and only waste the ALNS budget. Compact M6 also accepts ordinary
        # no-transfer routes as a valid incumbent.
        if model in (1, 3, 5, 6) and n_customers >= 50:
            alns_fn = ALNS_SOLVERS[model]
            alns_kwargs = dict(
                problem=problem, e1_tasks=e1_tasks,
                e2_tasks_by_sat=e2_tasks_by_sat,
                vehicles_per_satellite=problem.fleet.e2_per_sat_count,
                max_iterations=5000, max_time=min(alns_time, 60), seed=seed,
                verbose=False,
            )
            if model == 3:
                alns_kwargs["TFp"] = 9999.0
            try:
                ws_buf = io.StringIO()
                with redirect_stdout(ws_buf):
                    ae1, ae2 = alns_fn(**alns_kwargs)
                if ae1 and ae2:
                    kwargs["warm_start_routes"] = (ae1, ae2)
                    kwargs["gurobi_params"] = {"MIPFocus": 1, "Heuristics": 0.5}
            except Exception:
                pass
    else:
        fn = ALNS_SOLVERS[model]
        kwargs = dict(
            problem=problem, e1_tasks=e1_tasks,
            e2_tasks_by_sat=e2_tasks_by_sat,
            vehicles_per_satellite=problem.fleet.e2_per_sat_count,
            max_iterations=5000, max_time=alns_time, seed=seed,
            verbose=True,
        )
        if model in (3, 6):
            kwargs["TFp"] = 9999.0

    if solver_kwargs:
        kwargs.update(solver_kwargs)

    log_buf = io.StringIO()
    t0 = time.time()
    try:
        with redirect_stdout(log_buf):
            result = fn(**kwargs)
        elapsed = time.time() - t0
        if solver == "exact":
            e1_routes, e2_routes, solve_info = result
        else:
            e1_routes, e2_routes = result
            solve_info = None
        if e1_routes and e2_routes:
            e1_distance, e2_distance = _distance_breakdown(
                problem, e1_routes, e2_routes)
            proven_optimal = bool(solve_info and solve_info.get("proven_optimal"))
            status = "PROVEN_OPTIMAL" if solver == "exact" and proven_optimal else (
                "FEASIBLE_NOT_PROVEN" if solver == "exact" else "OK"
            )
            return dict(
                cost=_total_cost(problem, e1_routes, e2_routes),
                time=elapsed, e1=len(e1_routes), e2=_count_e2(e2_routes),
                e1_distance=e1_distance, e2_distance=e2_distance,
                status=status, solver_info=solve_info, log=log_buf.getvalue(),
            )
        status = "NO_PROOF" if solver == "exact" else "INFEASIBLE"
        return dict(cost=None, time=elapsed, e1=0, e2=0,
                    e1_distance=None, e2_distance=None, status=status,
                    solver_info=solve_info, log=log_buf.getvalue())
    except Exception as e:
        elapsed = time.time() - t0
        return dict(cost=None, time=elapsed, e1=0, e2=0,
                    e1_distance=None, e2_distance=None,
                    status=f"ERROR: {e}", solver_info=None,
                    log=log_buf.getvalue())


# ─── 绘图 ───

def _extract_num(label):
    import re
    m = re.match(r"(\d+)", label)
    return int(m.group(1)) if m else 0


def _metric_or_nan(summary, metric):
    """Return NaN for failed combinations so plots do not imply zero cost/time."""
    import math
    if not summary or summary.get("n", 0) <= 0:
        return math.nan
    value = summary.get(metric, 0)
    return value if value and value > 0 else math.nan


def comparison_output_dirs(output_dir):
    """Centralize comparison outputs so figures, tables, and data stay separated."""
    dirs = {
        "data": output_dir / "00_data",
        "main": output_dir / "01_main_figures",
        "diagnostics": output_dir / "02_solver_diagnostics",
        "tables": output_dir / "03_tables",
        "logs": output_dir / "04_logs",
        "legacy": output_dir / "99_legacy",
    }
    for path in dirs.values():
        path.mkdir(parents=True, exist_ok=True)
    return dirs


def write_comparison_readme(output_dir):
    out_dirs = comparison_output_dirs(output_dir)
    readme = output_dir / "README.md"
    readme.write_text(
        "# Comparison Output Guide\n\n"
        "This folder is organized by output purpose:\n\n"
        "- `00_data/`: machine-readable experiment summaries, such as `data_01_comparison_summary_by_scale_model_solver.json`.\n"
        "- `01_main_figures/`: presentation/report-ready figures that communicate the main findings.\n"
        "- `02_solver_diagnostics/`: detailed solver comparison plots for checking behavior and anomalies.\n"
        "- `03_tables/`: table-style image outputs for reports or appendices.\n"
        "- `04_logs/`: comparison logs or trace outputs, when available.\n"
        "- `99_legacy/`: older or less-central figures kept for reference.\n\n"
        "Naming convention: `<kind>_<group>_<number>_<scope>_<metric>_<comparison>.<ext>`.\n\n"
        "Recommended figures for reports:\n\n"
        "- `01_main_figures/fig_main_01_all_scales_alns_cost_by_model.png`\n"
        "- `01_main_figures/fig_main_02_all_scales_alns_gap_m5_m6_vs_m3.png`\n"
        "- `01_main_figures/fig_main_03_all_scales_exact_success_rate.png` (certified optimality only)\n",
        encoding="utf-8",
    )
    return out_dirs


def plot_all(avg_results, output_dir):
    """
    avg_results: dict[ scale_key ][ (model, solver) ] = {cost_mean, cost_std, ...}
    生成对比图。
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    plt.rcParams.update({
        "font.family": "sans-serif",
        "font.sans-serif": ["Helvetica Neue", "Arial", "PingFang SC", "SimHei"],
        "font.size": 11,
        "axes.unicode_minus": False,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.linewidth": 0.8,
        "axes.grid": True,
        "grid.linestyle": "--", "grid.alpha": 0.35, "grid.color": "#CCCCCC",
        "figure.facecolor": "white", "axes.facecolor": "#FAFAFA",
        "legend.frameon": True, "legend.framealpha": 0.9,
        "legend.edgecolor": "#DDDDDD", "legend.fancybox": True,
    })

    out_dirs = write_comparison_readme(output_dir)
    scales = sorted(avg_results.keys(), key=_extract_num)
    scale_labels = [str(_extract_num(s)) for s in scales]
    model_names = {
        1: "M1 Delivery",
        2: "M3 Pickup-Delivery",
        3: "M5 Iterative Trans.",
        4: "M6 Two-Phase Trans.",
    }
    model_short = {1: "M1", 3: "M3", 5: "M5", 6: "M6"}
    solver_labels = {"exact": "Certified Exact", "alns": "ALNS"}
    model_colors = {1: "#4C78A8", 3: "#E45756", 5: "#F58518", 6: "#72B7B2"}
    solver_alpha = {"exact": 0.92, "alns": 0.72}
    combo_marker = {"exact": "o", "alns": "s"}
    combo_ls = {"exact": "-", "alns": "--"}

    x = np.arange(len(scales))
    models = [1, 3, 5, 6]
    solvers = ["exact", "alns"]

    def _series(model, solver, metric):
        values, stds, ok_counts, total_counts = [], [], [], []
        for s in scales:
            d = avg_results[s].get((model, solver), {})
            values.append(_metric_or_nan(d, metric))
            std_key = metric.replace("_mean", "_std")
            stds.append(d.get(std_key, 0) if d.get("n", 0) > 0 else np.nan)
            ok_counts.append(d.get("n", 0))
            total_counts.append(d.get("n_total", 0))
        return (
            np.array(values, dtype=float),
            np.array(stds, dtype=float),
            np.array(ok_counts, dtype=int),
            np.array(total_counts, dtype=int),
        )

    def _annotate_missing(ax, xpos, ok_counts, total_counts):
        ymin, ymax = ax.get_ylim()
        y = ymin + (ymax - ymin) * 0.025
        for xi, ok, total in zip(xpos, ok_counts, total_counts):
            if total and ok == 0:
                ax.text(xi, y, "Fail", ha="center", va="bottom",
                        fontsize=7, color="#A23B3B", rotation=90)

    def _finish_axis(ax, title, ylabel):
        ax.set_title(title, fontsize=13, fontweight="bold", pad=10, color="#333")
        ax.set_xlabel("Number of Customers", fontsize=11, color="#555")
        ax.set_ylabel(ylabel, fontsize=11, color="#555")
        ax.set_xticks(x)
        ax.set_xticklabels(scale_labels, fontsize=10)
        ax.margins(x=0.04)

    # ────────────── Figure 1: 分组柱状图 (Exact / ALNS 两列子图) ──────────────
    fig, axes = plt.subplots(1, 2, figsize=(18, 7))
    fig.suptitle("2E-VRPTW-PDD: Cost Comparison by Solver",
                 fontsize=18, fontweight="bold", y=0.99, color="#222222")

    for col, solver in enumerate(solvers):
        ax = axes[col]
        width = 0.17
        for i, m in enumerate(models):
            means, stds, ok_counts, total_counts = _series(m, solver, "cost_mean")
            pos = x + (i - 1.5) * width
            valid = ~np.isnan(means)
            bars = ax.bar(pos[valid], means[valid], width,
                          yerr=stds[valid], capsize=3,
                          label=model_short[m], color=model_colors[m],
                          alpha=solver_alpha[solver],
                          edgecolor="white", linewidth=0.6, zorder=3,
                          error_kw={"linewidth": 0.8, "color": "#555"})
            valid_ok = ok_counts[valid]
            valid_total = total_counts[valid]
            for bar, mu, ok, total in zip(bars, means[valid], valid_ok, valid_total):
                ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height(),
                        f"{mu:.0f}", ha="center", va="bottom", fontsize=7,
                        color="#444")
                if total and ok < total:
                    ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() * 0.96,
                            f"{ok}/{total}", ha="center", va="top", fontsize=6,
                            color="white", fontweight="bold")
            _annotate_missing(ax, pos, ok_counts, total_counts)
        _finish_axis(ax, f"{solver_labels[solver]} Solver", "Avg. Total Cost")
        ax.legend(fontsize=9, ncol=2, loc="upper left")

    plt.tight_layout(rect=[0, 0, 1, 0.95])
    fig.savefig(out_dirs["diagnostics"] / "fig_diag_01_all_scales_cost_exact_vs_alns_by_solver.png", dpi=250,
                bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print("[1/7] 柱状图已保存")

    # ────────────── Figure 2: 折线趋势 (8 条线) ──────────────
    fig, ax = plt.subplots(figsize=(13, 7))
    x_nums = [_extract_num(s) for s in scales]

    for m in models:
        for solver in solvers:
            means, stds, _, _ = _series(m, solver, "cost_mean")
            if np.any(~np.isnan(means)):
                lbl = f"{model_short[m]}-{solver_labels[solver]}"
                ax.plot(x_nums, means, marker=combo_marker[solver],
                        linestyle=combo_ls[solver], linewidth=2, markersize=7,
                        color=model_colors[m], alpha=solver_alpha[solver],
                        label=lbl, zorder=4)
                lower = np.maximum(means - stds, 0)
                upper = means + stds
                ax.fill_between(x_nums, lower, upper, where=~np.isnan(means),
                                alpha=0.07, color=model_colors[m], zorder=2)

    ax.set_title("Cost Trend: Exact (solid) vs ALNS (dashed), failed runs omitted",
                 fontsize=16, fontweight="bold", pad=15, color="#222")
    ax.set_xlabel("Number of Customers", fontsize=12, color="#555")
    ax.set_ylabel("Avg. Total Distance Cost", fontsize=12, color="#555")
    ax.set_xticks(x_nums)
    ax.set_xticklabels(scale_labels, fontsize=12)
    ax.legend(fontsize=8.5, ncol=2, loc="upper left", borderpad=1, labelspacing=0.6)

    plt.tight_layout()
    fig.savefig(out_dirs["diagnostics"] / "fig_diag_02_all_scales_cost_trend_exact_and_alns.png", dpi=250,
                bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print("[2/7] 趋势折线图已保存")

    # ────────────── Figure 3: 求解时间对比 ──────────────
    fig, axes = plt.subplots(1, 2, figsize=(16, 7))
    for col, solver in enumerate(solvers):
        ax = axes[col]
        for i, m in enumerate(models):
            means, stds, ok_counts, total_counts = _series(m, solver, "time_mean")
            pos = x + (i - 1.5) * 0.17
            valid = ~np.isnan(means)
            ax.bar(pos[valid], means[valid], 0.17, yerr=stds[valid], capsize=3,
                   label=model_short[m], color=model_colors[m],
                   alpha=solver_alpha[solver],
                   edgecolor="white", linewidth=0.6, zorder=3,
                   error_kw={"linewidth": 0.8, "color": "#555"})
        _finish_axis(ax, f"{solver_labels[solver]} Solve Time", "Time (s)")
        ax.set_yscale("log")
        ax.legend(fontsize=9, ncol=2, loc="upper left")

    plt.tight_layout()
    fig.savefig(out_dirs["diagnostics"] / "fig_diag_03_all_scales_solve_time_log_exact_and_alns.png", dpi=250,
                bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print("[3/7] 时间对比图已保存")

    # ────────────── Figure 4: 结果汇总表格 ──────────────
    plot_table(avg_results, scales, out_dirs["tables"])
    print("[4/7] 表格图已保存")

    plot_alns_cost_summary(avg_results, scales, out_dirs["main"])
    print("[5/7] ALNS 主对比图已保存")
    plot_gap_vs_m3(avg_results, scales, out_dirs["main"])
    print("[6/7] Gap 对比图已保存")
    plot_exact_success_heatmap(avg_results, scales, out_dirs["main"])
    print("[7/7] Exact 成功率图已保存")


def plot_table(avg_results, scales, output_dir):
    """生成 LaTeX 风格的对比结果表格图"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    solvers = ["exact", "alns"]
    models = [1, 3, 5, 6]
    solver_hdr = ["Exact", "ALNS"]
    model_hdr = ["M1", "M3", "M5", "M6"]

    # 构造 cell 文本：每个 (scale, solver) 对应 4 个 model
    n_scales = len(scales)
    n_solvers = 2
    n_models = 4

    # 行: scales, 列: solver × model
    n_cols = n_solvers * n_models
    n_rows = n_scales

    cell_text = []
    cell_colors = []
    for s in scales:
        row_text = []
        row_color = []
        for solver in solvers:
            # 找该 (scale, solver) 下的最佳 cost
            costs = []
            for m in models:
                d = avg_results[s].get((m, solver), {})
                c = d.get("cost_mean", None)
                if c is not None and c > 0 and d.get("n", 0) > 0:
                    costs.append(c)
            best = min(costs) if costs else None

            for m in models:
                d = avg_results[s].get((m, solver), {})
                c = d.get("cost_mean", None)
                n_ok = d.get("n", 0)
                if c is not None and n_ok > 0:
                    std = d.get("cost_std", 0)
                    txt = f"{c:.1f}\n({std:.1f})"
                    if best and abs(c - best) < 0.01:
                        bg = "#d4edda"  # 绿色标记最优
                    else:
                        bg = "white"
                else:
                    txt = "N/A"
                    bg = "#f8d7da"  # 红色标记失败
                row_text.append(txt)
                row_color.append(bg)
        cell_text.append(row_text)
        cell_colors.append(row_color)

    fig, ax = plt.subplots(figsize=(18, 3 + n_rows * 1.2))
    ax.axis("off")

    # 构建列头
    col_headers = []
    for sh in solver_hdr:
        for mh in model_hdr:
            col_headers.append(f"{sh}\n{mh}")

    row_labels = [str(_extract_num(s)) + " cust." for s in scales]

    table = ax.table(
        cellText=cell_text,
        cellColours=cell_colors,
        colLabels=col_headers,
        rowLabels=row_labels,
        loc="center",
        cellLoc="center",
    )
    table.auto_set_font_size(False)
    table.set_fontsize(9)
    table.scale(1.0, 2.2)

    # 美化
    for (row, col), cell in table.get_celld().items():
        cell.set_edgecolor("#CCCCCC")
        cell.set_linewidth(0.6)
        if row == 0:  # header
            cell.set_facecolor("#E8E8E8")
            cell.set_text_props(fontweight="bold", fontsize=9)
        if col == -1:  # row label
            cell.set_facecolor("#F0F0F0")
            cell.set_text_props(fontweight="bold")

    # 在 solver 之间加粗分割线
    for row in range(n_rows + 1):
        for col in [n_models - 0.5]:  # 不直接支持, 用 linewidth 区分
            pass

    ax.set_title("Comparison Table: Avg. Cost (Std)  |  Green = Best  |  Red = Failed",
                 fontsize=14, fontweight="bold", pad=20, color="#222")

    plt.tight_layout()
    fig.savefig(output_dir / "table_01_all_scales_cost_mean_std_by_model_solver.png", dpi=250,
                bbox_inches="tight", facecolor="white")
    plt.close(fig)


def plot_alns_cost_summary(avg_results, scales, output_dir):
    """Focused ALNS cost figure for presentation/report use."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    scale_nums = [_extract_num(s) for s in scales]
    models = [1, 3, 5, 6]
    model_labels = {
        1: "M1 Delivery",
        2: "M3 Pickup-Delivery",
        3: "M5 Iterative Trans.",
        4: "M6 Two-Phase Trans.",
    }
    colors = {1: "#4C78A8", 2: "#E45756", 3: "#F58518", 4: "#72B7B2"}
    label_offsets = {1: 0, 2: -45, 3: 0, 4: 45}

    fig, ax = plt.subplots(figsize=(11, 6.5))
    for m in models:
        means = np.array([
            _metric_or_nan(avg_results[s].get((m, "alns"), {}), "cost_mean")
            for s in scales
        ], dtype=float)
        stds = np.array([
            avg_results[s].get((m, "alns"), {}).get("cost_std", np.nan)
            if avg_results[s].get((m, "alns"), {}).get("n", 0) > 0 else np.nan
            for s in scales
        ], dtype=float)
        ax.plot(scale_nums, means, marker="o", linewidth=2.4, markersize=7,
                color=colors[m], label=model_labels[m])
        ax.fill_between(scale_nums, np.maximum(means - stds, 0), means + stds,
                        where=~np.isnan(means), color=colors[m], alpha=0.09)
        valid_idx = np.where(~np.isnan(means))[0]
        if len(valid_idx) > 0:
            last = valid_idx[-1]
            ax.text(scale_nums[last], means[last] + label_offsets[m],
                    f"{means[last]:.0f}",
                    ha="left", va="center", fontsize=9, color=colors[m],
                    fontweight="bold")

    ax.set_title("ALNS Cost by Model and Scale", fontsize=16,
                 fontweight="bold", pad=12)
    ax.set_xlabel("Number of Customers")
    ax.set_ylabel("Avg. Total Cost")
    ax.set_xticks(scale_nums)
    ax.grid(True, linestyle="--", alpha=0.35)
    ax.legend(ncol=2, fontsize=9, loc="upper left")
    plt.tight_layout()
    fig.savefig(output_dir / "fig_main_01_all_scales_alns_cost_by_model.png", dpi=250,
                bbox_inches="tight", facecolor="white")
    plt.close(fig)


def plot_gap_vs_m3(avg_results, scales, output_dir):
    """Show whether transshipment variants improve on the M3 ALNS baseline."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    scale_labels = [str(_extract_num(s)) for s in scales]
    x = np.arange(len(scales))
    targets = [(5, "M5 vs M3"), (6, "M6 vs M3")]
    colors = {3: "#F58518", 4: "#72B7B2"}
    width = 0.32

    fig, ax = plt.subplots(figsize=(10.5, 6))
    for i, (m, label) in enumerate(targets):
        gaps = []
        for s in scales:
            base = _metric_or_nan(avg_results[s].get((2, "alns"), {}), "cost_mean")
            val = _metric_or_nan(avg_results[s].get((m, "alns"), {}), "cost_mean")
            gap = (val - base) / base * 100 if base and not np.isnan(base) and not np.isnan(val) else np.nan
            gaps.append(gap)
        gaps = np.array(gaps, dtype=float)
        pos = x + (i - 0.5) * width
        bars = ax.bar(pos, gaps, width, label=label, color=colors[m],
                      edgecolor="white", linewidth=0.7, zorder=3)
        for bar, gap in zip(bars, gaps):
            if not np.isnan(gap):
                ax.text(bar.get_x() + bar.get_width() / 2,
                        gap + (0.5 if gap >= 0 else -0.5),
                        f"{gap:+.1f}%", ha="center",
                        va="bottom" if gap >= 0 else "top", fontsize=8)

    ax.axhline(0, color="#333333", linewidth=1.0)
    ax.set_title("ALNS Cost Gap Relative to M3 Baseline", fontsize=16,
                 fontweight="bold", pad=12)
    ax.set_ylabel("Gap vs M3 ALNS (%)")
    ax.set_xlabel("Number of Customers")
    ax.set_xticks(x)
    ax.set_xticklabels(scale_labels)
    ax.grid(True, axis="y", linestyle="--", alpha=0.35)
    ax.legend(fontsize=9, loc="upper left")
    plt.tight_layout()
    fig.savefig(output_dir / "fig_main_02_all_scales_alns_gap_m5_m6_vs_m3.png", dpi=250,
                bbox_inches="tight", facecolor="white")
    plt.close(fig)


def plot_exact_success_heatmap(avg_results, scales, output_dir):
    """Exact solver success rate is clearer as a heatmap than zero-height bars."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    models = [1, 3, 5, 6]
    model_labels = ["M1", "M3", "M5", "M6"]
    scale_labels = [str(_extract_num(s)) for s in scales]
    rates = np.zeros((len(models), len(scales)))
    labels = []

    for i, m in enumerate(models):
        label_row = []
        for j, s in enumerate(scales):
            d = avg_results[s].get((m, "exact"), {})
            ok = d.get("n", 0)
            total = d.get("n_total", 0)
            rate = ok / total if total else 0
            rates[i, j] = rate
            label_row.append(f"{ok}/{total}\n{rate:.0%}" if total else "-")
        labels.append(label_row)

    fig, ax = plt.subplots(figsize=(8.8, 4.8))
    im = ax.imshow(rates, cmap="YlGn", vmin=0, vmax=1, aspect="auto")
    ax.set_title("Certified Exact Solver Success Rate", fontsize=16,
                 fontweight="bold", pad=12)
    ax.set_xticks(np.arange(len(scales)))
    ax.set_xticklabels(scale_labels)
    ax.set_yticks(np.arange(len(models)))
    ax.set_yticklabels(model_labels)
    ax.set_xlabel("Number of Customers")

    for i in range(len(models)):
        for j in range(len(scales)):
            color = "white" if rates[i, j] > 0.55 else "#222222"
            ax.text(j, i, labels[i][j], ha="center", va="center",
                    fontsize=9, color=color, fontweight="bold")

    cbar = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    cbar.set_label("Success Rate")
    plt.tight_layout()
    fig.savefig(output_dir / "fig_main_03_all_scales_exact_success_rate.png", dpi=250,
                bbox_inches="tight", facecolor="white")
    plt.close(fig)


# ─── 主流程 ───

def main():
    fleet_cfg = load_fleet_config()

    scale_dirs = ["5_customers", "10_customers", "50_customers", "100_customers"]
    # ALNS 时间限制按规模递增；exact 时间限制也按规模调整
    alns_time_map = {"5_customers": 15, "10_customers": 30,
                     "50_customers": 60, "100_customers": 90}
    exact_time_map = {"5_customers": 60, "10_customers": 120,
                      "50_customers": 300, "100_customers": 300}

    # raw_results[ scale ][ (model, solver, instance_name) ] = {cost, time, ...}
    raw_results = {}
    total = 0
    for sd in scale_dirs:
        csvs = sorted(DATA_DIR.glob(f"{sd}/*.csv"))
        total += len(csvs) * 4 * 2  # instances × models × solvers
    done = 0

    for sd in scale_dirs:
        csvs = sorted(DATA_DIR.glob(f"{sd}/*.csv"))
        raw_results[sd] = {}
        print(f"\n{'=' * 70}")
        print(f"  Scale: {sd}  ({len(csvs)} instances)")
        print(f"{'=' * 70}")

        for csv_path in csvs:
            inst_name = csv_path.stem
            for model in [1, 3, 5, 6]:
                for solver in ["exact", "alns"]:
                    done += 1
                    tag = f"{inst_name}|M{model}|{solver}"
                    t_limit = (exact_time_map if solver == "exact" else alns_time_map)[sd]
                    print(f"  [{done}/{total}] {tag}  t_limit={t_limit}s", end="", flush=True)

                    r = run_one(
                        model, solver, csv_path, fleet_cfg,
                        alns_time=t_limit, exact_time=t_limit, seed=42,
                    )

                    success_status = "PROVEN_OPTIMAL" if solver == "exact" else "OK"
                    status_icon = "OK" if r["status"] == success_status else "FAIL"
                    cost_str = f"{r['cost']:.1f}" if r["cost"] is not None else "N/A"
                    print(f"  -> {status_icon}  cost={cost_str}  t={r['time']:.1f}s")

                    raw_results[sd][(model, solver, inst_name)] = r

    # ─── 聚合: 按 (scale, model, solver) 求均值 ───
    avg_results = {}  # [scale][(model, solver)] = {cost_mean, cost_std, ...}
    for sd in scale_dirs:
        avg_results[sd] = {}
        for model in [1, 3, 5, 6]:
            for solver in ["exact", "alns"]:
                costs, times, e1s, e2s = [], [], [], []
                n_ok = 0
                for (m, s, inst), r in raw_results[sd].items():
                    success_status = "PROVEN_OPTIMAL" if solver == "exact" else "OK"
                    if m == model and s == solver and r["status"] == success_status:
                        costs.append(r["cost"])
                        times.append(r["time"])
                        e1s.append(r["e1"])
                        e2s.append(r["e2"])
                        n_ok += 1
                n_total = len(csvs) if sd == scale_dirs[-1] else \
                    len(list(DATA_DIR.glob(f"{sd}/*.csv")))

                def _safe(vals):
                    return mean(vals) if vals else 0, (stdev(vals) if len(vals) > 1 else 0)

                c_mean, c_std = _safe(costs)
                t_mean, t_std = _safe(times)
                e1_mean, e1_std = _safe(e1s)
                e2_mean, e2_std = _safe(e2s)

                avg_results[sd][(model, solver)] = {
                    "cost_mean": c_mean, "cost_std": c_std,
                    "time_mean": t_mean, "time_std": t_std,
                    "e1_mean": e1_mean, "e1_std": e1_std,
                    "e2_mean": e2_mean, "e2_std": e2_std,
                    "n": n_ok, "n_total": n_total,
                }

    # ─── 保存 JSON ───
    output_dir = OUTPUT_DIR / "comparison"
    output_dir.mkdir(parents=True, exist_ok=True)
    out_dirs = write_comparison_readme(output_dir)

    json_out = {}
    for sd in scale_dirs:
        json_out[sd] = {}
        for model in [1, 3, 5, 6]:
            for solver in ["exact", "alns"]:
                d = avg_results[sd][(model, solver)]
                json_out[sd][f"M{model}_{solver}"] = {
                    "cost_mean": round(d["cost_mean"], 2),
                    "cost_std": round(d["cost_std"], 2),
                    "time_mean": round(d["time_mean"], 2),
                    "time_std": round(d["time_std"], 2),
                    "e1_mean": round(d["e1_mean"], 1),
                    "e2_mean": round(d["e2_mean"], 1),
                    "n_ok": d["n"], "n_total": d["n_total"],
                    "exact_rows_require_proven_optimal": solver == "exact",
                }
    json_path = out_dirs["data"] / "data_01_comparison_summary_by_scale_model_solver.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(json_out, f, indent=2, ensure_ascii=False)
    print(f"\nJSON saved: {json_path}")

    # Preserve every outcome, including time-limited feasible solutions and
    # their bounds.  The aggregate above intentionally omits those rows from
    # exact statistics, but they remain useful diagnostic evidence.
    raw_rows = []
    for scale, results in raw_results.items():
        for (model, solver, instance), result in results.items():
            info = result.get("solver_info") or {}
            raw_rows.append({
                "scale": scale,
                "instance": instance,
                "model": model,
                "solver": solver,
                "classification": result["status"],
                "cost": result.get("cost"),
                "runtime": result.get("time"),
                "proven_optimal": info.get("proven_optimal"),
                "solver_status": info.get("status"),
                "solution_type": info.get("solution_type"),
                "best_bound": info.get("best_bound"),
                "mip_gap": info.get("mip_gap"),
            })
    raw_path = out_dirs["data"] / "data_02_comparison_run_statuses.json"
    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump(raw_rows, f, indent=2, ensure_ascii=False)
    print(f"Run statuses saved: {raw_path}")

    # ─── 打印汇总表 ───
    print("\n" + "=" * 100)
    print(f"{'Scale':<14} {'Solver':<7} {'Model':<6} "
          f"{'Cost(mean)':<12} {'Cost(std)':<10} {'Time(mean)':<10} {'OK/Total':<10}")
    print("-" * 100)
    for sd in scale_dirs:
        for solver in ["exact", "alns"]:
            for model in [1, 3, 5, 6]:
                d = avg_results[sd][(model, solver)]
                c = f"{d['cost_mean']:.1f}" if d["cost_mean"] > 0 else "N/A"
                cs = f"{d['cost_std']:.1f}" if d["cost_mean"] > 0 else "-"
                t = f"{d['time_mean']:.1f}" if d["time_mean"] > 0 else "-"
                print(f"{sd:<14} {solver:<7} M{model}     {c:<12} {cs:<10} {t:<10} {d['n']}/{d['n_total']}")
            print("-" * 100)

    # ─── 绘图 ───
    plot_all(avg_results, output_dir)

    print(f"\n{'=' * 70}")
    print("All done!")
    print(f"Output: {output_dir}")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    main()
