"""Opt-in fixed-M3 physical-site floors; original integer kernel stays intact.

Delete E1 routes and their incident rows from each site's original formulation.
Keep the entire road network, necessary supply bounds, all D/P orders, original
vehicles, trip slots, clocks, preparation, turnaround and cross-trip precedence.
Every full schedule projects, so finite local ObjBound values are E2 cost floors.
They are neither executable full schedules nor integer optimality certificates.
"""
from collections import defaultdict
from copy import deepcopy
import math
from pathlib import Path
import time

from models.common.cycle import MultiBatchInstance
from models.common.cycle.audit import audit_solution

SCOPE = 'B1_M3_original_physical_E2_site_floors'


def _scope(problem):
    if problem.model_number != 3 or problem.free_assignment or len(problem.operating_policy.batches) != 1:
        raise ValueError('Physical site floors require fixed single-batch M3')


def _budget(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError('Time budget must be finite and positive')


def site_costs(problem, result):
    costs = defaultdict(float)
    for trip in result['e2_trips']:
        site = trip['satellite_id']
        path = [site]+[problem.order_map[e['order_id']].customer for e in trip['events']]+[site]
        costs[site] += math.fsum(problem.distance(a, b) for a, b in zip(path, path[1:]))
    return dict(costs)


def build_physical_site(problem, site, *, deadline=None, compress_routes=None):
    """A projection only. The input instance and its business data are untouched."""
    import gurobipy as gp
    from models.routing.model_5.exact.single_batch_formulation import build_model
    _scope(problem)
    orders = tuple(o for o in problem.orders if o.satellite == site)
    if site not in problem.satellites or not orders:
        raise ValueError('An active original satellite is required')
    local = MultiBatchInstance(problem.network, orders, problem.operating_policy, model=3)
    model, context = build_model(local, deadline=deadline, capacity_cuts=False,
                                 compress_routes=compress_routes)
    try:
        def check():
            if deadline is not None and time.perf_counter() >= deadline:
                raise TimeoutError('Physical site projection reached total budget')
        removed = {}

        def collect(value):
            if isinstance(value, gp.Var):
                removed[value.index] = value
            elif isinstance(value, gp.LinExpr):
                for i in range(value.size()):
                    collect(value.getVar(i))
            elif isinstance(value, dict):
                for item in value.values():
                    collect(item)
            elif isinstance(value, (tuple, list)):
                for item in value:
                    collect(item)
        for route in context['e1']:
            collect(route)
        rank_prefixes = tuple(r['label']+'_rank[' for r in context['e1'])
        for var in model.getVars():
            if var.VarName.startswith(rank_prefixes):
                collect(var)

        def touches(expression):
            return any(expression.getVar(i).index in removed for i in range(expression.size()))
        rows, indicators = [], []
        for i, row in enumerate(model.getConstrs()):
            if i % 256 == 0:
                check()
            if touches(model.getRow(row)):
                rows.append(row)
        for i, row in enumerate(model.getGenConstrs()):
            if i % 256 == 0:
                check()
            if row.GenConstrType != gp.GRB.GENCONSTR_INDICATOR:
                raise ValueError('Unexpected general constraint in original M3')
            flag, _, expression, _, _ = model.getGenConstrIndicator(row)
            if flag.index in removed or touches(expression):
                indicators.append(row)
        objective, e2 = model.getObjective(), gp.LinExpr()
        for i in range(objective.size()):
            var = objective.getVar(i)
            if var.index not in removed:
                e2.addTerms(objective.getCoeff(i), var)
        proof = dict(removed_variables=[v.VarName for v in removed.values()],
            removed_linear_rows=[r.ConstrName for r in rows],
            removed_indicators=[r.GenConstrName for r in indicators],
            original_network_retained=True, all_site_tasks=2*len(orders),
            physical_vehicles=problem.fleet.e2_per_sat_count,
            original_max_trips=problem.operating_policy.e2_max_trips_per_vehicle,
            original_pool=problem.operating_policy.container_pool_size)
        model.remove(rows)
        model.remove(indicators)
        model.remove(list(removed.values()))
        model.setObjective(e2)
        model.update()
        context['e1'] = []
        supply = model.getVarByName(f'delivery_{site}_{problem.operating_policy.batches[0].id}')
        check()
        return model, {**context, 'local_problem': local, 'satellite': site,
                       'supply': supply, 'projection_proof': proof}
    except Exception:
        model.dispose()
        raise


def project_original_site(problem, context, initial, *, fix=False):
    """Fix original paths and integer clocks, allowing only vehicle relabeling."""
    if not audit_solution(problem, initial)['valid']:
        raise ValueError('Projection requires an audited original full schedule')
    site = context['satellite']
    supply = next(e['start']+problem.service(site) for t in initial['e1_trips']
                  for e in t['events'] if e['kind'] == 'd' and e['site_id'] == site)
    trips = [t for t in initial['e2_trips'] if t['satellite_id'] == site]
    vehicles = defaultdict(list)
    for trip in trips:
        vehicles[trip['vehicle_id']].append(trip)
    chosen = {}
    for vehicle, old in enumerate(sorted(vehicles, key=lambda v: (-len(vehicles[v]), v))):
        for slot, trip in enumerate(sorted(vehicles[old], key=lambda t: t['trip_index'])):
            chosen[vehicle, slot] = trip

    def put(var, value):
        var.Start = value
        if fix:
            var.LB = var.UB = value
    put(context['supply'], supply)
    for var in context.get('global_e2_arcs', {}).values():
        put(var, 0)
    covered = set()
    for route in context['e2']:
        trip = chosen.get((route['vehicle'], route['slot']))
        lookup = {(n['order'].id, n['kind']): i for i, n in enumerate(route['nodes'])}
        indices = [lookup[e['order_id'], e['kind']] for e in trip['events']] if trip else []
        arcs = set(zip([-1]+indices, indices+[-2])) if trip else set()
        if not arcs <= route['x'].keys():
            raise ValueError('Original trip has an absent projected arc')
        if trip:
            covered.add((route['vehicle'], route['slot']))
        put(route['used'], int(trip is not None))
        put(route['start'], trip['departure'] if trip else 0)
        put(route['end'], trip['return_complete'] if trip else 0)
        for i, var in route['visit'].items():
            put(var, int(i in indices))
        for arc, var in route['x'].items():
            if not (route.get('global_internal_arcs') and min(arc) >= 0) or arc in arcs:
                put(var, int(arc in arcs))
        for index, event in zip(indices, trip['events'] if trip else []):
            put(route['times'][index], event['start'])
    if covered != set(chosen):
        raise ValueError('Original schedule exceeds projected physical slots')
    return dict(site=site, tasks=sum(len(t['events']) for t in trips),
                trips=len(trips), supply=supply, cost=site_costs(problem, initial)[site])


def physical_site_bounds(problem, time_limit=10, *, seed=42, initial_result=None,
                         diagnostics_dir=None):
    """Interrupted local MIPs yield conservative floors, never full proof flags."""
    import gurobipy as gp
    from models.routing.model_5.exact.single_batch_columns import problem_fingerprint
    _scope(problem)
    _budget(time_limit)
    begun = time.perf_counter()
    deadline = begun+time_limit
    output = dict(proof_scope=SCOPE, problem_sha256=problem_fingerprint(problem),
        status='TIME_LIMIT', sites={}, bound=0.0, proven_optimal=False, proven_infeasible=False,
        physical_model_unchanged=True)
    if initial_result is not None and not audit_solution(problem, initial_result)['valid']:
        raise ValueError('Initial schedule must pass original audit')
    diagnostics = Path(diagnostics_dir) if diagnostics_dir is not None else None
    if diagnostics is not None:
        diagnostics.mkdir(parents=True, exist_ok=True)
    sites = sorted({o.satellite for o in problem.orders})
    for i, site in enumerate(sites):
        left = deadline-time.perf_counter()
        if left <= 0:
            break
        site_begun = time.perf_counter()
        site_deadline = min(deadline, site_begun+left/(len(sites)-i))
        record = dict(status='BUILD_LIMIT', bound=0.0, raw_bound=None,
                      subproblem_optimal=False, work=0.0)
        output['sites'][site] = record
        try:
            model, context = build_physical_site(problem, site, deadline=site_deadline)
        except TimeoutError:
            record['runtime'] = time.perf_counter()-site_begun
            continue
        try:
            if initial_result is not None:
                record['projection'] = project_original_site(problem, context, initial_result)
            model.Params.Seed = int(seed)
            model.Params.DualReductions = 0
            model.Params.FeasibilityTol = model.Params.IntFeasTol = 1e-9
            if diagnostics is not None:
                model.Params.OutputFlag = 1
                model.Params.LogToConsole = 0
                model.Params.LogFile = str(diagnostics/f'site_{i}.log')
                model.write(str(diagnostics/f'site_{i}.mps'))
                record['model_file'] = str(diagnostics/f'site_{i}.mps')
            model.Params.TimeLimit = max(0, site_deadline-time.perf_counter())
            model.optimize()
            record.update(status=int(model.Status), subproblem_optimal=model.Status == gp.GRB.OPTIMAL,
                          work=model.Work, proof=context['projection_proof'], variables=model.NumVars)
            if model.Status == gp.GRB.INFEASIBLE and initial_result is not None:
                raise ValueError('Physical projection conflicts with audited full initial schedule')
            try:
                raw = float(model.ObjBound)
            except (AttributeError, gp.GurobiError):
                raw = -math.inf
            if math.isfinite(raw) and abs(raw) < 1e90 and model.Status not in (gp.GRB.INFEASIBLE, gp.GRB.INF_OR_UNBD):
                margin = 1e-7*max(1, abs(raw))*(1+context['projection_proof']['all_site_tasks'])
                record.update(raw_bound=raw, margin=margin, bound=max(0.0, raw-margin))
            if initial_result is not None and record['bound'] > record['projection']['cost']+1e-6:
                raise ValueError('Site floor exceeds an audited original site schedule')
        finally:
            record['runtime'] = time.perf_counter()-site_begun
            model.dispose()
    output['bound'] = math.fsum(r['bound'] for r in output['sites'].values())
    output['status'] = 'SITES_COMPLETE' if len(output['sites']) == len(sites) and all(
        r['subproblem_optimal'] for r in output['sites'].values()) else 'INTERRUPTED_SITE_BOUNDS'
    output['runtime'] = time.perf_counter()-begun
    return output


def apply_physical_bounds(model, problem, context, certificate):
    """Private certificate hook. Compressed internal arcs are counted once."""
    import gurobipy as gp
    from models.routing.model_5.exact.single_batch_columns import problem_fingerprint
    _scope(problem)
    if certificate.get('proof_scope') != SCOPE or certificate.get('problem_sha256') != problem_fingerprint(problem):
        raise ValueError('Physical certificate does not match this problem/scope')
    sites = {o.satellite for o in problem.orders}
    if not set(certificate['sites']) <= sites:
        raise ValueError('Unknown site in physical certificate')
    model.update()
    expressions = {s: gp.LinExpr() for s in sites}
    seen = set()
    for route in context['e2']:
        for (a, b), variable in route['x'].items():
            if variable.index in seen:
                continue
            seen.add(variable.index)
            left = route['satellite'] if a < 0 else route['nodes'][a]['site']
            right = route['satellite'] if b < 0 else route['nodes'][b]['site']
            expressions[route['satellite']].addTerms(problem.distance(left, right), variable)
    added = {}
    for site, record in certificate['sites'].items():
        value = record['bound']
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ValueError('Physical bounds must be finite and nonnegative')
        if value > 0:
            model.addConstr(expressions[site] >= value, name=f'B1_M3_physical_floor_{site}')
            added[site] = value
    return added


def solve_exact_with_physical_bounds(problem, time_limit=60, seed=42, verbose=False,
                                     initial_result=None, *, diagnostics_dir=None):
    """Spend 20% on site floors, then run the unchanged complete original MILP."""
    from models.routing.model_5.exact.engine import solve_exact
    _scope(problem)
    _budget(time_limit)
    begun = time.perf_counter()
    certificate = physical_site_bounds(problem, .2*time_limit, seed=seed,
        initial_result=initial_result, diagnostics_dir=diagnostics_dir)
    stage = time.perf_counter()-begun
    remaining = time_limit-stage
    if remaining > 0:
        result = solve_exact(problem, remaining, seed, verbose, initial_result,
                             _single_batch_physical_certificate=certificate)
    else:
        from models.common.cycle.state import empty_result
        result = deepcopy(initial_result) if initial_result is not None else empty_result(problem, 'exact')
        result['info'].update(status='TIME_LIMIT_PHYSICAL_COMPONENT', proven_optimal=False,
                              proven_infeasible=False, best_bound=None)
    info = result['info']
    # The sum covers E2 only; never add the existing total distance bound to it.
    previous = info.get('best_bound')
    previous = previous if isinstance(previous, (int, float)) and math.isfinite(previous) else 0.0
    bound = max(previous, certificate['bound'])
    if certificate['bound'] > previous:
        info['best_bound_source'] = 'physical_sites_and_original_MILP'
    upper = info.get('objective')
    if upper is not None and bound > upper+1e-6:
        raise ValueError('Physical bound conflicts with original feasible upper')
    info.update(best_bound=bound, physical_site_certificate=certificate,
                physical_stage_runtime=stage, physical_kernel_budget=max(0, remaining),
                runtime=time.perf_counter()-begun)
    if upper is not None:
        info['mip_gap'] = max(0, upper-bound)/max(1e-9, abs(upper))
    return result
