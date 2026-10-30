"""Lower-bound validity against exact M8 and an independent trip oracle."""
from dataclasses import replace
from itertools import permutations

import pytest

from experiments.runs.research_model8_shared_relaxation import enumerate_trip_library, build_relaxation
from models.common.cycle.calendar import CalendarCycleInstance
from models.routing.model_8.exact import solve_exact
from tests.routing.test_model8_physical_bounds import instance


@pytest.mark.parametrize('kind', ['ordinary', 'late_release', 'multiple_satellites'])
@pytest.mark.parametrize('refined', [False, True])
def test_bound_below_full_exact_optimum_and_each_K(kind, refined):
    p = instance(kind)
    if refined:
        p = CalendarCycleInstance(p.network, p.orders, replace(p.operating_policy, cycle_end=133),
                                  dict(batch_count_candidates=[1, 2, 3]), p.cost_profile.config, model=8)
    library, _ = enumerate_trip_library(p)
    model, context = build_relaxation(p, library)
    try:
        model.Params.TimeLimit = 15
        model.optimize()
        assert model.Status == 2
        lower = model.ObjVal
        for k in p.batch_count_candidates:
            exact_p = CalendarCycleInstance(p.network, p.orders, p.operating_policy,
                                            dict(batch_count_candidates=[k]), p.cost_profile.config, model=8)
            exact = solve_exact(exact_p, time_limit=20, milp_options=dict(formulation='compact'))
            assert exact['info']['proven_optimal'] or exact['info']['proven_infeasible']
            if exact['info']['proven_optimal']:
                assert lower <= exact['info']['objective'] + 1e-6
    finally:
        model.dispose()


def test_complete_trip_masks_equal_independent_permutation_oracle():
    p = instance('ordinary')
    orders, policy = list(p.orders), p.operating_policy
    n = len(orders)
    assert n == 2
    oracle = {}
    for size in range(1, 2 * n + 1):
        for sequence in permutations(range(2 * n), size):
            ds = [i for i in sequence if i < n]
            site = orders[sequence[0] % n].satellite
            if any(orders[t % n].satellite != site for t in sequence):
                continue
            w = sum(p.task(orders[i].id, 'd')[1] for i in ds)
            v = sum(p.task(orders[i].id, 'd')[2] for i in ds)
            if w > p.fleet.e2_weight_cap or v > p.fleet.e2_volume_cap:
                continue
            clock, at, distance, delivery_times, feasible = policy.cycle_start, site, 0., {}, True
            for task in sequence:
                i, kind = task % n, 'd' if task < n else 'p'
                o = orders[i]
                window = o.delivery_window if kind == 'd' else o.pickup_window
                ready = window[0]
                if kind == 'p':
                    if i in ds and i not in delivery_times:
                        feasible = False
                        break
                    ready = max(ready, delivery_times.get(i, o.delivery_window[0]) + o.service_time + o.min_use_time)
                clock = max(clock + p.travel(at, o.customer), ready)
                if clock > window[1]:
                    feasible = False
                    break
                if kind == 'd':
                    delivery_times[i] = clock
                _, dw, dv = p.task(o.id, kind)
                sign = -1 if kind == 'd' else 1
                w, v = w + sign * dw, v + sign * dv
                if not (-1e-8 <= w <= p.fleet.e2_weight_cap + 1e-8 and -1e-8 <= v <= p.fleet.e2_volume_cap + 1e-8):
                    feasible = False
                    break
                distance += p.distance(at, o.customer)
                at, clock = o.customer, clock + o.service_time
            if not feasible or clock + p.travel(at, site) + policy.e2_return_unload_time + policy.e2_turnaround_time > policy.cycle_end:
                continue
            mask = sum(1 << i for i in sequence)
            oracle[mask] = min(oracle.get(mask, float('inf')), distance + p.distance(at, site))
    library, _ = enumerate_trip_library(p)
    actual = {r['mask']: r['distance'] for r in library}
    assert actual == pytest.approx(oracle)


def test_multi_box_orders_are_explicitly_rejected():
    p = instance('unequal_counts')
    with pytest.raises(ValueError, match='one-box'):
        build_relaxation(p, [])
