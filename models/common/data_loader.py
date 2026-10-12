# models/common/data_loader.py
# -*- coding: utf-8 -*-
import csv
from typing import Dict, Tuple

from models.common.domain.node import Customer, Depot, Node, Satellite


def _float_field(row: Dict[str, str], key: str, default: float) -> float:
    """Read an optional numeric CSV field without treating zero as missing."""
    raw = row.get(key)
    if raw is None or not raw.strip():
        return float(default)
    return float(raw)


def parse_dataset(filepath: str) -> Tuple[Dict[str, Node], Dict]:
    """
    解析标准的 CSV 格式数据文件。
    """
    nodes: Dict[str, Node] = {}

    with open(filepath, mode="r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if not row.get("node_id"):
                continue

            node_id = row["node_id"].strip()
            node_type = row["node_type"].strip().lower()
            x = float(row["x"])
            y = float(row["y"])

            if node_type == "d":
                nodes[node_id] = Depot(node_id=node_id, x=x, y=y)
            elif node_type == "s":
                nodes[node_id] = Satellite(node_id=node_id, x=x, y=y)
            elif node_type == "c":
                assigned_sat = row.get("assigned_sat", "").strip() or None
                fallback_count = _float_field(row, "container_count", 1.0)
                delivery_container_count = _float_field(
                    row, "delivery_container_count", fallback_count
                )
                pickup_container_count = _float_field(
                    row, "pickup_container_count", fallback_count
                )
                customer = Customer(
                    node_id=node_id,
                    x=x,
                    y=y,
                    demand=float(row.get("delivery_demand", 0.0)),
                    service_time=float(row.get("service_time", 0.0)),
                    tw_del=(
                        float(row.get("delivery_ready", 0.0)),
                        float(row.get("delivery_due", 99999.0)),
                    ),
                    tw_pick=(
                        float(row.get("pickup_ready", 0.0)),
                        float(row.get("pickup_due", 99999.0)),
                    ),
                    assigned_sat=assigned_sat,
                    container_count_del=delivery_container_count,
                    container_count_pick=pickup_container_count,
                )

                nodes[node_id] = customer

    if not any(isinstance(n, Depot) for n in nodes.values()):
        raise ValueError("数据文件中未找到总仓库 ('d' 类型节点)。")

    fleet_config = {}
    return nodes, fleet_config
