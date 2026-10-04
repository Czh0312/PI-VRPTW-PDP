"""Solver-independent customer assignment validation and result conversion."""
from copy import copy, deepcopy
from dataclasses import replace

from .instance import MultiBatchInstance


def validate_assignment(problem, assignment):
    customers = {o.customer for o in problem.orders}
    if not isinstance(assignment, dict) or set(assignment) != customers:
        raise ValueError('Assignment must cover exactly the customer IDs in the orders')
    if any(site not in problem.satellites for site in assignment.values()):
        raise ValueError('Assignment contains an unknown satellite')
    return dict(assignment)


def assigned_problem(problem, assignment):
    assignment = validate_assignment(problem, assignment)
    # Assignment changes only order ownership and its aggregates. Reusing the
    # validated, read-only network avoids O(|V|^2) matrix validation and Decimal
    # time conversion on every candidate and every independent replay.
    fixed = copy(problem)
    fixed.model_number = 3 if problem.reusable else 1
    fixed.free_assignment = False
    fixed.orders = tuple(replace(o, satellite=assignment[o.customer]) for o in problem.orders)
    fixed.order_map = {o.id: o for o in fixed.orders}
    fixed.demands = {key: 0 for key in problem.demands}
    fixed.weights = {key: 0.0 for key in problem.weights}
    for order in fixed.orders:
        key = order.satellite, order.batch
        fixed.demands[key] += order.count
        fixed.weights[key] += order.goods_weight+order.count*fixed.fleet.container_weight
    return fixed


def input_assignment(problem):
    result = {}
    for order in problem.orders:
        result.setdefault(order.customer, order.satellite)
    return result


def lift_result(problem, fixed_result, assignment):
    result = deepcopy(fixed_result)
    result['model'] = problem.model_number
    result['assignment'] = dict(assignment)
    result.pop('audit', None)
    if result['info'].get('objective') is not None:
        from .audit import audit_solution
        result['audit'] = audit_solution(problem, result)
        if not result['audit']['valid']:
            result['info'].update(status='AUDIT_FAILED', proven_optimal=False)
    return result
