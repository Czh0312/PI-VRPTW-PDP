# run_comparison.py
# -*- coding: utf-8 -*-
"""
批量对比实验：4 规模 × 4 模型 × 2 求解器 (exact / ALNS)
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

from core.instance import ProblemInstance
from models.model_1.solver import solve_exact as exact_m1
from models.model_2.solver import solve_exact as exact_m2
from models.model_3.solver import solve_exact as exact_m3
from models.model_4.solver import solve_exact as exact_m4
from models.model_1.alns_solver import solve_alns as alns_m1
from models.model_2.alns_solver import solve_alns as alns_m2
from models.model_3.alns_solver import solve_alns as alns_m3
from models.model_4.alns_solver import solve_alns as alns_m4
from utils.data_loader import parse_dataset
from utils.task_builder import build_tasks_for_model

PROJECT_ROOT = Path(__file__).resolve().parent
CONFIG_DIR = PROJECT_ROOT / "config"
OUTPUT_DIR = PROJECT_ROOT / "output"
DATA_DIR = PROJECT_ROOT / "data"

EXACT_SOLVERS = {1: exact_m1, 2: exact_m2, 3: exact_m3, 4: exact_m4}
ALNS_SOLVERS = {1: alns_m1, 2: alns_m2, 3: alns_m3, 4: alns_m4}


def load_fleet_config():
    with open(CONFIG_DIR / "fleet_config.json", "r", encoding="utf-8") as f:
        return json.load(f)


# ─── 工具函数 ───

def _to_phys(nid):
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


def _count_e2(e2_by_sat):
    return sum(len(v) for v in e2_by_sat.values())


# ─── 单次求解 ───

def run_one(model, solver, data_path, fleet_cfg, alns_time=120, seed=42):
    """
    运行单次实验。
    solver: "exact" | "alns"
    返回 dict: cost / time / e1 / e2 / status
    """
    nodes_dict, _ = parse_dataset(str(data_path))
    problem = ProblemInstance(nodes_dict, fleet_cfg, speed=fleet_cfg.get("speed", 1.0))
    problem.aggregate_demands_from_pre_assignment()
    e1_tasks, e2_tasks_by_sat = build_tasks_for_model(problem, model)

    if solver == "exact":
        fn = EXACT_SOLVERS[model]
        kwargs = dict(
            problem=problem, e1_tasks=e1_tasks,
            e2_tasks_by_sat=e2_tasks_by_sat,
            vehicles_per_satellite=problem.fleet.e2_per_sat_count,
            verbose=True,
        )
        if model in (2, 4):
            kwargs["TFp"] = 9999.0
    else:
        fn = ALNS_SOLVERS[model]
        kwargs = dict(
            problem=problem, e1_tasks=e1_tasks,
            e2_tasks_by_sat=e2_tasks_by_sat,
            vehicles_per_satellite=problem.fleet.e2_per_sat_count,
            max_iterations=5000, max_time=alns_time, seed=seed,
            verbose=True,
        )
        if model in (2, 4):
            kwargs["TFp"] = 9999.0

    log_buf = io.StringIO()
    t0 = time.time()
    try:
        with redirect_stdout(log_buf):
            e1_routes, e2_routes = fn(**kwargs)
        elapsed = time.time() - t0
        if e1_routes and e2_routes:
            return dict(
                cost=_total_cost(problem, e1_routes, e2_routes),
                time=elapsed, e1=len(e1_routes), e2=_count_e2(e2_routes),
                status="OK", log=log_buf.getvalue(),
            )
        return dict(cost=None, time=elapsed, e1=0, e2=0,
                    status="INFEASIBLE", log=log_buf.getvalue())
    except Exception as e:
        elapsed = time.time() - t0
        return dict(cost=None, time=elapsed, e1=0, e2=0,
                    status=f"ERROR: {e}", log=log_buf.getvalue())


# ─── 绘图 ───

def _extract_num(label):
    import re
    m = re.match(r"(\d+)", label)
    return int(m.group(1)) if m else 0


def plot_all(avg_results, output_dir):
    """
    avg_results: dict[ scale_key ][ (model, solver) ] = {cost_mean, cost_std, ...}
    生成三张图。
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.ticker as ticker
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

    scales = sorted(avg_results.keys(), key=_extract_num)
    scale_labels = [str(_extract_num(s)) for s in scales]
    model_names = {1: "M1: Delivery", 2: "M2: P-D", 3: "M3: Iter.Trans.", 4: "M4: 2-Phase"}
    model_short = {1: "M1", 2: "M2", 3: "M3", 4: "M4"}
    solver_labels = {"exact": "Exact", "alns": "ALNS"}

    # 8 条线的样式：4 模型 × 2 求解器
    combo_colors = {
        (1, "exact"): "#1f77b4", (1, "alns"): "#8ecae6",
        (2, "exact"): "#d62728", (2, "alns"): "#f4a582",
        (3, "exact"): "#b5651d", (3, "alns"): "#f0c987",
        (4, "exact"): "#2ca02c", (4, "alns"): "#98df8a",
    }
    combo_marker = {"exact": "o", "alns": "s"}
    combo_ls = {"exact": "-", "alns": "--"}

    x = np.arange(len(scales))
    width = 0.09
    combos = [(m, s) for m in [1, 2, 3, 4] for s in ["exact", "alns"]]

    # ────────────── Figure 1: 分组柱状图 (Exact / ALNS 两列子图) ──────────────
    fig, axes = plt.subplots(1, 2, figsize=(18, 7))
    fig.suptitle("2E-VRPTW-PDD  ·  Exact vs. ALNS  Comparison",
                 fontsize=20, fontweight="bold", y=1.0, color="#222222")

    for col, solver in enumerate(["exact", "alns"]):
        ax = axes[col]
        n_bars = 4
        for i, m in enumerate([1, 2, 3, 4]):
            means, stds = [], []
            for s in scales:
                d = avg_results[s].get((m, solver), {})
                means.append(d.get("cost_mean", 0))
                stds.append(d.get("cost_std", 0))
            pos = x + (i - (n_bars - 1) / 2) * width
            bars = ax.bar(pos, means, width, yerr=stds, capsize=3,
                          label=model_short[m], color=combo_colors[(m, solver)],
                          edgecolor="white", linewidth=0.6, zorder=3,
                          error_kw={"linewidth": 0.8, "color": "#555"})
            for bar, mu in zip(bars, means):
                if mu > 0:
                    ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 1,
                            f"{mu:.0f}", ha="center", va="bottom", fontsize=7,
                            color="#444", fontweight="medium")
        n_inst = list(avg_results[scales[0]].values())[0].get("n", "?")
        ax.set_title(f"{solver_labels[solver]} Solver  (mean +/- std, n={n_inst}/scale)",
                     fontsize=14, fontweight="bold", pad=10, color="#333")
        ax.set_ylabel("Avg. Total Cost", fontsize=11, color="#555")
        ax.set_xlabel("Number of Customers", fontsize=11, color="#555")
        ax.set_xticks(x)
        ax.set_xticklabels(scale_labels, fontsize=11)
        ax.legend(fontsize=9)

    plt.tight_layout(rect=[0, 0, 1, 0.95])
    fig.savefig(output_dir / "comparison_bar_exact_vs_alns.png", dpi=250,
                bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print("[1/4] 柱状图已保存")

    # ────────────── Figure 2: 折线趋势 (8 条线) ──────────────
    fig, ax = plt.subplots(figsize=(13, 7))
    x_nums = [_extract_num(s) for s in scales]

    for m in [1, 2, 3, 4]:
        for solver in ["exact", "alns"]:
            means, stds = [], []
            for s in scales:
                d = avg_results[s].get((m, solver), {})
                means.append(d.get("cost_mean", 0))
                stds.append(d.get("cost_std", 0))
            if any(v > 0 for v in means):
                lbl = f"{model_short[m]}-{solver_labels[solver]}"
                ax.plot(x_nums, means, marker=combo_marker[solver],
                        linestyle=combo_ls[solver], linewidth=2, markersize=7,
                        color=combo_colors[(m, solver)], label=lbl, zorder=4)
                ax.fill_between(x_nums,
                                [mu - sigma for mu, sigma in zip(means, stds)],
                                [mu + sigma for mu, sigma in zip(means, stds)],
                                alpha=0.08, color=combo_colors[(m, solver)], zorder=2)

    ax.set_title("Cost Trend: Exact (solid) vs ALNS (dashed)  (Mean +/- Std)",
                 fontsize=16, fontweight="bold", pad=15, color="#222")
    ax.set_xlabel("Number of Customers", fontsize=12, color="#555")
    ax.set_ylabel("Avg. Total Distance Cost", fontsize=12, color="#555")
    ax.set_xticks(x_nums)
    ax.set_xticklabels(scale_labels, fontsize=12)
    ax.legend(fontsize=8.5, ncol=2, loc="upper left", borderpad=1, labelspacing=0.6)

    plt.tight_layout()
    fig.savefig(output_dir / "comparison_trend.png", dpi=250,
                bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print("[2/4] 趋势折线图已保存")

    # ────────────── Figure 3: 求解时间对比 ──────────────
    fig, axes = plt.subplots(1, 2, figsize=(16, 7))
    for col, solver in enumerate(["exact", "alns"]):
        ax = axes[col]
        for i, m in enumerate([1, 2, 3, 4]):
            means, stds = [], []
            for s in scales:
                d = avg_results[s].get((m, solver), {})
                means.append(d.get("time_mean", 0))
                stds.append(d.get("time_std", 0))
            pos = x + (i - 1.5) * 0.19
            ax.bar(pos, means, 0.19, yerr=stds, capsize=3,
                   label=model_short[m], color=combo_colors[(m, solver)],
                   edgecolor="white", linewidth=0.6, zorder=3,
                   error_kw={"linewidth": 0.8, "color": "#555"})
        n_inst = list(avg_results[scales[0]].values())[0].get("n", "?")
        ax.set_title(f"{solver_labels[solver]} Solve Time  (mean +/- std, n={n_inst}/scale)",
                     fontsize=14, fontweight="bold", pad=10, color="#333")
        ax.set_ylabel("Time (s)", fontsize=11, color="#555")
        ax.set_xlabel("Number of Customers", fontsize=11, color="#555")
        ax.set_xticks(x)
        ax.set_xticklabels(scale_labels, fontsize=11)
        ax.legend(fontsize=9)

    plt.tight_layout()
    fig.savefig(output_dir / "comparison_time.png", dpi=250,
                bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print("[3/4] 时间对比图已保存")

    # ────────────── Figure 4: 结果汇总表格 ──────────────
    plot_table(avg_results, scales, output_dir)
    print("[4/4] 表格图已保存")


def plot_table(avg_results, scales, output_dir):
    """生成 LaTeX 风格的对比结果表格图"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    solvers = ["exact", "alns"]
    models = [1, 2, 3, 4]
    solver_hdr = ["Exact", "ALNS"]
    model_hdr = ["M1", "M2", "M3", "M4"]

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
                if c is not None:
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
    fig.savefig(output_dir / "comparison_table.png", dpi=250,
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
            for model in [1, 2, 3, 4]:
                for solver in ["exact", "alns"]:
                    done += 1
                    tag = f"{inst_name}|M{model}|{solver}"
                    t_limit = (exact_time_map if solver == "exact" else alns_time_map)[sd]
                    print(f"  [{done}/{total}] {tag}  t_limit={t_limit}s", end="", flush=True)

                    r = run_one(model, solver, csv_path, fleet_cfg,
                                alns_time=t_limit, seed=42)

                    status_icon = "OK" if r["status"] == "OK" else "FAIL"
                    cost_str = f"{r['cost']:.1f}" if r["cost"] is not None else "N/A"
                    print(f"  -> {status_icon}  cost={cost_str}  t={r['time']:.1f}s")

                    raw_results[sd][(model, solver, inst_name)] = r

    # ─── 聚合: 按 (scale, model, solver) 求均值 ───
    avg_results = {}  # [scale][(model, solver)] = {cost_mean, cost_std, ...}
    for sd in scale_dirs:
        avg_results[sd] = {}
        for model in [1, 2, 3, 4]:
            for solver in ["exact", "alns"]:
                costs, times, e1s, e2s = [], [], [], []
                n_ok = 0
                for (m, s, inst), r in raw_results[sd].items():
                    if m == model and s == solver and r["status"] == "OK":
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

    json_out = {}
    for sd in scale_dirs:
        json_out[sd] = {}
        for model in [1, 2, 3, 4]:
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
                }
    with open(output_dir / "comparison_results.json", "w", encoding="utf-8") as f:
        json.dump(json_out, f, indent=2, ensure_ascii=False)
    print(f"\nJSON saved: {output_dir / 'comparison_results.json'}")

    # ─── 打印汇总表 ───
    print("\n" + "=" * 100)
    print(f"{'Scale':<14} {'Solver':<7} {'Model':<6} "
          f"{'Cost(mean)':<12} {'Cost(std)':<10} {'Time(mean)':<10} {'OK/Total':<10}")
    print("-" * 100)
    for sd in scale_dirs:
        for solver in ["exact", "alns"]:
            for model in [1, 2, 3, 4]:
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
