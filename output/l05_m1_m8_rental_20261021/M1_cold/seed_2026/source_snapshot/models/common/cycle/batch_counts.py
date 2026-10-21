"""Positive-integer batch domains and exact clock refinement for equal batches."""
from dataclasses import replace
import math

from models.common.cycle.policy import integer


def batch_count_domain(options, duration, minimum):
    """The two positive minimum phases give K <= floor(H / (2m))."""
    upper = duration // (2 * minimum)
    raw = options.get('batch_count_candidates', 'all_positive_integers')
    if raw == 'all_positive_integers':
        if upper < 1:
            raise ValueError('Cycle cannot accommodate both minimum phases')
        return tuple(range(1, upper + 1))
    if not isinstance(raw, (list, tuple)) or not raw:
        raise ValueError('batch_count_candidates must be a nonempty list or all_positive_integers')
    counts = [integer(k, 'batch_count_candidate', 1) for k in raw]
    if len(set(counts)) != len(counts):
        raise ValueError('Duplicate batch count candidate')
    for count in counts:
        if count > upper:
            raise ValueError(f'K={count}: both phases must meet min_phase_minutes')
    return tuple(sorted(counts))


def equal_batch_clock(problem, count):
    """Rescale integer times, preserving physical minutes and rounded input data.

    Only this K's clock is refined; there is no enormous LCM across all counts.
    The subdivision is serialized with solutions for independent replay.
    """
    p = problem.operating_policy
    factor = count // math.gcd(problem.cycle_duration, count)
    if factor == 1:
        return p, problem.orders, factor
    fields = ('cycle_start', 'cycle_end', 'cleaning_duration', 'e1_load_time',
              'e2_dispatch_prep_time', 'e2_return_unload_time',
              'depot_return_unload_time', 'e1_turnaround_time', 'e2_turnaround_time')
    policy = replace(p, **{key: getattr(p, key) * factor for key in fields},
                     time_tick_subdivision=p.time_tick_subdivision * factor,
                     batches=tuple(replace(b, start=b.start * factor,
                                           cutoff=b.cutoff * factor, end=b.end * factor)
                                   for b in p.batches))
    orders = tuple(replace(o, delivery_window=tuple(t * factor for t in o.delivery_window),
                           pickup_window=tuple(t * factor for t in o.pickup_window),
                           service_time=o.service_time * factor,
                           min_use_time=o.min_use_time * factor,
                           release_time=o.release_time * factor)
                   for o in problem.orders)
    return policy, orders, factor
