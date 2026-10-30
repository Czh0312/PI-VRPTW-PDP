"""Bounded heuristic repairs for small instances; full audits decide acceptance."""
import time

from models.routing.model_8.alns.state import clone_joint_state, state_key
from models.routing.model_8.alns.evaluator import _e1_trip, route_distance
from models.routing.model_8.alns.neighborhoods import repair_orders, _local_cost_scorer, _route_score, _goods_ready
from models.routing.model_8.alns.cutoff_moves import _cutoff_events, _with_cutoff


def retained_repair(child, evaluator, old_assignment, assignment, state, cutoffs, deadline, *, rebuild=False):
    """Reinsert changed requests while preserving other routes and valid waits."""
    fixed = evaluator.fixed(assignment, cutoffs)
    trial = clone_joint_state(state)
    trial['cutoffs'] = dict(cutoffs)
    changed = [o for o in fixed.orders if rebuild or old_assignment.get(o.id) != assignment[o.id]]
    for batch in fixed.operating_policy.batches:
        rows = trial['deliveries'][batch.id]
        for row in rows:
            row[:] = [site for site in row if fixed.demands[site, batch.id]]
        present = {site for row in rows for site in row}
        for site in fixed.satellites:
            if site in present or not fixed.demands[site, batch.id]:
                continue
            options = []
            for vehicle, row in enumerate(rows):
                for pos in range(len(row)+1):
                    sequence = row[:pos]+[site]+row[pos:]
                    trip, issues = _e1_trip(fixed, batch, vehicle, sequence, [], {}, fixed.operating_policy.cycle_start)
                    options.append((len(issues), route_distance(fixed, trip, 'e1'), vehicle, pos))
            _, _, vehicle, pos = min(options)
            rows[vehicle].insert(pos, site)
    if changed:
        fixed._joint_cost_rates = child.cost_profile.rates
        repaired = repair_orders(fixed, trial, changed, deadline, scorer=_local_cost_scorer)
        if repaired is None:
            return None
        trial = repaired
        trial['cutoffs'] = dict(cutoffs)
    best = (*evaluator(assignment, trial), trial)
    # Retained waits can be essential. Test earliest timing as an alternative,
    # rather than deleting those waits before the only evaluation.
    if time.perf_counter() < deadline:
        earliest = clone_joint_state(trial)
        for route in earliest['e2']:
            route.pop('depart_after', None)
            route.pop('return_after', None)
        value = (*evaluator(assignment, earliest), earliest)
        if value[0] < best[0]:
            best = value
    score, result, issues, chosen = best
    return score, dict(assignment), chosen, result


def _route_distance(problem, state):
    distance = 0.
    for route in state['e2']:
        nodes = [route['satellite']]+[problem.order_map[o].customer for o, _ in route['tasks']]+[route['satellite']]
        distance += sum(problem.distance(a, b) for a, b in zip(nodes, nodes[1:]))
    return distance


def _route_moves(problem, state, deadline):
    routes = state['e2']
    problem._joint_cost_rates = problem.cost_profile.rates if hasattr(problem, 'cost_profile') else problem._joint_cost_rates
    for order in problem.orders:
        if time.perf_counter() >= deadline:
            return
        repaired = repair_orders(problem, state, [order], deadline, scorer=_local_cost_scorer)
        if repaired is not None:
            repaired['cutoffs'] = dict(state['cutoffs'])
            yield repaired
    # A complete request moves in one proposal, preserving its precedence.
    for order in problem.orders:
        for target, route in enumerate(routes):
            if route['satellite'] != order.satellite:
                continue
            if any(kind == 'd' and oid != order.id and problem.order_map[oid].batch != order.batch
                   for oid, kind in route['tasks']):
                continue
            trial = clone_joint_state(state)
            for row in trial['e2']:
                row['tasks'] = [task for task in row['tasks'] if task[0] != order.id]
            sequence = trial['e2'][target]['tasks']
            for pos in range(len(sequence)+1):
                if time.perf_counter() >= deadline:
                    return
                value = clone_joint_state(trial)
                value['e2'][target]['tasks'][pos:pos] = [(order.id, kind) for kind in problem.task_kinds]
                value['e2'] = [row for row in value['e2'] if row['tasks']]
                yield value
    for i, route in enumerate(routes):
        for left in range(len(route['tasks'])):
            for right in range(left+2, len(route['tasks'])+1):
                if time.perf_counter() >= deadline:
                    return
                trial = clone_joint_state(state)
                trial['e2'][i]['tasks'][left:right] = reversed(route['tasks'][left:right])
                yield trial
        for j in range(i+1, len(routes)):
            other = routes[j]
            if route['satellite'] != other['satellite']:
                continue
            sequence = route['tasks']+other['tasks']
            if len({problem.order_map[o].batch for o, kind in sequence if kind == 'd'}) > 1:
                continue
            for tasks in [sequence, other['tasks']+route['tasks'],
                          [task for task in sequence if task[1] == 'd']+[task for task in sequence if task[1] == 'p']]:
                if time.perf_counter() >= deadline:
                    return
                trial = clone_joint_state(state)
                trial['e2'][i]['tasks'] = list(tasks)
                trial['e2'].pop(j)
                yield trial


def _e1_and_wait_moves(problem, assignment, state, result, deadline):
    cuts = state['cutoffs']
    for batch in problem.operating_policy.batches:
        bid = batch.id
        for vehicle, sites in enumerate(state['deliveries'][bid]):
            if len(sites) > 1:
                trial = clone_joint_state(state)
                trial['deliveries'][bid][vehicle] = list(reversed(sites))
                yield trial
        if len(state['pickup_order'][bid]) > 1:
            trial = clone_joint_state(state)
            trial['pickup_order'][bid].reverse()
            yield trial
    for batch in problem.operating_policy.batches[:-1]:
        bid = batch.id
        events = _cutoff_events(problem, cuts, result, bid)
        targets = [cuts[bid]]+sorted(events-{cuts[bid]}, key=lambda t: (abs(t-cuts[bid]), t))[:2]
        for selected in targets:
            base = _with_cutoff(problem, state, bid, selected)
            for site in problem.satellites:
                if time.perf_counter() >= deadline:
                    return
                trial = clone_joint_state(base)
                touched = False
                position = list(cuts).index(bid)
                previous_cut = list(cuts.values())[position-1] if position else problem.operating_policy.cycle_start-1
                decoded = iter(result['e2_trips'])
                for route in trial['e2']:
                    if not route['tasks']:
                        continue
                    physical = next(decoded)
                    if (route['satellite'] == site and any(kind == 'p' for _, kind in route['tasks'])
                            and previous_cut < physical['return_complete'] <= cuts[bid]):
                        route['return_after'] = max(route.get('return_after', 0), trial['cutoffs'][bid]+1)
                        touched = True
                if not touched:
                    continue
                yield trial
                for vehicle, sites in enumerate(trial['deliveries'][bid]):
                    if len(sites) > 1:
                        paired = clone_joint_state(trial)
                        paired['deliveries'][bid][vehicle] = list(reversed(sites))
                        yield paired


def polish_small(child, evaluator, assignment, state, deadline, rounds=4):
    """Rank a bounded route frontier, without nested cutoff descents per move."""
    best = clone_joint_state(state)
    score, result, _ = evaluator(assignment, best)
    evaluations = improvements = 0
    if result is None or len(child.orders) > 9:
        return best, evaluations, improvements
    for _ in range(rounds):
        if time.perf_counter() >= deadline:
            break
        fixed = evaluator.fixed(assignment, best['cutoffs'])
        fixed._joint_cost_rates = child.cost_profile.rates
        goods = _goods_ready(fixed, best)
        proposed = []
        seen = {state_key(best)}
        floor = _route_distance(fixed, best)
        for trial in _route_moves(fixed, best, deadline):
            key = state_key(trial)
            if key in seen:
                continue
            seen.add(key)
            distance = _route_distance(fixed, trial)
            if distance < floor-1e-8:
                penalty = 0.
                for route in trial['e2']:
                    score, _ = _route_score(fixed, route, goods, fixed.operating_policy.cycle_start)
                    nodes = [route['satellite']]+[fixed.order_map[o].customer for o, _ in route['tasks']]+[route['satellite']]
                    d = sum(fixed.distance(a, b) for a, b in zip(nodes, nodes[1:]))
                    penalty += max(0., score-d)
                proposed.append((penalty, distance, key, trial))
        frontier = [trial for _, _, _, trial in sorted(proposed, key=lambda r: (r[0], r[1], repr(r[2])))[:24]]
        frontier += list(_e1_and_wait_moves(child, assignment, best, result, deadline))[:24]
        chosen = None
        for trial in frontier:
            if time.perf_counter() >= deadline:
                break
            value, candidate, _ = evaluator(assignment, trial)
            evaluations += 1
            if candidate is not None and value < score-1e-8:
                score, chosen, result = value, trial, candidate
        if chosen is None:
            break
        best = chosen
        improvements += 1
    return best, evaluations, improvements
