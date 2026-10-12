"""Private primal route pools that preserve an entire proposed task cover.

The proposed cover is not a full schedule or a lower-bound certificate.
Rebuild every route from the original directed network, append it to each
physical slot of its site, and let the original pool master enforce actual
vehicle calendars, E1 supply, stock, cleaning and cross-trip precedence.
"""
from copy import deepcopy
import json
import math
from pathlib import Path
import time

from models.common.cycle.audit import audit_solution
from models.routing.model_5.exact.multibatch_physical_site import normalize_trip_groups
from models.routing.model_5.exact.pickup_route_pricing import evaluate_column, route_context
from models.routing.model_5.exact.multibatch_route_pool import expanded_trip_slots, candidate_pools, add_route_pool, extract_pool
from models.routing.model_5.exact.multibatch_e1_routes import build_e1_routes, set_route_projection
from models.routing.model_5.exact.multibatch_e1_inventory import build_e1_inventory, set_e1_projection


def preserve_cover_routes(problem, layout, pools, covers, *, deadline=None):
    """Append rebuilt cover paths without removing/reordering old columns.

    Each declared site must have a nonempty exact cover of all its tasks.
    Cached costs and clocks are ignored. A valid cover can still be globally
    infeasible: this function makes no physical/full feasibility claim.
    """
    if len(pools) != len(layout['e2_trips']):
        raise ValueError('One candidate pool per physical slot required')
    if not isinstance(covers, dict):
        raise ValueError('Site-to-cover mapping required')
    rebuilt = {}
    for site, proposals in covers.items():
        if deadline is not None and time.perf_counter() >= deadline:
            raise TimeoutError('Cover preservation reached its total budget')
        sequences = [p['tasks'] if isinstance(p, dict) else p for p in proposals]
        if not sequences:
            raise ValueError('A nonempty complete site task cover required')
        normalize_trip_groups(problem, site, sequences)
        context = route_context(problem, site)
        columns = []
        for sequence in sequences:
            if deadline is not None and time.perf_counter() >= deadline:
                raise TimeoutError('Cover preservation reached its total budget')
            column = evaluate_column(problem, context, sequence)
            if column is None:
                raise ValueError('Cover path violates original route necessities')
            columns.append(column)
        if not any(t['satellite_id'] == site for t in layout['e2_trips']):
            raise ValueError('Cover site needs at least one physical slot')
        rebuilt[site] = columns
    output, appended = [], 0
    for trip, old in zip(layout['e2_trips'], pools):
        kept = list(old)
        known = {tuple(tuple(k) for k in c['tasks']) for c in kept}
        for column in rebuilt.get(trip['satellite_id'], []):
            if column['tasks'] not in known:
                kept.append(column)
                known.add(column['tasks'])
                appended += 1
        output.append(kept)
    return output, dict(
        sites=len(rebuilt), cover_paths=sum(map(len, rebuilt.values())),
        original_columns=sum(map(len, pools)), added_columns=appended,
        retained_columns=sum(map(len, output)),
        cached_costs_and_clocks_ignored=True, original_columns_preserved=True,
        full_feasible=False, full_proven_optimal=False,
    )


def reoptimize_cover_pool(problem, initial_result, covers, *, time_limit=90,
        seed=42, max_columns=64, transfers=True, exchanges=True,
        recombinations=True, additional_columns=None,
        additional_columns_per_slot=32, extra_slots_per_vehicle=1,
        diagnostics_dir=None):
    """One restricted-pool pass; only audited complete upper bounds return.

    Generation, preservation, construction and pre-solve exports share one
    deadline. A timeout retains the audited initial schedule. Subproblem
    bounds and optimality are explicitly restricted to this candidate pool.
    """
    if not math.isfinite(time_limit) or time_limit <= 0:
        raise ValueError('Finite positive total time required')
    for value in (max_columns, additional_columns_per_slot):
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError('Positive integer candidate limits required')
    if (problem.model_number != 5 or problem.free_assignment or not problem.reusable
            or len(problem.operating_policy.batches) <= 1):
        raise ValueError('Cover pools require fixed reusable multibatch M5')
    begun = time.perf_counter()
    deadline = begun + time_limit
    before = audit_solution(problem, initial_result)
    if not before['valid']:
        raise ValueError('An independently audited full initial schedule required')
    result = deepcopy(initial_result)
    for field in ('audit', 'cutoff_snapshots', 'depot_releases'):
        result.pop(field, None)
    layout = expanded_trip_slots(problem, initial_result, extra_slots_per_vehicle)
    proposals = {s: list((additional_columns or {}).get(s, [])) for s in problem.satellites}
    if extra_slots_per_vehicle:
        # Same short-route proposals as the original route-pool pass.
        for trip in initial_result['e2_trips']:
            for kind in ('d', 'p'):
                keys = [(e['order_id'], kind) for e in trip['events'] if e['kind'] == kind]
                for a in range(len(keys)):
                    for b in range(a+1, len(keys)+1):
                        proposals[trip['satellite_id']].append(dict(tasks=keys[a:b]))
    active = len({o.satellite for o in problem.orders})
    backend = 'patterns' if active <= 4 and problem.operating_policy.e1_turnaround_time == 0 else 'indexed'
    result['info'] = dict(status='FEASIBLE', objective=before['objective'],
        best_bound=None, mip_gap=None, proven_optimal=False, proven_infeasible=False,
        proof_scope='restricted_E2_cover_preserving_pool_primal',
        route_pool_complete=False, upper_before=before['objective'],
        route_pool_backend=backend, route_pool_subproblem_optimal=False,
        route_pool_subproblem_bound=None)
    model, status = None, 'TIME_LIMIT_BUILD'
    try:
        pools = candidate_pools(problem, layout, deadline=begun+time_limit*.2,
            max_columns=max_columns, transfers=transfers, exchanges=exchanges,
            recombinations=recombinations, additional_columns=proposals,
            additional_columns_per_slot=additional_columns_per_slot,
            balanced_extra=bool(extra_slots_per_vehicle))
        pools, preservation = preserve_cover_routes(problem, layout, pools, covers, deadline=deadline)
        result['info']['cover_preservation'] = preservation
        if time.perf_counter() >= deadline:
            raise TimeoutError
        if backend == 'patterns':
            model, context = build_e1_routes(problem, deadline=deadline)
            set_route_projection(problem, context, initial_result)
        else:
            model, context = build_e1_inventory(problem, deadline=deadline)
            set_e1_projection(problem, context, initial_result)
        add_route_pool(problem, model, context, layout, pools, deadline=deadline)
        model.Params.Seed = int(seed)
        if diagnostics_dir is not None:
            out = Path(diagnostics_dir)
            out.mkdir(parents=True, exist_ok=True)
            model.write(str(out/'route_pool.mps.gz'))
            (out/'columns.json').write_text(json.dumps(pools, indent=2)+'\n')
            (out/'slots.json').write_text(json.dumps(layout['e2_trips'], indent=2)+'\n')
        model.Params.TimeLimit = max(0, deadline-time.perf_counter())
        model.optimize()
        status = int(model.Status)
        import gurobipy as gp
        try:
            raw = float(model.ObjBound)
        except (gp.GurobiError, AttributeError):
            raw = math.nan
        result['info'].update(route_pool_subproblem_optimal=model.Status == gp.GRB.OPTIMAL,
            route_pool_subproblem_bound=raw if math.isfinite(raw) else None,
            route_pool_variables=model.NumVars, route_pool_constraints=model.NumConstrs,
            route_pool_columns=sum(map(len, pools)), solver_runtime=model.Runtime)
        if model.SolCount:
            candidate = extract_pool(problem, context, layout, pools, backend)
            audited = audit_solution(problem, candidate)
            if not audited['valid']:
                raise RuntimeError('Cover pool candidate failed the original full-cycle audit')
            if not math.isclose(audited['objective'], model.ObjVal, abs_tol=1e-5, rel_tol=1e-7):
                raise RuntimeError('Cover pool objective differs from actual directed distance')
            if audited['objective'] < before['objective']-1e-6:
                candidate['info'] = result['info']
                result = candidate
    except TimeoutError:
        pass
    finally:
        if model is not None:
            model.dispose()
    audited = audit_solution(problem, {**result, 'info': {**result['info'], 'objective': None}})
    if not audited['valid']:
        raise RuntimeError('Returned cover pool schedule is invalid')
    result['audit'] = audited
    result['info'].update(objective=audited['objective'], route_pool_status=status,
        route_pool_improved=audited['objective'] < before['objective']-1e-6,
        runtime=time.perf_counter()-begun)
    return result
