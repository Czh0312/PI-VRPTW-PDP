"""Complete elementary E2 enumeration when resources make the domain small.

All raw task paths are explored, including nonmetric detours. Retain the
cheapest feasible path per TASK SET: route-cover rows and prices depend only
on that set. A partial enumeration supplies columns, never a pricing bound.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import time


def enumerate_route_universe(problem, context, *, deadline=None, max_states=2000000):
    if not isinstance(max_states, int) or isinstance(max_states, bool) or max_states < 1:
        raise ValueError('max_states must be a positive integer')
    from models.common.cycle.precheck import travel_lower_bounds
    begun = time.perf_counter()
    p, f, site = problem.operating_policy, problem.fleet, context['satellite']
    travel = travel_lower_bounds(problem)
    nodes = context['nodes']
    categories = [None]+[b.id for b in p.batches if any(n['key'][1] == 'd' and n['order'].batch == b.id for n in nodes)]
    groups, states, completed = [], 0, True
    class Interrupted(Exception):
        pass
    for category in categories:
        selected = [n for n in nodes if n['key'][1] == 'p' or n['order'].batch == category]
        n = len(selected)
        keys = [node['key'] for node in selected]
        orders = [node['order'] for node in selected]
        positions = [o.customer for o in orders]
        is_delivery = [key[1] == 'd' for key in keys]
        weights = [node['weight'] for node in selected]
        volumes = [node['volume'] for node in selected]
        low = [node['window'][0] for node in selected]
        high = [node['window'][1] for node in selected]
        services = [o.service_time for o in orders]
        by_key = {key: i for i, key in enumerate(keys)}
        mates = [by_key.get((oid, 'p' if kind == 'd' else 'd'), -1) for oid, kind in keys]
        duration = [[problem.travel(a, b) for b in positions] for a in positions+[site]]
        distance = [[problem.distance(a, b) for b in positions] for a in positions+[site]]
        back_time = [problem.travel(a, site)+p.e2_return_unload_time for a in positions]
        back_cost = [problem.distance(a, site) for a in positions]
        min_back = [travel['inward'][site][a]+p.e2_return_unload_time for a in positions]
        start = p.cycle_start if category is None else context['release'][category]
        cap_w, cap_v = f.e2_weight_cap+1e-6, f.e2_volume_cap+1e-6
        best, path, starts, delivered = {}, [], [], [-1]*n
        group_begun, group_states, complete = time.perf_counter(), states, True
        def dfs(mask, last, clock, cost, dw, dv, pw, pv, net_w, net_v, peak_w, peak_v, has_d, has_p):
            nonlocal states
            states += 1
            if states > max_states or (states % 256 == 0 and deadline is not None and time.perf_counter() >= deadline):
                raise Interrupted
            if mask:
                end = clock+back_time[last]
                if (has_d or category is None) and end+p.e2_turnaround_time <= p.cycle_end and (not has_p or end <= p.batches[-1].cutoff):
                    full_cost = cost+back_cost[last]
                    if mask not in best or full_cost < best[mask]['cost']:
                        best[mask] = dict(tasks=tuple(keys[j] for j in path), cost=full_cost,
                            departure=int(start), starts=list(starts), return_complete=int(end))
            for j in range(n):
                bit = 1 << j
                if mask & bit or low[j] > high[j]:
                    continue
                mate = mates[j]
                if is_delivery[j] and mate >= 0 and mask & (1 << mate):
                    continue
                at = max(clock+duration[last][j], low[j])
                if not is_delivery[j] and mate >= 0 and delivered[mate] >= 0:
                    at = max(at, delivered[mate]+services[mate]+orders[j].min_use_time)
                if at > high[j]:
                    continue
                d_w, d_v = dw+(weights[j] if is_delivery[j] else 0), dv+(volumes[j] if is_delivery[j] else 0)
                p_w, p_v = pw+(0 if is_delivery[j] else weights[j]), pv+(0 if is_delivery[j] else volumes[j])
                delta_w, delta_v = net_w+weights[j]*(-1 if is_delivery[j] else 1), net_v+volumes[j]*(-1 if is_delivery[j] else 1)
                peak_w_new, peak_v_new = max(peak_w, delta_w), max(peak_v, delta_v)
                # Initial load is ALL deliveries selected anywhere on the
                # path. Future deliveries increase every earlier load, so
                # these partial-path violations can never be repaired.
                if d_w+peak_w_new > cap_w or d_v+peak_v_new > cap_v or p_w > cap_w or p_v > cap_v:
                    continue
                has_pickup = has_p or not is_delivery[j]
                limit = min(p.cycle_end-p.e2_turnaround_time, p.batches[-1].cutoff) if has_pickup else p.cycle_end-p.e2_turnaround_time
                # Direct return may be slower than a detour on nonmetric
                # inputs. Only the shortest-time lower bound can prune.
                if at+services[j]+min_back[j] > limit:
                    continue
                path.append(j)
                starts.append(int(at))
                if is_delivery[j]:
                    delivered[j] = at
                dfs(mask | bit, j, at+services[j], cost+distance[last][j],
                    d_w, d_v, p_w, p_v, delta_w, delta_v, peak_w_new, peak_v_new,
                    has_d or is_delivery[j], has_pickup)
                if is_delivery[j]:
                    delivered[j] = -1
                path.pop()
                starts.pop()
        try:
            if deadline is not None and time.perf_counter() >= deadline:
                raise Interrupted
            dfs(0, n, start, 0.0, 0, 0, 0, 0, 0, 0, 0, 0, False, False)
        except Interrupted:
            completed, complete = False, False
        groups.append(dict(category=category, complete=complete, states=states-group_states,
            columns=list(best.values()), runtime=time.perf_counter()-group_begun))
        if not complete:
            break
    return dict(complete=completed and len(groups) == len(categories), groups=groups,
                states=states, columns=sum(len(g['columns']) for g in groups), runtime=time.perf_counter()-begun)


class EnumeratedRoutePricer:
    """Exact pricing over a COMPLETE union of elementary route task sets."""
    def __init__(self, universe):
        if not universe['complete']:
            raise ValueError('A partial route universe cannot certify pricing')
        self.universe = universe
        self.model = self
        self.NumVars = 0

    def price(self, duals, fleet_dual=0.0, *, time_limit=2, phase_one=False, stop_at_negative=False):
        begun, nu = time.perf_counter(), min(0.0, float(fleet_dual))
        reports, candidates = [], []
        margin = 1e-7*max(1.0, math.fsum(abs(float(v)) for v in duals.values()), abs(nu))
        for group in self.universe['groups']:
            column = min(group['columns'], key=lambda c: (0.0 if phase_one else c['cost'])-math.fsum(duals.get(tuple(k), 0.0) for k in c['tasks']), default=None)
            value = None if column is None else (0.0 if phase_one else column['cost'])-math.fsum(duals.get(tuple(k), 0.0) for k in column['tasks'])-nu
            reports.append(dict(category=group['category'], optimal=True, infeasible=column is None,
                status=2 if column is not None else 3, lower_bound=0 if value is None else value-margin,
                raw_lower_bound=value, reduced_cost=value, margin=margin, runtime=0, work=0,
                method='complete_enumeration', columns=len(group['columns']), states=group['states']))
            if value is not None:
                candidates.append((value, column))
        value, column = min(candidates, key=lambda row: row[0]) if candidates else (None, None)
        return dict(status=2, optimal=True, infeasible=not candidates,
            lower_bound=0.0 if value is None else value-margin,
            raw_lower_bound=value, margin=margin, reduced_cost=value, column=column,
            runtime=time.perf_counter()-begun, work=0, groups=reports, variables=0,
            method='complete_enumeration')

    def write(self, filename):
        import gzip
        path = Path(filename).with_suffix('.json.gz')
        encoded = json.dumps(self.universe, ensure_ascii=False, allow_nan=False,
                             separators=(',', ':')).encode('utf-8')
        with gzip.open(path, 'wb', compresslevel=1) as stream:
            stream.write(encoded)

    def dispose(self):
        pass


def enumeration_stats(universe):
    return {**{k: v for k, v in universe.items() if k != 'groups'},
            'groups': [{**{k: v for k, v in g.items() if k != 'columns'},
                        'columns': len(g['columns'])} for g in universe['groups']]}


def enumerated_route_cover(problem, context, universe, *, deadline, verbose=False, export_path=None,
                           integer_cover_time_limit=0.0, initial_result=None, seed=42):
    """Solve the full route-cover LP, then certify all route dual inequalities."""
    import gurobipy as gp
    from gurobipy import GRB
    begun = time.perf_counter()
    pricer = EnumeratedRoutePricer(universe)
    site, K = context['satellite'], context['maximum_trips']
    model = gp.Model('M5_complete_enumerated_E2_route_cover')
    model.Params.OutputFlag = int(verbose)
    model.Params.Threads = 1
    model.Params.Method = 1
    model.Params.DualReductions = 0
    columns = [c for g in universe['groups'] for c in g['columns']]
    variables, witness, kept, status, bound, integer_cover = [], None, [], 'TIME_LIMIT_BUILD', 0.0, None
    try:
        rows = {n['key']: model.addConstr(gp.LinExpr() == 1, name=f'cover_{i}') for i, n in enumerate(context['nodes'])}
        fleet = model.addConstr(gp.LinExpr() <= K, name='aggregate_trip_limit')
        for i, column in enumerate(columns):
            if i % 256 == 0 and time.perf_counter() >= deadline:
                raise TimeoutError
            tasks = [tuple(key) for key in column['tasks']]
            variables.append(model.addVar(obj=column['cost'],
                column=gp.Column([1]*(len(tasks)+1), [rows[key] for key in tasks]+[fleet]), name=f'route_{i}'))
        model.Params.TimeLimit = max(0, deadline-time.perf_counter())
        model.optimize()
        if model.Status == GRB.OPTIMAL:
            duals, nu = {key: row.Pi for key, row in rows.items()}, min(0.0, fleet.Pi)
            priced = pricer.price(duals, nu)
            raw = math.fsum(duals.values())+K*nu+K*min(0, priced['lower_bound'])
            bound, status = max(0, raw), 'LP_CONVERGED'
            witness = dict(phase='distance', method='complete_enumeration', master_objective=model.ObjVal,
                duals=[[list(key), value] for key, value in duals.items()], fleet_dual=nu, maximum_trips=K,
                pricing={k: v for k, v in priced.items() if k != 'column'}, candidate_bound=raw, new_column=priced['column'])
            kept = [{**c, 'lp_value': v.X} for c, v in zip(columns, variables) if v.X > 1e-8]
            if export_path is not None:
                path = Path(export_path)
                path.parent.mkdir(parents=True, exist_ok=True)
                pricer.write(path)
                model.write(str(path.with_name(path.stem+'_master.mps.gz')))
            if integer_cover_time_limit > 0:
                from models.routing.model_5.exact.bounds.route_cover_cg.route_integer_cover import integer_route_cover
                integer_cover = integer_route_cover(problem, context, universe, model,
                    lp_bound=bound, deadline=min(deadline, time.perf_counter()+integer_cover_time_limit),
                    initial_result=initial_result, seed=seed, export_path=export_path)
                bound = integer_cover['bound']
        else:
            status = 'RELAXATION_INFEASIBLE' if model.Status == GRB.INFEASIBLE else 'MASTER_TIME_LIMIT'
        return dict(satellite=site, bound=bound, status=status, lp_converged=status == 'LP_CONVERGED',
            best_witness=0 if witness is not None else None, iterations=[witness] if witness else [], columns=kept,
            variables=model.NumVars, pricing_variables=0, runtime=time.perf_counter()-begun,
            enumeration=enumeration_stats(universe), columns_generated=len(columns),
            lp_bound=witness['candidate_bound'] if witness else 0.0, integer_cover=integer_cover,
            pricing_backend='complete_enumeration', columns_retained='positive_LP_variables')
    except TimeoutError:
        return dict(satellite=site, bound=0.0, status='TIME_LIMIT_BUILD', lp_converged=False,
            best_witness=None, iterations=[], columns=[], runtime=time.perf_counter()-begun,
            enumeration=enumeration_stats(universe), pricing_backend='complete_enumeration')
    finally:
        model.dispose()
