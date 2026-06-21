#!/usr/bin/env python3
"""Generate thesis concept figures for the 2E-VRPTW-PDD project.

The figures are intentionally illustrative rather than instance-specific.
Each figure is exported as SVG for thesis editing and PNG for direct insertion
into Word or presentation slides.
"""

from __future__ import annotations

import math
import os
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/mplconfig-2e-vrptw-pdd")
os.environ.setdefault("XDG_CACHE_HOME", "/tmp/fontconfig-2e-vrptw-pdd")

import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import (
    Circle,
    FancyArrowPatch,
    FancyBboxPatch,
    Rectangle,
    RegularPolygon,
)


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "docs" / "figures"

COLORS = {
    "ink": "#25313f",
    "muted": "#6d7784",
    "grid": "#e7ebef",
    "blue": "#2f6fb2",
    "blue_light": "#d9e8f8",
    "green": "#2f8f5b",
    "green_light": "#dcf0e6",
    "orange": "#d9822b",
    "orange_light": "#fde8cf",
    "red": "#c94f4f",
    "red_light": "#f6d9d9",
    "purple": "#6d5bd0",
    "purple_light": "#e7e1ff",
    "gray": "#97a1ad",
    "gray_light": "#f3f5f7",
}


def configure_matplotlib() -> None:
    candidates = [
        "Hiragino Sans GB",
        "PingFang SC",
        "Heiti SC",
        "Songti SC",
        "STHeiti",
        "Arial Unicode MS",
        "Noto Sans CJK SC",
        "SimHei",
        "Microsoft YaHei",
    ]
    installed = {f.name for f in font_manager.fontManager.ttflist}
    for name in candidates:
        if name in installed:
            plt.rcParams["font.sans-serif"] = [name, "DejaVu Sans"]
            break
    else:
        plt.rcParams["font.sans-serif"] = ["DejaVu Sans"]

    plt.rcParams.update(
        {
            "axes.unicode_minus": False,
            "svg.fonttype": "none",
            "figure.facecolor": "white",
            "savefig.facecolor": "white",
            "font.size": 11,
            "axes.titleweight": "bold",
        }
    )


def save_figure(fig: plt.Figure, name: str) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    svg_path = OUT_DIR / f"{name}.svg"
    png_path = OUT_DIR / f"{name}.png"
    fig.savefig(svg_path, bbox_inches="tight", pad_inches=0.14)
    fig.savefig(png_path, bbox_inches="tight", pad_inches=0.14, dpi=300)
    plt.close(fig)


def clean_axis(ax: plt.Axes, xlim=(0, 10), ylim=(0, 7)) -> None:
    ax.set_xlim(*xlim)
    ax.set_ylim(*ylim)
    ax.set_aspect("equal")
    ax.axis("off")


def arrow(
    ax: plt.Axes,
    start: tuple[float, float],
    end: tuple[float, float],
    *,
    color: str,
    lw: float = 2.5,
    rad: float = 0.0,
    linestyle: str | tuple = "solid",
    alpha: float = 1.0,
    zorder: int = 3,
    mutation_scale: float = 14,
) -> FancyArrowPatch:
    patch = FancyArrowPatch(
        start,
        end,
        arrowstyle="-|>",
        connectionstyle=f"arc3,rad={rad}",
        mutation_scale=mutation_scale,
        linewidth=lw,
        linestyle=linestyle,
        color=color,
        alpha=alpha,
        zorder=zorder,
        shrinkA=12,
        shrinkB=12,
    )
    ax.add_patch(patch)
    return patch


def text_box(
    ax: plt.Axes,
    xy: tuple[float, float],
    text: str,
    *,
    width: float,
    height: float,
    fc: str = "white",
    ec: str = "#d7dde3",
    color: str = COLORS["ink"],
    fontsize: float = 10.5,
    weight: str = "normal",
    radius: float = 0.13,
    lw: float = 1.1,
    zorder: int = 2,
) -> FancyBboxPatch:
    x, y = xy
    patch = FancyBboxPatch(
        (x, y),
        width,
        height,
        boxstyle=f"round,pad=0.03,rounding_size={radius}",
        facecolor=fc,
        edgecolor=ec,
        linewidth=lw,
        zorder=zorder,
    )
    ax.add_patch(patch)
    ax.text(
        x + width / 2,
        y + height / 2,
        text,
        ha="center",
        va="center",
        color=color,
        fontsize=fontsize,
        weight=weight,
        linespacing=1.25,
        zorder=zorder + 1,
    )
    return patch


def draw_customer(
    ax: plt.Axes,
    xy: tuple[float, float],
    label: str,
    *,
    fill: str = "white",
    edge: str = COLORS["ink"],
    sublabel: str | None = None,
    radius: float = 0.22,
) -> None:
    ax.add_patch(
        Circle(xy, radius, facecolor=fill, edgecolor=edge, linewidth=1.6, zorder=5)
    )
    ax.text(
        xy[0],
        xy[1],
        label,
        ha="center",
        va="center",
        fontsize=11,
        weight="bold",
        color=edge,
        zorder=6,
    )
    if sublabel:
        ax.text(
            xy[0],
            xy[1] - 0.48,
            sublabel,
            ha="center",
            va="top",
            fontsize=9.5,
            color=COLORS["muted"],
            linespacing=1.15,
            zorder=6,
        )


def draw_satellite(ax: plt.Axes, xy: tuple[float, float], label: str = "S") -> None:
    ax.add_patch(
        RegularPolygon(
            xy,
            numVertices=6,
            radius=0.34,
            orientation=math.pi / 6,
            facecolor=COLORS["blue_light"],
            edgecolor=COLORS["blue"],
            linewidth=1.8,
            zorder=5,
        )
    )
    ax.text(
        xy[0],
        xy[1],
        label,
        ha="center",
        va="center",
        fontsize=12,
        weight="bold",
        color=COLORS["blue"],
        zorder=6,
    )
    ax.text(
        xy[0],
        xy[1] - 0.55,
        "卫星站",
        ha="center",
        va="top",
        fontsize=9.5,
        color=COLORS["muted"],
        zorder=6,
    )


def draw_depot(ax: plt.Axes, xy: tuple[float, float], label: str = "D") -> None:
    x, y = xy
    ax.add_patch(
        Rectangle(
            (x - 0.28, y - 0.24),
            0.56,
            0.48,
            facecolor=COLORS["purple_light"],
            edgecolor=COLORS["purple"],
            linewidth=1.8,
            zorder=5,
        )
    )
    ax.text(
        x,
        y,
        label,
        ha="center",
        va="center",
        fontsize=12,
        weight="bold",
        color=COLORS["purple"],
        zorder=6,
    )


def route_length(points: list[tuple[float, float]]) -> float:
    return sum(
        math.dist(points[i], points[i + 1]) for i in range(len(points) - 1)
    )


def draw_transshipment_concept() -> None:
    s = (1.0, 0.9)
    c = (2.7, 2.05)
    b = (5.2, 1.55)
    a = (7.2, 5.2)
    d = (6.35, 5.8)
    e = (6.3, 1.2)

    helped_before_route = [s, c, b, a, s]
    helped_after_route = [s, c, b, s]
    helper_before_route = [s, d, e, s]
    helper_after_route = [s, d, a, b, e, s]

    helped_before = route_length(helped_before_route)
    helped_after = route_length(helped_after_route)
    helper_before = route_length(helper_before_route)
    helper_after = route_length(helper_after_route)
    helped_decrease = helped_before - helped_after
    helper_increase = helper_after - helper_before
    total_before = helped_before + helper_before
    total_after = helped_after + helper_after
    net_saving = total_before - total_after
    saving_pct = net_saving / total_before * 100

    fig, axes = plt.subplots(1, 2, figsize=(13.8, 6.25), constrained_layout=False)
    fig.subplots_adjust(left=0.035, right=0.985, bottom=0.06, top=0.8, wspace=0.08)
    for ax in axes:
        clean_axis(ax, xlim=(0, 9), ylim=(0, 7.55))
        ax.add_patch(
            Rectangle(
                (0.35, 0.35),
                8.3,
                6.8,
                facecolor="#fbfcfd",
                edgecolor=COLORS["grid"],
                linewidth=1.0,
                zorder=0,
            )
        )

    ax = axes[0]
    ax.set_title("不中转：两辆车各自完成原路线", fontsize=15, color=COLORS["ink"], pad=8)
    arrow(ax, s, c, color=COLORS["blue"], lw=2.8)
    arrow(ax, c, b, color=COLORS["blue"], lw=2.8)
    arrow(ax, b, a, color=COLORS["red"], lw=3.1, rad=0.08)
    arrow(ax, a, s, color=COLORS["red"], lw=3.1, rad=0.08)
    arrow(ax, s, d, color=COLORS["orange"], lw=2.4, rad=-0.12, linestyle=(0, (6, 3)), alpha=0.78)
    arrow(ax, d, e, color=COLORS["orange"], lw=2.4, rad=-0.08, linestyle=(0, (6, 3)), alpha=0.78)
    arrow(ax, e, s, color=COLORS["orange"], lw=2.4, rad=-0.10, linestyle=(0, (6, 3)), alpha=0.78)
    ax.text(6.46, 3.55, "被帮助车绕行到 A", color=COLORS["red"], fontsize=10.3, weight="bold")
    ax.text(3.78, 5.75, "B 是原本要服务的节点，\n不是“路过但不服务”", color=COLORS["muted"], fontsize=9.6)
    ax.text(4.15, 4.3, "帮助车完整原路线", color=COLORS["orange"], fontsize=10.0, weight="bold")

    draw_satellite(ax, s)
    draw_customer(ax, c, "C", fill=COLORS["blue_light"], edge=COLORS["blue"])
    draw_customer(ax, b, "B", fill=COLORS["green_light"], edge=COLORS["green"])
    draw_customer(ax, a, "A", fill=COLORS["red_light"], edge=COLORS["red"])
    draw_customer(ax, d, "D", fill=COLORS["orange_light"], edge=COLORS["orange"], radius=0.2)
    draw_customer(ax, e, "E", fill=COLORS["orange_light"], edge=COLORS["orange"], radius=0.2)
    ax.text(a[0], a[1] - 0.5, "A：空容器原位置", ha="center", va="top", fontsize=9.2, color=COLORS["muted"])
    ax.add_patch(
        Rectangle(
            (a[0] - 0.1, a[1] + 0.32),
            0.22,
            0.18,
            facecolor=COLORS["orange"],
            edgecolor="white",
            linewidth=0.8,
            zorder=7,
        )
    )
    text_box(
        ax,
        (0.82, 5.98),
        f"被帮助车：{helped_before:.1f}\n帮助车：{helper_before:.1f}\n系统总路程：{total_before:.1f}",
        width=2.65,
        height=0.92,
        fc=COLORS["gray_light"],
        ec=COLORS["grid"],
        fontsize=10.0,
        weight="bold",
    )

    ax = axes[1]
    ax.set_title("中转：帮助车增加搬运，被帮助车减少绕行", fontsize=15, color=COLORS["ink"], pad=8)
    arrow(ax, s, c, color=COLORS["blue"], lw=2.8)
    arrow(ax, c, b, color=COLORS["blue"], lw=2.8)
    arrow(ax, b, s, color=COLORS["green"], lw=3.1, rad=-0.08)
    arrow(ax, s, d, color=COLORS["orange"], lw=2.2, rad=-0.10, linestyle=(0, (6, 3)), alpha=0.8)
    arrow(ax, d, a, color=COLORS["orange"], lw=2.7, rad=0.05, linestyle=(0, (6, 3)))
    arrow(
        ax,
        a,
        b,
        color=COLORS["orange"],
        lw=2.6,
        rad=0.16,
        linestyle=(0, (5, 3)),
        mutation_scale=13,
    )
    arrow(ax, b, e, color=COLORS["orange"], lw=2.5, rad=-0.10, linestyle=(0, (6, 3)))
    arrow(ax, e, s, color=COLORS["orange"], lw=2.2, rad=-0.12, linestyle=(0, (6, 3)), alpha=0.8)
    arrow(
        ax,
        b,
        a,
        color=COLORS["red"],
        lw=1.6,
        rad=0.05,
        linestyle=(0, (2, 3)),
        alpha=0.32,
        mutation_scale=10,
    )
    arrow(
        ax,
        a,
        s,
        color=COLORS["red"],
        lw=1.6,
        rad=0.08,
        linestyle=(0, (2, 3)),
        alpha=0.32,
        mutation_scale=10,
    )
    ax.text(6.82, 3.42, "原绕行被替代", color=COLORS["red"], fontsize=10, alpha=0.72)
    ax.text(4.35, 4.02, f"帮助车插入 A→B 搬运\n路线增加 {helper_increase:.1f}", color=COLORS["orange"], fontsize=9.8, weight="bold")

    draw_satellite(ax, s)
    draw_customer(ax, c, "C", fill=COLORS["blue_light"], edge=COLORS["blue"])
    draw_customer(ax, b, "B", fill=COLORS["green_light"], edge=COLORS["green"])
    draw_customer(ax, a, "A", fill=COLORS["red_light"], edge=COLORS["red"])
    draw_customer(ax, d, "D", fill=COLORS["orange_light"], edge=COLORS["orange"], radius=0.2)
    draw_customer(ax, e, "E", fill=COLORS["orange_light"], edge=COLORS["orange"], radius=0.2)
    ax.text(a[0], a[1] - 0.5, "A：空容器原位置", ha="center", va="top", fontsize=9.2, color=COLORS["muted"])
    ax.add_patch(
        Rectangle(
            (b[0] - 0.1, b[1] + 0.32),
            0.22,
            0.18,
            facecolor=COLORS["orange"],
            edgecolor="white",
            linewidth=0.8,
            zorder=7,
        )
    )
    text_box(
        ax,
        (0.68, 5.82),
        f"被帮助车：{helped_after:.1f}（-{helped_decrease:.1f}）\n帮助车：{helper_after:.1f}（+{helper_increase:.1f}）\n系统总路程：{total_after:.1f}\n净减少：{net_saving:.1f}（{saving_pct:.0f}%）",
        width=3.55,
        height=1.1,
        fc=COLORS["green_light"],
        ec=COLORS["green"],
        fontsize=9.8,
        weight="bold",
    )
    text_box(
        ax,
        (6.32, 5.98),
        "pickup 物理位置\nA 改为 B",
        width=1.95,
        height=0.72,
        fc=COLORS["orange_light"],
        ec=COLORS["orange"],
        fontsize=9.8,
        weight="bold",
    )
    text_box(
        ax,
        (4.38, 5.98),
        f"系统优化看两车合计：\n{helped_decrease:.1f} - {helper_increase:.1f} = {net_saving:.1f}",
        width=1.72,
        height=0.92,
        fc="white",
        ec=COLORS["grid"],
        fontsize=8.7,
        weight="bold",
    )

    fig.suptitle(
        "中转概念示意：帮助车路径增长，但被帮助车路径减少更多，系统总路程下降",
        fontsize=18,
        weight="bold",
        color=COLORS["ink"],
        y=0.965,
    )
    save_figure(fig, "fig_01_transshipment_concept")


def draw_two_echelon_network() -> None:
    fig, ax = plt.subplots(figsize=(12, 6.3))
    clean_axis(ax, xlim=(0, 12), ylim=(0, 6.5))
    ax.set_title("两级取送货配送网络结构", fontsize=18, color=COLORS["ink"], pad=14)

    depot = (1.35, 3.25)
    sats = [(4.1, 4.75), (4.1, 1.65)]
    customers_top = [(7.2, 5.4), (8.95, 4.65), (7.8, 3.65)]
    customers_bottom = [(7.05, 1.0), (8.85, 1.75), (9.65, 2.95)]

    draw_depot(ax, depot)
    ax.text(depot[0], depot[1] - 0.58, "仓库/中心仓", ha="center", va="top", fontsize=10.5, color=COLORS["muted"])

    for i, sat in enumerate(sats, start=1):
        draw_satellite(ax, sat, label=f"S{i}")
        arrow(ax, depot, sat, color=COLORS["purple"], lw=3.0, rad=0.08 if i == 1 else -0.08)
        arrow(ax, sat, depot, color=COLORS["green"], lw=2.0, rad=-0.08 if i == 1 else 0.08, linestyle=(0, (5, 3)), alpha=0.82)

    for idx, xy in enumerate(customers_top, start=1):
        draw_customer(ax, xy, f"C{idx}", fill=COLORS["blue_light"], edge=COLORS["blue"])
        arrow(ax, sats[0], xy, color=COLORS["blue"], lw=2.4, rad=0.05 * (idx - 2))
        arrow(ax, xy, sats[0], color=COLORS["green"], lw=1.8, rad=-0.08 * (idx - 2), linestyle=(0, (4, 3)), alpha=0.75)

    for idx, xy in enumerate(customers_bottom, start=4):
        draw_customer(ax, xy, f"C{idx}", fill=COLORS["blue_light"], edge=COLORS["blue"])
        arrow(ax, sats[1], xy, color=COLORS["blue"], lw=2.4, rad=0.05 * (idx - 5))
        arrow(ax, xy, sats[1], color=COLORS["green"], lw=1.8, rad=-0.07 * (idx - 5), linestyle=(0, (4, 3)), alpha=0.75)

    arrow(
        ax,
        customers_top[0],
        customers_top[1],
        color=COLORS["orange"],
        lw=2.4,
        rad=-0.16,
        linestyle=(0, (5, 3)),
    )
    ax.text(7.85, 5.25, "顾客间中转", color=COLORS["orange"], fontsize=10.5, weight="bold")

    arrow(
        ax,
        customers_bottom[0],
        customers_bottom[2],
        color=COLORS["orange"],
        lw=2.2,
        rad=0.18,
        linestyle=(0, (5, 3)),
    )
    ax.text(7.65, 0.48, "空容器搬运", color=COLORS["orange"], fontsize=10.5, weight="bold")

    text_box(
        ax,
        (0.55, 5.55),
        "E1：仓库与卫星站",
        width=2.3,
        height=0.46,
        fc=COLORS["purple_light"],
        ec=COLORS["purple"],
        fontsize=10.5,
        weight="bold",
    )
    text_box(
        ax,
        (3.3, 5.55),
        "E2：卫星站与顾客",
        width=2.35,
        height=0.46,
        fc=COLORS["blue_light"],
        ec=COLORS["blue"],
        fontsize=10.5,
        weight="bold",
    )
    text_box(
        ax,
        (6.1, 5.55),
        "配送：实线",
        width=1.55,
        height=0.46,
        fc=COLORS["gray_light"],
        ec=COLORS["grid"],
        fontsize=10,
    )
    text_box(
        ax,
        (7.95, 5.55),
        "回收/中转：虚线",
        width=2.0,
        height=0.46,
        fc=COLORS["orange_light"],
        ec=COLORS["orange"],
        fontsize=10,
    )

    save_figure(fig, "fig_02_two_echelon_pdd_network")


def draw_model_progression() -> None:
    fig, ax = plt.subplots(figsize=(13, 4.8))
    clean_axis(ax, xlim=(0, 13), ylim=(0, 4.8))
    ax.set_title("模型体系演进：从基础配送到受控中转搜索", fontsize=18, color=COLORS["ink"], pad=10)

    models = [
        ("Model 1", "纯配送\nDelivery"),
        ("Model 2", "加入取货\nPickup"),
        ("Model 3", "迭代中转\n中转作为算子"),
        ("Model 4", "两阶段中转\n路线后处理"),
        ("Model 5", "受控中转\ncandidate 内试探"),
    ]
    xs = [0.6, 3.05, 5.5, 7.95, 10.4]
    fills = [
        COLORS["gray_light"],
        COLORS["blue_light"],
        COLORS["orange_light"],
        COLORS["green_light"],
        COLORS["purple_light"],
    ]
    edges = [
        COLORS["gray"],
        COLORS["blue"],
        COLORS["orange"],
        COLORS["green"],
        COLORS["purple"],
    ]

    for i, ((name, desc), x, fc, ec) in enumerate(zip(models, xs, fills, edges)):
        text_box(
            ax,
            (x, 2.35),
            f"{name}\n{desc}",
            width=1.95,
            height=1.22,
            fc=fc,
            ec=ec,
            fontsize=10.8,
            weight="bold",
            lw=1.5,
        )
        if i < len(xs) - 1:
            arrow(ax, (x + 1.98, 2.96), (xs[i + 1] - 0.04, 2.96), color=COLORS["muted"], lw=2.0, mutation_scale=13)

    ax.plot([6.35, 11.38], [1.55, 1.55], color=COLORS["orange"], linewidth=2.4)
    arrow(ax, (6.35, 1.55), (11.38, 1.55), color=COLORS["orange"], lw=0.0, mutation_scale=14)
    ax.text(8.85, 1.78, "路线与中转协同增强", ha="center", fontsize=10.5, color=COLORS["orange"], weight="bold")

    ax.plot([7.9, 11.38], [0.8, 0.8], color=COLORS["green"], linewidth=2.4)
    arrow(ax, (7.9, 0.8), (11.38, 0.8), color=COLORS["green"], lw=0.0, mutation_scale=14)
    ax.text(9.65, 1.03, "稳定性与防劣化控制增强", ha="center", fontsize=10.5, color=COLORS["green"], weight="bold")

    text_box(
        ax,
        (0.72, 0.62),
        "研究主线：先扩大问题表达能力，再控制中转带来的扰动风险",
        width=5.8,
        height=0.52,
        fc="#fbfcfd",
        ec=COLORS["grid"],
        fontsize=10.5,
    )

    save_figure(fig, "fig_03_model_progression")


def draw_model5_candidate_flow() -> None:
    fig, ax = plt.subplots(figsize=(12.6, 6.2))
    clean_axis(ax, xlim=(0, 12.6), ylim=(0, 6.2))
    ax.set_title("Model 5：candidate 内嵌受控中转的搜索流程", fontsize=18, color=COLORS["ink"], pad=12)

    boxes = {
        "initial": (0.55, 3.85, "强可行初始解\nE1/E2 路线构造", COLORS["blue_light"], COLORS["blue"]),
        "alns": (3.05, 3.85, "ALNS\n破坏 / 修复", COLORS["gray_light"], COLORS["muted"]),
        "cand": (5.15, 3.85, "候选解\ncandidate", COLORS["gray_light"], COLORS["muted"]),
        "probe": (7.25, 3.85, "受控中转试探\nH 次 / 上限 T,S", COLORS["orange_light"], COLORS["orange"]),
        "check": (7.25, 2.05, "全局可行性检查\n时间窗 / 载重 / 顺序", COLORS["green_light"], COLORS["green"]),
        "accept": (5.15, 2.05, "整体接受准则\n路线 + 中转一起判断", COLORS["purple_light"], COLORS["purple"]),
        "portfolio": (3.05, 0.62, "Safety portfolio\n多策略取优 + 回退", COLORS["green_light"], COLORS["green"]),
        "best": (8.95, 0.62, "最终解\n低成本且不劣化", COLORS["purple_light"], COLORS["purple"]),
    }

    for key, (x, y, txt, fc, ec) in boxes.items():
        text_box(ax, (x, y), txt, width=1.82 if key != "portfolio" else 2.25, height=0.82, fc=fc, ec=ec, fontsize=10.3, weight="bold", lw=1.5)

    arrow(ax, (2.4, 4.26), (3.05, 4.26), color=COLORS["muted"], lw=2.2)
    arrow(ax, (4.87, 4.26), (5.15, 4.26), color=COLORS["muted"], lw=2.2)
    arrow(ax, (6.97, 4.26), (7.25, 4.26), color=COLORS["orange"], lw=2.4)
    arrow(ax, (8.16, 3.85), (8.16, 2.87), color=COLORS["orange"], lw=2.4)
    arrow(ax, (7.25, 2.46), (6.97, 2.46), color=COLORS["green"], lw=2.4)
    arrow(ax, (5.15, 2.46), (4.3, 1.44), color=COLORS["purple"], lw=2.2, rad=0.08)
    arrow(ax, (5.6, 2.05), (4.0, 3.85), color=COLORS["muted"], lw=1.7, rad=0.35, linestyle=(0, (5, 3)), alpha=0.8)
    ax.text(4.15, 3.0, "进入下一轮", fontsize=9.8, color=COLORS["muted"])
    arrow(ax, (5.3, 1.03), (8.95, 1.03), color=COLORS["green"], lw=2.6)
    arrow(ax, (9.86, 1.44), (9.0, 3.85), color=COLORS["purple"], lw=1.7, rad=0.28, linestyle=(0, (5, 3)), alpha=0.72)
    ax.text(9.3, 2.85, "保留 best", fontsize=9.8, color=COLORS["purple"])

    text_box(
        ax,
        (10.0, 3.05),
        "只在 candidate\n内部临时生效；\n未被接受则丢弃",
        width=1.95,
        height=1.1,
        fc="#fbfcfd",
        ec=COLORS["grid"],
        fontsize=10,
    )

    save_figure(fig, "fig_04_model5_candidate_flow")


def draw_net_gain_waterfall() -> None:
    fig, ax = plt.subplots(figsize=(11.4, 5.6))
    ax.set_title("中转净收益：必须同时计算节省与新增搬运成本", fontsize=18, color=COLORS["ink"], pad=12)
    ax.set_xlim(-0.6, 6.1)
    ax.set_ylim(0, 110)
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.spines["bottom"].set_color(COLORS["grid"])
    ax.tick_params(axis="y", left=False, labelleft=False)
    ax.tick_params(axis="x", length=0)
    ax.set_xticks([0, 1.25, 2.5, 3.75])
    ax.set_xticklabels(["不中转成本", "避免取货绕行", "新增搬运增量", "中转后成本"], fontsize=11)

    base = 100
    saved = 18
    added = 5
    final = base - saved + added

    bars = [
        (0, 0, base, COLORS["gray"], "100"),
        (1.25, base - saved, saved, COLORS["green"], "-18"),
        (2.5, base - saved, added, COLORS["orange"], "+5"),
        (3.75, 0, final, COLORS["purple"], "87"),
    ]
    for x, bottom, height, color, label in bars:
        ax.bar(x, height, bottom=bottom, width=0.58, color=color, alpha=0.92)
        ax.text(
            x,
            bottom + height + 3,
            label,
            ha="center",
            va="bottom",
            fontsize=12,
            color=color,
            weight="bold",
        )

    ax.hlines(base, -0.25, 1.25, color=COLORS["grid"], linewidth=1.2, linestyle=(0, (4, 3)))
    ax.hlines(base - saved, 1.25, 2.5, color=COLORS["grid"], linewidth=1.2, linestyle=(0, (4, 3)))
    ax.hlines(final, 2.5, 4.05, color=COLORS["grid"], linewidth=1.2, linestyle=(0, (4, 3)))

    ax.text(
        5.15,
        74,
        "净收益\n"
        "gain = C(x) - C(T_r(x))",
        ha="center",
        va="center",
        fontsize=10.8,
        weight="bold",
        color=COLORS["ink"],
        bbox={
            "boxstyle": "round,pad=0.45,rounding_size=0.12",
            "facecolor": COLORS["blue_light"],
            "edgecolor": COLORS["blue"],
            "linewidth": 1.4,
        },
    )
    ax.text(
        5.15,
        42,
        "gain > 0\n且全局可行\n才接受中转",
        ha="center",
        va="center",
        fontsize=10.6,
        weight="bold",
        color=COLORS["ink"],
        bbox={
            "boxstyle": "round,pad=0.45,rounding_size=0.12",
            "facecolor": COLORS["green_light"],
            "edgecolor": COLORS["green"],
            "linewidth": 1.4,
        },
    )

    ax.text(
        2.75,
        104,
        "示意数值，用于说明计算逻辑",
        ha="center",
        fontsize=10,
        color=COLORS["muted"],
    )
    save_figure(fig, "fig_05_transshipment_net_gain")


def write_index() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    text = """# 毕设概念插图初稿

本目录包含 SVG 和 PNG 两种格式。SVG 适合继续编辑，PNG 适合直接插入 Word、PPT 或论文初稿。

| 图号 | 文件 | 建议用途 |
|---|---|---|
| 图 1 | `fig_01_transshipment_concept` | 解释“中转为什么能减少路程”，建议放在中转机制定义附近 |
| 图 2 | `fig_02_two_echelon_pdd_network` | 解释两级取送货网络结构，建议放在问题描述章节 |
| 图 3 | `fig_03_model_progression` | 解释 Model 1-5 的递进关系，建议放在模型设计或算法章节开头 |
| 图 4 | `fig_04_model5_candidate_flow` | 解释 Model 5 中转嵌入 ALNS candidate 的流程，建议放在算法设计章节 |
| 图 5 | `fig_05_transshipment_net_gain` | 解释中转收益计算与防劣化判断，建议放在中转接受准则附近 |

图 1 中的距离数值是概念示意值，不对应某个具体算例。若论文希望更严谨，可以后续替换为某个实验实例的真实路径距离。
"""
    (OUT_DIR / "README.md").write_text(text, encoding="utf-8")


def main() -> None:
    configure_matplotlib()
    draw_transshipment_concept()
    draw_two_echelon_network()
    draw_model_progression()
    draw_model5_candidate_flow()
    draw_net_gain_waterfall()
    write_index()
    print(f"Generated thesis figures in {OUT_DIR}")


if __name__ == "__main__":
    main()
