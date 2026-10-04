"""Experimental, certified route-partition lower bound for single-batch M1/M2.

E1 delivery tours are enumerated completely (few warehouses), with fractional
customer-to-tour cargo allocation. Elementary E2 delivery trips are generated
by pricing. Actual E1/E2 synchronization and E2 physical vehicle sequencing
are RELAXED. This is a lower-bound algorithm, not branch-and-price and not an
integer solver for a restricted route pool. Production defaults are unchanged.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import hashlib
import json
import math
import time

from models.routing.common.cycle.audit import audit_solution
from models.routing.common.cycle.precheck import check_necessary_conditions, travel_lower_bounds
from .bounds import distance_lower_bound, free_assignment_lower_bound
from .domains import assignment_domains
from .single_batch_route_pricing import (delivery_context, evaluate_delivery_column,
    insertion_columns, price_delivery_routes)
from .single_batch_time_budget import reduced_route_cost, route_work, time_deadlines, work_penalty


def problem_fingerprint(problem):
    """Bind an experimental certificate to the actual model, data and ticks."""
    payload = dict(model=problem.model_number, depot=problem.depot, orders=[vars(o) for o in problem.orders],
        policy=vars(problem.operating_policy), fleet={k: getattr(problem.fleet, k) for k in (
            'e1_count', 'e2_per_sat_count', 'e1_weight_cap', 'e1_volume_cap', 'e2_weight_cap',
            'e2_volume_cap', 'container_weight', 'container_volume')},
        satellites=[(s, problem.service(s)) for s in problem.satellites],
        arcs=[(a, b, problem.travel(a, b), problem.distance(a, b))
              for a in problem.network.nodes for b in problem.network.nodes])
    # Policy contains immutable Batch records.
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=lambda x: vars(x),
                                     separators=(',', ':')).encode()).hexdigest()


def apply_route_bound(model, problem, certificate):
    """Add the certified objective floor only through the explicit opt-in hook."""
    if certificate.get('proof_scope') != 'B1_delivery_route_relaxation' or problem.model_number not in (1, 2):
        raise ValueError('Wrong route certificate scope')
    if certificate.get('problem_sha256') != problem_fingerprint(problem):
        raise ValueError('Route certificate does not match this problem')
    bound = certificate.get('bound')
    if isinstance(bound, bool) or not isinstance(bound, (int, float)) or not math.isfinite(bound) or bound < 0:
        raise ValueError('Route certificate bound must be finite and nonnegative')
    if bound > 0:
        model.addConstr(model.getObjective() >= bound, name='B1_certified_route_distance_floor')
    return float(bound)


def e1_delivery_tours(problem, domains, travel, *, deadline=None, tour_limit=10000):
    """Complete ordered subsets, with earliest E1 times and raw directed cost.

    An infeasible direct return does not prune a prefix. There may be a cheap
    or faster return via another visited warehouse in a nonmetric matrix.
    """
    p, f, batch = problem.operating_policy, problem.fleet, problem.operating_policy.batches[0]
    sites = sorted({s for choices in domains.values() for s in choices})
    groups = defaultdict(list)
    for o in problem.orders:
        groups[o.customer].append(o)
    tours = []

    def extend(sequence, clock, costs, arrivals):
        if deadline is not None and time.perf_counter() >= deadline:
            raise TimeoutError('E1 tour enumeration reached its budget')
        previous = sequence[-1] if sequence else problem.depot
        if sequence:
            returned = clock+problem.travel(previous, problem.depot)+p.depot_return_unload_time
            if returned <= min(batch.end, p.cycle_end-p.e1_turnaround_time):
                permitted = {(c, s) for c, orders in groups.items() for s in sequence if s in domains[c]
                    and all(arrivals[s]+p.e2_dispatch_prep_time+travel['outward'][s][c]
                            <= min(o.delivery_window[1], batch.cutoff-o.service_time) for o in orders)}
                tours.append(dict(sites=sequence, cost=math.fsum(costs+[problem.distance(previous, problem.depot)]),
                                  supply=arrivals.copy(), permitted=permitted))
                if len(tours) > tour_limit:
                    raise TimeoutError('E1 tour limit: partial enumeration cannot certify the master')
        for site in sites:
            if site in sequence:
                continue
            supply = clock+problem.travel(previous, site)+problem.service(site)
            if supply <= batch.cutoff:
                extend(sequence+(site,), supply, costs+[problem.distance(previous, site)], {**arrivals, site: supply})
    extend((), batch.start+p.e1_load_time, [], {})
    return tours


class DeliveryMaster:
    """Restricted LP; its uncorrected value is NEVER a global lower bound."""

    def __init__(self, problem, domains, contexts, tours, *, deadline=None, verbose=False,
                 time_cuts=False, time_cut_limit=8):
        import gurobipy as gp
        self.problem, self.contexts = problem, contexts
        self.model = m = gp.Model('B1_delivery_route_bound')
        m.Params.OutputFlag = int(verbose)
        m.Params.Threads = 1
        m.Params.Method = 1
        self.variables, self.columns, self.known = [], [], set()
        self.time_rows = {s: {} for s in contexts}
        self.time_scale = max(1, problem.operating_policy.cycle_end-problem.operating_policy.cycle_start)
        self.phase_one = True
        try:
            def check():
                if deadline is not None and time.perf_counter() >= deadline:
                    raise TimeoutError('Column master construction reached its budget')
            groups = defaultdict(list)
            for o in problem.orders:
                groups[o.customer].append(o)
            weights = {c: math.fsum(o.goods_weight+o.count*problem.fleet.container_weight for o in orders)
                       for c, orders in groups.items()}
            volumes = {c: sum(o.count for o in orders)*problem.fleet.container_volume for c, orders in groups.items()}
            self.assignment = z = {(c, s): m.addVar(ub=1, name=f'z_{c}_{s}') for c, choices in domains.items() for s in choices}
            self.active = active = {s: m.addVar(ub=1, name=f'active_{s}') for s in contexts}
            self.alpha = alpha = [m.addVar(ub=1, name=f'e1_tour_{i}') for i in range(len(tours))]
            m.addConstr(gp.quicksum(alpha) <= problem.fleet.e1_count, name='e1_fleet')
            for c, choices in domains.items():
                m.addConstr(gp.quicksum(z[c, s] for s in choices) == 1)
                for s in choices:
                    m.addConstr(z[c, s] <= active[s])
            for s in contexts:
                m.addConstr(gp.quicksum(alpha[i] for i, t in enumerate(tours) if s in t['sites']) == active[s])
                m.addConstr(active[s] <= gp.quicksum(v for (c, site), v in z.items() if site == s))
                if time_cuts:
                    for b in time_deadlines(problem, contexts[s], time_cut_limit):
                        check()
                        available = gp.quicksum(problem.fleet.e2_per_sat_count*max(0,
                            b-max(problem.operating_policy.cycle_start, t['supply'][s]))/self.time_scale*alpha[i]
                            for i, t in enumerate(tours) if s in t['sites'])
                        self.time_rows[s][b] = m.addConstr(-available <= 0, name=f'e2_time_{s}_{b}')
            allocation = defaultdict(list)
            for i, tour in enumerate(tours):
                check()
                cargo = []
                for c, s in sorted(tour['permitted']):
                    variable = m.addVar(ub=1, name=f'cargo_{i}_{c}_{s}')
                    m.addConstr(variable <= alpha[i])
                    allocation[c, s].append(variable)
                    cargo.append((c, variable))
                m.addConstr(gp.quicksum(weights[c]*v for c, v in cargo)
                            <= (problem.fleet.e1_weight_cap+1e-6)*alpha[i])
                m.addConstr(gp.quicksum(volumes[c]*v for c, v in cargo)
                            <= (problem.fleet.e1_volume_cap+1e-6)*alpha[i])
            for key, variable in z.items():
                m.addConstr(gp.quicksum(allocation[key]) == variable)
            self.artificial, self.coverage, self.fleet = {}, {}, {}
            for site, context in contexts.items():
                check()
                self.fleet[site] = m.addConstr(-context['maximum_trips']*active[site] <= 0, name=f'e2_fleet_{site}')
                for node in context['nodes']:
                    key = site, node['key']
                    self.artificial[key] = a = m.addVar(ub=1, obj=1, name=f'artificial_{site}_{node["key"]}')
                    self.coverage[key] = m.addConstr(a == z[node['order'].customer, site])
            self.e1_costs = list(zip(alpha, (t['cost'] for t in tours)))
            m.update()
        except Exception:
            m.dispose()
            raise

    def add(self, site, column):
        import gurobipy as gp
        key = site, column['tasks']
        if key in self.known:
            return False
        rows = [self.coverage[site, k] for k in column['tasks']]+[self.fleet[site]]
        coefficients = [1.0]*len(rows)
        for b, row in self.time_rows[site].items():
            rows.append(row)
            coefficients.append(route_work(self.problem, site, column['tasks'], b)/self.time_scale)
        variable = self.model.addVar(obj=0 if self.phase_one else column['cost'],
            column=gp.Column(coefficients, rows), name=f'e2_column_{len(self.columns)}')
        self.variables.append(variable)
        self.columns.append((site, column))
        self.known.add(key)
        return True

    def phase_two(self):
        self.phase_one = False
        for a in self.artificial.values():
            a.UB, a.Obj = 0, 0
        for variable, cost in self.e1_costs:
            variable.Obj = cost
        for variable, (_, column) in zip(self.variables, self.columns):
            variable.Obj = column['cost']
        self.model.update()

    def prices(self, site):
        return ({n['key']: self.coverage[site, n['key']].Pi for n in self.contexts[site]['nodes']},
                min(0.0, self.fleet[site].Pi))

    def work_prices(self, site):
        return tuple((b, -min(0.0, row.Pi)/self.time_scale)
                     for b, row in self.time_rows[site].items() if row.Pi < 0)

    def dispose(self):
        self.model.dispose()


def corrected_master_bound(objective, pricing_bounds, maximum_trips, *, margin=0.0):
    """L = restricted LP dual value + sum_s K_s min(0, min reduced cost_s).

    This dual correction remains valid for incomplete pricing if its supplied
    values are LOWER bounds, not pricing incumbents or heuristic route costs.
    """
    value = objective+math.fsum(maximum_trips[s]*min(0.0, pricing_bounds[s]) for s in maximum_trips)-margin
    return max(0.0, value)


class _ShortCatalog(dict):
    def __init__(self, *, ordered):
        super().__init__()
        self.ordered = ordered


def short_route_catalog(problem, context, *, deadline=None, ordered=False):
    """Complete 1/2-task patterns for a stronger interrupted-pricing bound."""
    keys = list(context['lookup'])
    best = _ShortCatalog(ordered=ordered)
    for left in keys:
        if deadline is not None and time.perf_counter() >= deadline:
            raise TimeoutError('Short-route enumeration reached its budget')
        for sequence in [(left,)]+[(left, right) for right in keys if right != left]:
            c = evaluate_delivery_column(problem, context, sequence)
            if c is not None:
                key = tuple(sequence) if ordered else tuple(sorted(sequence))
                best[key] = min(best.get(key, math.inf), c['cost'])
    return best


def task_price_floor(prices, fleet_price, pricing_lower, short_catalog, *, phase_one=False,
                     work_prices=(), problem=None, site=None):
    """beta such that every route's reduced cost >= beta * task count.

    Enumerate sizes 1/2 completely. For size >= 3, a nonpositive certified
    pricing lower bound divided by 3 is safe. All covered tasks across ALL
    warehouses sum to the number of orders, a tighter limit than summing
    every warehouse's maximal trip count independently.
    """
    beta = min(0.0, pricing_lower)/3
    if work_prices and not getattr(short_catalog, 'ordered', False):
        raise ValueError('Time penalties require all ordered short routes, not cheapest task sets')
    for tasks, cost in short_catalog.items():
        penalty = work_penalty(problem, site, tasks, work_prices) if work_prices else 0
        reduced = (0.0 if phase_one else cost)+penalty-math.fsum(prices[k] for k in tasks)-min(0.0, fleet_price)
        beta = min(beta, reduced/len(tasks))
    margin = 1e-7*max(1.0, math.fsum(abs(v) for v in prices.values()), abs(fleet_price))
    if work_prices:
        margin = max(margin, 1e-7*math.fsum(v*max(1, problem.operating_policy.cycle_end-problem.operating_policy.cycle_start)
                                         for b, v in work_prices))
    return min(0.0, beta-margin)


def root_route_bound(problem, time_limit=30, seed=42, verbose=False, initial_result=None,
                     *, label_limit=200000, tour_limit=10000, pricing_time_limit=2.0,
                     pricing_method='label', _capture_pool=False, time_cuts=False, time_cut_limit=8):
    """Certified lower bound only. LP completion never means integer optimality."""
    from gurobipy import GRB
    if problem.model_number not in (1, 2) or len(problem.operating_policy.batches) != 1 or problem.reusable:
        raise ValueError('The delivery column prototype supports only single-batch M1/M2')
    if any(not math.isfinite(t) or t <= 0 for t in (time_limit, pricing_time_limit)):
        raise ValueError('Time budgets must be finite and positive')
    if label_limit < 1 or tour_limit < 1:
        raise ValueError('Enumeration limits must be positive')
    if pricing_method not in ('label', 'mip'):
        raise ValueError('Pricing method must be label or mip')
    if isinstance(time_cut_limit, bool) or not isinstance(time_cut_limit, int) or time_cut_limit < 2:
        raise ValueError('Time-cut limit must be an integer >= 2')
    if not problem.free_assignment:
        owners = defaultdict(set)
        for o in problem.orders:
            owners[o.customer].add(o.satellite)
        if any(len(sites) > 1 for sites in owners.values()):
            raise ValueError('The M1 prototype requires one input warehouse per customer')
    begun, master, pricers = time.perf_counter(), None, {}
    deadline = begun+time_limit
    travel = travel_lower_bounds(problem)
    domain_check = assignment_domains(problem, travel)
    precheck = domain_check if problem.free_assignment else check_necessary_conditions(problem, travel)
    domains = domain_check['domains'] if problem.free_assignment else {o.customer: [o.satellite] for o in problem.orders}
    baseline = (free_assignment_lower_bound(problem, domains) if problem.free_assignment else distance_lower_bound(problem))
    output = dict(status='TIME_LIMIT', bound=baseline, baseline_bound=baseline,
                  proven_optimal=False, proven_infeasible=False, relaxation_complete=False,
                  proof_scope='B1_delivery_route_relaxation',
                  relaxed_constraints=['actual_E1_E2_supply_synchronization', 'physical_E2_vehicle_trip_scheduling'],
                  iterations=0, columns=0, pricing_runtime=0.0, generated_labels=0,
                  master_runtime=0.0, history=[], seed=int(seed))
    output['pricing_method'] = pricing_method
    output['time_cuts'] = bool(time_cuts)
    output['problem_sha256'] = problem_fingerprint(problem)
    if not precheck['passed']:
        output.update(status='INFEASIBLE_PRECHECK', proven_infeasible=True,
                      runtime=time.perf_counter()-begun)
        return output
    if not problem.orders:
        output.update(status='LP_OPTIMAL', bound=0.0, relaxation_complete=True,
                      runtime=time.perf_counter()-begun)
        return output
    try:
        contexts = {s: delivery_context(problem, s, domains, domain_check['windows'], travel)
                    for s in problem.satellites if any(s in choices for choices in domains.values())}
        tours = e1_delivery_tours(problem, domains, travel, deadline=deadline, tour_limit=tour_limit)
        output['e1_tours'] = len(tours)
        master = DeliveryMaster(problem, domains, contexts, tours, deadline=deadline, verbose=verbose,
            time_cuts=time_cuts, time_cut_limit=time_cut_limit)
        output['time_rows'] = sum(map(len, master.time_rows.values()))
        for s, context in contexts.items():
            for node in context['nodes']:
                c = evaluate_delivery_column(problem, context, (node['key'],))
                if c:
                    master.add(s, c)
        if initial_result is not None and audit_solution(problem, initial_result)['valid']:
            for trip in initial_result['e2_trips']:
                s = trip['satellite_id']
                if s not in contexts:
                    continue
                c = evaluate_delivery_column(problem, contexts[s], [e['order_id'] for e in trip['events']])
                if c:
                    master.add(s, c)
        output['build_runtime'] = time.perf_counter()-begun
        maximum = {s: c['maximum_trips'] for s, c in contexts.items()}
        short_catalogs = {s: short_route_catalog(problem, c, deadline=deadline, ordered=time_cuts) for s, c in contexts.items()}
        additions = 0
        while time.perf_counter() < deadline:
            m = master.model
            m.Params.TimeLimit = max(0.0, deadline-time.perf_counter())
            m.optimize()
            output['master_runtime'] += m.Runtime
            if m.Status != GRB.OPTIMAL:
                output['status'] = 'INFEASIBLE_E1_RELAXATION' if m.Status == GRB.INFEASIBLE else 'TIME_LIMIT_MASTER'
                output['proven_infeasible'] = m.Status == GRB.INFEASIBLE
                break
            output['iterations'] += 1
            if master.phase_one and m.ObjVal <= 1e-8:
                master.phase_two()
                continue
            restricted = m.ObjVal
            round_prices = {s: master.prices(s) for s in contexts}
            round_work = {s: master.work_prices(s) for s in contexts}
            # Before any additions, freeze all duals from this solved LP.
            additions = 0
            for s, context in contexts.items():
                prices, fleet = round_prices[s]
                known = [c for site, c in master.columns if site == s]
                candidates = insertion_columns(problem, context, prices, known, phase_one=master.phase_one,
                    deadline=min(deadline, time.perf_counter()+.05), work_prices=round_work[s])
                for c in candidates:
                    rc = reduced_route_cost(problem, s, c, prices, fleet,
                        phase_one=master.phase_one, work_prices=round_work[s])
                    if rc < -1e-7:
                        additions += master.add(s, c)
            if additions:
                output['history'].append(dict(iteration=output['iterations'], phase=1 if master.phase_one else 2,
                                              restricted_objective=restricted, discovery='insertion', added=additions))
                continue
            pricing_bounds, records, pending = {}, {}, []
            for s, context in contexts.items():
                prices, fleet = round_prices[s]
                # Even unvisited pricing subproblems retain a safe analytic bound.
                pricing_bounds[s] = -math.fsum(max(0.0, v) for v in prices.values())-fleet
            for s, context in contexts.items():
                if time.perf_counter() >= deadline:
                    break
                prices, fleet = round_prices[s]
                pricing_deadline = min(deadline, time.perf_counter()+pricing_time_limit)
                if pricing_method == 'label':
                    record = price_delivery_routes(problem, context, prices, fleet,
                        phase_one=master.phase_one, label_limit=label_limit,
                        deadline=pricing_deadline, stop_after_columns=True, work_prices=round_work[s])
                else:
                    from .single_batch_delivery_pricer import DeliveryMipPricer
                    if s not in pricers:
                        pricers[s] = DeliveryMipPricer(problem, context, seed=seed, deadline=pricing_deadline)
                    record = pricers[s].price(prices, fleet, phase_one=master.phase_one,
                        deadline=pricing_deadline, work_prices=round_work[s])
                records[s] = {k: v for k, v in record.items() if k != 'columns'}
                pricing_bounds[s] = 0.0 if record['infeasible'] else record['lower_bound']
                output['pricing_runtime'] += record['runtime']
                output['generated_labels'] += record['generated_labels']
                pending.extend((s, c) for c in record['columns'])
            norm = math.fsum(abs(v) for prices, fleet in round_prices.values() for v in list(prices.values())+[fleet])
            norm += math.fsum(v*master.time_scale for pairs in round_work.values() for b, v in pairs)
            margin = 1e-7*max(1.0, abs(restricted), norm)*(1+sum(maximum.values()))
            certified = corrected_master_bound(restricted, pricing_bounds, maximum, margin=margin)
            task_floors = {s: task_price_floor(*round_prices[s], pricing_bounds[s], short_catalogs[s],
                phase_one=master.phase_one, problem=problem, site=s, work_prices=round_work[s]) for s in contexts}
            task_correction = math.fsum(min(task_floors[s] for s in domains[o.customer]) for o in problem.orders)
            certified = max(certified, max(0.0, restricted+task_correction-margin))
            complete = len(records) == len(contexts) and all(r['complete'] for r in records.values())
            closed = complete and all(r['infeasible'] or r['minimum_reduced_cost'] >= -1e-7 for r in records.values())
            row = dict(iteration=output['iterations'], phase=1 if master.phase_one else 2,
                       restricted_objective=restricted, corrected_bound=certified,
                       pricing_complete=complete, pricing=records, margin=margin,
                       task_price_floors=task_floors, time_prices=round_work)
            if not master.phase_one:
                output['bound'] = max(output['bound'], certified)
            elif certified > 1e-6:
                output.update(status='INFEASIBLE_PHASE_ONE', proven_infeasible=True)
                output['history'].append(row)
                break
            additions = sum(master.add(s, c) for s, c in pending)
            row['added'] = additions
            output['history'].append(row)
            if closed:
                output.update(status='LP_OPTIMAL' if not master.phase_one else 'NUMERICAL_PHASE_ONE_STALL',
                    relaxation_complete=not master.phase_one,
                    restricted_objective=restricted)
                break
            if not additions:
                output['status'] = 'PRICING_LIMIT' if complete is False else 'NUMERICAL_PRICING_STALL'
                break
        master.model.update()
        output.update(columns=len(master.columns), variables=master.model.NumVars,
                      constraints=master.model.NumConstrs)
    except TimeoutError as error:
        output.update(status='TIME_LIMIT_BUILD', limitation=str(error))
    finally:
        for pricer in pricers.values():
            pricer.dispose()
        if master is not None:
            if _capture_pool and not master.phase_one and not output['proven_infeasible']:
                # Private, in-memory handoff. Never serialize this as a proof.
                output['_route_pool'] = dict(domains=domains, contexts=contexts,
                    tours=tours, columns=list(master.columns))
            master.dispose()
    output['runtime'] = time.perf_counter()-begun
    return output


def solve_with_route_bound(problem, time_limit=60, *, root_time=10, seed=42,
                           verbose=False, initial_result=None, pool_time=0.0, **root_options):
    """Opt-in hybrid: root certificate + unchanged complete integer solver.

    All stages share ONE total wall-clock budget. Optional pool_time is an
    additional primal-stage cap after root_time; only audited schedules from
    the restricted pool, never its bound or proof flags, are transferred.
    """
    if not math.isfinite(time_limit) or time_limit <= 0 or not math.isfinite(root_time) or root_time <= 0:
        raise ValueError('Hybrid budgets must be finite and positive')
    if not math.isfinite(pool_time) or pool_time < 0:
        raise ValueError('Pool budget must be finite and nonnegative')
    begun = time.perf_counter()
    certificate = root_route_bound(problem, min(root_time, time_limit), seed, verbose,
        initial_result=initial_result, _capture_pool=pool_time > 0, **root_options)
    pool = certificate.pop('_route_pool', None)
    warm = initial_result
    pool_stats = None
    if pool is not None and pool_time > 0 and time.perf_counter() < begun+time_limit:
        from .single_batch_route_pool import route_pool_incumbent
        candidate, pool_stats = route_pool_incumbent(problem, pool, initial_result=initial_result,
            deadline=min(begun+time_limit, time.perf_counter()+pool_time), seed=seed)
        if candidate is not None:
            warm = candidate
    remaining = time_limit-(time.perf_counter()-begun)
    from models.routing.common.cycle.state import empty_result
    if remaining > 0 and not certificate['proven_infeasible']:
        from .solver import solve_exact
        # Supplied start avoids changing the primal strategy in the experiment.
        result = solve_exact(problem, time_limit=remaining, seed=seed, verbose=verbose,
            initial_result=warm, _single_batch_route_certificate=certificate)
    else:
        result = deepcopy(warm) if warm is not None and audit_solution(problem, warm)['valid'] else empty_result(problem, 'exact')
        result['info'].update(status='TIME_LIMIT_ROOT_COLUMNS', proven_optimal=False,
                              proven_infeasible=False, best_bound=None, mip_gap=None,
                              proof_scope=certificate['proof_scope'])
    info = result['info']
    info['route_bound_certificate'] = certificate
    if pool_stats is not None:
        info['route_pool_primal'] = pool_stats
    info['runtime'] = time.perf_counter()-begun
    if certificate['proven_infeasible']:
        if result.get('audit', {}).get('valid'):
            raise RuntimeError('Route relaxation infeasibility conflicts with an audited incumbent')
        info.update(status='INFEASIBLE', proven_infeasible=True, proof_scope=certificate['proof_scope'])
    elif not info.get('proven_infeasible'):
        bound = max(certificate['bound'], info.get('best_bound') or 0)
        objective = info.get('objective')
        if objective is not None and bound > objective+1e-5:
            raise RuntimeError('Certified route bound exceeds audited feasible objective')
        info.update(best_bound=bound, best_bound_source='route_certificate_and_full_MILP',
                    mip_gap=max(0.0, objective-bound)/max(1e-9, abs(objective)) if objective is not None else None)
    return result
