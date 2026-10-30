"""Completeness and cost equivalence of the accelerated relaxed trip library."""
from dataclasses import replace
from pathlib import Path
import random
from types import SimpleNamespace
import time

import pytest

from experiments.runs.model8_trip_library import dominates, enumerate_trip_library
from experiments.runs.research_model8_shared_relaxation import enumerate_trip_library_reference
from models.common.cycle import load_instance
from models.common.cycle.instance import Order
from tools.configuration.loader import load_routing_case

ROOT = Path(__file__).resolve().parents[2]


def compare(problem, enumerator=enumerate_trip_library):
    expected, old_prefixes = enumerate_trip_library_reference(problem)
    counters = {}
    actual, prefixes = enumerator(problem, stats=counters)
    assert {r['mask']: r['distance'] for r in actual} == pytest.approx(
        {r['mask']: r['distance'] for r in expected}, abs=1e-9, rel=1e-12)
    assert counters['complete']
    assert prefixes <= old_prefixes
    # Check the retained path against independent forward simulation, rather
    # than trusting a mask/distance pair copied out of the search.
    orders, p = list(problem.orders), problem.operating_policy
    for row in actual:
        chosen = [i for i, kind in row['path'] if kind == 'd']
        weight = sum(problem.task(orders[i].id, 'd')[1] for i in chosen)
        volume = sum(problem.task(orders[i].id, 'd')[2] for i in chosen)
        assert weight <= problem.fleet.e2_weight_cap+1e-8
        assert volume <= problem.fleet.e2_volume_cap+1e-8
        clock, at, distance, mask = p.cycle_start, row['satellite'], 0., 0
        delivered = {}
        for i, kind in row['path']:
            o = orders[i]
            assert o.satellite == row['satellite']
            task = i+(len(orders) if kind == 'p' else 0)
            assert not mask & (1 << task)
            mask |= 1 << task
            lo, hi = o.delivery_window if kind == 'd' else o.pickup_window
            if kind == 'p':
                if i in chosen:
                    assert i in delivered
                lo = max(lo, delivered.get(i, o.delivery_window[0])+o.service_time+o.min_use_time)
            arrive = max(clock+problem.travel(at, o.customer), lo)
            assert arrive <= hi
            if kind == 'd':
                delivered[i] = arrive
            sign = -1 if kind == 'd' else 1
            _, w, v = problem.task(o.id, kind)
            weight += sign*w
            volume += sign*v
            assert -1e-8 <= weight <= problem.fleet.e2_weight_cap+1e-8
            assert -1e-8 <= volume <= problem.fleet.e2_volume_cap+1e-8
            clock = arrive+o.service_time
            distance += problem.distance(at, o.customer)
            at = o.customer
        assert clock+problem.travel(at, row['satellite'])+p.e2_return_unload_time+p.e2_turnaround_time <= p.cycle_end
        assert row['mask'] == mask
        assert row['deliveries'] == mask & ((1 << len(orders))-1)
        assert row['distance'] == pytest.approx(distance+problem.distance(at, row['satellite']), abs=1e-9)


@pytest.mark.parametrize('case', [f'S{i:02}' for i in range(1, 10)])
def test_all_nine_real_small_libraries_equal_exhaustive_search(case):
    p = load_instance(*load_routing_case(
        ROOT / f'data/formal_benchmark27_uniform_7_20_50_v1/model_8/small/{case}/case.json',
        use_pool_config=False))
    compare(p)


def synthetic(seed):
    rng = random.Random(seed)
    n = 4
    sites = ('s0', 's1') if seed % 3 == 0 else ('s0',)
    orders = []
    for i in range(n):
        lo = rng.randrange(0, 30)
        hi = lo+rng.randrange(5, 35)
        orders.append(Order(str(i), f'c{i % 3}' if seed % 5 == 0 else f'c{i}',
            sites[i % len(sites)], 'b1', 1+(i == 0 and seed % 4 == 0),
            rng.choice((0., 0.1, 2., 4.)), (lo, hi), (lo, min(100, hi+30)),
            rng.randrange(0, 4), rng.randrange(0, 15)))
    locations = list(sites)+sorted({o.customer for o in orders})
    # Asymmetric and nonmetric matrices deliberately decouple cost and time.
    travel = {(a, b): 0 if a == b else rng.randrange(0, 12) for a in locations for b in locations}
    distances = {(a, b): 0. if a == b else rng.choice((0., 0.1, 1., 7., 20.)) for a in locations for b in locations}
    fleet = SimpleNamespace(e2_weight_cap=rng.choice((3., 8., 20.)),
        e2_volume_cap=rng.choice((2., 4., 10.)), container_weight=0.2, container_volume=0.3)
    by_id = {o.id: o for o in orders}

    def task(oid, kind):
        o = by_id[oid]
        return o, o.count*fleet.container_weight+(o.goods_weight if kind == 'd' else 0), o.count*fleet.container_volume

    return SimpleNamespace(orders=orders, satellites=sites, fleet=fleet,
        operating_policy=SimpleNamespace(cycle_start=0, cycle_end=100, e2_return_unload_time=1, e2_turnaround_time=1),
        task=task, travel=lambda a, b: travel[a, b], distance=lambda a, b: distances[a, b])


@pytest.mark.parametrize('seed', range(36))
def test_asymmetric_fractional_multibox_and_repeated_location_oracles(seed):
    compare(synthetic(seed))


def test_pickup_readiness_prevents_invalid_time_distance_only_dominance():
    assert not dominates((10, 5., (40,)), (10, 6., (20,)))
    assert not dominates((10, 6., (20,)), (10, 5., (40,)))
    assert dominates((9, 5., (19, 21)), (10, 6., (20, 22)))


def test_expired_deadline_never_returns_a_partial_library():
    counters, events = {}, []
    with pytest.raises(TimeoutError, match='Incomplete trip enumeration'):
        enumerate_trip_library(synthetic(1), deadline=time.perf_counter()-1,
                               stats=counters, on_progress=events.append)
    assert not counters['complete']
    assert events and not events[-1]['complete']
