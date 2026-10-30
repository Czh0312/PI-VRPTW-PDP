"""Request removal and greedy time/capacity-aware reinsertion for cycle ALNS.

Local scores rank proposals only. The complete decoder and independent audit
remain the authorities; no local estimate proves infeasibility or optimality.
"""

from models.routing.model_8.alns.state import (
    clone_joint_state,
    clone_state,
    state_key,
    state_from_result,
)

from models.routing.model_8.alns.evaluator import EvaluationCache

from models.routing.model_8.alns.cutoff_moves import (
    _cutoff_events, _with_cutoff, _polish_cutoff, _cutoff_move,
)

from models.routing.model_8.alns.calendar_moves import boundary_events, move_boundaries

from collections import Counter, OrderedDict
import time


def _goods_ready(problem, state):
    ready = {}
    for b in problem.operating_policy.batches:
        for sites in state["deliveries"][b.id]:
            current, previous = b.start + problem.operating_policy.e1_load_time, problem.depot
            for site in sites:
                current += problem.travel(previous, site) + problem.service(site)
                ready[site, b.id] = current
                previous = site
    return ready


def _route_score(problem, route, goods, vehicle_ready):
    p, f = problem.operating_policy, problem.fleet
    tasks, site = route["tasks"], route["satellite"]
    if not tasks:
        return 0.0, vehicle_ready
    dbatches = {problem.order_map[oid].batch for oid, kind in tasks if kind == "d"}
    current = max(p.cycle_start, vehicle_ready, route.get("depart_after", p.cycle_start))
    if dbatches:
        current = max(current, *(goods[site, b] for b in dbatches)) + p.e2_dispatch_prep_time
    weight = sum(problem.task(oid, kind)[1] for oid, kind in tasks if kind == "d")
    volume = sum(problem.task(oid, kind)[2] for oid, kind in tasks if kind == "d")
    load_penalty = max(0, weight - f.e2_weight_cap) / max(1, f.e2_weight_cap)
    load_penalty += max(0, volume - f.e2_volume_cap) / max(1, f.e2_volume_cap)
    distance, late, delivered, previous = 0.0, 0, {}, site
    for oid, kind in tasks:
        order, w, vol = problem.task(oid, kind)
        window = order.delivery_window if kind == "d" else order.pickup_window
        current = max(current + problem.travel(previous, order.customer), window[0])
        if kind == "p":
            earliest_delivery = order.delivery_window[0]+order.service_time
            if len(problem.orders) <= 9:
                earliest_delivery = max(order.delivery_window[0],
                    goods[site, order.batch]+p.e2_dispatch_prep_time
                    +problem.travel(site, order.customer))+order.service_time
            current = max(
                current,
                delivered.get(oid, earliest_delivery)
                + order.min_use_time,
            )
        due = window[1]
        if kind == "d" and len(p.batches) == 1:
            due = min(due, p.batches[0].cutoff - order.service_time)
        late += max(0, current - due)
        current += order.service_time
        if kind == "d":
            delivered[oid] = current
        distance += problem.distance(previous, order.customer)
        previous = order.customer
        weight += w if kind == "p" else -w
        volume += vol if kind == "p" else -vol
        load_penalty += max(0, weight - f.e2_weight_cap) / max(1, f.e2_weight_cap)
        load_penalty += max(0, volume - f.e2_volume_cap) / max(1, f.e2_volume_cap)
    completed = max(
        current + problem.travel(previous, site) + p.e2_return_unload_time,
        route.get("return_after", p.cycle_start),
    )
    late += max(0, completed + p.e2_turnaround_time - p.cycle_end)
    if any(k == "p" for _, k in tasks):
        late += max(0, completed - p.batches[-1].cutoff)
    distance += problem.distance(previous, site)
    return (
        distance + 1e6 * (load_penalty + max(0, len(dbatches) - 1)) + p.minutes(late) * 1e4,
        completed,
    )


def destroy_repair(
    problem,
    original,
    rng,
    mode,
    deadline,
    *,
    scorer=None,
    cache_distances=False,
    cache_pickup_prefix=False
):
    if not problem.orders:
        return clone_state(original)
    orders = list(problem.orders)
    count = min(len(orders), max(2, min(5, len(orders) // 5)))
    if mode == "related":
        anchor = rng.choice(orders)
        orders.sort(
            key=lambda o: (
                o.satellite != anchor.satellite,
                problem.distance(anchor.customer, o.customer)
                + abs(o.delivery_window[0] - anchor.delivery_window[0]) * 0.01,
            )
        )
        removed = orders[:count]
    else:
        removed = rng.sample(orders, count)
    repaired = repair_orders(
        problem,
        original,
        removed,
        deadline,
        scorer=scorer,
        cache_distances=cache_distances,
        cache_pickup_prefix=cache_pickup_prefix,
    )
    return repaired if repaired is not None else clone_state(original)


def repair_orders(
    problem,
    original,
    removed,
    deadline,
    *,
    screen=True,
    cache_scores=True,
    fast_scores=True,
    scorer=None,
    cache_distances=False,
    cache_pickup_prefix=False
):
    """Reinsert complete requests; interruption never exposes a partial state.

    The distance-only bound safely screens this *local ranking score* because
    all its time/capacity penalties are nonnegative, including nonmetric arcs.
    It does not screen the global feasible set or certify infeasibility.
    """
    state = clone_state(original)
    ids = {o.id for o in removed}
    for route in state["e2"]:
        route["tasks"] = [t for t in route["tasks"] if t[0] not in ids]
    state["e2"] = [r for r in state["e2"] if r["tasks"]]
    goods = _goods_ready(problem, state)
    p, f = problem.operating_policy, problem.fleet
    # Deliveries are inserted first; each pickup may then join the same route
    # at a later position or a different vehicle/trip.
    tasks = [
        (o.id, kind)
        for kind in problem.task_kinds
        for o in sorted(
            removed, key=lambda o: (o.delivery_window if kind == "d" else o.pickup_window)[1]
        )
    ]
    score_route = _route_score
    if scorer is not None and len(p.batches) > 1:
        score_route = scorer
        # A distance floor cannot screen an externally supplied cost score.
        screen = False
    distance = problem.distance
    prefix_enabled = False
    if tasks and fast_scores and len(p.batches) == 1:
        from models.routing.model_8.alns.performance.scoring import SingleBatchRouteScorer

        score_route = scorer if scorer is not None else SingleBatchRouteScorer(problem)
        prefix_enabled = cache_pickup_prefix and type(score_route) is SingleBatchRouteScorer
        if cache_distances and isinstance(score_route, SingleBatchRouteScorer):
            # Static directed arcs belong to the same fixed problem as the
            # scorer. Reuse its bounded cache; scores and ready times stay fresh.
            distance = score_route.distance
    # Scoped to this repair: goods-ready times and the problem stay fixed.
    # Keep the route object alive with its score to prevent object-id reuse.
    route_scores = {}
    pickup_prefixes = {}
    for task in tasks:
        if time.perf_counter() >= deadline:
            return None
        oid, kind = task
        order, routes = problem.order_map[oid], state["e2"]
        delivery_route = next(((index, route['vehicle']) for index, route in enumerate(routes)
                               if (oid, 'd') in route['tasks']), None) if kind == 'p' and len(problem.orders) <= 9 else None
        ready, before, scores, distances = {}, {}, {}, {}
        counts = Counter((r["satellite"], r["vehicle"]) for r in routes)
        for index, route in enumerate(routes):
            # Other satellites cannot share this physical vehicle or supply.
            if route["satellite"] != order.satellite:
                continue
            key = route["satellite"], route["vehicle"]
            before[index] = ready.get(key, p.cycle_start)
            saved = route_scores.get(id(route)) if cache_scores else None
            if saved is not None and saved[0] is route and saved[1] == before[index]:
                _, _, scores[index], end, distances[index] = saved
            else:
                scores[index], end = score_route(problem, route, goods, before[index])
                locations = (
                    [route["satellite"]]
                    + [problem.order_map[o].customer for o, _ in route["tasks"]]
                    + [route["satellite"]]
                )
                distances[index] = sum(distance(a, b) for a, b in zip(locations, locations[1:]))
                if cache_scores:
                    route_scores[id(route)] = (
                        route,
                        before[index],
                        scores[index],
                        end,
                        distances[index],
                    )
            ready[key] = end + p.e2_turnaround_time
        best = None
        for index, route in enumerate(routes):
            if route["satellite"] != order.satellite:
                continue
            existing = route["tasks"]
            if delivery_route is not None and route['vehicle'] == delivery_route[1] and index < delivery_route[0]:
                continue
            if kind == "d" and any(
                k == "d" and problem.order_map[i].batch != order.batch for i, k in existing
            ):
                continue
            saved_prefix = (
                pickup_prefixes.get(id(route)) if prefix_enabled and kind == "p" else None
            )
            pickup_prefix = (
                saved_prefix[2]
                if saved_prefix is not None
                and saved_prefix[0] is route
                and saved_prefix[1] == before[index]
                else None
            )
            for pos in range(len(existing) + 1):
                if time.perf_counter() >= deadline:
                    return None
                if kind == "d" and (oid, "p") in existing[:pos]:
                    continue
                if kind == "p" and (oid, "d") in existing[pos:]:
                    continue
                if screen and best is not None:
                    left = (
                        route["satellite"]
                        if pos == 0
                        else problem.order_map[existing[pos - 1][0]].customer
                    )
                    right = (
                        route["satellite"]
                        if pos == len(existing)
                        else problem.order_map[existing[pos][0]].customer
                    )
                    bound = (
                        distances[index]
                        + distance(left, order.customer)
                        + distance(order.customer, right)
                        - distance(left, right)
                        - scores[index]
                    )
                    tolerance = 1e-9 * max(1, abs(bound), abs(best[0]), abs(scores[index]))
                    if bound > best[0] + tolerance:
                        continue
                candidate = {**route, "tasks": existing[:pos] + [task] + existing[pos:]}
                if prefix_enabled and kind == "p":
                    if pickup_prefix is None:
                        pickup_prefix = score_route.prepare_pickup_insertions(
                            problem, route, goods, before[index]
                        )
                        # Goods are fixed within this repair. A held route
                        # reference and its readiness protect reuse when another
                        # route changes, shifts indices, or delays this vehicle.
                        pickup_prefixes[id(route)] = (route, before[index], pickup_prefix)
                    score, _ = pickup_prefix(oid, pos)
                else:
                    score, _ = score_route(problem, candidate, goods, before[index])
                option = (score - scores[index], index, candidate, False)
                if best is None or option[0] < best[0]:
                    best = option
        for vehicle in range(f.e2_per_sat_count):
            if counts[order.satellite, vehicle] >= p.e2_max_trips_per_vehicle:
                continue
            candidate = dict(satellite=order.satellite, vehicle=vehicle, tasks=[task])
            window = order.delivery_window if kind == "d" else order.pickup_window
            pos = next(
                (
                    i
                    for i, route in enumerate(routes)
                    if route["satellite"] == order.satellite
                    and route["vehicle"] == vehicle
                    and min(
                        (
                            problem.order_map[o].delivery_window
                            if k == "d"
                            else problem.order_map[o].pickup_window
                        )[0]
                        for o, k in route["tasks"]
                    )
                    > window[0]
                ),
                len(routes),
            )
            if delivery_route is not None and vehicle == delivery_route[1]:
                pos = max(pos, delivery_route[0]+1)
            begin = (
                before[pos]
                if pos < len(routes)
                else ready.get((order.satellite, vehicle), p.cycle_start)
            )
            if delivery_route is not None:
                begin = next((before[index] for index in range(pos, len(routes))
                              if routes[index]['satellite'] == order.satellite and routes[index]['vehicle'] == vehicle),
                             ready.get((order.satellite, vehicle), p.cycle_start))
            score, _ = score_route(problem, candidate, goods, begin)
            if best is None or score < best[0]:
                best = (score, pos, candidate, True)
        if best is None:
            return None
        _, index, candidate, new_route = best
        if new_route:
            routes.insert(index, candidate)
        else:
            routes[index] = candidate
    return state


def _batch_move(problem, evaluator, assignment, rng, operator, deadline, cutoffs=None):
    from models.routing.model_8.alns.construction import _best_construction

    proposed = dict(assignment)
    ids = list(proposed)
    if operator == "split_batch":
        chosen = rng.choice(sorted(set(proposed.values())))
        members = [oid for oid in ids if proposed[oid] == chosen]
        ids = rng.sample(members, max(1, len(members) // 2))
    elif operator == "destroy_related_batch":
        anchor = problem.order_map[rng.choice(ids)]
        ids.sort(
            key=lambda oid: (
                problem.order_map[oid].satellite != anchor.satellite,
                problem.distance(anchor.customer, problem.order_map[oid].customer),
            )
        )
        ids = ids[: min(len(ids), max(2, min(5, len(ids) // 3)))]
    else:
        ids = rng.sample(ids, min(len(ids), max(1, min(4, len(ids) // 3))))
    rng.shuffle(ids)
    built = None
    for oid in ids:
        if time.perf_counter() >= deadline:
            break
        singleton = sum(bid == proposed[oid] for bid in proposed.values()) == 1
        choices = [b for b in problem.legal_batches[oid] if b != proposed[oid]]
        if not choices:
            continue
        rng.shuffle(choices)
        best = None
        for bid in choices:
            if time.perf_counter() >= deadline:
                break
            trial = {**proposed, oid: bid}
            if singleton:
                # Exchange orders when moving the last one would empty its
                # batch. This keeps fixed-K assignments searchable at K=|O|.
                partners = [other for other, current in proposed.items()
                            if current == bid and proposed[oid] in problem.legal_batches[other]]
                if not partners:
                    continue
                trial[rng.choice(partners)] = proposed[oid]
            construction = _best_construction(
                problem, evaluator, trial, rng, deadline, cutoffs=cutoffs
            )
            if best is None or construction[0] < best[0]:
                best = (construction[0], trial, construction)
        if best is not None:
            _, proposed, built = best
    if built is None:
        built = _best_construction(problem, evaluator, proposed, rng, deadline, cutoffs=cutoffs)
    return proposed, built[1]


def _route_move(problem, state, rng, operator):
    proposed = clone_joint_state(state)
    routes = proposed["e2"]
    f, p = problem.fleet, problem.operating_policy
    if operator in ("relocate", "pair") and routes:
        source = rng.choice(routes)
        oid, kind = rng.choice(source["tasks"])
        items = [(oid, "d"), (oid, "p")] if operator == "pair" else [(oid, kind)]
        for r in routes:
            r["tasks"] = [t for t in r["tasks"] if t not in items]
        routes[:] = [r for r in routes if r["tasks"]]
        order = problem.order_map[oid]
        candidates = [
            r
            for r in routes
            if r["satellite"] == order.satellite
            and (
                not any(k == "d" for _, k in items)
                or all(k != "d" or problem.order_map[o].batch == order.batch for o, k in r["tasks"])
            )
        ]
        if not candidates or rng.random() < 0.2:
            target = dict(
                satellite=order.satellite, vehicle=rng.randrange(f.e2_per_sat_count), tasks=[]
            )
            routes.append(target)
        else:
            target = rng.choice(candidates)
        position = rng.randrange(len(target["tasks"]) + 1)
        target["tasks"][position:position] = items
    elif operator == "split_merge" and routes:
        source = rng.choice(routes)
        others = [r for r in routes if r is not source and r["satellite"] == source["satellite"]]
        if len(source["tasks"]) > 1 and (not others or rng.random() < 0.5):
            i = rng.randrange(1, len(source["tasks"]))
            routes.insert(
                routes.index(source) + 1,
                dict(
                    satellite=source["satellite"],
                    vehicle=rng.randrange(f.e2_per_sat_count),
                    tasks=source["tasks"][i:],
                ),
            )
            source["tasks"] = source["tasks"][:i]
        elif others:
            other = rng.choice(others)
            source["tasks"] += other["tasks"]
            routes.remove(other)
    elif operator == "vehicle" and routes:
        if rng.random() < 0.5:
            rng.choice(routes)["vehicle"] = rng.randrange(f.e2_per_sat_count)
        elif len(routes) > 1:
            a, b = rng.sample(range(len(routes)), 2)
            routes[a], routes[b] = routes[b], routes[a]
    elif operator == "wait" and routes:
        r = rng.choice(routes)
        boundary = rng.choice(p.batches)
        t = rng.choice([p.cycle_start, boundary.cutoff, boundary.cutoff + 1, boundary.start])
        r[rng.choice(["depart_after", "return_after"])] = min(t, p.cycle_end)
    elif operator == "e1_delivery":
        rows = proposed["deliveries"][rng.choice(p.batches).id]
        nonempty = [r for r in rows if r]
        if nonempty:
            source = rng.choice(nonempty)
            site = source.pop(rng.randrange(len(source)))
            target = rng.choice(rows)
            target.insert(rng.randrange(len(target) + 1), site)
    elif operator == "e1_pickup":
        b = rng.choice(p.batches)
        rng.shuffle(proposed["pickup_order"][b.id])
        proposed["pickup_vehicle"][b.id][rng.choice(problem.satellites)] = rng.randrange(f.e1_count)
    elif operator == "reverse" and routes:
        r = rng.choice(routes)
        if len(r["tasks"]) >= 2:
            a, b = sorted(rng.sample(range(len(r["tasks"])), 2))
            r["tasks"][a : b + 1] = reversed(r["tasks"][a : b + 1])
    return proposed


def _local_cost_scorer(problem, route, goods, ready):
    """Insertion surrogate; only complete audited cost ranks final solutions."""
    score, end = _route_score(problem, route, goods, ready)
    ids = (
        [route["satellite"]]
        + [problem.order_map[o].customer for o, _ in route["tasks"]]
        + [route["satellite"]]
    )
    distance = sum(problem.distance(a, b) for a, b in zip(ids, ids[1:]))
    delivered = [problem.order_map[o].batch for o, k in route["tasks"] if k == "d"]
    start = max(problem.operating_policy.cycle_start, ready, route.get("depart_after", 0))
    if delivered:
        start = (
            max(start, max(goods[route["satellite"], b] for b in delivered))
            + problem.operating_policy.e2_dispatch_prep_time
        )
    rates = problem._joint_cost_rates
    return (
        distance * rates["distance_scale_to_km"] * rates["e2_distance_cost_per_km"]
        + problem.operating_policy.minutes(
            max(0, end - start)
            + (problem.operating_policy.e2_dispatch_prep_time if delivered else 0)
        )
        * rates["e2_work_cost_per_minute"]
        + rates["e2_trip_cost"]
        + max(0, score - distance)
    ), end
