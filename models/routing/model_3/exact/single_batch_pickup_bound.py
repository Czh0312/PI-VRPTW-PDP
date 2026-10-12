"""Opt-in B1 M3/M4 mixed-task E2 LP lower bound, never an integer solver.

Each original nonempty E2 trip projects unchanged to the mixed-task pricing
domain. The route-cover LP preserves both task kinds and M4 shared warehouse
assignment. E1 synchronization, stocks, physical sequencing and cross-trip
use-time are relaxed ONLY here; the original complete MILP is unchanged.
"""
from collections import Counter
import heapq
import math
import time

from models.common.cycle.audit import audit_solution
from models.common.cycle.precheck import travel_lower_bounds
from models.routing.model_3.exact.domains import assignment_domains
from models.routing.model_3.exact.single_batch_columns import problem_fingerprint
from models.routing.model_3.exact.pickup_route_pricing import RoutePricer, evaluate_column, insertion_columns


def pickup_contexts(problem, travel=None):
    if problem.model_number not in (3,4) or len(problem.operating_policy.batches)!=1:
        raise ValueError('Mixed-task bound requires B1 M3/M4')
    p,f = problem.operating_policy,problem.fleet
    b = p.batches[0]
    travel = travel or travel_lower_bounds(problem)
    if problem.free_assignment:
        check = assignment_domains(problem,travel)
        if not check['passed']:
            return check['domains'],{},check
        domains = check['domains']
    else:
        # Fixed orders may share a customer location yet have different sites.
        # Only M4 uses customer assignment variables; never collapse M3 orders.
        domains = None
        check = None
    contexts = {}
    for site in problem.satellites:
        orders = [o for o in problem.orders if (site in domains[o.customer]
                    if problem.free_assignment else o.satellite==site)]
        if not orders:continue
        release = b.start+p.e1_load_time+travel['outward'][problem.depot][site]
        release += problem.service(site)+p.e2_dispatch_prep_time
        nodes = []
        for order in orders:
            delivered = max(order.delivery_window[0],release+travel['outward'][site][order.customer])
            picked = max(order.pickup_window[0],delivered+order.service_time+order.min_use_time)
            windows = {'d':(delivered,min(order.delivery_window[1],b.cutoff-order.service_time)),
                'p':(picked,min(order.pickup_window[1],b.cutoff-order.service_time
                    -travel['inward'][site][order.customer]-p.e2_return_unload_time))}
            for kind in ('d','p'):
                _,weight,volume = problem.task(order.id,kind)
                nodes.append(dict(key=(order.id,kind),order=order,weight=weight,volume=volume,window=windows[kind]))
        contexts[site] = dict(satellite=site,release={b.id:release},nodes=nodes,
            lookup={n['key']:n for n in nodes},
            maximum_trips=min(len(nodes),f.e2_per_sat_count*p.e2_max_trips_per_vehicle))
    return domains,contexts,check


def e1_component_floor(problem, domains=None):
    """Independent E1-only bound; customer vertices are never E1 detours."""
    if not problem.orders:return 0.0
    if not problem.free_assignment:
        from models.routing.model_3.exact.distance_bounds import _visit_bound
        active = sorted({o.satellite for o in problem.orders})
        value = _visit_bound(problem,problem.depot,[(s,k) for s in active for k in ('d','p')],True)
    else:
        locations = (problem.depot,)+problem.satellites
        def shortest(reverse):
            distances = {s:math.inf for s in locations};distances[problem.depot] = 0.0
            heap = [(0.0,problem.depot)]
            while heap:
                value,node = heapq.heappop(heap)
                if value!=distances[node]:continue
                for other in locations:
                    edge = problem.distance(other,node) if reverse else problem.distance(node,other)
                    candidate = value+edge
                    if candidate<distances[other]:
                        distances[other] = candidate;heapq.heappush(heap,(candidate,other))
            return distances
        outward,inward = shortest(False),shortest(True)
        value = max(min(outward[s]+inward[s] for s in choices) for choices in domains.values())
    return max(0.0,value-1e-7*max(1.0,value))


def project_original_schedule(problem,result,contexts):
    """Prove an audited original schedule embeds without changing any arc."""
    audit = audit_solution(problem,result)
    if not audit['valid'] or not audit['periodic_replay']['valid']:
        raise ValueError('Projection requires a valid original integer schedule')
    columns,counts,coverage = [],Counter(),Counter()
    for trip in result['e2_trips']:
        site = trip['satellite_id']
        sequence = [(e['order_id'],e['kind']) for e in trip['events']]
        column = evaluate_column(problem,contexts[site],sequence)
        if column is None:raise RuntimeError('Original audited trip did not project')
        if any(t>e['start'] for t,e in zip(column['starts'],trip['events'])):
            raise RuntimeError('Relaxed earliest service exceeds original service')
        if column['return_complete']>trip['return_complete']:
            raise RuntimeError('Relaxed return exceeds original return')
        counts[site] += 1;coverage.update(sequence)
        columns.append((site,column))
    if coverage!=Counter((o.id,k) for o in problem.orders for k in ('d','p')):
        raise RuntimeError('Projection lost or duplicated a task')
    if any(counts[s]>c['maximum_trips'] for s,c in contexts.items()):
        raise RuntimeError('Aggregate trip bound excludes physical original trips')
    distance = math.fsum(c['cost'] for _,c in columns)
    if not math.isclose(distance,audit['distances']['e2'],rel_tol=1e-10,abs_tol=1e-7):
        raise RuntimeError('Projection changed original raw directed distance')
    return columns


class PickupCoverMaster:
    """A restricted LP; its objective alone never certifies a global bound."""
    def __init__(self,problem,domains,contexts):
        import gurobipy as gp
        self.problem,self.contexts = problem,contexts
        m = self.model = gp.Model('B1_M3_M4_mixed_task_bound')
        m.Params.OutputFlag = 0;m.Params.Threads = 1;m.Params.Method = 1;m.Params.DualReductions = 0
        self.phase_one = True
        self.cover = {};self.assignment = {};self.assignment_rows = {}
        if problem.free_assignment:
            for customer,sites in domains.items():
                # No explicit UB: sum-to-one and nonnegativity already imply it.
                for site in sites:self.assignment[customer,site] = m.addVar(name=f'z_{customer}_{site}')
                self.assignment_rows[customer] = m.addConstr(gp.quicksum(self.assignment[customer,s]
                    for s in sites)==1,name=f'assign_{customer}')
        self.artificial = []
        for site,ctx in contexts.items():
            for index,node in enumerate(ctx['nodes']):
                artificial = m.addVar(obj=1,name=f'art_{site}_{index}');self.artificial.append(artificial)
                rhs = self.assignment[node['order'].customer,site] if problem.free_assignment else 1
                self.cover[site,node['key']] = m.addConstr(artificial==rhs,name=f'cover_{site}_{index}')
        self.fleet = {s:m.addConstr(gp.LinExpr()<=ctx['maximum_trips'],name=f'trips_{s}') for s,ctx in contexts.items()}
        self.columns,self.variables,self.known = [],[],set()

    def add(self,site,column):
        import gurobipy as gp
        key = site,tuple(tuple(k) for k in column['tasks'])
        if key in self.known:return False
        replay = evaluate_column(self.problem,self.contexts[site],column['tasks'])
        if replay is None or not math.isclose(replay['cost'],column['cost'],abs_tol=1e-7):
            raise ValueError('Invalid mixed-task route column')
        rows = [self.cover[site,tuple(k)] for k in replay['tasks']]+[self.fleet[site]]
        var = self.model.addVar(obj=0 if self.phase_one else replay['cost'],
            column=gp.Column([1.0]*len(rows),rows),name=f'route_{len(self.variables)}')
        self.variables.append(var);self.columns.append((site,replay));self.known.add(key)
        return True

    def phase_two(self):
        for var in self.artificial:var.UB = 0;var.Obj = 0
        for var,(_,column) in zip(self.variables,self.columns):var.Obj = column['cost']
        self.phase_one = False

    def duals(self):
        prices = {s:({n['key']:self.cover[s,n['key']].Pi for n in c['nodes']},min(0.0,self.fleet[s].Pi))
            for s,c in self.contexts.items()}
        assignment = {c:row.Pi for c,row in self.assignment_rows.items()}
        return prices,assignment

    def dispose(self):self.model.dispose()


def balanced_order_prices(context, prices):
    """Discovery prices only; changing them invalidates old pricing bounds.

    Pair task IDs, not customer addresses: distinct orders may share a customer.
    The mean preserves each order's D/P price sum up to floating roundoff.
    """
    expected = set(context['lookup'])
    if set(prices) != expected:
        raise ValueError('Balanced discovery requires all original task prices')
    balanced = {}
    for order_id in dict.fromkeys(key[0] for key in context['lookup']):
        delivery, pickup = (order_id, 'd'), (order_id, 'p')
        if delivery not in expected or pickup not in expected:
            raise ValueError('Balanced discovery requires both D/P tasks')
        left, right = float(prices[delivery]), float(prices[pickup])
        if not math.isfinite(left) or not math.isfinite(right):
            raise ValueError('Balanced discovery prices must be finite')
        mean = left * 0.5 + right * 0.5
        balanced[delivery] = balanced[pickup] = mean
    return balanced


def supply_balanced_columns(problem, contexts, master, pricers, prices, *, deadline, call_cap):
    """Discover under balanced prices; admit columns under ORIGINAL prices.

    Balanced pricing bounds/status below are diagnostics, never an original
    certificate. Later certificates require fresh pricing under current original
    master prices. The route domain and shared customer assignment stay intact.
    """
    begun = time.perf_counter()
    history = dict(added=0, original_negative=0, returned=0, sites={},
                   corrected_e2_bound=None, proof_scope='discovery_only')
    for site, (pi, nu) in prices.items():
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            break
        balanced = balanced_order_prices(contexts[site], pi)
        record = pricers[site].price(balanced, nu, time_limit=min(call_cap, remaining),
                                     phase_one=False, stop_at_negative=False)
        item = dict(original_prices=[[list(k), v] for k, v in pi.items()],
                    balanced_prices=[[list(k), v] for k, v in balanced.items()],
                    fleet_price=nu, pricing={k: v for k, v in record.items()
                                            if k not in ('column', 'columns')}, columns=[])
        for column in record.get('columns', []):
            replay = evaluate_column(problem, contexts[site], column['tasks'])
            if replay is None or not math.isclose(replay['cost'], column['cost'], rel_tol=0, abs_tol=1e-7):
                raise ValueError('Balanced discovery returned an invalid route')
            reduced = replay['cost'] - math.fsum(pi[tuple(k)] for k in replay['tasks']) - nu
            eligible = reduced < -1e-7
            added = master.add(site, replay) if eligible else False
            item['columns'].append(dict(tasks=replay['tasks'], cost=replay['cost'],
                original_reduced_cost=reduced,
                balanced_reduced_cost=replay['cost']-math.fsum(balanced[tuple(k)] for k in replay['tasks'])-nu,
                original_negative=eligible, added=added))
            history['returned'] += 1
            history['original_negative'] += int(eligible)
            history['added'] += int(added)
        history['sites'][site] = item
    history['runtime'] = time.perf_counter() - begun
    return history


def root_pickup_route_bound(problem,time_limit=20,*,seed=42,initial_result=None,pricing_time_limit=1.0,
                            heuristic_columns=False,e1_route_cover=False,balanced_columns=False):
    """Valid interrupted LP bound, optional research entry, no solver defaults."""
    if not math.isfinite(time_limit) or time_limit<=0 or not math.isfinite(pricing_time_limit) or pricing_time_limit<=0:
        raise ValueError('Mixed-task budgets must be finite and positive')
    if not isinstance(heuristic_columns,bool):
        raise ValueError('heuristic_columns must be boolean')
    if not isinstance(e1_route_cover,bool):
        raise ValueError('e1_route_cover must be boolean')
    if not isinstance(balanced_columns,bool):
        raise ValueError('Balanced columns must be boolean')
    if balanced_columns and problem.model_number != 4:
        raise ValueError('Balanced columns currently require M4')
    if balanced_columns and heuristic_columns:
        raise ValueError('Choose one discovery strategy per experiment')
    if e1_route_cover and problem.model_number!=3:
        raise ValueError('E1 route cover requires M3')
    begun = time.perf_counter();deadline = begun+time_limit
    domains,contexts,check = pickup_contexts(problem)
    output = dict(proof_scope='B1_M3_M4_all_task_route_relaxation',problem_sha256=problem_fingerprint(problem),
        model=problem.model_number,status='TIME_LIMIT',proven_optimal=False,proven_infeasible=False,
        relaxation_complete=False,bound=0.0,e2_bound=0.0,e1_floor=0.0,history=[],iterations=0,
        heuristic_columns=heuristic_columns,heuristic_runtime=0.0,heuristic_history=[],
        balanced_columns=balanced_columns,balanced_runtime=0.0,balanced_history=[],
        all_task_count=2*len(problem.orders),physical_model_unchanged=True,
        relaxation='All tasks/shared assignment retained; E1 sync, stocks, serial vehicles and cross-trip use-time relaxed only for this lower bound')
    if check is not None and not check['passed']:
        output.update(status='NECESSARY_CONDITION_FAILURE',domain_errors=check['errors'],runtime=time.perf_counter()-begun)
        return output
    output['e1_floor'] = e1_component_floor(problem,domains);output['bound'] = output['e1_floor']
    if e1_route_cover and time.perf_counter()<deadline:
        from models.routing.model_3.exact.single_batch_e1_bound import e1_route_cover_bound
        component=e1_route_cover_bound(problem,min(2.0,0.1*time_limit,deadline-time.perf_counter()),seed=seed)
        output['e1_route_cover']=component
        output['e1_floor']=max(output['e1_floor'],component['bound']);output['bound']=output['e1_floor']
    output['maximum_trips'] = {s:c['maximum_trips'] for s,c in contexts.items()}
    if not contexts:
        output.update(status='EMPTY',relaxation_complete=True,runtime=time.perf_counter()-begun);return output
    master,pricers = None,{}
    try:
        master = PickupCoverMaster(problem,domains,contexts)
        if initial_result is not None:
            for site,column in project_original_schedule(problem,initial_result,contexts):master.add(site,column)
        for site,ctx in contexts.items():
            for node in ctx['nodes']:
                column = evaluate_column(problem,ctx,[node['key']])
                if column is not None:master.add(site,column)
        for site,ctx in contexts.items():
            if time.perf_counter()>=deadline:raise TimeoutError('Mixed-task pricing build deadline')
            pricers[site] = RoutePricer(problem,ctx,deadline=deadline,seed=seed)
        consecutive_heuristics = 0
        consecutive_balanced = 0
        while time.perf_counter()<deadline:
            master.model.Params.TimeLimit = max(0.0,deadline-time.perf_counter());master.model.optimize()
            if master.model.Status!=2:
                output['status'] = 'MASTER_LIMIT';break
            if master.phase_one and master.model.ObjVal<=1e-7:
                master.phase_two();continue
            output['iterations'] += 1
            prices,assignment = master.duals()
            # A discovery pass may trigger reoptimization; the next pass must
            # price with original duals. All work consumes the same root clock.
            quota = 0.2*time_limit-output['balanced_runtime']
            if balanced_columns and not master.phase_one and consecutive_balanced == 0 and quota > 0:
                restricted = master.model.ObjVal
                discovery = supply_balanced_columns(problem,contexts,master,pricers,prices,
                    deadline=min(deadline,time.perf_counter()+quota),call_cap=min(1.0,pricing_time_limit))
                discovery.update(iteration=output['iterations'],restricted_objective=restricted)
                output['balanced_runtime'] += discovery['runtime']
                output['balanced_history'].append(discovery)
                if discovery['added']:
                    consecutive_balanced = 1
                    continue
            consecutive_balanced = 0
            # Cheap discovery supplies columns only: it never certifies absence
            # of a negative route or promotes the restricted objective. Charge
            # it to the same root budget; force exact pricing after two passes.
            quota = 0.1*time_limit-output['heuristic_runtime']
            if heuristic_columns and consecutive_heuristics<2 and quota>0:
                heuristic_begun = time.perf_counter()
                heuristic_deadline = min(deadline,heuristic_begun+quota)
                restricted = master.model.ObjVal
                added = 0
                for site,(pi,nu) in prices.items():
                    if time.perf_counter()>=heuristic_deadline:break
                    known = [c for s,c in master.columns if s==site]
                    columns = insertion_columns(problem,contexts[site],pi,known,
                        phase_one=master.phase_one,deadline=heuristic_deadline)
                    for column in columns:
                        reduced = (0.0 if master.phase_one else column['cost'])
                        reduced -= math.fsum(pi[tuple(k)] for k in column['tasks'])+nu
                        if reduced<-1e-7:added += master.add(site,column)
                elapsed = time.perf_counter()-heuristic_begun
                output['heuristic_runtime'] += elapsed
                output['heuristic_history'].append(dict(iteration=output['iterations'],
                    phase=1 if master.phase_one else 2,restricted_objective=restricted,
                    added=added,runtime=elapsed,corrected_e2_bound=None))
                if added:
                    consecutive_heuristics += 1
                    continue
            consecutive_heuristics = 0
            bounds = {s:-math.fsum(max(0.0,v) for v in pi.values())-nu for s,(pi,nu) in prices.items()}
            records,pending = {},[]
            for site,(pi,nu) in prices.items():
                remaining = deadline-time.perf_counter()
                if remaining<=0:break
                record = pricers[site].price(pi,nu,time_limit=min(pricing_time_limit,remaining),
                    phase_one=master.phase_one,stop_at_negative=False)
                records[site] = {k:v for k,v in record.items() if k not in ('column','columns')}
                bounds[site] = 0.0 if record['infeasible'] else record['lower_bound']
                for column in record.get('columns',[]):
                    if ((0.0 if master.phase_one else column['cost'])-math.fsum(pi[tuple(k)] for k in column['tasks'])-nu)<-1e-7:
                        pending.append((site,column))
            norm = math.fsum(abs(v) for pi,nu in prices.values() for v in list(pi.values())+[nu])
            norm += math.fsum(abs(v) for v in assignment.values())
            restricted = master.model.ObjVal
            margin = 1e-7*max(1.0,abs(restricted),norm)*(1+sum(c['maximum_trips'] for c in contexts.values()))
            certified = restricted+math.fsum(contexts[s]['maximum_trips']*min(0.0,bounds[s]) for s in contexts)-margin
            complete = len(records)==len(contexts) and all(r['optimal'] or r['infeasible'] for r in records.values())
            closed = complete and all(r['infeasible'] or (r['reduced_cost'] is not None and r['reduced_cost']>=-1e-7)
                for r in records.values())
            history = dict(iteration=output['iterations'],phase=1 if master.phase_one else 2,
                restricted_objective=restricted,corrected_e2_bound=certified,margin=margin,
                prices={s:dict(tasks=[[list(k),v] for k,v in pi.items()],fleet=nu) for s,(pi,nu) in prices.items()},
                assignment_duals=assignment,pricing=records,pricing_lower_bounds=bounds,pricing_complete=complete)
            if not master.phase_one:
                output['e2_bound'] = max(output['e2_bound'],certified,0.0)
                output['bound'] = output['e1_floor']+output['e2_bound']
            added = sum(master.add(s,c) for s,c in pending);history['added'] = added;output['history'].append(history)
            if closed:
                output.update(status='LP_OPTIMAL' if not master.phase_one else 'PHASE_ONE_CLOSED',
                    relaxation_complete=not master.phase_one,restricted_e2_objective=restricted)
                break
            if not added:
                output['status'] = 'PRICING_LIMIT' if not complete else 'NUMERICAL_STALL';break
        output['columns'] = len(master.columns)
    except TimeoutError as error:
        output.update(status='TIME_LIMIT_BUILD',limitation=str(error))
    finally:
        for pricer in pricers.values():pricer.dispose()
        if master is not None:master.dispose()
    output['runtime'] = time.perf_counter()-begun
    return output
