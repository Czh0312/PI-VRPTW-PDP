"""M10 inputs. Input minutes are rounded conservatively to integer seconds.

There are no batch fields in the physical problem. Legacy batch annotations
may be read as source metadata, but never constrain M10 order assignments.
"""
from dataclasses import dataclass, asdict, replace
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
import csv
import json
import math

from models.common.cycle.instance import Order
from models.common.cycle.policy import integer, nonnegative
from models.common.data_loader import parse_dataset
from models.common.domain.instance import ProblemInstance
from models.common.matrix_loader import load_square_matrix
from tools.configuration.loader import resolve_project_path

VERSION = 'no_batch_container_cycle_v1'


@dataclass(frozen=True)
class NoBatchPolicy:
    cycle_start: int
    cycle_end: int
    cleaning_duration: int
    e1_load_time: int
    e2_dispatch_prep_time: int
    e2_return_unload_time: int
    depot_return_unload_time: int
    e1_turnaround_time: int
    e2_turnaround_time: int
    e2_max_trips_per_vehicle: int
    repeat_interval: int = 86400
    time_tick_seconds: int = 1
    policy_version: str = VERSION
    terminal_policy: str = 'all_clean_at_depot'

    def ticks(self, minutes, rounding='ceil'):
        nonnegative(minutes, 'time')
        value = Decimal(str(minutes))*60/self.time_tick_seconds
        return int(value.to_integral_value(rounding=ROUND_FLOOR if rounding == 'floor' else ROUND_CEILING))

    def minutes(self, ticks):
        return ticks*self.time_tick_seconds/60

    def as_dict(self):
        return {**asdict(self), 'serialized_time_unit': 'ticks'}

    @classmethod
    def from_config(cls, raw):
        if isinstance(raw, cls):
            return raw
        if not isinstance(raw, dict) or raw.get('policy_version') not in (VERSION, 'multi_batch_cutoff_v1'):
            raise ValueError('M10 requires an explicit no-batch policy or a source cycle policy')
        if raw.get('time_unit', 'minutes') != 'minutes':
            raise ValueError('M10 input time_unit must be minutes')
        tick = integer(raw.get('time_tick_seconds', 1), 'time_tick_seconds', 1)
        if raw.get('time_tick_subdivision', 1) != 1:
            raise ValueError('M10 source clock must use whole ticks; convert subdivided results explicitly')
        def ticks(name, default=0, floor=False):
            value = nonnegative(raw.get(name, default), name)
            return int((Decimal(str(value))*60/tick).to_integral_value(rounding=ROUND_FLOOR if floor else ROUND_CEILING))
        result = cls(ticks('cycle_start', 480), ticks('cycle_end', 1260, True),
                     ticks('cleaning_duration', 15), ticks('e1_load_time', 2),
                     ticks('e2_dispatch_prep_time', 1), ticks('e2_return_unload_time', 1),
                     ticks('depot_return_unload_time', 1), ticks('e1_turnaround_time'),
                     ticks('e2_turnaround_time', 1), integer(raw.get('e2_max_trips_per_vehicle', 12), 'e2 trips', 1),
                     ticks('repeat_interval_minutes', 1440), tick)
        if result.cycle_end <= result.cycle_start or result.repeat_interval < result.cycle_end-result.cycle_start:
            raise ValueError('Invalid operating window/repeat interval')
        if result.e1_load_time+result.cleaning_duration <= 0:
            raise ValueError('M10 requires positive loading or cleaning time to exclude zero-time reuse cycles')
        if raw.get('terminal_policy', 'all_clean_at_depot') != 'all_clean_at_depot':
            raise ValueError('M10 requires all_clean_at_depot')
        return result


class NoBatchInstance:
    model_number = 10
    reusable = True
    free_assignment = False
    task_kinds = ('d', 'p')

    def __init__(self, network, orders, policy, options=None):
        self.network, self.fleet = network, network.fleet
        self.operating_policy = p = NoBatchPolicy.from_config(policy)
        self.orders = tuple(replace(o, batch='', allowed_batch_ids=None) for o in orders)
        self.order_map = {o.id: o for o in self.orders}
        self.satellites = tuple(s.id for s in network.satellites)
        self.depot = network.depot.id
        self.total = len(self.orders)
        self.options = dict(options or {})
        permitted = {'pool_max', 'objective', 'transport_budget', 'e1_distance_cost', 'e2_distance_cost',
                     'e1_max_trips_per_vehicle'}
        if set(self.options)-permitted:
            raise ValueError(f'Unknown M10 options: {sorted(set(self.options)-permitted)}')
        self.pool_max = integer(self.options.get('pool_max', self.total), 'pool_max')
        self.objective_mode = self.options.get('objective', 'transport')
        if self.objective_mode not in ('transport', 'min_pool'):
            raise ValueError('M10 objective must be transport or min_pool')
        self.transport_budget = self.options.get('transport_budget')
        if self.transport_budget is not None:
            nonnegative(self.transport_budget, 'transport_budget')
        self.distance_cost = {k: nonnegative(self.options.get(k+'_distance_cost', v), k+' cost')
                              for k, v in (('e1', .1), ('e2', .05))}
        self.e1_max_trips = integer(self.options.get('e1_max_trips_per_vehicle', max(1, 2*self.total)), 'e1 trips', 1)
        self.restricted_trip_domain = self.e1_max_trips < 2*self.total
        if len(self.orders) != len(self.order_map) or not self.orders:
            raise ValueError('M10 requires nonempty orders with unique IDs')
        customers = {c.id for c in network.customers}
        for o in self.orders:
            if o.count != 1:
                raise ValueError('M10 v1 requires one container per order')
            if not o.id or o.satellite not in self.satellites or o.customer not in customers:
                raise ValueError('Invalid order identity or fixed warehouse assignment')
            nonnegative(o.goods_weight, 'goods_weight')
            for value in (o.service_time, o.min_use_time):
                integer(value, 'order duration')
            for lo, hi in (o.delivery_window, o.pickup_window):
                integer(lo, 'window'); integer(hi, 'window')
                if not p.cycle_start <= lo <= hi <= p.cycle_end:
                    raise ValueError(f'{o.id}: invalid/out-of-horizon time window')
            if o.release_time is not None:
                integer(o.release_time, 'release_time')
        for key in ('e1_count', 'e2_per_sat_count'):
            integer(getattr(self.fleet, key), key, 1)
        for key in ('e1_weight_cap', 'e1_volume_cap', 'e2_weight_cap', 'e2_volume_cap', 'container_weight', 'container_volume'):
            nonnegative(getattr(self.fleet, key), key)
        if self.fleet.container_weight == self.fleet.container_volume == 0:
            raise ValueError('Containers need positive weight or volume')
        self._travel = {}
        for a in network.nodes:
            for b in network.nodes:
                nonnegative(network.distance(a, b), 'distance')
                self._travel[a, b] = p.ticks(network.travel_time(a, b))
        self._service = {s.id: p.ticks(s.service_time) for s in network.satellites}

    def travel(self, a, b):
        return self._travel[a, b]

    def distance(self, a, b):
        return self.network.distance(a, b)

    def service(self, site):
        return self._service[site]

    def task(self, oid, kind):
        o = self.order_map[oid]
        return o, self.fleet.container_weight+(o.goods_weight if kind == 'd' else 0), self.fleet.container_volume

    def with_options(self, **options):
        return NoBatchInstance(self.network, self.orders, self.operating_policy, {**self.options, **options})


def load_instance(config, fleet_config):
    p = NoBatchPolicy.from_config(fleet_config.get('operating_policy'))
    path = resolve_project_path(config['data'])
    with path.open(encoding='utf-8-sig', newline='') as f:
        rows = [r for r in csv.DictReader(f) if r.get('node_id')]
    if len({r['node_id'].strip() for r in rows}) != len(rows):
        raise ValueError('Duplicate node_id')
    nodes, _ = parse_dataset(str(path))
    if sum(n.type == 'd' for n in nodes.values()) != 1:
        raise ValueError('Exactly one depot required')
    for r in rows:
        if nodes[r['node_id'].strip()].type == 's':
            nodes[r['node_id'].strip()].service_time = float(r.get('service_time') or 0)
    for key, default in (('E1_vehicle_count', 2), ('vehicles_per_satellite', 4)):
        integer(fleet_config.get(key, default), key, 1)
    speed = nonnegative(fleet_config.get('speed', 1), 'speed')
    if not speed:
        raise ValueError('speed must be positive')
    distance = load_square_matrix(resolve_project_path(config['distance_matrix'])) if config.get('distance_matrix') else None
    duration = load_square_matrix(resolve_project_path(config['duration_matrix'])) if config.get('duration_matrix') else None
    if duration is not None and distance is None:
        raise ValueError('duration_matrix requires distance_matrix')
    network = ProblemInstance(nodes, {k:v for k,v in fleet_config.items() if k != 'operating_policy'},
                              speed=speed, distance_matrix=distance, travel_time_matrix=duration)
    opath = resolve_project_path(config['orders'])
    with opath.open(encoding='utf-8-sig', newline='') as f:
        rows = json.load(f) if opath.suffix == '.json' else list(csv.DictReader(f))
    orders = []
    for r in rows:
        def number(k, default=0):
            value = r.get(k, default)
            if isinstance(value, bool):
                raise ValueError(f'{k} must be numeric')
            return nonnegative(float(value), k)
        count = integer(number('container_count', 1), 'container_count', 1)
        if integer(number('pickup_container_count', count), 'pickup_container_count') != count:
            raise ValueError('UNBALANCED_ORDER_COUNTS')
        orders.append(Order(str(r['order_id']), str(r['customer_id']), str(r['satellite_id']), '', count,
                            number('goods_weight'),
                            (p.ticks(number('delivery_ready')), p.ticks(number('delivery_due'), 'floor')),
                            (p.ticks(number('pickup_ready')), p.ticks(number('pickup_due'), 'floor')),
                            p.ticks(number('service_time')), p.ticks(number('min_use_time')),
                            p.ticks(number('release_time', p.minutes(p.cycle_start))), None))
    return NoBatchInstance(network, orders, p, config.get('no_batch_options'))
