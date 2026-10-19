"""Cheap distance certificates for fixed-satellite, reusable M5 cycles.

E1 uses shortest distances on all E1 locations before projecting each batch
onto its mandatory delivery stops. E2 retains all mandatory task visits,
with only universally forbidden adjacency removed. The entering/leaving
arc minima and rounded minimum route count bound the two echelons separately.
Neither a metric input nor paired delivery/pickup trips are required.
"""
import heapq
import math

from models.routing.model_5.exact.branch_cut.capacity import minimum_trip_count


def _shortest(problem, origin, sites, check_budget):
    distances = {site: math.inf for site in sites}
    distances[origin] = 0.0
    queue = [(0.0, origin)]
    while queue:
        check_budget()
        value, site = heapq.heappop(queue)
        if value != distances[site]:
            continue
        for other in sites:
            candidate = value+problem.distance(site, other)
            if candidate < distances[other]:
                distances[other] = candidate
                heapq.heappush(queue, (candidate, other))
    return distances


def _tour_bound(closure, base, required, check_budget):
    """Minimum closed tour on the relaxed metric, or a safe arc-minimum bound.

Concatenating any number of delivery trips and shortcutting repeated nodes
on this directed shortest-path closure yields a Hamiltonian tour no longer
than those trips. Held-Karp is therefore a lower bound even with finite
capacities and optional pickup stops. Bound its exponential part at 12 stops.
"""
    if not required:
        return 0.0
    incoming = math.fsum(min(closure[other][site] for other in [base]+required if other != site)
                         for site in required)+min(closure[site][base] for site in required)
    outgoing = math.fsum(min(closure[site][other] for other in [base]+required if other != site)
                         for site in required)+min(closure[base][site] for site in required)
    if len(required) > 12:
        return max(incoming, outgoing)
    states = {(1 << i, i): closure[base][site] for i, site in enumerate(required)}
    for mask in range(1, 1 << len(required)):
        check_budget()
        for i, site in enumerate(required):
            current = states.get((mask, i))
            if current is None:
                continue
            for j, other in enumerate(required):
                if mask & (1 << j):
                    continue
                key = mask | (1 << j), j
                candidate = current+closure[site][other]
                if candidate < states.get(key, math.inf):
                    states[key] = candidate
    mask = (1 << len(required))-1
    return max(incoming, outgoing,
               min(states[mask, i]+closure[site][base] for i, site in enumerate(required)))


def distance_certificate(problem, deadline=None):
    """Return a conservative bound and its independently checkable components."""
    if (problem.model_number != 5 or problem.free_assignment or not problem.reusable
            or len(problem.operating_policy.batches) <= 1):
        return dict(bound=0.0, components=[], scope='not_applicable')
    def check_budget():
        if deadline is not None:
            import time
            if time.perf_counter() >= deadline:
                raise TimeoutError('distance certificate reached the total budget')

    sites = (problem.depot,)+problem.satellites
    closure = {site: _shortest(problem, site, sites, check_budget) for site in sites}
    components = []
    for batch in problem.operating_policy.batches:
        required = [site for site in problem.satellites if problem.demands[site, batch.id]]
        if not required:
            continue
        incoming = math.fsum(min(closure[other][site] for other in [problem.depot]+required if other != site)
                             for site in required)
        outgoing = math.fsum(min(closure[site][other] for other in [problem.depot]+required if other != site)
                             for site in required)
        incoming += min(closure[site][problem.depot] for site in required)
        outgoing += min(closure[problem.depot][site] for site in required)
        tour = _tour_bound(closure, problem.depot, required, check_budget)
        components.append(dict(echelon='E1', batch=batch.id, incoming=incoming,
                               outgoing=outgoing, tour_bound=tour, bound=max(incoming, outgoing, tour)))
    for site in problem.satellites:
        orders = [o for o in problem.orders if o.satellite == site]
        if not orders:
            continue
        tasks = [(o, kind) for o in orders for kind in problem.task_kinds]
        locations = tuple(sorted({site}|{o.customer for o in orders}))
        local_closure = {origin: _shortest(problem, origin, locations, check_budget) for origin in locations}
        tours = []
        for batch in problem.operating_policy.batches:
            required = sorted({o.customer for o in orders if o.batch == batch.id})
            if required:
                tours.append(dict(batch=batch.id, bound=_tour_bound(local_closure, site, required, check_budget)))

        def allowed(left, right):
            if left == right:
                return False
            a, kind_a = tasks[left]
            b, kind_b = tasks[right]
            if kind_a == kind_b == 'd' and a.batch != b.batch:
                return False
            return not (kind_a == 'p' and kind_b == 'd' and a.id == b.id)

        incoming = math.fsum(min([problem.distance(site, o.customer)]+[
            problem.distance(other.customer, o.customer) for j, (other, _) in enumerate(tasks)
            if allowed(j, i)]) for i, (o, _) in enumerate(tasks))
        outgoing = math.fsum(min([problem.distance(o.customer, site)]+[
            problem.distance(o.customer, other.customer) for j, (other, _) in enumerate(tasks)
            if allowed(i, j)]) for i, (o, _) in enumerate(tasks))
        routes = minimum_trip_count(problem, orders)
        incoming += routes*min(problem.distance(o.customer, site) for o in orders)
        outgoing += routes*min(problem.distance(site, o.customer) for o in orders)
        delivery_tours = math.fsum(row['bound'] for row in tours)
        components.append(dict(echelon='E2', satellite=site, minimum_routes=routes,
                               incoming=incoming, outgoing=outgoing, delivery_tours=tours,
                               bound=max(incoming, outgoing, delivery_tours)))
    raw = math.fsum(row['bound'] for row in components)
    # Subtract a relative/absolute margin before combining with solver bounds.
    safe = max(0.0, raw-1e-7*max(1.0, raw)) if math.isfinite(raw) else 0.0
    return dict(bound=safe, raw_bound=raw, components=components,
                scope='full_fixed_M5_cycle_distance', method='shortest_closure_tours_and_mandatory_visits')
