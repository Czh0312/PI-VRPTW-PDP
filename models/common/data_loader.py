# models/common/data_loader.py
# -*- coding: utf-8 -*-
import csv
import json
from pathlib import Path
from typing import Dict, Tuple

from models.common.domain.node import Customer, Depot, Node, Satellite
from tools.configuration.loader import resolve_project_path


CUSTOMER_ORDER_COLUMNS = ('container_count', 'goods_weight', 'release_time', 'delivery_ready',
                          'delivery_due', 'pickup_ready', 'pickup_due', 'service_time', 'min_use_time')


def read_order_rows(path, *, network=None, batch_ids=()):
    """Load orders and fill derived fields for a shared benchmark when requested."""
    path = Path(path)
    with path.open(encoding='utf-8-sig', newline='') as handle:
        if path.suffix == '.json':
            rows = json.load(handle)
        else:
            reader = csv.DictReader(handle)
            fields = set(reader.fieldnames or ())
            if {'node_id', 'node_type'} <= fields:
                missing = set(CUSTOMER_ORDER_COLUMNS) - fields
                if missing:
                    raise ValueError(f'Missing order columns in {path.name}: {sorted(missing)}')
                rows = [dict(order_id=r['node_id'].strip()+'_order', customer_id=r['node_id'].strip(),
                             **{key: r[key] for key in CUSTOMER_ORDER_COLUMNS})
                        for r in reader if r['node_type'].strip().lower() == 'c']
                rows.sort(key=lambda r: r['order_id'])
            else:
                rows = list(reader)
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError('Orders must be a list of objects or a CSV table')
    if network is not None:
        sites = tuple(s.id for s in network.satellites)
        nearest = {}
        for row in rows:
            if 'satellite_id' not in row:
                customer = row.get('customer_id')
                if (not isinstance(customer, str) or customer not in network.nodes
                        or network.nodes[customer].type != 'c'):
                    raise ValueError(f'Unknown order customer: {customer!r}')
                if not sites:
                    raise ValueError('Nearest assignment requires at least one satellite')
                if customer not in nearest:
                    nearest[customer] = min(sites, key=lambda s: (network.distance(s, customer), s))
                row['satellite_id'] = nearest[customer]
            if batch_ids:
                row.setdefault('delivery_batch_id', batch_ids[0])
                row.setdefault('allowed_batch_ids', list(batch_ids))
    return rows


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

    source = Path(filepath)
    if not source.exists():
        source = resolve_project_path(source)
    with source.open(mode="r", encoding="utf-8-sig") as f:
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
