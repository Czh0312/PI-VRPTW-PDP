"""M8 v2 adaptive pickup destroy/repair with capacity-release handoffs.

No MILP is built during stage two. Small pickup groups are inserted with a
bounded beam; handoff event, positions and integer quantities are enumerated.
Every accepted state passes an independent full-system schedule/load audit.
This is a heuristic subset of the conditional exact model, not an exact DP.
"""
from copy import deepcopy
import math
import random
import time

from models.routing.variants.model_8.baseline import prepare_baseline
from models.routing.variants.model_8.state import (
    Evaluator, baseline_state, event_id, initial_info, record_solution, TOL,
)


def _remove_group(state, sat, vehicle, group):
    state = deepcopy(state)
    required = {t['event'] for t in state['transfers'] if t['satellite'] == sat
                and vehicle in (t['sender_vehicle'], t['receiver_vehicle'])}
    route = state['e2_routes'][sat][vehicle]
    state['e2_routes'][sat][vehicle] = [
        ('MEET@' + n if n in required else None) if n in group else n for n in route]
    state['e2_routes'][sat][vehicle] = [n for n in state['e2_routes'][sat][vehicle] if n is not None]
    return state


def _insert_group(evaluator, route, group, width=4):
    """Distance beam preserving all remaining anchors; capacity checked jointly."""
    beam = [route]
    for task in group:
        candidates = {}
        for r in beam:
            if 'MEET@' + task in r:
                trial = [task if n == 'MEET@' + task else n for n in r]
                candidates[tuple(trial)] = evaluator.route_cost(trial)
            else:
                for pos in range(1, len(r)):
                    trial = r[:pos] + [task] + r[pos:]
                    candidates[tuple(trial)] = evaluator.route_cost(trial)
        beam = [list(r) for r in sorted(candidates, key=candidates.get)[:width]]
    return beam


def _visit_insertions(evaluator, route, event, width=5):
    for pos, n in enumerate(route[1:-1], 1):
        if event_id(n) == event:
            return [(route, pos)]
    candidates = [(route[:p] + ['MEET@' + event] + route[p:], p)
                  for p in range(1, len(route))]
    return sorted(candidates, key=lambda item: evaluator.route_cost(item[0]))[:width]


def _q_range(e, ra, rb, s, a, b, ia, ib, sent, received):
    pa, _, ca = e.profile(ra, s, a, sent, received)
    pb, _, cb = e.profile(rb, s, b, sent, received)
    if not ca or not cb:
        return range(0)
    for rows in (pa[:ia], pb[:ib]):
        if any(r['weight'] > e.problem.e2_weight_cap + TOL or
               r['volume'] > e.problem.e2_volume_cap + TOL or r['own'] < 0 for r in rows):
            return range(0)
    n = event_id(ra[ia])
    maximum = min(pa[ia]['own_in'] - sent.get((s, a, n), 0),
                  min(r['own'] for r in pa[ia:]))
    minimum = 1
    for field, cap, unit in (('weight', e.problem.e2_weight_cap, e.cw),
                              ('volume', e.problem.e2_volume_cap, e.cv)):
        excess = max(r[field] - cap for r in pa[ia:])
        spare = min(cap - r[field] for r in pb[ib:])
        if unit > TOL:
            minimum = max(minimum, math.ceil((excess - TOL) / unit))
            maximum = min(maximum, math.floor((spare + TOL) / unit))
        elif excess > TOL or spare < -TOL:
            return range(0)
    return range(max(1, minimum), int(maximum) + 1)


def _repair_handoff(e, state, sat, a, b, deadline, ceiling, max_events=16):
    """Resolve a pickup relocation's capacity deficit with one native handoff."""
    sent, received = e.transfer_maps(state)
    ra, rb = state['e2_routes'][sat][a], state['e2_routes'][sat][b]
    pa, feasible_a, carried_a = e.profile(ra, sat, a, sent, received)
    _, feasible_b, _ = e.profile(rb, sat, b, sent, received)
    # No extra transfer is needed if ordinary relocation is load-feasible.
    if feasible_a or not carried_a or not feasible_b:
        return None, 0
    if any(r['own'] < 0 for r in pa):
        return None, 0
    events = [n for n in e.tasks if e.sat[n] == sat]
    ranked = []
    for n in events:
        if time.perf_counter() >= deadline:
            return None, 0
        ais = _visit_insertions(e, ra, n)
        bis = _visit_insertions(e, rb, n)
        ranked.append((e.route_cost(ais[0][0]) + e.route_cost(bis[0][0]), n, ais, bis))
    ranked.sort(key=lambda x: (x[0], x[1]))
    unchanged = e.cost(state) - e.route_cost(ra) - e.route_cost(rb)
    best, checks = None, 0
    for _, n, ais, bis in ranked[:max_events]:
        for ar, ia in ais:
            for br, ib in bis:
                if time.perf_counter() >= deadline:
                    return best, checks
                cost = unchanged + e.route_cost(ar) + e.route_cost(br)
                if cost >= ceiling - TOL:
                    continue
                for q in _q_range(e, ar, br, sat, a, b, ia, ib, sent, received):
                    trial = deepcopy(state)
                    trial['e2_routes'][sat][a] = ar
                    trial['e2_routes'][sat][b] = br
                    trial['transfers'].append({'satellite': sat, 'event': n,
                                               'sender_vehicle': a, 'receiver_vehicle': b,
                                               'quantity': q})
                    checks += 1
                    try:
                        audit = e.audit(trial)
                    except ValueError:
                        continue
                    best = (trial, audit)
                    ceiling = audit['objective']
                    # Fixed positions: all quantities have the same distance.
                    break
    return best, checks


def _drop_transfer(state, index):
    result = deepcopy(state)
    result['transfers'].pop(index)
    needed = {(t['satellite'], k, t['event']) for t in result['transfers']
              for k in (t['sender_vehicle'], t['receiver_vehicle'])}
    for s, paths in result['e2_routes'].items():
        for k, r in enumerate(paths):
            paths[k] = [n for n in r if not n.startswith('MEET@') or (s, k, event_id(n)) in needed]
    return result


def solve_alns(problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite,
               TFp=9999.0, max_iterations=5000, max_time=120, seed=42,
               verbose=True, return_info=False, stage1_time_ratio=None,
               base_routes=None, base_info=None, stage1_method='alns',
               repair_time_limit=0.5, max_remove=3, gurobi_params=None,
               allow_transfers=True, insertion_beam=4, max_handoff_events=16):
    if max_time <= 0 or repair_time_limit <= 0 or max_remove < 1 or max_iterations < 0:
        raise ValueError('M8 时间预算须为正，max_remove>=1，max_iterations>=0')
    if stage1_method not in ('exact', 'alns') or insertion_beam < 1 or max_handoff_events < 1:
        raise ValueError('M8 搜索参数不合法')
    ratio = .6 if stage1_time_ratio is None else stage1_time_ratio
    if not 0 < ratio < 1:
        raise ValueError('stage1_time_ratio 必须在 (0,1) 内')
    started = time.perf_counter()
    deadline = started + max_time
    rng = random.Random(seed)
    if base_routes is None:
        if stage1_method == 'exact':
            from models.routing.variants.model_3.exact_solver import solve_exact
            a, b, base_info = solve_exact(
                problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite,
                TFp=TFp, verbose=verbose, time_limit=max_time * ratio,
                return_info=True, return_incumbent_on_time_limit=True,
                gurobi_params=gurobi_params)
        else:
            from models.routing.alns.variants.model_3 import solve_alns as solve_m3
            a, b, base_info = solve_m3(
                problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite,
                TFp=TFp, verbose=verbose, max_time=max_time * ratio,
                max_iterations=max_iterations, seed=seed, return_info=True)
        if a is None:
            info = {'model': 8, 'status': 'NO_BASE_SOLUTION', 'objective': None,
                    'proven_optimal': False, 'stage1': base_info}
            return (None, None, info) if return_info else (None, None)
        base_routes = (a, b)
    base = prepare_baseline(problem, e1_tasks, e2_tasks_by_sat,
                            vehicles_per_satellite, base_routes, TFp)
    stage1 = dict(base_info or {})
    if stage1.get('objective') is not None and not math.isclose(
            stage1['objective'], base['objective'], abs_tol=1e-6, rel_tol=1e-8):
        raise ValueError('M3 证书目标值与基础路线不一致')
    e = Evaluator(problem, e1_tasks, e2_tasks_by_sat, base, TFp)
    current = baseline_state(base)
    current_audit = e.audit(current)
    best, best_audit = deepcopy(current), current_audit
    info = initial_info(base, stage1)
    info.update(algorithm='adaptive_joint_pickup_handoff_neighborhood', requires_gurobi=False,
                stage1_method=stage1_method, seed=seed, allow_transfers=allow_transfers,
                iterations=0, accepted_moves=0, improving_moves=0,
                capacity_blocked_candidates=0, feasible_relay_repairs=0,
                full_audits=1, relay_audits=0, search_history=[])
    # Cluster relocation, high-detour relocation, random relocation, relay undo.
    weights, uses, scores = [1.0] * 4, [0] * 4, [0.0] * 4
    available = [s for s, paths in current['e2_routes'].items() if paths]
    stage2_start = time.perf_counter()
    for iteration in range(max_iterations):
        if time.perf_counter() >= deadline or not available:
            break
        operator = rng.choices(range(4), weights=weights)[0]
        working = current
        if operator == 3 and current['transfers']:
            working = _drop_transfer(current, rng.randrange(len(current['transfers'])))
        sat = rng.choice(available)
        paths = working['e2_routes'][sat]
        donors = [k for k, r in enumerate(paths) if any(n in e.boxes for n in r)]
        if not donors:
            continue
        b = rng.choice(donors)
        pickups = [n for n in paths[b] if n in e.boxes]
        if operator == 1:
            def removal_saving(n):
                pos = paths[b].index(n)
                p, z = paths[b][pos - 1], paths[b][pos + 1]
                return e.distance(p, n) + e.distance(n, z) - e.distance(p, z)
            pickups.sort(key=removal_saving, reverse=True)
            anchor = rng.choice(pickups[:min(3, len(pickups))])
        else:
            anchor = rng.choice(pickups)
        # Spatial proximity guides receiver-of-tasks selection, with exploration.
        candidates = list(range(len(paths)))
        proximity = [min(e.distance(anchor, n) for n in r) for r in paths]
        order = sorted(candidates, key=lambda k: proximity[k])
        other = [k for k in order if k != b]
        if other and rng.random() < .85:
            a = rng.choice(other[:min(3, len(other))])
        else:
            a = rng.choice(candidates)
        count = rng.randint(1, min(max_remove, len(pickups)))
        if operator == 0:
            group = sorted(pickups, key=lambda n: e.distance(anchor, n))[:count]
        elif operator == 1:
            group = pickups[:count]
        else:
            group = [anchor] + rng.sample([n for n in pickups if n != anchor], count - 1)
        # Match pickup order to original donor for one beam, occasionally reverse.
        group.sort(key=paths[b].index, reverse=rng.random() < .25)
        removed = _remove_group(working, sat, b, set(group))
        proposals = _insert_group(e, removed['e2_routes'][sat][a], group, insertion_beam)
        best_candidate = None
        local_deadline = min(deadline, time.perf_counter() + repair_time_limit)
        for route in proposals:
            if time.perf_counter() >= local_deadline:
                break
            trial = deepcopy(removed)
            trial['e2_routes'][sat][a] = route
            sent, received = e.transfer_maps(trial)
            _, valid_a, carried = e.profile(route, sat, a, sent, received)
            if valid_a:
                try:
                    info['full_audits'] += 1
                    audit = e.audit(trial)
                except ValueError:
                    continue
                if best_candidate is None or audit['objective'] < best_candidate[1]['objective'] - TOL:
                    best_candidate = (trial, audit)
            elif allow_transfers and a != b and carried:
                info['capacity_blocked_candidates'] += 1
                ceiling = current_audit['objective']
                if best_candidate:
                    ceiling = min(ceiling, best_candidate[1]['objective'])
                if e.cost(trial) >= ceiling - TOL:
                    continue
                candidate, checks = _repair_handoff(
                    e, trial, sat, a, b, local_deadline, ceiling, max_handoff_events)
                info['relay_audits'] += checks
                if candidate:
                    info['feasible_relay_repairs'] += 1
                    best_candidate = candidate
        reward = 0.0
        if best_candidate:
            state, audit = best_candidate
            delta = audit['objective'] - current_audit['objective']
            progress = min(1.0, (time.perf_counter() - stage2_start) / max(deadline - stage2_start, 1e-9))
            temperature = max(1e-9, .001 * base['objective'] * .01 ** progress)
            accepted = delta <= TOL or rng.random() < math.exp(-delta / temperature)
            if accepted:
                current, current_audit = state, audit
                info['accepted_moves'] += 1
                reward = 2 if delta >= -TOL else 8
            if audit['objective'] < best_audit['objective'] - TOL:
                best, best_audit = deepcopy(state), audit
                info['improving_moves'] += 1
                reward = 25
                info['search_history'].append({
                    'iteration': iteration + 1, 'operator': operator, 'satellite': sat,
                    'task_receiver_vehicle': a, 'task_donor_vehicle': b,
                    'moved_pickups': group, 'objective': audit['objective'],
                    'transfers': deepcopy(state['transfers']),
                    'elapsed': time.perf_counter() - started})
        uses[operator] += 1
        scores[operator] += reward
        info['iterations'] = iteration + 1
        if (iteration + 1) % 50 == 0:
            for k in range(4):
                if uses[k]:
                    weights[k] = max(.2, .8 * weights[k] + .2 * scores[k] / uses[k])
                uses[k], scores[k] = 0, 0.0
        if (iteration + 1) % 200 == 0:
            current, current_audit = deepcopy(best), best_audit
    # Recheck the final state, never infer feasibility from objective bookkeeping.
    record_solution(info, best, e.audit(best))
    info.update(runtime=time.perf_counter() - started, destroy_weights=weights,
                destroy_operators=['nearby_pickup_group', 'expensive_pickups', 'random_pickups', 'undo_handoff'],
                repair_operators=['pickup_insertion_beam', 'capacity_release_handoff'],
                termination_reason='time_limit' if time.perf_counter() >= deadline else 'iteration_limit')
    if verbose:
        print(f"M8 v2: base={base['objective']:.6f}, best={info['objective']:.6f}, "
              f"handoffs={info['transshipment_count']}, iterations={info['iterations']}")
    routes = (base['e1_routes'], best['e2_routes'])
    return (*routes, info) if return_info else routes
