"""Small bounded construction beam, without optimization backends or certificates."""
from itertools import product
import time

from models.routing.model_8.alns.neighborhoods import _goods_ready
from models.routing.model_8.alns.state import clone_joint_state


def _simulate(problem, site, sequence, goods, ready, delivered):
    p, f = problem.operating_policy, problem.fleet
    batches = {problem.order_map[o].batch for o, kind in sequence if kind == 'd'}
    if len(batches) > 1:
        return None
    prep = p.e2_dispatch_prep_time if batches else 0
    start = max(ready+prep, max((goods[site, bid]+prep for bid in batches), default=p.cycle_start))
    current, previous, distance = start, site, 0.
    weight = sum(problem.task(o, kind)[1] for o, kind in sequence if kind == 'd')
    volume = sum(problem.task(o, kind)[2] for o, kind in sequence if kind == 'd')
    if weight > f.e2_weight_cap+1e-6 or volume > f.e2_volume_cap+1e-6:
        return None
    deliveries = dict(delivered)
    for oid, kind in sequence:
        order, w, v = problem.task(oid, kind)
        window = order.delivery_window if kind == 'd' else order.pickup_window
        current = max(current+problem.travel(previous, order.customer), window[0])
        if kind == 'p':
            if oid not in deliveries:
                return None
            current = max(current, deliveries[oid]+order.min_use_time)
        due = window[1]
        if kind == 'd' and len(p.batches) == 1:
            due = min(due, p.batches[0].cutoff-order.service_time)
        if current > due:
            return None
        current += order.service_time
        if kind == 'd':
            deliveries[oid] = current
        distance += problem.distance(previous, order.customer)
        previous = order.customer
        weight += w if kind == 'p' else -w
        volume += v if kind == 'p' else -v
        if min(weight, volume) < -1e-6 or weight > f.e2_weight_cap+1e-6 or volume > f.e2_volume_cap+1e-6:
            return None
    finish = current+problem.travel(previous, site)+p.e2_return_unload_time
    if finish+p.e2_turnaround_time > p.cycle_end:
        return None
    if any(kind == 'p' for _, kind in sequence) and finish > p.batches[-1].cutoff:
        return None
    distance += problem.distance(previous, site)
    rates = problem._joint_cost_rates
    cost = (distance*rates['distance_scale_to_km']*rates['e2_distance_cost_per_km']
        +p.minutes(finish-start+prep)*rates['e2_work_cost_per_minute']+rates['e2_trip_cost'])
    return cost, finish, deliveries


def _station_plans(problem, state, site, deadline, width):
    p, f = problem.operating_policy, problem.fleet
    orders = sorted((o for o in problem.orders if o.satellite == site), key=lambda o: o.id)
    if len(orders) > 6:
        return []
    tasks = [(o.id, kind) for o in orders for kind in problem.task_kinds]
    if not tasks:
        return [[]]
    bits = {task: 1 << index for index, task in enumerate(tasks)}
    goods = _goods_ready(problem, state)
    optimistic = {o.id: max(o.delivery_window[0], goods[site, o.batch]+p.e2_dispatch_prep_time
                        +problem.travel(site, o.customer))+o.service_time for o in orders}
    columns = {}
    layer = [((), 0)]
    generation_deadline = time.perf_counter()+.4*max(0., deadline-time.perf_counter())
    for _ in tasks:
        partials = {}
        for sequence, mask in layer:
            for task in tasks:
                if time.perf_counter() >= generation_deadline:
                    break
                if mask & bits[task] or (task[1] == 'd' and (task[0], 'p') in sequence):
                    continue
                proposed = sequence+(task,)
                values = _simulate(problem, site, proposed, goods, p.cycle_start, optimistic)
                if values is None:
                    continue
                cost, finish, _ = values
                covered = mask | bits[task]
                row = (cost, finish, proposed)
                bucket = partials.setdefault(covered, [])
                if proposed not in {value[2] for value in bucket}:
                    bucket.append(row)
                    bucket.sort()
                    del bucket[2:]
                columns[covered, proposed] = row
        layer = [(sequence, mask) for _, _, mask, sequence in sorted(
            (cost, finish, mask, sequence) for mask, rows in partials.items()
            for cost, finish, sequence in rows)[:width]]
        if not layer or time.perf_counter() >= generation_deadline:
            break
    full = (1 << len(tasks))-1
    options = [(mask, row[2]) for (mask, _), row in columns.items()]
    options.sort(key=lambda row: (-row[0].bit_count(), row[1]))
    # State: covered tasks, local cost, vehicle availability/slots, delivery
    # completion times, physical routes. Bounded beams deliberately discard
    # alternatives and therefore never prove infeasibility or optimality.
    beam = [(0, 0., (p.cycle_start,)*f.e2_per_sat_count, (0,)*f.e2_per_sat_count, {}, [])]
    complete = {}
    max_spoke = max((problem.distance(site, o.customer)+problem.distance(o.customer, site) for o in orders), default=0.)
    remaining_cost = max_spoke*problem._joint_cost_rates['distance_scale_to_km']*problem._joint_cost_rates['e2_distance_cost_per_km']
    for _ in range(min(len(tasks), f.e2_per_sat_count*p.e2_max_trips_per_vehicle)):
        expanded = {}
        for mask, cost, available, slots, delivered, routes in beam:
            for cover, sequence in options:
                if time.perf_counter() >= deadline:
                    break
                if mask & cover:
                    continue
                internal = {o for o, kind in sequence if kind == 'd'}
                if any(kind == 'p' and o not in delivered and o not in internal for o, kind in sequence):
                    continue
                symmetric = set()
                for vehicle in range(f.e2_per_sat_count):
                    if slots[vehicle] >= p.e2_max_trips_per_vehicle or (available[vehicle], slots[vehicle]) in symmetric:
                        continue
                    symmetric.add((available[vehicle], slots[vehicle]))
                    values = _simulate(problem, site, sequence, goods, available[vehicle], delivered)
                    if values is None:
                        continue
                    extra, finish, deliveries = values
                    if not slots[vehicle]:
                        extra += problem._joint_cost_rates['e2_vehicle_cost_per_cycle']
                    new_available, new_slots = list(available), list(slots)
                    new_available[vehicle] = finish+p.e2_turnaround_time
                    new_slots[vehicle] += 1
                    path = routes+[dict(satellite=site, vehicle=vehicle, tasks=list(sequence))]
                    next_mask = mask | cover
                    row = (next_mask, cost+extra, tuple(new_available), tuple(new_slots), deliveries, path)
                    if next_mask == full:
                        recovery = []
                        for route in path:
                            # Preserve route diversity by covered task chains;
                            # the full global decoder determines recovery bins.
                            recovery.append(tuple(route['tasks']))
                        complete[tuple(recovery)] = row
                    else:
                        key = next_mask, tuple(new_slots)
                        bucket = expanded.setdefault(key, [])
                        bucket.append(row)
                        bucket.sort(key=lambda value: (value[1], max(value[2])))
                        del bucket[2:]
        def rank(row):
            return row[1]+remaining_cost*(len(tasks)-row[0].bit_count())/len(tasks), -row[0].bit_count(), max(row[2])
        beam = sorted((row for bucket in expanded.values() for row in bucket), key=rank)[:width]
        if not beam or time.perf_counter() >= deadline:
            break
    return [row[5] for row in sorted(complete.values(), key=lambda row: (row[1], max(row[2])))[:6]]


def beam_routes(child, evaluator, assignment, state, deadline, width=32):
    """Offer fully audited route constructions; retain the supplied incumbent."""
    best = clone_joint_state(state)
    score, _, _ = evaluator(assignment, best)
    if len(child.orders) > 9:
        return best
    fixed = evaluator.fixed(assignment, state['cutoffs'])
    fixed._joint_cost_rates = child.cost_profile.rates
    sites = [site for site in fixed.satellites if any(o.satellite == site for o in fixed.orders)]
    plans = []
    build_deadline = deadline-min(.05, .1*max(0., deadline-time.perf_counter()))
    for index, site in enumerate(sites):
        local_deadline = time.perf_counter()+max(0., build_deadline-time.perf_counter())/(len(sites)-index)
        alternatives = _station_plans(fixed, state, site, local_deadline, width)
        if not alternatives:
            return best
        plans.append(alternatives)
    for index, choice in enumerate(product(*plans)):
        if index >= 36 or time.perf_counter() >= deadline:
            break
        trial = clone_joint_state(state)
        trial['e2'] = [route for group in choice for route in group]
        value, candidate, _ = evaluator(assignment, trial)
        if candidate is not None and value < score-1e-8:
            score, best = value, trial
    return best
