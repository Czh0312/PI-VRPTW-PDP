"""Independent one-order enumeration: no solver, decoder or cost evaluator in oracle."""
from dataclasses import replace
import itertools
import math

import pytest

from models.common.cycle.calendar import CalendarCycleInstance
from models.common.cycle.uniform import UniformCycleInstance
from models.common.cycle.policy import Batch
from models.routing.service import solve_routing
from tests.routing.test_model6_joint import joint_fixture, exact_available


def one_order_problem(number, variant):
    source = joint_fixture(zero=True)
    origin = 10 if variant % 2 else 0
    horizon = 16
    # Nonzero travel, site service, loading, unloading, use, turnaround and
    # absolute origin are independently varied across the predeclared cases.
    source.network.nodes['S'].x = 1
    source.network.nodes['C1'].x = 2
    source.network.nodes['S'].service_time = variant % 2
    from models.common.domain.instance import ProblemInstance
    network = ProblemInstance(source.network.nodes, dict(E1_vehicle_count=1, vehicles_per_satellite=1,
        E1_Qk=100, E1_Ck=100, E2_Qv=100, E2_Cv=100, container_weight=1, container_volume=1))
    policy = replace(source.operating_policy, cycle_start=origin, cycle_end=origin+horizon,
        batches=(Batch('b1', origin, origin+4, origin+8), Batch('b2', origin+8, origin+12, origin+16)),
        cleaning_duration=variant % 3, e1_load_time=variant // 3 % 2,
        e2_dispatch_prep_time=variant % 2, depot_return_unload_time=variant // 2 % 2,
        e2_return_unload_time=variant // 2 % 2, e1_turnaround_time=variant // 4,
        e2_turnaround_time=variant // 4)
    order = replace(source.orders[0], count=1, goods_weight=1,
                    delivery_window=(origin+3, origin+10), pickup_window=(origin+5, origin+12),
                    min_use_time=variant % 3, service_time=variant % 2,
                    release_time=origin+(7 if variant >= 6 else 0))
    options = dict(batch_count_candidates=[1, 2], min_phase_minutes=1)
    if number == 6:
        return UniformCycleInstance(network, [order], policy, dict(options, schedule_mode='uniform_cycle'), source.cost_profile.config)
    return CalendarCycleInstance(network, [order], policy, options, source.cost_profile.config, model=number)


def one_order_oracle(problem, number):
    """Enumerate complete route topologies/times for one order and one satellite.

    E2 either serves delivery+pickup in one trip, or two separate trips. E1
    either delivers+recovers in its delivery round or returns in a later round.
    Later pure-pick departure is optimized by shifting idle time outside work.
    """
    p, o = problem.operating_policy, problem.orders[0]
    a, z = p.cycle_start, p.cycle_end
    travel1 = problem.travel(problem.depot, o.satellite)
    back1 = problem.travel(o.satellite, problem.depot)
    travel2 = problem.travel(o.satellite, o.customer)
    back2 = problem.travel(o.customer, o.satellite)
    service = problem.service(o.satellite)
    rates = problem.cost_profile.config
    best = math.inf
    for count in (1, 2):
        domains = [(a, z)] if count == 1 else [(a, t, z) for t in
                   ([a+(z-a)//2] if number in (6, 7) else range(a+2, z-1))]
        for bounds in domains:
            for cuts in itertools.product(*(range(x+1, y) for x, y in zip(bounds, bounds[1:]))):
                if number == 6 and len({c-s for c, s in zip(cuts, bounds)}) != 1:
                    continue
                for batch in range(count):
                    start, end = bounds[batch:batch+2]
                    if start < o.release_time:
                        continue
                    supplied = start+p.e1_load_time+travel1+service
                    if supplied > cuts[batch]:
                        continue
                    for td in range(o.delivery_window[0], o.delivery_window[1]+1):
                        depart_d = td-travel2
                        if depart_d < supplied+p.e2_dispatch_prep_time:
                            continue
                        for tp in range(max(o.pickup_window[0], td+o.service_time+o.min_use_time), o.pickup_window[1]+1):
                            returned = tp+o.service_time+back2+p.e2_return_unload_time
                            if returned+p.e2_turnaround_time > z:
                                continue
                            recovery = next((i for i, c in enumerate(cuts) if returned <= c), None)
                            if recovery is None or recovery < batch:
                                continue
                            if recovery == batch:
                                return1 = cuts[batch]+service+back1+p.depot_return_unload_time
                                if return1 > end:
                                    continue
                                work1 = return1-start
                                trips1 = 1
                            else:
                                delivery_end = supplied+back1+p.depot_return_unload_time
                                if delivery_end > end:
                                    continue
                                pickup_depart = max(cuts[recovery], delivery_end+p.e1_turnaround_time)
                                return1 = pickup_depart+travel1+service+back1+p.depot_return_unload_time
                                if return1 > bounds[recovery+1]:
                                    continue
                                work1 = delivery_end-start+return1-pickup_depart
                                trips1 = 2
                            if return1+max(p.cleaning_duration, p.e1_turnaround_time) > z:
                                continue
                            for split in (False, True):
                                if split:
                                    delivery_end2 = td+o.service_time+back2+p.e2_return_unload_time
                                    pickup_depart2 = tp-travel2
                                    if pickup_depart2 < delivery_end2+p.e2_turnaround_time:
                                        continue
                                    work2 = delivery_end2-depart_d+p.e2_dispatch_prep_time+returned-pickup_depart2
                                else:
                                    work2 = returned-depart_d+p.e2_dispatch_prep_time
                                trips2 = 2 if split else 1
                                cost = (rates['container_pool_cost_per_period']+rates['cleaning_cost_per_return']
                                        +rates['tracking_cost_per_use']+rates['container_price']/rates['container_lifetime_uses'])
                                for layer, trips, work, distance in (
                                        ('e1', trips1, work1, trips1*(problem.distance(problem.depot, o.satellite)+problem.distance(o.satellite, problem.depot))),
                                        ('e2', trips2, work2, trips2*(problem.distance(o.satellite, o.customer)+problem.distance(o.customer, o.satellite)))):
                                    cost += (trips*rates[layer+'_trip_cost']+rates[layer+'_vehicle_cost_per_cycle']
                                             +work*p.time_tick_seconds/60*rates[layer+'_work_cost_per_minute']
                                             +distance*rates['distance_scale_to_km']*rates[layer+'_distance_cost_per_km'])
                                best = min(best, cost)
    return best


@pytest.mark.parametrize('variant', range(8))
@pytest.mark.parametrize('number', [6, 7, 8])
def test_full_milp_matches_independent_one_order_oracle(number, variant, exact_available):
    p = one_order_problem(number, variant)
    reference = one_order_oracle(p, number)
    result = solve_routing(model=number, method='exact', problem=p, exact_time=5)
    if math.isinf(reference):
        assert result['info']['proven_infeasible'], result['info']
    else:
        assert result['info']['proven_optimal'] and result['audit']['valid'], result['info']
        assert result['info']['objective'] == pytest.approx(reference)
