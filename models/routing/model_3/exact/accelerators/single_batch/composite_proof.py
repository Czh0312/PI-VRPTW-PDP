"""Opt-in M3 component equality proof; no distance-floor rows in the kernel.

The full schedule attains every component relaxation's minimum, or there is
no composite proof. Equality uses exact sums of the original binary-float
arc coefficients, never a small-Gap tolerance. Local MIP optimality remains
subject to the backend's numerical feasibility standards, like solve_exact.
"""
from copy import deepcopy
from fractions import Fraction
from functools import lru_cache
import itertools
import json
import math
from pathlib import Path
import time

from models.common.cycle.audit import audit_solution
from models.routing.model_3.exact.accelerators.single_batch.physical_bounds import _scope, _budget, build_physical_site, project_original_site

SCOPE = 'B1_M3_complete_component_equality'


def rational_cost(problem, path):
    return sum((Fraction.from_float(float(problem.distance(a, b)))
                for a, b in zip(path, path[1:])), Fraction())


def encode(value):
    return [value.numerator, value.denominator]


def schedule_costs(problem, result):
    costs = {'e1': Fraction(), **{s: Fraction() for s in {o.satellite for o in problem.orders}}}
    for layer in ('e1', 'e2'):
        for trip in result[layer+'_trips']:
            base = problem.depot if layer == 'e1' else trip['satellite_id']
            sites = [e['site_id'] if layer == 'e1' else problem.order_map[e['order_id']].customer
                     for e in trip['events']]
            costs['e1' if layer == 'e1' else base] += rational_cost(problem, [base]+sites+[base])
    return costs


def e1_integer_cover(problem, *, deadline=None, max_sites=5):
    """Complete clocked paths, exact coefficient sums and integer-cover DP."""
    from models.common.cycle.precheck import travel_lower_bounds
    from models.routing.model_3.exact.accelerators.strengthening import single_batch_bounds
    from models.routing.model_3.exact.accelerators.single_batch.e1_bound import _clocked_replay
    _scope(problem)
    sites = sorted({o.satellite for o in problem.orders})
    output = dict(complete=False, status='SITE_LIMIT', sites=sites, bound=0.0, paths=0)
    if len(sites) > max_sites:
        return output

    def check():
        if deadline is not None and time.perf_counter() >= deadline:
            raise TimeoutError
    try:
        check()
        windows = single_batch_bounds(problem, travel_lower_bounds(problem))['visits']
        index = {s:i for i,s in enumerate(sites)}
        parts = []
        for size in range(len(sites)+1):
            for part in itertools.permutations(sites, size):
                check(); parts.append(part)
        groups = {}
        for delivery in parts:
            for pickup in parts:
                check()
                tasks = tuple((s, 'd') for s in delivery)+tuple((s, 'p') for s in pickup)
                if not tasks:
                    continue
                output['paths'] += 1
                route = _clocked_replay(problem, tasks, windows)
                if route is None:
                    continue
                mask = sum(1 << (index[s]+(len(sites) if k == 'p' else 0)) for s,k in tasks)
                cost = rational_cost(problem, [problem.depot]+[s for s,k in tasks]+[problem.depot])
                if mask not in groups or cost < groups[mask][0]:
                    groups[mask] = (cost, tasks)
        full = (1 << (2*len(sites)))-1

        @lru_cache(None)
        def cover(remaining, vehicles):
            check()
            if not remaining:
                return Fraction(), ()
            if not vehicles:
                return None
            first = remaining & -remaining
            best = None
            for mask, (cost, tasks) in groups.items():
                if mask & first and mask & remaining == mask:
                    suffix = cover(remaining ^ mask, vehicles-1)
                    if suffix is not None:
                        choice = cost+suffix[0], (mask,)+suffix[1]
                        if best is None or choice[0] < best[0]:
                            best = choice
            return best
        answer = cover(full, min(problem.fleet.e1_count, 2*len(sites)))
        check()
        output.update(complete=True, status='COVER_INFEASIBLE' if answer is None else 'COVER_OPTIMAL',
            groups=[dict(mask=m, cost=encode(c), tasks=t) for m,(c,t) in sorted(groups.items())],
            dp_states=cover.cache_info().currsize)
        if answer is not None:
            output.update(exact_cost=encode(answer[0]), selected_masks=answer[1],
                          bound=max(0.0, math.nextafter(float(answer[0]), -math.inf)))
        return output
    except TimeoutError:
        output['status'] = 'ENUMERATION_OR_DP_LIMIT'
        return output


def integer_objective(model):
    """Read exact original coefficients on integral objective-bearing variables."""
    import gurobipy as gp
    expression = model.getObjective()
    cost = Fraction.from_float(float(expression.getConstant()))
    for i in range(expression.size()):
        variable = expression.getVar(i)
        if variable.VType not in (gp.GRB.BINARY, gp.GRB.INTEGER) or abs(variable.X-round(variable.X)) > 1e-9:
            raise ValueError('Component objective requires integral original arc variables')
        cost += Fraction.from_float(float(expression.getCoeff(i)))*round(variable.X)
    return cost


def component_certificate(problem, initial_result, time_limit=10, *, seed=42, diagnostics_dir=None):
    """A proof only if every complete component matches the same audited start."""
    import gurobipy as gp
    from models.routing.model_3.exact.accelerators.single_batch.columns import problem_fingerprint
    _scope(problem); _budget(time_limit)
    begun = time.perf_counter(); deadline = begun+time_limit
    output = dict(proof_scope=SCOPE, problem_sha256=problem_fingerprint(problem),
        status='NO_INITIAL', closed=False, bound=0.0, sites={}, e1=None, runtime=0.0)

    def finish(status):
        output.update(status=status, runtime=time.perf_counter()-begun)
        return output
    if initial_result is None:
        return finish('NO_INITIAL')
    audit = audit_solution(problem, initial_result)
    if not audit['valid'] or not audit['periodic_replay']['valid']:
        raise ValueError('Composite proof requires an audited original full schedule')
    targets = schedule_costs(problem, initial_result)
    output['target_costs'] = {k:encode(v) for k,v in targets.items()}
    e1 = e1_integer_cover(problem, deadline=min(deadline, begun+.2))
    output['e1'] = e1
    output['bound'] = e1['bound']
    if not e1['complete'] or e1['status'] != 'COVER_OPTIMAL':
        return finish('E1_NOT_COMPLETE')
    if Fraction(*e1['exact_cost']) != targets['e1']:
        return finish('E1_NOT_TIGHT_AT_INITIAL')
    folder = Path(diagnostics_dir) if diagnostics_dir is not None else None
    if folder is not None:
        folder.mkdir(parents=True, exist_ok=True)
    sites = sorted({o.satellite for o in problem.orders},
                   key=lambda s:(sum(o.satellite == s for o in problem.orders), s))
    for i, site in enumerate(sites):
        if time.perf_counter() >= deadline:
            return finish('SITE_BUDGET_LIMIT')
        record = dict(status='BUILD_LIMIT', bound=0.0, complete=False, exact_match=False, work=0.0)
        output['sites'][site] = record
        site_begun = time.perf_counter()
        try:
            model, context = build_physical_site(problem, site, deadline=deadline)
        except TimeoutError:
            record['runtime'] = time.perf_counter()-site_begun
            return finish('SITE_BUILD_LIMIT')
        try:
            record['projection'] = project_original_site(problem, context, initial_result)
            model.Params.Seed = int(seed); model.Params.DualReductions = 0
            model.Params.IntFeasTol = model.Params.FeasibilityTol = 1e-9
            model.Params.MIPGap = model.Params.MIPGapAbs = 0
            if folder is not None:
                model.Params.OutputFlag = 1; model.Params.LogToConsole = 0
                model.Params.LogFile = str(folder/f'site_{i}.log')
                model.write(str(folder/f'site_{i}.mps'))
                # Retain the ORIGINAL full-schedule warm start, not a better
                # solution learned during this solve, for independent replay.
                warm = {v.VarName:v.Start for v in model.getVars() if abs(v.Start) < 1e90}
                (folder/f'site_{i}_start.json').write_text(json.dumps(warm, indent=2)+'\n')
                record.update(model_file=str(folder/f'site_{i}.mps'), start_file=str(folder/f'site_{i}_start.json'))
            model.Params.TimeLimit = max(0, deadline-time.perf_counter())
            model.optimize()
            record.update(status=int(model.Status), work=model.Work,
                          complete=model.Status == gp.GRB.OPTIMAL,
                          original_tasks=context['projection_proof']['all_site_tasks'])
            if model.Status == gp.GRB.INFEASIBLE:
                raise ValueError('Component contradicts the audited original projection')
            try:
                raw = float(model.ObjBound)
            except (AttributeError, gp.GurobiError):
                raw = -math.inf
            if math.isfinite(raw) and abs(raw) < 1e90:
                margin = 1e-7*max(1, abs(raw))*(1+record['original_tasks'])
                record.update(raw_bound=raw, margin=margin, bound=max(0, raw-margin))
            if record['complete'] and model.SolCount:
                cost = integer_objective(model)
                record.update(exact_objective=encode(cost), objective=model.ObjVal,
                    native_bound_equals_objective=(model.ObjBound == model.ObjVal),
                    max_violation=model.MaxVio)
                record['exact_match'] = (cost == targets[site] and model.ObjBound == model.ObjVal
                                         and model.MaxVio <= 1e-7)
                # Diagnostics only: a failed guard is not necessarily a worse
                # incumbent. Native double bounds can differ by a final bit.
                record['proof_guard_failures'] = [reason for failed, reason in (
                    (cost != targets[site], 'exact_objective_mismatch'),
                    (model.ObjBound != model.ObjVal, 'native_float_bound_not_equal'),
                    (model.MaxVio > 1e-7, 'native_feasibility_violation')) if failed]
            output['bound'] = math.fsum([e1['bound']]+[r['bound'] for r in output['sites'].values()])
        finally:
            record['runtime'] = time.perf_counter()-site_begun
            model.dispose()
        if not record['complete']:
            return finish('SITE_NOT_COMPLETE')
        if not record['exact_match']:
            return finish('SITE_NOT_TIGHT_AT_INITIAL')
    output['closed'] = True
    output['exact_total_cost'] = encode(sum(targets.values(), Fraction()))
    # Only now is the audited upper also a lower bound for the FULL model.
    output['bound'] = audit['objective']
    return finish('ORIGINAL_OPTIMUM_BY_COMPONENT_EQUALITY')


def solve_exact_with_composite_proof(problem, time_limit=60, seed=42, verbose=False,
                                    initial_result=None, *, diagnostics_dir=None):
    from models.routing.model_3.exact.search.engine import solve_exact
    _scope(problem); _budget(time_limit)
    begun = time.perf_counter()
    cert = component_certificate(problem, initial_result, .2*time_limit, seed=seed,
                                 diagnostics_dir=diagnostics_dir)
    stage = time.perf_counter()-begun; remaining = max(0, time_limit-stage)
    if cert['closed']:
        result = deepcopy(initial_result)
        upper = audit_solution(problem, result)['objective']
        result['info'].update(status='OPTIMAL_BY_COMPONENTS', objective=upper, best_bound=upper,
            mip_gap=0.0, proven_optimal=True, proven_infeasible=False,
            best_bound_source='complete_component_equality', mip_work=0.0)
    elif remaining > 0:
        # No certificate hook: the original kernel has exactly its old rows.
        result = solve_exact(problem, remaining, seed, verbose, initial_result)
    else:
        from models.common.cycle.state import empty_result
        result = deepcopy(initial_result) if initial_result is not None else empty_result(problem, 'exact')
        result['info'].update(status='TIME_LIMIT_COMPONENT_PREFIX', proven_optimal=False,
                              proven_infeasible=False, best_bound=None, mip_work=0.0)
    info = result['info']; previous = info.get('best_bound')
    previous = previous if isinstance(previous, (int,float)) and math.isfinite(previous) else 0.0
    if cert['bound'] > previous:
        info.update(best_bound=cert['bound'], best_bound_source='component_relaxation_and_original_MILP')
    elif info.get('best_bound') is None:
        info['best_bound'] = previous
    upper = info.get('objective')
    if upper is not None:
        if info['best_bound'] > upper+1e-6:
            raise ValueError('Composite bound contradicts the original feasible schedule')
        info['mip_gap'] = max(0, upper-info['best_bound'])/max(1e-9, abs(upper))
    info.update(component_certificate=cert, component_stage_runtime=stage,
                component_kernel_budget=remaining, runtime=time.perf_counter()-begun)
    result['solver'] = 'exact'
    return result
