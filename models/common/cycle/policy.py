"""Immutable calendar. Input minutes are explicitly converted to integer ticks."""
from __future__ import annotations

from dataclasses import dataclass, asdict, replace
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
import math

VERSION = 'multi_batch_cutoff_v1'
CONSOLIDATED_PICKUP = 'consolidated_after_unload'


def daily_cycle_bounds(orders, repeat_interval):
    """Anchor the daily reset to the first order release (midnight if unspecified)."""
    first_release = min((o.release_time or 0 for o in orders), default=0)
    return first_release, first_release + repeat_interval


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
    cutoff: int | None
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
    repeat_interval: int | None = None

    @property
    def single_day(self):
        return False

    @property
    def multi_batch(self):
        return True

    @property
    def consolidated_handoff(self):
        return len(self.batches) == 1 and self.batches[0].cutoff is None

    def for_consolidated_handoff(self, orders):
        self.require_model(3)
        repeat = self.repeat_interval or self.ticks(1440, 'exact')
        start, end = daily_cycle_bounds(orders, repeat)
        return replace(self, cycle_start=start, cycle_end=end, repeat_interval=repeat,
                       batches=(Batch(self.batches[0].id, start, None, end),))

    def require_model(self, model):
        number = integer(model, 'model', 1)
        if self.consolidated_handoff and number not in (3, 4):
            raise ValueError('Consolidated handoff is supported by Models 3 and 4')
        if number == 5:
            return
        if len(self.batches) == 1 and number in (1, 2, 3, 4):
            return
        raise ValueError('M1–M4 require B=1 and M5 requires a batch policy (multi_batch_cutoff_v1)')

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
    def from_config(cls, config, *, no_work_window=False, flexible_calendar=False):
        if isinstance(config, cls):
            return config
        if not isinstance(config, dict) or config.get('policy_version') != VERSION:
            raise ValueError(f'Expected {VERSION} operating policy')
        required = {'container_pool_size', 'cleaning_duration', 'e2_max_trips_per_vehicle', 'time_tick_seconds'}
        if no_work_window and flexible_calendar:
            required.add('batches')
        elif not no_work_window:
            required.update(('cycle_start', 'cycle_end', 'batches'))
        missing = required - config.keys()
        if missing:
            raise ValueError(f'Missing multibatch policy fields: {sorted(missing)}')
        allowed = (set(cls.__dataclass_fields__) - {'repeat_interval'}) | {'description', 'source_type', 'time_unit'}
        if no_work_window:
            allowed.add('repeat_interval_minutes')
        if set(config) - allowed:
            raise ValueError(f'Unknown multibatch policy fields: {sorted(set(config)-allowed)}')
        if config.get('time_unit', 'minutes') != 'minutes':
            raise ValueError('Policy input times must be in minutes')
        if config.get('terminal_policy', 'all_clean_at_depot') != 'all_clean_at_depot':
            raise ValueError('Only all_clean_at_depot is supported')
        tick = integer(config['time_tick_seconds'], 'time_tick_seconds', 1)
        subdivision = integer(config.get('time_tick_subdivision', 1), 'time_tick_subdivision', 1)
        converter = cls(0, 1, (), 0, 0, 1, tick, time_tick_subdivision=subdivision)
        times = {key: converter.ticks(config.get(key, 0)) for key in (
            'cleaning_duration', 'e1_load_time', 'e2_dispatch_prep_time',
            'e2_return_unload_time', 'depot_return_unload_time',
            'e1_turnaround_time', 'e2_turnaround_time')}
        physical = dict(container_pool_size=integer(config['container_pool_size'], 'container_pool_size'),
                        e2_max_trips_per_vehicle=integer(config['e2_max_trips_per_vehicle'], 'e2_max_trips_per_vehicle', 1),
                        time_tick_seconds=tick, time_tick_subdivision=subdivision, **times)
        if no_work_window:
            rows = config.get('batches', [{'id': 'b1'}])
            repeat = converter.ticks(config.get('repeat_interval_minutes', 1440), 'exact')
            if repeat <= 0:
                raise ValueError('Repeat interval must be positive')
            if flexible_calendar:
                if not isinstance(rows, list) or not rows or any(
                        not isinstance(row, dict) or not isinstance(row.get('id'), str) or not row['id']
                        or set(row) not in ({'id'}, {'id', 'delivery_duration', 'return_duration'})
                        for row in rows):
                    raise ValueError('Flexible calendar requires source batch IDs')
                ids = [row['id'] for row in rows]
                if len(set(ids)) != len(ids):
                    raise ValueError('Duplicate source batch IDs')
                # Only the IDs are used to read source orders. The reference
                # slots are rebuilt from the order clock before scheduling.
                return cls(0, repeat, tuple(Batch(bid, 0, 1, repeat) for bid in ids),
                           repeat_interval=repeat, **physical)
            if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
                raise ValueError('Consolidated handoff requires one batch')
            bid = rows[0].get('id')
            if not isinstance(bid, str) or not bid:
                raise ValueError('Batch id must be a nonempty string')
            return cls(0, repeat, (Batch(bid, 0, None, repeat),), repeat_interval=repeat, **physical)

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
        return cls(start, end, tuple(batches), **physical)

    def as_dict(self, problem=None):
        result = asdict(self)
        if self.time_tick_subdivision == 1:
            result.pop('time_tick_subdivision')
        if self.repeat_interval is None:
            result.pop('repeat_interval')
        pickup_policy = CONSOLIDATED_PICKUP if self.consolidated_handoff else 'cutoff_all_available'
        result.update(time_unit='ticks', pickup_policy=pickup_policy,
                      batch_overlap=False, allow_transfers=False,
                      input_time_unit='minutes', objective_mode='distance')
        return result
