"""Immutable calendar. Input minutes are explicitly converted to integer ticks."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
import math

VERSION = 'multi_batch_cutoff_v1'


def nonnegative(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{label} must be a finite nonnegative number')
    if not math.isfinite(value) or value < 0:
        raise ValueError(f'{label} must be a finite nonnegative number')
    return value


def integer(value, label, minimum=0):
    nonnegative(value, label)
    if int(value) != value or value < minimum:
        raise ValueError(f'{label} must be an integer >= {minimum}')
    return int(value)


@dataclass(frozen=True)
class Batch:
    id: str
    start: int
    cutoff: int
    end: int


@dataclass(frozen=True)
class MultiBatchPolicy:
    cycle_start: int
    cycle_end: int
    batches: tuple
    container_pool_size: int
    cleaning_duration: int
    e2_max_trips_per_vehicle: int
    time_tick_seconds: int = 1
    e1_load_time: int = 0
    e2_dispatch_prep_time: int = 0
    e2_return_unload_time: int = 0
    depot_return_unload_time: int = 0
    e1_turnaround_time: int = 0
    e2_turnaround_time: int = 0
    policy_version: str = VERSION
    terminal_policy: str = 'all_clean_at_depot'
    time_tick_subdivision: int = 1

    @property
    def single_day(self):
        return False

    @property
    def multi_batch(self):
        return True

    def require_model(self, model):
        number = integer(model, 'model', 1)
        if number in (6, 7, 8, 9):
            return
        if len(self.batches) == 1 and number in (1, 2, 3, 4):
            return
        if len(self.batches) > 1 and number == 5:
            return
        raise ValueError('M1-M4 require B=1; current Model 5 requires B>1 (multi_batch_cutoff_v1)')

    def ticks(self, minutes, rounding='ceil'):
        nonnegative(minutes, 'time')
        scaled = Decimal(str(minutes)) * 60 * self.time_tick_subdivision / self.time_tick_seconds
        if rounding == 'exact':
            if scaled != scaled.to_integral_value():
                raise ValueError('Calendar boundaries must be multiples of time_tick_seconds')
            return int(scaled)
        return int(scaled.to_integral_value(rounding=(ROUND_CEILING if rounding == 'ceil' else ROUND_FLOOR)))

    def minutes(self, ticks):
        return ticks * self.time_tick_seconds / (60.0 * self.time_tick_subdivision)

    @classmethod
    def from_config(cls, config):
        if isinstance(config, cls):
            return config
        if not isinstance(config, dict) or config.get('policy_version') != VERSION:
            raise ValueError(f'Expected {VERSION} operating policy')
        required = {'cycle_start', 'cycle_end', 'batches', 'container_pool_size',
                    'cleaning_duration', 'e2_max_trips_per_vehicle', 'time_tick_seconds'}
        missing = required - config.keys()
        if missing:
            raise ValueError(f'Missing multibatch policy fields: {sorted(missing)}')
        allowed = set(cls.__dataclass_fields__) | {'description', 'source_type', 'time_unit'}
        if set(config) - allowed:
            raise ValueError(f'Unknown multibatch policy fields: {sorted(set(config)-allowed)}')
        if config.get('time_unit', 'minutes') != 'minutes':
            raise ValueError('Policy input times must be in minutes')
        if config.get('terminal_policy', 'all_clean_at_depot') != 'all_clean_at_depot':
            raise ValueError('Only all_clean_at_depot is supported')
        tick = integer(config['time_tick_seconds'], 'time_tick_seconds', 1)
        subdivision = integer(config.get('time_tick_subdivision', 1), 'time_tick_subdivision', 1)
        converter = cls(0, 1, (), 0, 0, 1, tick, time_tick_subdivision=subdivision)
        start = converter.ticks(config['cycle_start'], 'exact')
        end = converter.ticks(config['cycle_end'], 'exact')
        if end <= start:
            raise ValueError('cycle_end must be after cycle_start')
        batches, cursor, ids = [], start, set()
        if not isinstance(config['batches'], list) or not config['batches']:
            raise ValueError('batches must be a nonempty list')
        for row in config['batches']:
            if not isinstance(row, dict) or set(row) != {'id', 'delivery_duration', 'return_duration'}:
                raise ValueError('Each batch requires exactly id, delivery_duration, return_duration')
            bid = row['id']
            if not isinstance(bid, str) or not bid or bid in ids:
                raise ValueError('Batch IDs must be unique nonempty strings')
            d = converter.ticks(row['delivery_duration'], 'exact')
            r = converter.ticks(row['return_duration'], 'exact')
            if min(d, r) <= 0:
                raise ValueError('Both phase durations must be positive')
            batches.append(Batch(bid, cursor, cursor+d, cursor+d+r))
            cursor += d+r
            ids.add(bid)
        if cursor > end:
            raise ValueError('Batch calendar exceeds cycle_end')
        times = {key: converter.ticks(config.get(key, 0)) for key in (
            'cleaning_duration', 'e1_load_time', 'e2_dispatch_prep_time',
            'e2_return_unload_time', 'depot_return_unload_time',
            'e1_turnaround_time', 'e2_turnaround_time')}
        return cls(start, end, tuple(batches),
                   integer(config['container_pool_size'], 'container_pool_size'),
                   times.pop('cleaning_duration'),
                   integer(config['e2_max_trips_per_vehicle'], 'e2_max_trips_per_vehicle', 1),
                   time_tick_seconds=tick, time_tick_subdivision=subdivision, **times)

    def as_dict(self, problem=None):
        result = asdict(self)
        if self.time_tick_subdivision == 1:
            result.pop('time_tick_subdivision')
        result.update(time_unit='ticks', pickup_policy='cutoff_all_available',
                      batch_overlap=False, allow_transfers=False,
                      input_time_unit='minutes', objective_mode='distance')
        return result
