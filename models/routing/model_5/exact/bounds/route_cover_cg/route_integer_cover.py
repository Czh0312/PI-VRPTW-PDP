"""Integer coverage over a COMPLETE E2 route universe gives a M5 lower bound.

Physical vehicle sequencing, E1 supply, stocks and cross-route use times
remain relaxed. This never returns a full-M5 feasible solution or proof.
"""
from __future__ import annotations

import math
from pathlib import Path
import time


def integer_route_cover(problem, context, universe, model, *, lp_bound, deadline,
                        initial_result=None, seed=42, export_path=None):
    """Convert the full LP to binary coverage; keep its bound on interruption."""
    from gurobipy import GRB
    if not universe['complete'] or not all(g['complete'] for g in universe['groups']):
        raise ValueError('Integer coverage needs a complete route universe')
    columns = [c for g in universe['groups'] for c in g['columns']]
    model.update()
    if len(columns) != model.NumVars or len(columns) != universe['columns']:
        raise ValueError('Integer coverage needs all complete-universe LP columns')
    begun = time.perf_counter()
    result = dict(scope='M5_relaxed_integer_E2_route_cover', complete_route_universe=True,
        bound=lp_bound, lp_bound=lp_bound, raw_bound=None, margin=0.0,
        status='TIME_LIMIT_BUILD', relaxation_optimal=False, relaxation_infeasible=False,
        proven_optimal=False, proven_infeasible=False, variables=len(columns),
        constraints=model.NumConstrs, maximum_trips=context['maximum_trips'],
        relaxed_objective=None, selected_columns=[], initial_projection=None,
        runtime=0.0, nodes=0.0)
    def check():
        if time.perf_counter() >= deadline:
            raise TimeoutError
    try:
        check()
        # Every nonempty route includes a task covered exactly once, so its
        # integer multiplicity is automatically at most one. Binary bounds
        # preserve every full-M5 trip cover after task-set cost dominance.
        selected = None
        if initial_result is not None:
            selected = {frozenset((e['order_id'], e['kind']) for e in t['events'])
                for t in initial_result['e2_trips'] if t['satellite_id'] == context['satellite']}
        mapped, mapped_sets = [], set()
        variables = model.getVars()
        for i, (variable, column) in enumerate(zip(variables, columns)):
            if i % 256 == 0:
                check()
            variable.VType = GRB.BINARY
            variable.LB, variable.UB = 0, 1
            if selected is not None:
                tasks = frozenset(tuple(k) for k in column['tasks'])
                chosen = tasks in selected
                variable.Start = int(chosen)
                if chosen:
                    if tasks in mapped_sets:
                        raise ValueError('Duplicate task-set columns in the complete universe')
                    mapped.append(column)
                    mapped_sets.add(tasks)
        if selected is not None:
            expected = {n['key'] for n in context['nodes']}
            covered = [tuple(k) for c in mapped for k in c['tasks']]
            if mapped_sets != selected or set(covered) != expected or len(covered) != len(expected) or len(mapped) > context['maximum_trips']:
                raise ValueError('Audited full-M5 trips failed to project to integer coverage')
            result['initial_projection'] = dict(mapped_trips=len(mapped),
                objective=math.fsum(c['cost'] for c in mapped), exact_task_cover=True)
        check()
        model.Params.Seed = int(seed)
        model.Params.MIPGap = model.Params.MIPGapAbs = 0
        model.Params.IntFeasTol = model.Params.FeasibilityTol = 1e-9
        model.Params.TimeLimit = max(0, deadline-time.perf_counter())
        model.optimize()
        raw = float(model.ObjBound)
        if math.isfinite(raw):
            margin = 1e-7*max(1.0, abs(raw))
            result.update(raw_bound=raw, margin=margin, bound=max(lp_bound, max(0, raw-margin)))
        result.update(status='INTEGER_RELAXATION_OPTIMAL' if model.Status == GRB.OPTIMAL else
            ('INTEGER_RELAXATION_INFEASIBLE' if model.Status == GRB.INFEASIBLE else f'INTEGER_RELAXATION_{model.Status}'),
            relaxation_optimal=model.Status == GRB.OPTIMAL,
            relaxation_infeasible=model.Status == GRB.INFEASIBLE, nodes=model.NodeCount)
        if model.SolCount:
            result['relaxed_objective'] = float(model.ObjVal)
            result['selected_columns'] = [{**c, 'integer_value': v.X}
                for c, v in zip(columns, variables) if v.X > .5]
        if export_path is not None:
            path = Path(export_path)
            model.write(str(path.with_name(path.stem+'_integer_master.mps.gz')))
    except TimeoutError:
        pass
    result['runtime'] = time.perf_counter()-begun
    return result
