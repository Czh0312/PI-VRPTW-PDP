"""Bounded deterministic route descent on an ALNS incumbent; no solver backend."""
import time
from models.routing.model_8.alns.state import clone_joint_state, state_key
from models.routing.model_8.alns.neighborhoods import _polish_cutoff, _with_cutoff


def _resegment(problem, state, rates, deadline):
    """Repartition task chains so a pickup can share the next delivery trip.

    One-task descent can get stuck: moving a delivery first adds distance,
    whereas moving its pickup first violates precedence. A bounded dynamic
    beam proposes whole partitions; the joint decoder checks timing and stock.
    """
    groups = {}
    for i, route in enumerate(state["e2"]):
        if route["tasks"]:
            groups.setdefault((route["satellite"], route["vehicle"]), []).append(i)
    for (site, vehicle), indices in groups.items():
        tasks = [task for i in indices for task in state["e2"][i]["tasks"]]
        if len(tasks) > 32:
            continue
        segments = {}
        for left in range(len(tasks)):
            if time.perf_counter() >= deadline:
                return
            batches = set()
            for right in range(left+1, len(tasks)+1):
                sequence = tasks[left:right]
                oid, kind = sequence[-1]
                if kind == "d":
                    batches.add(problem.order_map[oid].batch)
                if len(batches) > 1:
                    break
                load_w = sum(problem.task(oid, kind)[1] for oid, kind in sequence if kind == "d")
                load_v = sum(problem.task(oid, kind)[2] for oid, kind in sequence if kind == "d")
                valid = True
                for oid, kind in sequence:
                    _, w, v = problem.task(oid, kind)
                    if kind == "p":
                        load_w += w
                        load_v += v
                    if load_w > problem.fleet.e2_weight_cap+1e-6 or load_v > problem.fleet.e2_volume_cap+1e-6:
                        valid = False
                    if kind == "d":
                        load_w -= w
                        load_v -= v
                if not valid:
                    continue
                nodes = [site]+[problem.order_map[oid].customer for oid, _ in sequence]+[site]
                distance = sum(problem.distance(a, b) for a, b in zip(nodes, nodes[1:]))
                segments[left, right] = (distance*rates["distance_scale_to_km"]
                                        * rates["e2_distance_cost_per_km"]+rates["e2_trip_cost"])
        paths = {(0, 0): [(0., ())]}
        cap = min(problem.operating_policy.e2_max_trips_per_vehicle, len(tasks))
        for end in range(1, len(tasks)+1):
            if time.perf_counter() >= deadline:
                return
            for count in range(1, min(end, cap)+1):
                proposed = [(score+segments[start, end], cuts+(end,))
                            for start in range(end) if (start, end) in segments
                            for score, cuts in paths.get((start, count-1), ())]
                if proposed:
                    paths[end, count] = sorted(proposed)[:16]
        candidates = sorted(path for count in range(1, cap+1)
                            for path in paths.get((len(tasks), count), ()))
        for _, cuts in candidates[:128]:
            rows, start = [], 0
            for end in cuts:
                rows.append(dict(satellite=site, vehicle=vehicle, tasks=list(tasks[start:end])))
                start = end
            trial = clone_joint_state(state)
            trial["e2"] = [r for i, r in enumerate(trial["e2"]) if i not in indices]
            trial["e2"][indices[0]:indices[0]] = rows
            yield trial


def _e1_neighbors(problem, state):
    for bid, rows in state["deliveries"].items():
        for vehicle, sites in enumerate(rows):
            for index, site in enumerate(sites):
                for target, sequence in enumerate(rows):
                    for pos in range(len(sequence)+1):
                        trial = clone_joint_state(state)
                        trial["deliveries"][bid][vehicle].pop(index)
                        insertion = pos-int(vehicle == target and pos > index)
                        trial["deliveries"][bid][target].insert(insertion, site)
                        yield trial
                        paired = clone_joint_state(trial)
                        paired["pickup_vehicle"][bid][site] = target
                        yield paired
        sites = state["pickup_order"][bid]
        for i in range(len(sites)):
            for j in range(i+1, len(sites)):
                trial = clone_joint_state(state)
                trial["pickup_order"][bid][i], trial["pickup_order"][bid][j] = sites[j], sites[i]
                yield trial
        for site in sites:
            for vehicle in range(problem.fleet.e1_count):
                trial = clone_joint_state(state)
                trial["pickup_vehicle"][bid][site] = vehicle
                yield trial


def _merge_trip_neighbors(problem, state, deadline):
    """Merge compatible trips across physical vehicles in one feasible move.

    Single-task relocation can strand a delivery or pickup in an infeasible
    intermediate. Rebuild both trips together; the full evaluator checks the
    shared unload time, capacity, customer precedence and vehicle schedules.
    """
    if len(problem.orders) > 9:
        return
    routes = state['e2']
    for i, left in enumerate(routes):
        if not left['tasks']:
            continue
        for j in range(i+1, len(routes)):
            if time.perf_counter() >= deadline:
                return
            right = routes[j]
            if not right['tasks'] or left['satellite'] != right['satellite']:
                continue
            batches = {problem.order_map[oid].batch for oid, kind in left['tasks']+right['tasks'] if kind == 'd'}
            if len(batches) > 1:
                continue
            ld = [task for task in left['tasks'] if task[1] == 'd']
            lp = [task for task in left['tasks'] if task[1] == 'p']
            rd = [task for task in right['tasks'] if task[1] == 'd']
            rp = [task for task in right['tasks'] if task[1] == 'p']
            sequences = {tuple(left['tasks']+right['tasks']), tuple(right['tasks']+left['tasks']),
                         tuple(ld+rd+lp+rp), tuple(rd+ld+rp+lp)}
            site = left['satellite']
            def distance(sequence):
                nodes = [site]+[problem.order_map[oid].customer for oid, _ in sequence]+[site]
                return sum(problem.distance(a, b) for a, b in zip(nodes, nodes[1:]))
            for sequence in sorted(sequences, key=lambda value: (distance(value), value)):
                for vehicle in sorted({left['vehicle'], right['vehicle']}):
                    for position in sorted({i, j-1}):
                        if time.perf_counter() >= deadline:
                            return
                        trial = clone_joint_state(state)
                        trial['e2'] = [route for index, route in enumerate(trial['e2']) if index not in (i, j)]
                        trial['e2'].insert(position, dict(satellite=site, vehicle=vehicle, tasks=list(sequence)))
                        yield trial


def _neighbors(problem, state, rates, deadline):
    yield from _e1_neighbors(problem, state)
    yield from _merge_trip_neighbors(problem, state, deadline)
    yield from _resegment(problem, state, rates, deadline)
    routes = state["e2"]
    for i, route in enumerate(routes):
        for left in range(len(route["tasks"])):
            for right in range(left+2, len(route["tasks"])+1):
                trial = clone_joint_state(state)
                trial["e2"][i]["tasks"][left:right] = reversed(route["tasks"][left:right])
                yield trial
    positions = [(i, j) for i, route in enumerate(routes) for j in range(len(route["tasks"]))]
    for i, j in positions:
        for target, route in enumerate(routes):
            if route["satellite"] != routes[i]["satellite"]:
                continue
            for pos in range(len(route["tasks"])+1):
                trial = clone_joint_state(state)
                task = trial["e2"][i]["tasks"].pop(j)
                insertion = pos-int(i == target and pos > j)
                trial["e2"][target]["tasks"].insert(insertion, task)
                yield trial
    for index, (i, j) in enumerate(positions):
        for target, pos in positions[index+1:]:
            if routes[i]["satellite"] != routes[target]["satellite"]:
                continue
            trial = clone_joint_state(state)
            a, b = trial["e2"][i]["tasks"], trial["e2"][target]["tasks"]
            a[j], b[pos] = b[pos], a[j]
            yield trial


def polish_routes(problem, evaluator, assignment, state, deadline):
    best = clone_joint_state(state)
    score, result, _ = evaluator(assignment, best)
    if result is None:
        return best, 0, 0
    def key_of(candidate):
        return state_key(candidate), tuple(sorted(candidate.get("cutoffs", {}).items()))
    seen = {key_of(best)}
    evaluations = improvements = 0
    while time.perf_counter() < deadline:
        improved = False
        for trial in _neighbors(problem, best, evaluator.problem.cost_profile.rates, deadline):
            if time.perf_counter() >= deadline:
                return best, evaluations, improvements
            key = key_of(trial)
            if key in seen:
                continue
            seen.add(key)
            value, candidate, issues = evaluator(assignment, trial)
            evaluations += 1
            if (candidate is None and evaluator.problem.optimize_cutoffs
                    and any(code in issues for code in
                            ("E1_RETURN_DEADLINE", "E1_TERMINAL_VEHICLE",
                             "E1_VEHICLE_OVERLAP", "TERMINAL_DIRTY"))):
                # Merging E1 trips can need an earlier cutoff immediately.
                # Explore the incumbent's physical pickup-return breakpoints,
                # including when the route-only intermediate is infeasible.
                for bid in best["deliveries"]:
                    changed = (trial["deliveries"][bid] != best["deliveries"][bid]
                        or trial["pickup_vehicle"][bid] != best["pickup_vehicle"][bid]
                        or trial["pickup_order"][bid] != best["pickup_order"][bid])
                    if not changed:
                        continue
                    lo, hi = evaluator.problem.cutoff_bounds[bid]
                    targets = {trip["return_complete"] for trip in result["e2_trips"]
                               if any(e["kind"] == "p" for e in trip["events"])
                               and lo <= trip["return_complete"] < trial["cutoffs"][bid]}
                    for target in sorted(targets, reverse=True)[:4]:
                        if time.perf_counter() >= deadline:
                            return best, evaluations, improvements
                        rescued = _with_cutoff(evaluator.problem, trial, bid, target)
                        rescued_value, rescued_result, _ = evaluator(assignment, rescued)
                        evaluations += 1
                        if rescued_result is not None and rescued_value < value:
                            trial, value, candidate = rescued, rescued_value, rescued_result
            if (candidate is not None and evaluator.problem.optimize_cutoffs
                    and (candidate["cost_components"]["e2_distance"]
                         + candidate["cost_components"]["e1_distance"])
                    < (result["cost_components"]["e2_distance"]
                       + result["cost_components"]["e1_distance"])-1e-8):
                # Route boundaries and return cutoffs must move together.
                # Replaying just the route can temporarily increase pool use.
                trial = _polish_cutoff(evaluator.problem, evaluator, assignment,
                                       trial, deadline, limit=24)
                value, candidate, _ = evaluator(assignment, trial)
            if candidate is not None and value < score-1e-8:
                best, score, result = trial, value, candidate
                improvements += 1
                improved = True
                break
        if not improved:
            break
    return best, evaluations, improvements
