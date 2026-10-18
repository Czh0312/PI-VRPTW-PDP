"""Certified disjoint-domain search when a complete joint MILP is too large.

Every open node represents a Cartesian product of customer domains. Splitting
one domain is exhaustive and disjoint; node MILP bounds cover every assignment
in that node. An unresolved or unvisited subtree always remains in the global
lower bound. This is exact branch-and-bound, not a restricted candidate list.
"""
import heapq
import math
import time

from models.common.cycle.assignment import assigned_problem, input_assignment, lift_result
from models.common.cycle.state import empty_result
from models.routing.model_4.exact.accelerators.distance_bounds import free_assignment_lower_bound
from models.routing.model_4.exact.accelerators.strengthening import joint_arc_estimate


def _forced_capacity_conflict(problem, domains):
    f = problem.fleet
    for site in problem.satellites:
        forced = [o for o in problem.orders if domains[o.customer] == [site]]
        if (sum(o.goods_weight+o.count*f.container_weight for o in forced) > f.e1_weight_cap+1e-6
                or sum(o.count*f.container_volume for o in forced) > f.e1_volume_cap+1e-6):
            return True
    return False


def solve_domain_tree(problem, time_limit, seed, verbose, travel, check, global_bound, arc_limit,
                      initial_result=None, initial_fixed_result=None):
    from models.routing.model_4.exact.search.engine import solve_exact as solve_node
    started = time.perf_counter()
    deadline = started+time_limit
    domains = check['domains']
    total = math.prod(len(sites) for sites in domains.values())
    best, best_cost = None, math.inf
    closed = infeasible_closed = visited = solved = created = pruned = 0
    records, frontier = [], []

    def push(options, bound, parent=None, depth=0):
        nonlocal created
        created += 1
        node = dict(id=created, parent=parent, depth=depth, domains=options,
                    size=math.prod(len(sites) for sites in options.values()),
                    bound=bound, solves=0, checked=False)
        heapq.heappush(frontier, (bound, -depth, created, node))

    def keep(candidate):
        nonlocal best, best_cost
        if candidate.get('audit', {}).get('valid') and candidate['info']['objective'] < best_cost:
            best, best_cost = candidate, candidate['info']['objective']

    def split(node):
        options = node['domains']
        customers = [c for c, sites in options.items() if len(sites) > 1]
        # Branch on a constrained customer, preferring domains with more sites.
        customer = min(customers, key=lambda c: (-len(options[c]), min(
            o.delivery_window[1] for o in problem.orders if o.customer == c), c))
        preferred = best.get('assignment', {}) if best else input_assignment(problem)
        sites = sorted(options[customer], key=lambda s: (s != preferred.get(customer),
                       problem.distance(s, customer)+problem.distance(customer, s), s))
        for site in sites:
            push({**options, customer: [site]}, node['bound'], node['id'], node['depth']+1)

    # A fixed partition can supply an upper bound only, never a tree certificate.
    if initial_result is not None:
        keep(initial_result)
    initial = input_assignment(problem)
    initial = {c: initial[c] if initial[c] in sites else sites[0] for c, sites in domains.items()}
    warm_budget = min(max(0, deadline-time.perf_counter()), time_limit*.2)
    if warm_budget > 0:
        warm = solve_node(assigned_problem(problem, initial), time_limit=warm_budget,
                          seed=seed, verbose=verbose, _travel_bounds=travel,
                          initial_result=initial_fixed_result if best is not None and best['assignment'] == initial else None)
        solved += 1
        if warm.get('audit', {}).get('valid'):
            keep(lift_result(problem, warm, initial))
    push({c: list(sites) for c, sites in domains.items()}, global_bound)
    while frontier and time.perf_counter() < deadline:
        _, _, _, node = heapq.heappop(frontier)
        if not node['checked']:
            node['checked'] = True
            if _forced_capacity_conflict(problem, node['domains']):
                closed += node['size']
                infeasible_closed += node['size']
                pruned += 1
                continue
            node['bound'] = max(node['bound'], free_assignment_lower_bound(problem, node['domains']))
        if node['bound'] >= best_cost:
            closed += node['size']
            pruned += 1
            continue
        local = {**check, 'domains': node['domains']}
        try:
            estimate = joint_arc_estimate(problem, local, deadline) if node['size'] > 1 else 0
        except TimeoutError:
            heapq.heappush(frontier, (node['bound'], -node['depth'], node['id'], node))
            break
        if estimate > arc_limit and node['size'] > 1:
            split(node)
            continue
        remaining = deadline-time.perf_counter()
        if remaining <= 0:
            heapq.heappush(frontier, (node['bound'], -node['depth'], node['id'], node))
            break
        budget = min(remaining, max(.05, remaining/min(4, len(frontier)+1)))
        compatible = best is not None and all(best['assignment'][c] in sites for c, sites in node['domains'].items())
        if node['size'] == 1:
            assignment = {c: sites[0] for c, sites in node['domains'].items()}
            # Root results have free-assignment metadata; do not pass them as a
            # fixed-model start. The fixed solver constructs its audited seed.
            trial = solve_node(assigned_problem(problem, assignment), time_limit=budget,
                               seed=seed, verbose=verbose, _travel_bounds=travel)
            if trial.get('audit', {}).get('valid'):
                trial = lift_result(problem, trial, assignment)
            visited += int(node['solves'] == 0)
        else:
            trial = solve_node(problem, time_limit=budget, seed=seed, verbose=verbose,
                               initial_result=best if compatible else None,
                               _travel_bounds=travel, _domain_check=local)
        solved += 1
        node['solves'] += 1
        info = trial['info']
        keep(trial)
        lower = info.get('best_bound')
        if lower is not None and math.isfinite(lower):
            node['bound'] = max(node['bound'], lower)
        infeasible = bool(info.get('proven_infeasible'))
        optimal = bool(info.get('proven_optimal') and trial.get('audit', {}).get('valid'))
        records.append(dict(node=node['id'], assignments=node['size'], status=info['status'],
                            bound=node['bound'], objective=info.get('objective'), optimal=optimal,
                            infeasible=infeasible, solves=node['solves']))
        # Keep bounded diagnostic history; complete coverage lives in frontier
        # cardinalities, not in a potentially exponential list of visited leaves.
        records = records[-128:]
        if infeasible or optimal or node['bound'] >= best_cost:
            closed += node['size']
            infeasible_closed += node['size'] if infeasible else 0
        elif node['size'] > 1:
            split(node)
        else:
            heapq.heappush(frontier, (node['bound'], -node['depth'], node['id'], node))
    # A final incumbent may close entire untouched subtrees without visiting
    # their individual assignments. Bound pruning never means infeasibility.
    open_nodes = []
    for _, _, _, node in frontier:
        if node['bound'] >= best_cost:
            closed += node['size']
            pruned += 1
        else:
            open_nodes.append(node)
    assert closed+sum(n['size'] for n in open_nodes) == total
    complete = not open_nodes
    bounds = [n['bound'] for n in open_nodes]+([best_cost] if best else [])
    lower = min(bounds) if bounds else None
    result = best if best is not None else empty_result(problem, 'exact')
    result['info'].update(status='OPTIMAL' if complete and best else 'INFEASIBLE' if complete else 'TIME_LIMIT',
        proven_optimal=bool(complete and best), proven_infeasible=bool(complete and not best),
        proof_scope='customer_assignment_domain_tree_integer_time_cycle',
        best_bound=lower, mip_gap=max(0, best_cost-lower)/max(1e-9, abs(best_cost)) if best and lower is not None else None,
        runtime=time.perf_counter()-started, assignments_closed=closed, assignments_visited=visited,
        domain_nodes_created=created, domain_nodes_pruned=pruned, domain_frontier_nodes=len(open_nodes),
        domain_frontier_assignments=sum(n['size'] for n in open_nodes),
        infeasible_assignments_closed=infeasible_closed, subproblem_solves=solved,
        domain_tree_complete=complete, subproblems=records, subproblem_history_limit=128)
    return result
