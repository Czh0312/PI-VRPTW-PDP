# utils/plot.py
# -*- coding: utf-8 -*-
import matplotlib.pyplot as plt
from core.instance import ProblemInstance

def plot_vrp_solution(problem: ProblemInstance, e1_routes: list, e2_routes_by_sat: dict, title="2E-VRP Solution"):
    """
    可视化 2E-VRP 求解结果
    """
    plt.figure(figsize=(12, 8))
    
    # ==========================================
    # 核心修正：任务 ID 转 物理节点 ID 解析器
    # ==========================================
    def get_node(nid):
        """将任务ID (如 'S0d', 'C1p') 安全映射回物理节点 (如 'S0', 'C1')"""
        if nid in problem.nodes:
            return problem.nodes[nid]
        
        # 尝试去掉尾部的 'd' (配送) 或 'p' (回收)
        if nid.endswith(('d', 'p')) and nid[:-1] in problem.nodes:
            return problem.nodes[nid[:-1]]
            
        raise KeyError(f"绘图失败: 无法将任务ID '{nid}' 映射到物理坐标点。")

    # 1. 画出所有节点
    # 客户点 (灰色小点)
    cx, cy = [c.x for c in problem.customers], [c.y for c in problem.customers]
    plt.scatter(cx, cy, c='gray', marker='.', s=50, zorder=3, label='Customers')
    
    # 【新增】: 遍历所有顾客，标上顾客 ID
    for c in problem.customers:
        # x和y加上0.5的偏移量，防止文字和点重叠
        plt.text(c.x + 0.5, c.y + 0.5, c.id, fontsize=8, color='dimgray', zorder=4)
    
    # 前置仓 (蓝色三角形)
    sx, sy = [s.x for s in problem.satellites], [s.y for s in problem.satellites]
    plt.scatter(sx, sy, c='blue', marker='^', s=150, edgecolors='black', zorder=5, label='Satellites')
    for s in problem.satellites:
        plt.text(s.x + 1, s.y + 1, s.id, fontsize=10, fontweight='bold', color='blue', zorder=6)
        
    # 总仓库 (红色正方形)
    plt.scatter(problem.depot.x, problem.depot.y, c='red', marker='s', s=200, edgecolors='black', zorder=6, label='Depot')
    plt.text(problem.depot.x + 1.5, problem.depot.y + 1.5, problem.depot.id, fontsize=12, fontweight='bold', color='red', zorder=7)

    # 2. 画出 E1 路线 (红色粗线)
    for idx, route in enumerate(e1_routes):
        rx = [get_node(node_id).x for node_id in route]
        ry = [get_node(node_id).y for node_id in route]
        plt.plot(rx, ry, color='red', linewidth=2.5, linestyle='-', alpha=0.7, zorder=1)

    # 3. 画出 E2 路线 (各前置仓用不同颜色)
    colors = ['green', 'purple', 'orange', 'cyan', 'magenta', 'brown']
    sat_ids = [s.id for s in problem.satellites]
    
    for sat_id, routes in e2_routes_by_sat.items():
        if sat_id in sat_ids:
            color = colors[sat_ids.index(sat_id) % len(colors)]
        else:
            color = 'black' # 兜底颜色
            
        for route in routes:
            # 判断 route 是对象(ALNS)还是字符串列表(Exact)
            route_list = [t.cust for t in route.tasks] if hasattr(route, 'tasks') else list(route)
            
            # 补齐首尾前置仓 (如果还没的话)
            if not route_list or route_list[0] != sat_id:
                route_list.insert(0, sat_id)
            if route_list[-1] != sat_id:
                route_list.append(sat_id)
                
            rx = [get_node(node_id).x for node_id in route_list]
            ry = [get_node(node_id).y for node_id in route_list]
            plt.plot(rx, ry, color=color, linewidth=1.2, linestyle='--', alpha=0.8, zorder=2)

    plt.title(title, fontsize=14, fontweight='bold')
    plt.xlabel('X Coordinate')
    plt.ylabel('Y Coordinate')
    plt.legend(loc='upper right')
    plt.grid(True, linestyle=':', alpha=0.6)
    plt.tight_layout()
    plt.show()