"""Order identity is separate from location identity and delivery wave."""
from __future__ import annotations

from dataclasses import dataclass, replace
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
    release_time: int | None = None
    allowed_batch_ids: tuple | None = None


class MultiBatchInstance:
    def __init__(self, network, orders, policy, model=None, *, _physical_source=None):
        if _physical_source is not None and (
                not isinstance(_physical_source,MultiBatchInstance) or _physical_source.network is not network):
            raise ValueError('Physical cache source must be a validated instance of the same network')
        self.network = network
        self.operating_policy = MultiBatchPolicy.from_config(policy)
        if model is None:
            model = 3 if len(self.operating_policy.batches) == 1 else 5
        self.operating_policy.require_model(model)
        self.model_number = int(model)
        self.reusable = self.model_number in (3, 4, 5, 6, 7, 8)
        self.free_assignment = self.model_number in (2, 4)
        self.task_kinds = ('d', 'p') if self.reusable else ('d',)
        # Direct Python callers can supply an integral float, just as JSON/CSV
        # loaders accept 1.0. Canonicalize it before quantities enter schedules
        # and MILP extraction, which require actual integer container counts.
        self.orders = tuple(o if isinstance(o.count, int) else
                            replace(o, count=integer(o.count, 'container_count', 1)) for o in orders)
        self.order_map = {o.id: o for o in self.orders}
        self.fleet = network.fleet
        self.satellites = tuple(s.id for s in network.satellites)
        self.depot = network.depot.id
        self.batch_map = {b.id: b for b in self.operating_policy.batches}
        self.batch_index = {b.id: i for i, b in enumerate(self.operating_policy.batches)}
        if len(self.order_map) != len(self.orders):
            raise ValueError('Duplicate order_id')
        p = self.operating_policy
        if self.reusable and self.orders and p.container_pool_size <= 0:
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
            if o.release_time is not None:
                integer(o.release_time, 'release_time')
            if o.allowed_batch_ids is not None:
                if (not isinstance(o.allowed_batch_ids, (tuple, list))
                        or any(not isinstance(b, str) or b not in self.batch_map for b in o.allowed_batch_ids)
                        or len(set(o.allowed_batch_ids)) != len(o.allowed_batch_ids)):
                    raise ValueError(f'{o.id}: invalid or duplicate allowed_batch_ids')
            for tw in (o.delivery_window, o.pickup_window):
                if len(tw) != 2 or tw[0] > tw[1]:
                    raise ValueError(f'{o.id}: invalid time window after rounding')
                for t in tw:
                    integer(t, 'time window')
                if tw[0] < p.cycle_start or tw[1] > p.cycle_end:
                    raise ValueError(f'{o.id}: time window outside cycle')
        from .availability import fixed_order_availability_errors
        # Orders and their batch bindings are immutable within an instance;
        # projections construct a new instance. Avoid an extra scan per move.
        self._availability_codes = tuple(e['code'] for e in fixed_order_availability_errors(self))
        if _physical_source is None:
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
        if _physical_source is not None and p.time_tick_seconds==_physical_source.operating_policy.time_tick_seconds:
            # M6 changes batches, quantities and cutoffs, not the road matrix
            # or time precision. Reuse validated immutable physical metrics.
            self._travel = _physical_source._travel
            self._service_ticks = _physical_source._service_ticks
        else:
            self._travel = {(a, b): p.ticks(network.travel_time(a, b))
                            for a in network.nodes for b in network.nodes}
            self._service_ticks = {}

    def travel(self, a, b):
        return self._travel[a, b]

    def distance(self, a, b):
        return self.network.distance(a, b)

    def service(self, satellite):
        raw = self.network.nodes[satellite].service_time
        key = (type(raw), raw, self.operating_policy.time_tick_seconds)
        if key not in self._service_ticks:
            self._service_ticks[key] = self.operating_policy.ticks(raw)
        return self._service_ticks[key]

    def task(self, order_id, kind):
        o = self.order_map[order_id]
        if kind not in self.task_kinds:
            raise ValueError(f'Invalid task kind: {kind}')
        weight = o.count*self.fleet.container_weight + (o.goods_weight if kind == 'd' else 0)
        return o, weight, o.count*self.fleet.container_volume


def load_orders(path, policy, *, joint=False):
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
        extras = set(row)-required-{'pickup_container_count', 'min_use_time', 'release_time', 'allowed_batch_ids'}
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
        release, allowed = None, None
        if joint and ('release_time' not in row or 'allowed_batch_ids' not in row):
            raise ValueError('M6 orders require explicit release_time and allowed_batch_ids')
        if 'release_time' in row:
            release = policy.ticks(number('release_time'))
        if 'allowed_batch_ids' in row:
            raw = row['allowed_batch_ids']
            if isinstance(raw, str):
                raw = json.loads(raw)
            if not isinstance(raw, list) or any(not isinstance(b, str) or not b for b in raw):
                raise ValueError('allowed_batch_ids must be a list of batch IDs')
            allowed = tuple(raw)
        result.append(Order(str(row['order_id']), str(row['customer_id']), str(row['satellite_id']),
                            str(row['delivery_batch_id']), count, number('goods_weight'),
                            (policy.ticks(number('delivery_ready')), policy.ticks(number('delivery_due'), 'floor')),
                            (policy.ticks(number('pickup_ready')), policy.ticks(number('pickup_due'), 'floor')),
                            policy.ticks(number('service_time')), policy.ticks(number('min_use_time', 0)), release, allowed))
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
        network_rows = [r for r in csv.DictReader(f) if r.get('node_id')]
        ids = [r['node_id'].strip() for r in network_rows]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate physical node_id; use the separate orders table')
    nodes, _ = parse_dataset(str(data_path))
    # The legacy reader constructs satellites without their declared service
    # duration. Restore it here so both solvers and the audit use the input.
    for row in network_rows:
        node = nodes[row['node_id'].strip()]
        if node.type == 's':
            node.service_time = nonnegative(float(row.get('service_time') or 0), 'satellite service_time')
    if sum(n.type == 'd' for n in nodes.values()) != 1:
        raise ValueError('Exactly one depot is required')
    distance = load_square_matrix(resolve_project_path(config['distance_matrix'])) if config.get('distance_matrix') else None
    duration = load_square_matrix(resolve_project_path(config['duration_matrix'])) if config.get('duration_matrix') else None
    if duration is not None and distance is None:
        raise ValueError('duration_matrix requires distance_matrix')
    network = ProblemInstance(nodes, {k: v for k, v in fleet_config.items() if k != 'operating_policy'},
                              speed=speed, distance_matrix=distance, travel_time_matrix=duration)
    orders = load_orders(resolve_project_path(config['orders']), policy, joint=config['model'] in (6, 7, 8))
    if config['model'] in (6, 7, 8):
        from .joint import JointBatchInstance
        from .uniform import UniformCycleInstance
        if not config.get('economic_config'):
            raise ValueError('M6–M8 require economic_config')
        options = config.get('joint_batch_options', {})
        if config['model'] in (7, 8):
            from .calendar import CalendarCycleInstance
            return CalendarCycleInstance(network, orders, policy, options, config['economic_config'], model=config['model'])
        factory = UniformCycleInstance if options.get('schedule_mode') == 'uniform_cycle' else JointBatchInstance
        return factory(network, orders, policy, options,
                                  config['economic_config'])
    return MultiBatchInstance(network, orders, policy, model=config['model'])
