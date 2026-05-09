# utils/distance.py
# -*- coding: utf-8 -*-
import math
from typing import Dict, List, Optional, Any

def euclid_dist(x1: float, y1: float, x2: float, y2: float) -> float:
    """计算两点坐标的欧氏距离"""
    return math.hypot(x1 - x2, y1 - y2)

def dist_dict(a: dict, b: dict) -> float:
    """计算两个字典形式节点坐标的欧氏距离"""
    return math.hypot(a["x"] - b["x"], a["y"] - b["y"])

# 修正点1: 增加 Optional[str]，因为如果 sat_ids 为空，best 会返回 None
def nearest_satellite(nodes_dict: Dict[str, dict], cust_id: str, sat_ids: List[str]) -> Optional[str]:
    """为客户寻找最近的前置仓"""
    c = nodes_dict[cust_id]
    best: Optional[str] = None
    best_d = float('inf')
    
    for s in sat_ids:
        d = dist_dict(c, nodes_dict[s])
        if d < best_d:
            best, best_d = s, d
    return best

# 修正点2: 为 route_obj 增加 Any 类型标注，消除编辑器的类型推断警告
def normalize_routes(route_obj: Any, depot: str) -> List[List[str]]:
    """
    接收 {veh:[n...]} 字典、列表或边集，统一转换为 [[...], ...] 格式
    这在解析 Gurobi 边集输出时非常有用
    """
    if route_obj is None:
        return []

    if isinstance(route_obj, (list, tuple)):
        if route_obj and isinstance(route_obj[0], (list, tuple)):
            return [list(r) for r in route_obj if r]
        else:
            return [list(route_obj)] if route_obj else []

    if isinstance(route_obj, dict):
        vals = list(route_obj.values())
        if vals and all(isinstance(v, (list, tuple)) for v in vals):
            return [list(route_obj[k]) for k in sorted(route_obj.keys(), key=str) if route_obj[k]]
        
        # 将边集转换为路径
        succ: Dict[str, str] = {}
        indeg: Dict[str, int] = {}
        
        for k, v in route_obj.items():
            if isinstance(k, (list, tuple)) and len(k) == 2 and v:
                i, j = k
                succ[i] = j
                indeg[j] = indeg.get(j, 0) + 1
                indeg.setdefault(i, 0)
                
        if not succ: 
            return []
        
        start = depot if depot in succ or depot in indeg else None
        if start is None:
            for n, d in indeg.items():
                if d == 0:
                    start = n
                    break
                    
        route: List[str] = []
        seen = set()
        cur = start
        
        while cur is not None and cur not in seen:
            route.append(cur)
            seen.add(cur)
            cur = succ.get(cur)
            
        if cur and (not route or route[-1] != cur):
            route.append(cur)
        return [route] if route else []

    return []