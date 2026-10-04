"""Order identity is separate from location identity and delivery wave."""
from __future__ import annotations

from dataclasses import dataclass
import csv
import json
from pathlib import Path

from configuration.loader import resolve_project_path
from models.routing.common.data_loader import parse_dataset
from models.routing.common.domain.instance import ProblemInstance
from models.routing.common.matrix_loader import load_square_matrix
from .policy import MultiBatchPolicy, integer, nonnegative


@dataclass(frozen=True)
class Order:
    id: str
    customer: str
    satellite: str
    batch: str
    count: int
    goods_weight: float
    delivery_window: tuple
    pickup_window: tuple
    service_time: int
    min_use_time: int = 0


class MultiBatchInstance:
    def __init__(self, network, orders, policy):
        self.network = network
        self.operating_policy = MultiBatchPolicy.from_config(policy)
        self.orders = tuple(orders)
        self.order_map = {o.id: o for o in self.orders}
        self.fleet = network.fleet
        self.satellites = tuple(s.id for s in network.satellites)
        self.depot = network.depot.id
        self.batch_map = {b.id: b for b in self.operating_policy.batches}
        self.batch_index = {b.id: i for i, b in enumerate(self.operating_policy.batches)}
        if len(self.order_map) != len(self.orders):
            raise ValueError('Duplicate order_id')
        p = self.operating_policy
        if self.orders and p.container_pool_size <= 0:
            raise ValueError('Positive demand requires a positive container_pool_size')
        for name in ('e1_count', 'e2_per_sat_count'):
            integer(getattr(self.fleet, name), name, 1)
        for name in ('e1_weight_cap', 'e1_volume_cap', 'e2_weight_cap', 'e2_volume_cap',
                     'container_weight', 'container_volume'):
            nonnegative(getattr(self.fleet, name), name)
        if self.fleet.container_weight == self.fleet.container_volume == 0:
            raise ValueError('Containers require positive weight or volume')
        customers = {c.id for c in network.customers}
        for satellite in network.satellites:
            nonnegative(satellite.service_time, 'satellite service_time')
        for o in self.orders:
            if not isinstance(o.id, str) or not o.id:
                raise ValueError('order_id must be nonempty')
            if o.customer not in customers or o.satellite not in self.satellites or o.batch not in self.batch_map:
                raise ValueError(f'{o.id}: invalid location, satellite or batch')
            integer(o.count, 'container_count', 1)
            nonnegative(o.goods_weight, 'goods_weight')
            integer(o.service_time, 'service_time')
            integer(o.min_use_time, 'min_use_time')
            for tw in (o.delivery_window, o.pickup_window):
                if len(tw) != 2 or tw[0] > tw[1]:
                    raise ValueError(f'{o.id}: invalid time window after rounding')
                for t in tw:
                    integer(t, 'time window')
                if tw[0] < p.cycle_start or tw[1] > p.cycle_end:
                    raise ValueError(f'{o.id}: time window outside cycle')
        for a in network.nodes:
            for b in network.nodes:
                nonnegative(network.distance(a, b), 'distance')
                nonnegative(network.travel_time(a, b), 'travel_time')
        self.total = sum(o.count for o in self.orders)
        self.demands = {(s, b.id): sum(o.count for o in self.orders if o.satellite == s and o.batch == b.id)
                        for s in self.satellites for b in p.batches}
        self.weights = {(s, b.id): sum(o.goods_weight + o.count*self.fleet.container_weight
                                      for o in self.orders if o.satellite == s and o.batch == b.id)
                        for s in self.satellites for b in p.batches}
        self.batch_demands = {b.id: sum(o.count for o in self.orders if o.batch == b.id) for b in p.batches}
        self._travel = {(a, b): p.ticks(network.travel_time(a, b))
                        for a in network.nodes for b in network.nodes}

    def travel(self, a, b):
        return self._travel[a, b]

    def distance(self, a, b):
        return self.network.distance(a, b)

    def service(self, satellite):
        return self.operating_policy.ticks(self.network.nodes[satellite].service_time)

    def task(self, order_id, kind):
        o = self.order_map[order_id]
        if kind not in ('d', 'p'):
            raise ValueError(f'Invalid task kind: {kind}')
        weight = o.count*self.fleet.container_weight + (o.goods_weight if kind == 'd' else 0)
        return o, weight, o.count*self.fleet.container_volume


def load_orders(path, policy):
    path = Path(path)
    if path.suffix == '.json':
        rows = json.loads(path.read_text(encoding='utf-8'))
    else:
        with path.open(encoding='utf-8-sig', newline='') as f:
            rows = list(csv.DictReader(f))
    if not isinstance(rows, list):
        raise ValueError('Orders must be a JSON list or CSV table')
    result = []
    required = {'order_id', 'customer_id', 'satellite_id', 'delivery_batch_id', 'container_count',
                'goods_weight', 'delivery_ready', 'delivery_due', 'pickup_ready', 'pickup_due', 'service_time'}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('Each order must be an object')
        if required-row.keys():
            raise ValueError(f'Missing order fields: {sorted(required-set(row))}')
        extras = set(row)-required-{'pickup_container_count', 'min_use_time'}
        if extras:
            raise ValueError(f'Unknown order fields: {sorted(extras)}')
        for key in ('order_id', 'customer_id', 'satellite_id', 'delivery_batch_id'):
            if not isinstance(row[key], str) or not row[key].strip():
                raise ValueError(f'{key} must be a nonempty string')
        def number(key, default=None):
            value = row.get(key, default)
            if isinstance(value, bool):
                raise ValueError(f'{key} must be numeric')
            return float(value)
        count = integer(number('container_count'), 'container_count', 1)
        if integer(number('pickup_container_count', count), 'pickup_container_count') != count:
            raise ValueError('UNBALANCED_ORDER_COUNTS: delivery and pickup counts must match')
        result.append(Order(str(row['order_id']), str(row['customer_id']), str(row['satellite_id']),
                            str(row['delivery_batch_id']), count, number('goods_weight'),
                            (policy.ticks(number('delivery_ready')), policy.ticks(number('delivery_due'), 'floor')),
                            (policy.ticks(number('pickup_ready')), policy.ticks(number('pickup_due'), 'floor')),
                            policy.ticks(number('service_time')), policy.ticks(number('min_use_time', 0))))
    return result


def load_instance(config, fleet_config):
    if config.get('schema_version') != 2 or 'orders' not in config:
        raise ValueError('Multibatch requires schema_version=2 and a separate orders file')
    policy = MultiBatchPolicy.from_config(fleet_config.get('operating_policy'))
    policy.require_model(config['model'])
    # Validate raw vehicle counts before the legacy FleetConfig casts to int.
    for name in ('E1_vehicle_count', 'vehicles_per_satellite'):
        integer(fleet_config.get(name, 2 if name == 'E1_vehicle_count' else 4), name, 1)
    speed = fleet_config.get('speed', 1)
    if nonnegative(speed, 'speed') == 0:
        raise ValueError('speed must be positive')
    data_path = resolve_project_path(config['data'])
    with data_path.open(encoding='utf-8-sig', newline='') as f:
        ids = [r['node_id'].strip() for r in csv.DictReader(f) if r.get('node_id')]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate physical node_id; use the separate orders table')
    nodes, _ = parse_dataset(str(data_path))
    if sum(n.type == 'd' for n in nodes.values()) != 1:
        raise ValueError('Exactly one depot is required')
    distance = load_square_matrix(resolve_project_path(config['distance_matrix'])) if config.get('distance_matrix') else None
    duration = load_square_matrix(resolve_project_path(config['duration_matrix'])) if config.get('duration_matrix') else None
    if duration is not None and distance is None:
        raise ValueError('duration_matrix requires distance_matrix')
    network = ProblemInstance(nodes, {k: v for k, v in fleet_config.items() if k != 'operating_policy'},
                              speed=speed, distance_matrix=distance, travel_time_matrix=duration)
    orders = load_orders(resolve_project_path(config['orders']), policy)
    return MultiBatchInstance(network, orders, policy)
