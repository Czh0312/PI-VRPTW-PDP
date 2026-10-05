"""Certified route-cover column generation LOWER BOUNDS, not a M5 solver.

Full M5 trips project to this relaxation. Each nonempty elementary E2 trip
covers individual delivery/pickup tasks. One delivery batch per trip and
weight/volume/time constraints remain; E1 synchronization, cleaning stocks,
physical vehicle sequencing and cross-trip delivery/pickup times are relaxed.
The master enforces exact task cover and the aggregate finite trip limit.

For arbitrary task multipliers pi, nonpositive fleet multiplier nu, and a
certified lower bound rho on min_r(c_r - a_r*pi - nu), every feasible route
cover costs at least sum(pi) + K*nu + K*min(0, rho). This remains valid when
pricing times out; the unfinished restricted master's objective is not a
lower bound. The integer pricer's bound (or an analytic fallback) supplies rho.
"""
from __future__ import annotations

from copy import deepcopy

import math
from pathlib import Path
import time

from models.routing.common.cycle.audit import audit_solution
from models.routing.common.cycle.precheck import travel_lower_bounds
from .multibatch_bounds import distance_certificate
from .multibatch_route_pricing import RoutePricer, evaluate_column, insertion_columns, route_context


def dual_certificate(duals, fleet_dual, maximum_trips, pricing_lower_bound):
    fleet_dual = min(0.0, float(fleet_dual))
    return math.fsum(duals.values())+maximum_trips*fleet_dual+maximum_trips*min(0.0, pricing_lower_bound)


def repair_category_duals(context, duals, fleet_dual, pricing):
    """Repair full-domain category bounds without multiplying each by K.

    Every delivery-batch route contains at least one delivery of that batch;
    every pickup-only route contains at least one pickup. Lowering the
    corresponding task prices by a category's negative pricing bound makes
    all its reduced costs nonnegative. A common fleet-price reduction can
    substitute for part of these corrections. The best breakpoint is exact
    for this one-dimensional piecewise-linear repair, not for full M5.
    """
    nodes = context['nodes']
    expected = {None}|{n['order'].batch for n in nodes if n['key'][1] == 'd'}
    reports = pricing['groups']
    if {g['category'] for g in reports} != expected or len(reports) != len(expected):
        raise ValueError('Dual repair requires all delivery-batch and pickup-only categories')
    if set(duals) != {n['key'] for n in nodes}:
        raise ValueError('Dual repair requires every task multiplier')
    nu, K = min(0.0, float(fleet_dual)), context['maximum_trips']
    bounds = {g['category']: float(g['lower_bound']) for g in reports if not g['infeasible']}
    if not all(math.isfinite(v) for v in list(duals.values())+[nu]+list(bounds.values())):
        raise ValueError('Dual repair requires finite multipliers and category bounds')
    candidates = {0.0}|{max(0.0, -rho) for rho in bounds.values()}
    best = None
    for shift in sorted(candidates):
        corrections = {category: max(0.0, -rho-shift) for category, rho in bounds.items()}
        repaired = {n['key']: float(duals[n['key']])-corrections.get(
            None if n['key'][1] == 'p' else n['order'].batch, 0.0) for n in nodes}
        value = math.fsum(repaired.values())+K*(nu-shift)
        if best is None or value > best['bound']:
            best = dict(bound=value, fleet_shift=shift, repaired_fleet_dual=nu-shift,
                category_shifts=[[category, value] for category, value in corrections.items()],
                repaired_duals=[[list(key), value] for key, value in repaired.items()])
    return best


def _site_route_cover(problem, context, *, deadline, initial_result=None, seed=42,
                     pricing_time_limit=2.0, verbose=False, pricing_export_path=None, heuristic_pricing=True,
                     pricing_backend='milp', enumeration_time_limit=15.0, enumeration_max_states=2000000,
                     integer_cover_time_limit=0.0, batch_pricing=False, dual_repair=False, delivery_count_cuts=False, subset_cuts=None,
                     reuse_pricing_budget=False, count_dual_repair=False, capacity_dual_repair=False):
    import gurobipy as gp
    from gurobipy import GRB
    begun = time.perf_counter()
    if (count_dual_repair or capacity_dual_repair) and not (delivery_count_cuts and dual_repair):
        raise ValueError('Count dual repair requires delivery-count cuts and dual repair')
    site, K = context['satellite'], context['maximum_trips']
    from .multibatch_capacity_cuts import capacity_floor, capacity_subsets
    subset_keys, subset_required = capacity_subsets(problem, context, subset_cuts)
    if integer_cover_time_limit > 0 and (delivery_count_cuts or subset_keys):
        raise ValueError('Capacity-cut pricing currently requires the full-domain MILP backend')
    if not context['nodes']:
        return dict(satellite=site, bound=0.0, status='EMPTY', lp_converged=True, iterations=[], columns=[], runtime=0.0)
    if time.perf_counter() >= deadline:
        return dict(satellite=site, bound=0.0, status='TIME_LIMIT_BUILD', lp_converged=False, iterations=[], columns=[], runtime=0.0)
    enumeration = None
    # Complete enumeration currently has no capacity rows. Keep exact MILP
    # pricing whenever cuts are requested instead of silently ignoring them.
    if pricing_backend == 'hybrid' and not delivery_count_cuts and not subset_keys:
        from .multibatch_route_enumeration import enumerate_route_universe, enumerated_route_cover, enumeration_stats
        universe = enumerate_route_universe(problem, context,
            deadline=min(deadline, time.perf_counter()+min(enumeration_time_limit, max(0, deadline-time.perf_counter())/3)),
            max_states=enumeration_max_states)
        enumeration = enumeration_stats(universe)
        if universe['complete']:
            result = enumerated_route_cover(problem, context, universe, deadline=deadline,
                verbose=verbose, export_path=pricing_export_path, integer_cover_time_limit=integer_cover_time_limit,
                initial_result=initial_result, seed=seed)
            result['runtime'] = time.perf_counter()-begun
            return result
        # An incomplete pool cannot certify min reduced cost. Retain the
        # full original MILP pricing domain, including unvisited categories.
        del universe
    m = gp.Model('M5_relaxed_E2_route_cover')
    m.Params.OutputFlag = int(verbose)
    m.Params.Threads = 1
    m.Params.Method = 1
    m.Params.DualReductions = 0
    rows = {node['key']: m.addConstr(gp.LinExpr() == 1, name=f'cover_{i}') for i, node in enumerate(context['nodes'])}
    fleet = m.addConstr(gp.LinExpr() <= K, name='aggregate_trip_limit')
    required, count_rows = {}, {}
    if delivery_count_cuts:
        for bid in context['release']:
            ns = [n for n in context['nodes'] if n['key'][1]=='d' and n['order'].batch==bid]
            if not ns:continue
            required[bid] = capacity_floor(problem, ns)
            count_rows[bid]=m.addConstr(gp.LinExpr()>=required[bid],name='minimum_delivery_routes_'+bid)
    subset_rows = {sid: m.addConstr(gp.LinExpr() >= rhs, name='subset_route_count_'+str(sid))
                   for sid, rhs in subset_required.items()}
    artificial = [m.addVar(obj=1, column=gp.Column([1], [row]), name=f'artificial_{i}') for i, row in enumerate(rows.values())]
    columns, variables, known = [], [], set()
    phase_one, best, best_witness = True, 0.0, None
    iterations, pricer, status, converged = [], None, 'TIME_LIMIT', False
    unresolved = 0
    def add(column):
        key = tuple(column['tasks'])
        if key in known:
            return False
        known.add(key)
        batches = {problem.order_map[oid].batch for oid,kind in key if kind=='d'}
        extra = [count_rows[bid] for bid in batches if bid in count_rows]
        extra += [row for sid,row in subset_rows.items() if subset_keys[sid].intersection(key)]
        variables.append(m.addVar(obj=0 if phase_one else column['cost'],
            column=gp.Column([1]*(len(key)+1+len(extra)), [rows[task] for task in key]+[fleet]+extra), name=f'route_{len(columns)}'))
        columns.append(column)
        return True
    try:
        # Audited whole-cycle trips guarantee phase-I feasibility without
        # imposing their vehicle assignment or departure times on pricing.
        if initial_result is not None:
            for trip in initial_result['e2_trips']:
                if trip['satellite_id'] == site:
                    column = evaluate_column(problem, context, [(e['order_id'], e['kind']) for e in trip['events']])
                    if column is None:
                        raise RuntimeError('A full-M5 audited trip did not project to the relaxation')
                    add(column)
        for node in context['nodes']:
            column = evaluate_column(problem, context, [node['key']])
            if column is not None:
                add(column)
        pricer = RoutePricer(problem, context, deadline=deadline, seed=seed, verbose=verbose, subset_keys=subset_keys)
        while True:
            while time.perf_counter() < deadline:
                m.Params.TimeLimit = max(0.0, deadline-time.perf_counter())
                m.optimize()
                if m.Status != GRB.OPTIMAL:
                    status = 'MASTER_TIME_LIMIT' if m.Status == GRB.TIME_LIMIT else f'MASTER_{m.Status}'
                    break
                if phase_one and m.ObjVal <= 1e-7:
                    phase_one = False
                    for variable in artificial:
                        variable.UB = 0
                        variable.Obj = 0
                    for variable, column in zip(variables, columns):
                        variable.Obj = column['cost']
                    continue
                duals = {key: row.Pi for key, row in rows.items()}
                nu = min(0.0, fleet.Pi)
                category_duals={bid:max(0.0,row.Pi) for bid,row in count_rows.items()}
                count_term=math.fsum(required[bid]*value for bid,value in category_duals.items())
                subset_duals={sid:max(0.0,row.Pi) for sid,row in subset_rows.items()}
                count_term += math.fsum(subset_required[sid]*value for sid,value in subset_duals.items())
                if heuristic_pricing:
                    found = insertion_columns(problem, context, duals, columns, phase_one=phase_one, deadline=deadline)
                    # nu <= 0 contributes a nonnegative constant to reduced cost.
                    added = sum(add(column) for column in found if (
                        (0.0 if phase_one else column['cost'])-math.fsum(duals[key] for key in column['tasks'])-nu-math.fsum(category_duals.get(bid,0) for bid in {problem.order_map[oid].batch for oid,kind in column['tasks'] if kind=='d'})-math.fsum(value for sid,value in subset_duals.items() if subset_keys[sid].intersection(column['tasks'])) < -1e-6))
                    if added:
                        unresolved = 0
                        iterations.append(dict(phase='feasibility' if phase_one else 'distance', method='insertion',
                                               master_objective=m.ObjVal, candidate_bound=None, columns_added=added))
                        continue
                remaining = deadline-time.perf_counter()
                if remaining <= 0:
                    break
                priced = pricer.price(duals, nu, time_limit=min(pricing_time_limit*(2**min(unresolved, 6)), remaining),
                                      phase_one=phase_one, stop_at_negative=not batch_pricing, category_duals=category_duals,
                                      subset_duals=subset_duals, reuse_budget=reuse_pricing_budget)
                lower = dual_certificate(duals, nu, K, priced['lower_bound'])+count_term if not phase_one else None
                repaired = None
                if dual_repair and not phase_one:
                    if capacity_dual_repair:
                        from .multibatch_capacity_dual_repair import repair_capacity_duals
                        repaired = repair_capacity_duals(context, duals, nu, priced,
                            {bid: (required[bid], value) for bid, value in category_duals.items()},
                            {sid: (subset_keys[sid], subset_required[sid], value)
                             for sid, value in subset_duals.items()}, deadline=deadline)
                    elif count_dual_repair:
                        from .multibatch_count_dual_repair import repair_count_duals
                        repaired = repair_count_duals(context, duals, nu, priced,
                            {bid: (required[bid], value) for bid, value in category_duals.items()})
                    else:
                        repaired = repair_category_duals(context, duals, nu, priced)
                if repaired is not None:repaired['bound'] += count_term
                standard_lower = lower
                if repaired is not None:
                    lower = max(lower, repaired['bound'])
                witness = dict(phase='feasibility' if phase_one else 'distance', method='integer_pricing',
                    master_objective=m.ObjVal, duals=[[list(key), value] for key, value in duals.items()],
                    fleet_dual=nu, maximum_trips=K, pricing={k: v for k, v in priced.items() if k not in ('column', 'columns')},
                    candidate_bound=lower, new_column=priced['column'])
                if delivery_count_cuts:witness['delivery_counts']=[[bid,required[bid],value] for bid,value in category_duals.items()]
                if subset_rows:witness['subset_counts']=[[sid,[list(k) for k in sorted(subset_keys[sid])],subset_required[sid],value] for sid,value in subset_duals.items()]
                if repaired is not None:
                    witness.update(standard_candidate_bound=standard_lower, dual_repair=repaired)
                if batch_pricing:
                    witness['new_columns'] = priced['columns']
                iterations.append(witness)
                if lower is not None and lower > best:
                    best, best_witness = lower, len(iterations)-1
                    if pricing_export_path is not None:
                        path = Path(pricing_export_path)
                        path.parent.mkdir(parents=True, exist_ok=True)
                        pricer.model.write(str(path))
                reduced = priced['reduced_cost']
                if reduced is not None and reduced < -1e-6:
                    unresolved = 0
                    proposals = priced['columns'] if batch_pricing else [priced['column']]
                    added = sum(add(column) for column in proposals if (
                        (0.0 if phase_one else column['cost'])-math.fsum(duals[key] for key in column['tasks'])-nu-math.fsum(category_duals.get(bid,0) for bid in {problem.order_map[oid].batch for oid,kind in column['tasks'] if kind=='d'})-math.fsum(value for sid,value in subset_duals.items() if subset_keys[sid].intersection(column['tasks'])) < -1e-6))
                    if batch_pricing:
                        witness['columns_added'] = added
                    if not added:
                        status = 'NUMERICAL_DUPLICATE_COLUMN'
                        break
                    continue
                if priced['infeasible']:
                    status = 'NO_RELAXED_TRIP'
                    break
                # The raw bound certifies no negative reduced cost within the
                # stated numerical tolerance; the reported cost bound still uses
                # its conservative margin and K*rho correction.
                if priced['raw_lower_bound'] is not None and priced['raw_lower_bound'] >= -1e-6:
                    status = 'RELAXATION_INFEASIBLE' if phase_one else 'LP_CONVERGED'
                    converged = not phase_one
                    break
                unresolved += 1
                # Pricing may time out without a negative incumbent. Retry at
                # these same dual prices until the site's total budget expires.
            m.update()
            certificate = dict(satellite=site, bound=max(0.0, best), status=status, lp_converged=converged,
                        best_witness=best_witness, iterations=iterations, columns=columns,
                        variables=m.NumVars, pricing_variables=pricer.model.NumVars if pricer else None,
                        runtime=time.perf_counter()-begun, enumeration=enumeration, pricing_backend='milp')
            resumed = yield deepcopy(certificate)
            if resumed is None:
                return certificate
            deadline, pricing_export_path = resumed
            # Retain LP basis, columns, current objective and pricing search;
            # each resumed stage exports its own proof witness and model files.
            begun = time.perf_counter()
            best, best_witness, iterations, status = 0.0, None, [], 'TIME_LIMIT'
    except TimeoutError:
        return dict(satellite=site, bound=max(0.0, best), status='TIME_LIMIT_BUILD', lp_converged=False,
                    best_witness=best_witness, iterations=iterations, columns=columns, runtime=time.perf_counter()-begun)
    finally:
        if pricer is not None:
            pricer.dispose()
        m.dispose()


class SiteRouteCoverSession:
    """Own a cover LP and its full-domain pricers across bounded stages."""
    def __init__(self, problem, context, **options):
        self.problem, self.context, self.options = problem, context, options
        self._generator, self._certificate = None, None
        self._disposed, self._finished, self._calls = False, False, 0

    def run(self, *, deadline, pricing_export_path=None):
        if self._disposed:
            raise RuntimeError('Route cover session is disposed')
        if self._finished:
            return deepcopy(self._certificate)
        first = self._generator is None
        if first:
            self._generator = _site_route_cover(self.problem, self.context, deadline=deadline,
                pricing_export_path=pricing_export_path, **self.options)
        try:
            certificate = next(self._generator) if first else self._generator.send((deadline, pricing_export_path))
        except StopIteration as stop:
            certificate = stop.value
            self._finished = True
        if certificate is None:
            raise RuntimeError('Route cover session ended without a certificate')
        self._certificate = deepcopy(certificate)
        if certificate['lp_converged']:
            self._generator.close()
            self._finished = True
        if self._calls:
            certificate['continuation'] = True
        self._calls += 1
        return deepcopy(certificate)

    def dispose(self):
        if self._generator is not None:
            self._generator.close()
        self._disposed = True

    @property
    def active(self):
        return not self._disposed and not self._finished and self._generator is not None


def site_route_cover(problem, context, *, deadline, **options):
    """One bounded stage; live sessions are opt-in and explicitly disposed."""
    export = options.pop('pricing_export_path', None)
    session = SiteRouteCoverSession(problem, context, **options)
    try:
        return session.run(deadline=deadline, pricing_export_path=export)
    finally:
        session.dispose()


def route_cover_certificate(problem, *, time_limit=30, initial_result=None, seed=42,
                            pricing_time_limit=2.0, verbose=False, diagnostics_dir=None, heuristic_pricing=True,
                            e1_time_limit=0.0, e1_inventory=True, e1_backend='auto', pricing_backend='milp',
                            enumeration_time_limit=15.0, enumeration_max_states=2000000,
                            integer_cover_time_limit=0.0, batch_pricing=False, dual_repair=False, delivery_count_cuts=False,
                            reuse_pricing_budget=False, route_sessions=None, count_dual_repair=False, capacity_dual_repair=False):
    if not math.isfinite(time_limit) or time_limit <= 0:
        raise ValueError('time_limit must be finite and positive')
    if not math.isfinite(pricing_time_limit) or pricing_time_limit <= 0:
        raise ValueError('pricing_time_limit must be finite and positive')
    if not math.isfinite(e1_time_limit) or e1_time_limit < 0:
        raise ValueError('e1_time_limit must be finite and nonnegative')
    if e1_backend not in ('auto', 'patterns', 'indexed'):
        raise ValueError('e1_backend must be auto, patterns or indexed')
    if pricing_backend not in ('milp', 'hybrid'):
        raise ValueError('pricing_backend must be milp or hybrid')
    if not math.isfinite(enumeration_time_limit) or enumeration_time_limit <= 0:
        raise ValueError('enumeration_time_limit must be finite and positive')
    if not isinstance(enumeration_max_states, int) or isinstance(enumeration_max_states, bool) or enumeration_max_states < 1:
        raise ValueError('enumeration_max_states must be a positive integer')
    if not math.isfinite(integer_cover_time_limit) or integer_cover_time_limit < 0:
        raise ValueError('integer_cover_time_limit must be finite and nonnegative')
    if integer_cover_time_limit > 0 and pricing_backend != 'hybrid':
        raise ValueError('integer_cover_time_limit requires hybrid complete enumeration')
    if integer_cover_time_limit > 0 and delivery_count_cuts:
        raise ValueError('Capacity-cut pricing currently requires the full-domain MILP backend')
    if (problem.model_number != 5 or problem.free_assignment or not problem.reusable
            or len(problem.operating_policy.batches) <= 1):
        raise ValueError('Route-cover certificates require fixed, reusable multibatch M5')
    begun = time.perf_counter()
    deadline = begun+time_limit
    if initial_result is not None and not audit_solution(problem, initial_result)['valid']:
        raise ValueError('Initial result must pass the full M5 independent audit')
    try:
        baseline = distance_certificate(problem, deadline=deadline)
    except TimeoutError:
        return dict(bound=0.0, scope='M5_relaxed_E2_route_cover_plus_E1_certificate',
                    status='TIME_LIMIT_CERTIFICATE', sites=[], runtime=time.perf_counter()-begun,
                    proven_optimal=False, proven_infeasible=False)
    travel = travel_lower_bounds(problem)
    e1_certificate = None
    remaining = max(0.0, deadline-time.perf_counter())
    if e1_time_limit > 0 and remaining > 0:
        from .multibatch_e1_inventory import e1_inventory_certificate
        # E1 shares the original total budget; reserve at least two thirds
        # of the remaining time for the existing E2 column generation.
        e1_certificate = e1_inventory_certificate(problem, time_limit=min(e1_time_limit, remaining/3),
            initial_result=initial_result, seed=seed, inventory=e1_inventory, verbose=verbose,
            backend=e1_backend,
            travel_bounds=travel, distance_bounds=baseline,
            diagnostics_path=Path(diagnostics_dir)/'e1_inventory.mps' if diagnostics_dir else None)
    site_rows = []
    contexts = sorted((route_context(problem, site, travel) for site in problem.satellites),
                      key=lambda context: (len(context['nodes']), context['satellite']))
    for index, context in enumerate(contexts):
        remaining = max(0.0, deadline-time.perf_counter())
        # Reserve equal remaining shares for all sites, including a difficult
        # first site. An unfinished site retains its independently safe bound.
        site_deadline = min(deadline, time.perf_counter()+remaining/(len(contexts)-index))
        options = dict(
            initial_result=initial_result, seed=seed, pricing_time_limit=pricing_time_limit, verbose=verbose,
            heuristic_pricing=heuristic_pricing, pricing_backend=pricing_backend,
            enumeration_time_limit=enumeration_time_limit, enumeration_max_states=enumeration_max_states,
            integer_cover_time_limit=integer_cover_time_limit,
            batch_pricing=batch_pricing, dual_repair=dual_repair, delivery_count_cuts=delivery_count_cuts,
            reuse_pricing_budget=reuse_pricing_budget, count_dual_repair=count_dual_repair,
            capacity_dual_repair=capacity_dual_repair)
        export = Path(diagnostics_dir)/f'site_{index}_best_pricing.mps' if diagnostics_dir else None
        if route_sessions is None:
            row = site_route_cover(problem, context, deadline=site_deadline,
                                   pricing_export_path=export, **options)
        else:
            session = SiteRouteCoverSession(problem, context, **options)
            route_sessions[context['satellite']] = session
            row = session.run(deadline=site_deadline, pricing_export_path=export)
        site_rows.append(row)
    distance_e1 = math.fsum(max(0.0, row['bound']-1e-7*max(1.0, row['bound'])) for row in baseline['components'] if row['echelon'] == 'E1')
    e1 = max(distance_e1, e1_certificate['bound'] if e1_certificate else 0.0)
    old_sites = {row['satellite']: max(0.0, row['bound']-1e-7*max(1.0, row['bound']))
                 for row in baseline['components'] if row['echelon'] == 'E2'}
    components = [dict(satellite=row['satellite'], route_cover_bound=row['bound'],
                       distance_bound=old_sites.get(row['satellite'], 0.0),
                       bound=max(row['bound'], old_sites.get(row['satellite'], 0.0))) for row in site_rows]
    raw = e1+math.fsum(row['bound'] for row in components)
    bound = max(baseline['bound'], max(0.0, raw-1e-7*max(1.0, raw)))
    integer_complete = integer_cover_time_limit > 0 and all(
        row['status'] == 'EMPTY' or (row.get('integer_cover') or {}).get('relaxation_optimal', False)
        for row in site_rows)
    return dict(bound=bound, raw_bound=raw, e1_bound=e1, distance_e1_bound=distance_e1,
                e1_certificate=e1_certificate, components=components, sites=site_rows,
                distance_certificate=baseline, scope='M5_relaxed_E2_route_cover_plus_E1_certificate',
                pricing_backend=pricing_backend,
                integer_cover_time_limit=integer_cover_time_limit,
                integer_cover_complete=integer_complete,
                status=('INTEGER_RELAXATION_OPTIMAL' if integer_complete else 'PARTIAL_INTEGER_RELAXATION_BOUND')
                    if integer_cover_time_limit > 0 else
                    ('LP_CONVERGED' if all(row['lp_converged'] for row in site_rows) else 'PARTIAL_CERTIFIED_BOUND'),
                runtime=time.perf_counter()-begun, proven_optimal=False, proven_infeasible=False,
                relaxed_constraints=['actual_E1_supply_times', 'cross_trip_delivery_pickup_use_times',
                    'physical_vehicle_trip_sequencing', 'cutoff_inventory_and_clean_container_reuse'])
