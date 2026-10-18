"""Complete-domain dual repair using count rows, subsets and task prices.

Every route of a delivery category contains at least one of its delivery
tasks. For each possible mandatory task, impose a sufficient correction
inequality. A subset that contains that task is necessarily hit by the route.
Pickup-only routes are covered by one inequality per possible pickup task.
The correction LP changes no routing model or physical data.
"""
import math
import time

from models.routing.model_5.exact.accelerators.multibatch.count_dual_repair import repair_count_duals


def repair_capacity_duals(context, duals, fleet_dual, pricing, delivery_counts,
                          subset_counts, *, deadline=None):
    base = repair_count_duals(context, duals, fleet_dual, pricing, delivery_counts)
    nodes = context['nodes'];lookup = {n['key']: n for n in nodes}
    subsets = {}
    for sid, (keys, required, multiplier) in subset_counts.items():
        keys = frozenset(keys);multiplier = float(multiplier)
        if not keys or not keys <= set(lookup) or any(k[1] != 'd' for k in keys):
            raise ValueError('Capacity repair requires nonempty subsets of actual delivery tasks')
        if isinstance(required, bool) or not isinstance(required, int) or required < 1:
            raise ValueError('Subset count must be a positive integer')
        if not math.isfinite(multiplier) or multiplier < 0:
            raise ValueError('Subset multipliers must be finite and nonnegative')
        subsets[sid] = (keys, required, multiplier)
    if deadline is not None and time.perf_counter() >= deadline:
        return {**base, 'capacity_repair_status': 'BUDGET_EXHAUSTED'}
    import gurobipy as gp
    m = gp.Model('M5_dual_correction_only');m.Params.OutputFlag=0;m.Params.Threads=1
    m.Params.FeasibilityTol=1e-9
    try:
        fleet = m.addVar(name='fleet_reduction')
        task = {key: m.addVar(name='task_'+str(i)) for i,key in enumerate(lookup)}
        counts = {b: m.addVar(ub=float(row[1]),name='count_'+str(i))
                  for i,(b,row) in enumerate(delivery_counts.items())}
        cuts = {sid: m.addVar(ub=row[2],name='subset_'+str(i))
                for i,(sid,row) in enumerate(subsets.items())}
        bounds = {g['category']: float(g['lower_bound']) for g in pricing['groups'] if not g['infeasible']}
        for key,node in lookup.items():
            category = None if key[1]=='p' else node['order'].batch
            if category not in bounds:
                continue
            correction = fleet+task[key]+counts.get(category,0)\
                         +gp.quicksum(cuts[sid] for sid,(keys,_,_) in subsets.items() if key in keys)
            m.addConstr(correction >= -bounds[category])
        m.setObjective(context['maximum_trips']*fleet+gp.quicksum(task.values())
                       +gp.quicksum(row[0]*counts[b] for b,row in delivery_counts.items())
                       +gp.quicksum(row[1]*cuts[sid] for sid,row in subsets.items()))
        if deadline is not None:
            remaining=deadline-time.perf_counter()
            if remaining<=0:return {**base,'capacity_repair_status':'BUDGET_EXHAUSTED'}
            m.Params.TimeLimit=min(.1,remaining)
        m.optimize()
        if not m.SolCount:
            return {**base,'capacity_repair_status':'NO_FEASIBLE_CORRECTION'}
        x=max(0.0,fleet.X);delta={k:max(0.0,v.X) for k,v in task.items()}
        reductions={b:min(float(delivery_counts[b][1]),max(0.0,v.X)) for b,v in counts.items()}
        cut_reductions={sid:min(subsets[sid][2],max(0.0,v.X)) for sid,v in cuts.items()}
        # Clipping cannot cause an uncorrected numerical feasibility residual.
        residual=0.0
        for key,node in lookup.items():
            category=None if key[1]=='p' else node['order'].batch
            if category in bounds:
                residual=max(residual,-bounds[category]-x-delta[key]-reductions.get(category,0)
                             -math.fsum(cut_reductions[sid] for sid,(keys,_,_) in subsets.items() if key in keys))
        x+=max(0.0,residual)+1e-9
        nu=min(0.0,float(fleet_dual));repaired={key:float(value)-delta[key] for key,value in duals.items()}
        loss=math.fsum(row[0]*reductions[b] for b,row in delivery_counts.items())\
             +math.fsum(row[1]*cut_reductions[sid] for sid,row in subsets.items())
        value=math.fsum(repaired.values())+context['maximum_trips']*(nu-x)-loss
        if value<base['bound']:
            return {**base,'capacity_repair_status':'COUNT_REPAIR_STRONGER'}
        return dict(bound=value,repair_mode='capacity_rows_lp',fleet_shift=x,repaired_fleet_dual=nu-x,
            repaired_duals=[[list(k),v] for k,v in repaired.items()],task_shifts=[[list(k),v] for k,v in delta.items()],
            delivery_count_shifts=[[b,v] for b,v in reductions.items()],
            repaired_delivery_duals=[[b,float(row[1])-reductions[b]] for b,row in delivery_counts.items()],
            subset_count_shifts=[[sid,v] for sid,v in cut_reductions.items()],
            repaired_subset_duals=[[sid,row[2]-cut_reductions[sid]] for sid,row in subsets.items()],
            capacity_repair_status=int(m.Status))
    finally:m.dispose()
