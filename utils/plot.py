# utils/plot.py
# -*- coding: utf-8 -*-
import os
from pathlib import Path
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from core.instance import ProblemInstance

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "output"


def plot_vrp_solution(
    problem: ProblemInstance,
    e1_routes: list,
    e2_routes_by_sat: dict,
    title="2E-VRP Solution",
    save_dir: str = None,
    filename: str = "solution.png",
):
    """
    可视化 2E-VRP 求解结果，并保存到 output/ 对应子目录
    """
    # 确定保存目录
    if save_dir:
        out_path = OUTPUT_DIR / save_dir
    else:
        out_path = OUTPUT_DIR
    os.makedirs(out_path, exist_ok=True)

    fig, ax = plt.subplots(figsize=(12, 8))
    
    # ==========================================
    # 核心修正：任务 ID 转 物理节点 ID 解析器
    # ==========================================
    def get_node(nid):
        """将任务ID (如 'S0d', 'C1p') 安全映射回物理节点 (如 'S0', 'C1')"""
        # 虚拟搬运节点: TRANS_PICK_X / TRANS_DROP_X → X
        if nid.startswith("TRANS_PICK_") or nid.startswith("TRANS_DROP_"):
            phys = nid.split("_", 2)[-1].rsplit("@", 1)[-1]
            if phys in problem.nodes:
                return problem.nodes[phys]

        if nid in problem.nodes:
            return problem.nodes[nid]

        # 尝试去掉尾部的 'd' (配送) 或 'p' (回收)
        if nid.endswith(('d', 'p')) and nid[:-1] in problem.nodes:
            return problem.nodes[nid[:-1]]

        raise KeyError(f"绘图失败: 无法将任务ID '{nid}' 映射到物理坐标点。")

    # 1. 画出所有节点
    cx, cy = [c.x for c in problem.customers], [c.y for c in problem.customers]
    ax.scatter(cx, cy, c="#6E6E6E", marker="o", s=34, zorder=4,
               linewidths=0.4, edgecolors="white", label="Customers")

    # 大规模算例不强制标所有客户，避免标签互相覆盖。
    if len(problem.customers) <= 30:
        for c in problem.customers:
            ax.text(c.x + 0.45, c.y + 0.45, c.id, fontsize=8,
                    color="#555555", zorder=6)

    sx, sy = [s.x for s in problem.satellites], [s.y for s in problem.satellites]
    ax.scatter(sx, sy, c="#2563EB", marker="^", s=170,
               edgecolors="black", linewidths=0.8, zorder=7, label="Satellites")
    for s in problem.satellites:
        ax.text(s.x + 1, s.y + 1, s.id, fontsize=10, fontweight="bold",
                color="#1D4ED8", zorder=8)

    ax.scatter(problem.depot.x, problem.depot.y, c="#DC2626", marker="s", s=210,
               edgecolors="black", linewidths=0.8, zorder=8, label="Depot")
    ax.text(problem.depot.x + 1.5, problem.depot.y + 1.5, problem.depot.id,
            fontsize=12, fontweight="bold", color="#DC2626", zorder=9)

    # 2. 画出 E1 路线 (红色粗线)
    for idx, route in enumerate(e1_routes):
        rx = [get_node(node_id).x for node_id in route]
        ry = [get_node(node_id).y for node_id in route]
        ax.plot(rx, ry, color="#DC2626", linewidth=2.8, linestyle="-",
                alpha=0.72, zorder=2)

    # 3. 画出 E2 路线：每辆车单独配色，避免同仓多车路线难以区分。
    cmap = plt.get_cmap("tab20")
    sat_ids = [s.id for s in problem.satellites]
    route_counter = 0
    
    for sat_id, routes in e2_routes_by_sat.items():
        sat_offset = sat_ids.index(sat_id) if sat_id in sat_ids else 0
        for local_idx, route in enumerate(routes):
            color = cmap((route_counter + sat_offset * 3) % cmap.N)
            route_list = list(route)

            # 补齐首尾前置仓 (如果还没的话)
            if not route_list or route_list[0] != sat_id:
                route_list.insert(0, sat_id)
            if route_list[-1] != sat_id:
                route_list.append(sat_id)
                
            rx = [get_node(node_id).x for node_id in route_list]
            ry = [get_node(node_id).y for node_id in route_list]
            ax.plot(rx, ry, color=color, linewidth=1.6, linestyle="--",
                    alpha=0.82, zorder=3)
            if len(problem.customers) <= 30 and len(routes) <= 8:
                mid = len(rx) // 2
                ax.text(rx[mid], ry[mid], f"{sat_id}-V{local_idx}", fontsize=7,
                        color=color, zorder=7,
                        bbox=dict(facecolor="white", edgecolor="none",
                                  alpha=0.72, pad=1.2))
            route_counter += 1

    legend_handles = [
        Line2D([0], [0], marker="o", color="w", label="Customers",
               markerfacecolor="#6E6E6E", markeredgecolor="white", markersize=7),
        Line2D([0], [0], marker="^", color="w", label="Satellites",
               markerfacecolor="#2563EB", markeredgecolor="black", markersize=9),
        Line2D([0], [0], marker="s", color="w", label="Depot",
               markerfacecolor="#DC2626", markeredgecolor="black", markersize=9),
        Line2D([0], [0], color="#DC2626", linewidth=2.8, label="E1 truck route"),
        Line2D([0], [0], color="#4C78A8", linewidth=1.8,
               linestyle="--", label="E2 vehicle route"),
    ]

    ax.set_title(title, fontsize=15, fontweight="bold", pad=10)
    ax.set_xlabel("X Coordinate")
    ax.set_ylabel("Y Coordinate")
    ax.legend(handles=legend_handles, loc="upper right", frameon=True,
              framealpha=0.92, fontsize=9)
    ax.grid(True, linestyle=":", alpha=0.45)
    ax.set_aspect("equal", adjustable="box")
    fig.tight_layout()
    save_file = out_path / filename
    fig.savefig(save_file, dpi=220, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"✓ 路径图已保存至: {save_file}")
