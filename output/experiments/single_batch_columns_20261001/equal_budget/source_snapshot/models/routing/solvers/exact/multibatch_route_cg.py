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


def site_route_cover(problem, context, *, deadline, initial_result=None, seed=42,
                     pricing_time_limit=2.0, verbose=False, pricing_export_path=None, heuristic_pricing=True):
    import gurobipy as gp
    from gurobipy import GRB
    begun = time.perf_counter()
    site, K = context['satellite'], context['maximum_trips']
    if not context['nodes']:
        return dict(satellite=site, bound=0.0, status='EMPTY', lp_converged=True, iterations=[], columns=[], runtime=0.0)
    if time.perf_counter() >= deadline:
        return dict(satellite=site, bound=0.0, status='TIME_LIMIT_BUILD', lp_converged=False, iterations=[], columns=[], runtime=0.0)
    m = gp.Model('M5_relaxed_E2_route_cover')
    m.Params.OutputFlag = int(verbose)
    m.Params.Threads = 1
    m.Params.Method = 1
    m.Params.DualReductions = 0
    rows = {node['key']: m.addConstr(gp.LinExpr() == 1, name=f'cover_{i}') for i, node in enumerate(context['nodes'])}
    fleet = m.addConstr(gp.LinExpr() <= K, name='aggregate_trip_limit')
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
        variables.append(m.addVar(obj=0 if phase_one else column['cost'],
            column=gp.Column([1]*(len(key)+1), [rows[task] for task in key]+[fleet]), name=f'route_{len(columns)}'))
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
        pricer = RoutePricer(problem, context, deadline=deadline, seed=seed, verbose=verbose)
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
            if heuristic_pricing:
                found = insertion_columns(problem, context, duals, columns, phase_one=phase_one, deadline=deadline)
                # nu <= 0 contributes a nonnegative constant to reduced cost.
                added = sum(add(column) for column in found if (
                    (0.0 if phase_one else column['cost'])-math.fsum(duals[key] for key in column['tasks'])-nu < -1e-6))
                if added:
                    unresolved = 0
                    iterations.append(dict(phase='feasibility' if phase_one else 'distance', method='insertion',
                                           master_objective=m.ObjVal, candidate_bound=None, columns_added=added))
                    continue
            remaining = deadline-time.perf_counter()
            if remaining <= 0:
                break
            priced = pricer.price(duals, nu, time_limit=min(pricing_time_limit*(2**min(unresolved, 6)), remaining),
                                  phase_one=phase_one, stop_at_negative=True)
            lower = dual_certificate(duals, nu, K, priced['lower_bound']) if not phase_one else None
            witness = dict(phase='feasibility' if phase_one else 'distance', method='integer_pricing',
                master_objective=m.ObjVal, duals=[[list(key), value] for key, value in duals.items()],
                fleet_dual=nu, maximum_trips=K, pricing={k: v for k, v in priced.items() if k != 'column'},
                candidate_bound=lower, new_column=priced['column'])
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
                if not add(priced['column']):
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
        return dict(satellite=site, bound=max(0.0, best), status=status, lp_converged=converged,
                    best_witness=best_witness, iterations=iterations, columns=columns,
                    variables=m.NumVars, pricing_variables=pricer.model.NumVars if pricer else None,
                    runtime=time.perf_counter()-begun)
    except TimeoutError:
        return dict(satellite=site, bound=max(0.0, best), status='TIME_LIMIT_BUILD', lp_converged=False,
                    best_witness=best_witness, iterations=iterations, columns=columns, runtime=time.perf_counter()-begun)
    finally:
        if pricer is not None:
            pricer.dispose()
        m.dispose()


def route_cover_certificate(problem, *, time_limit=30, initial_result=None, seed=42,
                            pricing_time_limit=2.0, verbose=False, diagnostics_dir=None, heuristic_pricing=True):
    if not math.isfinite(time_limit) or time_limit <= 0:
        raise ValueError('time_limit must be finite and positive')
    if not math.isfinite(pricing_time_limit) or pricing_time_limit <= 0:
        raise ValueError('pricing_time_limit must be finite and positive')
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
    site_rows = []
    contexts = sorted((route_context(problem, site, travel) for site in problem.satellites),
                      key=lambda context: (len(context['nodes']), context['satellite']))
    for index, context in enumerate(contexts):
        remaining = max(0.0, deadline-time.perf_counter())
        # Reserve equal remaining shares for all sites, including a difficult
        # first site. An unfinished site retains its independently safe bound.
        site_deadline = min(deadline, time.perf_counter()+remaining/(len(contexts)-index))
        site_rows.append(site_route_cover(problem, context, deadline=site_deadline,
            initial_result=initial_result, seed=seed, pricing_time_limit=pricing_time_limit, verbose=verbose,
            heuristic_pricing=heuristic_pricing,
            pricing_export_path=Path(diagnostics_dir)/f'site_{index}_best_pricing.mps' if diagnostics_dir else None))
    e1 = math.fsum(max(0.0, row['bound']-1e-7*max(1.0, row['bound'])) for row in baseline['components'] if row['echelon'] == 'E1')
    old_sites = {row['satellite']: max(0.0, row['bound']-1e-7*max(1.0, row['bound']))
                 for row in baseline['components'] if row['echelon'] == 'E2'}
    components = [dict(satellite=row['satellite'], route_cover_bound=row['bound'],
                       distance_bound=old_sites.get(row['satellite'], 0.0),
                       bound=max(row['bound'], old_sites.get(row['satellite'], 0.0))) for row in site_rows]
    raw = e1+math.fsum(row['bound'] for row in components)
    bound = max(baseline['bound'], max(0.0, raw-1e-7*max(1.0, raw)))
    return dict(bound=bound, raw_bound=raw, e1_bound=e1, components=components, sites=site_rows,
                distance_certificate=baseline, scope='M5_relaxed_E2_route_cover_plus_E1_certificate',
                status='LP_CONVERGED' if all(row['lp_converged'] for row in site_rows) else 'PARTIAL_CERTIFIED_BOUND',
                runtime=time.perf_counter()-begun, proven_optimal=False, proven_infeasible=False,
                relaxed_constraints=['actual_E1_supply_times', 'cross_trip_delivery_pickup_use_times',
                    'physical_vehicle_trip_sequencing', 'cutoff_inventory_and_clean_container_reuse'])
