# models/alns_base.py
# -*- coding: utf-8 -*-
"""
ALNS 求解器公共组件。
各模型共享的 Route / Solution 数据结构、工具函数和常量。
"""
import random

# 可行性检查用浮点容差
FEAS_TOL = 1e-6

# TFp 禁用标记（当 TFp >= 此值时不生效）
TFP_DISABLED_THRESHOLD = 9000.0


def is_tfp_active(tfp: float) -> bool:
    return tfp < TFP_DISABLED_THRESHOLD


# =====================================================================
#  数据结构
# =====================================================================

class Route:
    """一条路径：depot/satellite + 中间节点序列"""
    def __init__(self, base_id, nodes=None):
        self.base = base_id
        self.nodes = nodes or []
        # 虚拟搬运节点载量: {virtual_node_id: (w, v)}
        self.transport_loads = {}

    def full_path(self):
        return [self.base] + self.nodes + [self.base]

    def copy(self):
        r = Route(self.base, list(self.nodes))
        r.transport_loads = dict(self.transport_loads)
        return r


class Solution:
    """完整的两阶段解"""
    def __init__(self):
        self.e1_routes = []
        self.e2_routes = {}
        self.cost = float('inf')
        # Per-solution physical location overrides for transshipment models.
        # Plain PDP models leave this empty and use the evaluator defaults.
        self.task_to_phys = {}
        # 中转记录: {(sat_id, beneficiary_idx, pick_node, new_phys):
        #   (old_phys, carrier_idx, container_w, container_v)}
        self.transshipments = {}

    def copy(self):
        s = Solution()
        s.e1_routes = [r.copy() for r in self.e1_routes]
        s.e2_routes = {
            sat: [r.copy() for r in routes]
            for sat, routes in self.e2_routes.items()
        }
        s.cost = self.cost
        s.task_to_phys = dict(self.task_to_phys)
        s.transshipments = dict(self.transshipments)
        return s


# =====================================================================
#  工具函数
# =====================================================================

def clean_empty_routes(sol):
    sol.e1_routes = [r for r in sol.e1_routes if r.nodes]
    for sat_id in sol.e2_routes:
        sol.e2_routes[sat_id] = [
            r for r in sol.e2_routes[sat_id] if r.nodes
        ]


def _roulette(weights):
    total = sum(weights)
    if total < 1e-10:
        return random.randint(0, len(weights) - 1)
    r = random.random() * total
    cumsum = 0.0
    for i, w in enumerate(weights):
        cumsum += w
        if cumsum >= r:
            return i
    return len(weights) - 1
