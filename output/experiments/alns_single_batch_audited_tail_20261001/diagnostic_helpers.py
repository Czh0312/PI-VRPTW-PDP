"""Prototype: keep a route suffix exchange only after full feasible improvement."""
import time

from models.routing.common.cycle.assignment import assigned_problem
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.alns.neighborhoods import clone_state, state_from_result
from models.routing.solvers.alns.single_batch_cache import SingleBatchCache


def _tail_route_cost(problem, site, orders):
    if not orders:
        return 0.0
    f, p = problem.fleet, problem.operating_policy
    if sum(o.goods_weight+o.count*f.container_weight for o in orders) > f.e2_weight_cap:
        return None
    if sum(o.count*f.container_volume for o in orders) > f.e2_volume_cap:
        return None
    current, previous, distance = p.cycle_start+p.e2_dispatch_prep_time, site, 0.0
    for order in orders:
        current = max(current+problem.travel(previous, order.customer), order.delivery_window[0])
        if current > min(order.delivery_window[1], problem.batch_map[order.batch].cutoff-order.service_time):
            return None
        current += order.service_time
        distance += problem.distance(previous, order.customer)
        previous = order.customer
    if current+problem.travel(previous, site)+p.e2_return_unload_time+p.e2_turnaround_time > p.cycle_end:
        return None
    return distance+problem.distance(previous, site)


def _tail_options(problem, current, state, deadline):
    customer_orders = {}
    for order in problem.orders:
        customer_orders.setdefault(order.customer,set()).add(order.id)
    routes = []
    for index, route in enumerate(state['e2']):
        if not route['tasks'] or any(kind != 'd' for _,kind in route['tasks']):
            continue
        site = route['satellite']
        orders = tuple(problem.order_map[oid] for oid,_ in route['tasks'])
        if any(current[o.customer] != site for o in orders):
            continue
        distance = _tail_route_cost(problem,site,orders)
        if distance is None:
            continue
        cuts = []
        for cut in range(len(orders)+1):
            suffix = orders[cut:];members = {o.customer for o in suffix};ids = {o.id for o in suffix}
            if all(customer_orders[c] <= ids for c in members):
                cuts.append((cut,members))
        routes.append((index,site,orders,distance,cuts))
    options = []
    for index, (ia,sa,a,da,cuts_a) in enumerate(routes):
        for ib,sb,b,db,cuts_b in routes[index+1:]:
            if time.perf_counter() >= deadline:
                return None
            if sa == sb:
                continue
            for ca,ma in cuts_a:
                for cb,mb in cuts_b:
                    if len(ma)+len(mb) < 2:
                        continue
                    cost_a = _tail_route_cost(problem,sa,a[:ca]+b[cb:])
                    if cost_a is None:
                        continue
                    cost_b = _tail_route_cost(problem,sb,b[:cb]+a[ca:])
                    if cost_b is None:
                        continue
                    delta = cost_a+cost_b-da-db
                    if delta < -1e-9:
                        options.append((delta,ia,ib,ca,cb,ma,mb))
    return sorted(options,key=lambda row:row[0])


def _tail_state(problem, current, original, option):
    _,ia,ib,ca,cb,ma,mb = option
    state = clone_state(original)
    a,b = state['e2'][ia],state['e2'][ib]
    assignment = dict(current)
    assignment.update((c,b['satellite']) for c in ma)
    assignment.update((c,a['satellite']) for c in mb)
    a['tasks'],b['tasks'] = a['tasks'][:ca]+b['tasks'][cb:],b['tasks'][:cb]+a['tasks'][ca:]
    state['e2'] = [r for r in state['e2'] if r['tasks']]
    fixed = assigned_problem(problem,assignment)
    # Both destinations already have a route; no new site needs inserting.
    for batch,rows in state['deliveries'].items():
        for row in rows:
            row[:] = [s for s in row if fixed.demands[s,batch]]
    return assignment,fixed,state


def _audited_tail_seed(problem, current, incumbent, deadline, statistics):
    statistics.update(geometric_options=0,decoded_candidates=0,audited_candidates=0,
                      improving_candidates=0,timed_out=0)
    if time.perf_counter() >= deadline:
        statistics['timed_out'] = 1
        return None
    original = state_from_result(problem,incumbent)
    options = _tail_options(problem,current,original,deadline)
    if options is None:
        statistics['timed_out'] = 1
        return None
    statistics['geometric_options'] = len(options)
    best, best_cost = None,incumbent['info']['objective']
    for option in options[:3]:
        if time.perf_counter() >= deadline:
            statistics['timed_out'] = 1
            break
        assignment,fixed,state = _tail_state(problem,current,original,option)
        context = SingleBatchCache(fixed)
        score,result,issues = context(state)
        statistics['decoded_candidates'] += 1
        if issues or score >= best_cost-1e-9:
            continue
        audit = audit_solution(fixed,result)
        statistics['audited_candidates'] += 1
        if not audit['valid']:
            continue
        statistics['improving_candidates'] += 1
        best_cost = score
        result['audit'] = audit
        best = dict(assignment=assignment,fixed=fixed,context=context,result=result,
                    geometry_delta=option[0],gain=incumbent['info']['objective']-score)
    return best
