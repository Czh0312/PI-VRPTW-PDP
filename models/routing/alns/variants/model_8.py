"""M8 joint ALNS: E1/E2 delivery, pickup and capacity-release handoffs.

One current state, one objective, one search loop and one wall-clock budget.
No M3 solve, stage-one optimum, fixed delivery backbone or fixed active fleet.
"""
from copy import deepcopy
import math
import random
import time

from models.routing.variants.model_8.initial_solution import construct_initial_state
from models.routing.variants.model_8.state import (
    Evaluator, state_from_routes, initial_info, record_solution, export_routes, TOL)
from models.routing.variants.model_8.neighborhoods import pickup_move
from models.routing.variants.model_8.e1_moves import e1_move
from .model8_delivery_moves import DELIVERY_OPERATORS, delivery_move


def solve_alns(problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite,
               TFp=9999.0, max_iterations=5000, max_time=120, seed=42,
               verbose=True, return_info=False, warm_start_routes=None,
               initial_state=None, allow_transfers=True, repair_time_limit=.5,
               max_remove=3, insertion_beam=4, max_handoff_events=16,
               max_handoff_receivers=3, handoff_receiver_candidates=6):
    if max_time <= 0 or max_iterations < 0 or repair_time_limit <= 0:
        raise ValueError('搜索预算必须为正，迭代上限不得为负')
    if min(max_remove, insertion_beam, max_handoff_events, max_handoff_receivers) < 1:
        raise ValueError('邻域参数必须为正整数')
    if handoff_receiver_candidates < max_handoff_receivers:
        raise ValueError('候选接车数不能小于联合接车数')
    if warm_start_routes is not None and initial_state is not None:
        raise ValueError('只能指定一个初始方案')
    started = time.perf_counter()
    deadline = started + max_time
    rng = random.Random(seed)
    e = Evaluator(problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite, TFp)
    info = initial_info(allow_transfers, 'joint_e1_e2_capacity_cooperation_alns')
    if initial_state is not None:
        current = state_from_routes(problem, vehicles_per_satellite,
            (initial_state['e1_routes'], initial_state['e2_routes']), initial_state['transfers'])
        source = 'supplied_joint_state_not_fixed'
    elif warm_start_routes is not None:
        current = state_from_routes(problem, vehicles_per_satellite, warm_start_routes)
        source = 'optional_warm_start_not_fixed'
    else:
        try:
            current = construct_initial_state(problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite, TFp, deadline)
        except ValueError as exc:
            info.update(status='NO_FEASIBLE_INITIAL_SOLUTION', initial_failure=str(exc),
                        runtime=time.perf_counter() - started)
            return (None, None, info) if return_info else (None, None)
        source = 'constructive_insertion_without_m3_solve'
    if not allow_transfers and current['transfers']:
        raise ValueError('禁止传递对照不能从含传递的方案起步')
    try:
        current_audit = e.audit(current)
    except ValueError as exc:
        if warm_start_routes is not None or initial_state is not None:
            raise
        info.update(status='NO_FEASIBLE_INITIAL_SOLUTION', initial_failure=str(exc),
                    runtime=time.perf_counter() - started)
        return (None, None, info) if return_info else (None, None)
    best, best_audit = deepcopy(current), current_audit
    initial = deepcopy(current)
    info.update(seed=seed, requires_gurobi=False, initial_solution_source=source,
                initial_objective=current_audit['objective'], initial_state=initial,
                initialization_seconds=time.perf_counter() - started,
                iterations=0, accepted_moves=0, improving_moves=0,
                capacity_blocked_candidates=0, feasible_relay_repairs=0,
                full_audits=1, relay_audits=0, split_repair_audits=0,
                feasible_split_repairs=0, pooled_pickup_attempts=0,
                multi_receiver_audits=0, multi_receiver_feasible=0,
                joint_single_receiver_audits=0, delivery_move_attempts=0,
                delivery_relay_repairs=0, e1_move_attempts=0, search_history=[])
    operators = ['nearby_pickup_group', 'expensive_pickups', 'random_pickups',
                 'undo_handoff', 'pooled_pickups_from_multiple_vehicles'] + DELIVERY_OPERATORS + ['e1_relocate', 'e1_swap']
    weights, uses, scores = [1.0]*len(operators), [0]*len(operators), [0.0]*len(operators)
    for iteration in range(max_iterations):
        if time.perf_counter() >= deadline:
            break
        op = rng.choices(range(len(operators)), weights=weights)[0]
        name = operators[op]
        local_deadline = min(deadline, time.perf_counter() + repair_time_limit)
        if op < 5:
            candidate, meta = pickup_move(e, current, current_audit, rng, op, deadline, info,
                repair_time_limit, max_remove, insertion_beam, max_handoff_events,
                max_handoff_receivers, handoff_receiver_candidates, True, allow_transfers)
        elif name in DELIVERY_OPERATORS:
            candidate, meta = delivery_move(e, current, rng, name, local_deadline,
                current_audit['objective'], allow_transfers, insertion_beam,
                max_handoff_events, max_handoff_receivers, handoff_receiver_candidates, info)
        else:
            info['e1_move_attempts'] += 1
            candidate, meta = e1_move(e, current, rng, name, local_deadline, max(6, insertion_beam))
        reward = 0.0
        if candidate:
            state, audit = candidate
            delta = audit['objective'] - current_audit['objective']
            progress = min(1.0, (time.perf_counter() - started) / max_time)
            temperature = max(1e-9, .001 * info['initial_objective'] * .01**progress)
            if delta <= TOL or rng.random() < math.exp(-delta/temperature):
                current, current_audit = state, audit
                info['accepted_moves'] += 1
                reward = 8 if delta < -TOL else 2
            if audit['objective'] < best_audit['objective'] - TOL:
                best, best_audit = deepcopy(state), audit
                info['improving_moves'] += 1
                reward = 25
                info['search_history'].append({'iteration': iteration+1, 'operator': name,
                    'objective': audit['objective'], 'move': meta,
                    'transfers': deepcopy(state['transfers']), 'elapsed': time.perf_counter()-started})
        uses[op] += 1
        scores[op] += reward
        info['iterations'] = iteration+1
        if (iteration+1) % 50 == 0:
            for k in range(len(operators)):
                if uses[k]:
                    weights[k] = max(.2, .8*weights[k]+.2*scores[k]/uses[k])
                uses[k], scores[k] = 0, 0.0
        if (iteration+1) % 200 == 0:
            current, current_audit = deepcopy(best), best_audit
    record_solution(info, best, e.audit(best))
    info.update(status='FEASIBLE', solution_type='feasible_not_proven',
                runtime=time.perf_counter()-started, destroy_operators=operators,
                destroy_weights=weights, total_improvement=info['initial_objective']-info['objective'],
                e1_routes_changed=best['e1_routes'] != initial['e1_routes'],
                termination_reason='time_limit' if time.perf_counter() >= deadline else 'iteration_limit')
    if verbose:
        print(f"M8 joint: initial={info['initial_objective']:.6f}, best={info['objective']:.6f}, "
              f"handoffs={info['transshipment_count']}, iterations={info['iterations']}")
    routes = export_routes(best)
    return (*routes, info) if return_info else routes
