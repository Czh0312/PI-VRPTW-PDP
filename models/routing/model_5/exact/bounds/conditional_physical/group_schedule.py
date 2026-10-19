"""Conditional physical schedules with every visiting order of fixed task sets.

Exhaustive permutations retain cost-dominated paths. If generation stops,
no partial domain may supply an infeasibility proof or positive lower bound.
Canonical positions per real vehicle cover every schedule of the fixed
number of groups, respecting original trip limits. Full-M5 flags stay false.
"""
from itertools import permutations
import json
import math
from pathlib import Path
import time
from models.routing.model_5.exact.bounds.conditional_physical.physical_site import normalize_trip_groups
from models.routing.model_5.exact.bounds.route_cg.pickup_pricing import route_context, evaluate_column
from models.routing.model_5.exact.primal.route_pool import add_route_pool, extract_pool
from models.routing.model_5.exact.bounds.inventory.e1_routes import build_e1_routes
from models.routing.model_5.exact.bounds.inventory.e1_inventory import build_e1_inventory
from models.common.cycle.audit import audit_solution


class NoGroupPath(ValueError):
    """An exhaustively tested task set has no locally valid visiting order."""


def enumerate_group_paths(problem,groups,*,deadline=None):
    if (problem.model_number!=5 or problem.free_assignment or not problem.reusable
            or len(problem.operating_policy.batches)<=1):
        raise ValueError('Fixed reusable multibatch M5 required')
    active={o.satellite for o in problem.orders}
    if not isinstance(groups,dict) or set(groups)!=active:
        raise ValueError('A complete task partition for exactly every active site required')
    normalized={s:normalize_trip_groups(problem,s,g) for s,g in groups.items()}
    columns,checked={},0
    for site in sorted(active):
        context=route_context(problem,site);rows=[]
        for group in normalized[site]:
            paths=[]
            for sequence in permutations(group):
                if deadline is not None and time.perf_counter()>=deadline:
                    raise TimeoutError('All-permutations group generation exceeded budget')
                checked+=1
                column=evaluate_column(problem,context,sequence)
                if column is not None:paths.append(column)
            if not paths:
                raise NoGroupPath('Complete permutation domain empty for '+repr((site,group)))
            rows.extend(paths)
        columns[site]=rows
    return normalized,columns,dict(permutations_complete=True,permutations_checked=checked,
        distinct_paths=sum(map(len,columns.values())),cost_dominated_paths_retained=True)


def build_group_schedule(problem,groups,*,deadline=None):
    normalized,columns,proof=enumerate_group_paths(problem,groups,deadline=deadline)
    trips=[]
    for site in sorted(normalized):
        count=min(len(normalized[site]),problem.operating_policy.e2_max_trips_per_vehicle)
        for vehicle in range(problem.fleet.e2_per_sat_count):
            for index in range(count):
                trips.append(dict(trip_id=f'group_{site}_{vehicle}_{index}',satellite_id=site,
                    vehicle_id=vehicle,trip_index=index,events=[],departure=0,return_complete=0,_pool_optional=True))
    layout=dict(e1_trips=[],e2_trips=trips)
    pools=[columns[t['satellite_id']] for t in trips]
    backend='patterns' if len(normalized)<=4 and problem.operating_policy.e1_turnaround_time==0 else 'indexed'
    model,context=(build_e1_routes(problem,deadline=deadline) if backend=='patterns'
        else build_e1_inventory(problem,deadline=deadline))
    try:add_route_pool(problem,model,context,layout,pools,deadline=deadline)
    except Exception:
        model.dispose();raise
    return model,context,layout,pools,backend,normalized,proof


def conditional_group_schedule(problem,groups,*,time_limit=90,seed=42,diagnostics_dir=None):
    if not math.isfinite(time_limit) or time_limit<=0:
        raise ValueError('Finite positive total budget required')
    started=time.perf_counter();deadline=started+time_limit
    certificate=dict(scope='M5_conditional_all_path_complete_task_groups_total_cost',
        full_proven_optimal=False,full_proven_infeasible=False,global_lower_bound=None,
        conditional_optimal=False,conditional_infeasible=False,conditional_lower_bound=0.0,
        permutations_complete=False,canonical_position_completeness=True,
        status='TIME_LIMIT_BUILD',groups=groups)
    model,candidate=None,None
    try:
        model,context,layout,pools,backend,normalized,proof=build_group_schedule(problem,groups,deadline=deadline)
        certificate.update(**proof,groups=normalized,backend=backend,slots=len(pools),route_choices=sum(map(len,pools)))
        if diagnostics_dir is not None:
            out=Path(diagnostics_dir);out.mkdir(parents=True,exist_ok=True)
            model.write(str(out/'route_pool.mps.gz'))
            for name,value in [('columns.json',pools),('slots.json',layout['e2_trips']),('groups.json',normalized)]:
                (out/name).write_text(json.dumps(value,indent=2)+'\n')
        model.Params.Threads=1;model.Params.Seed=int(seed)
        model.Params.IntFeasTol=model.Params.FeasibilityTol=1e-9
        model.Params.TimeLimit=max(0,deadline-time.perf_counter());model.optimize()
        import gurobipy as gp
        certificate.update(status=int(model.Status),conditional_optimal=model.Status==gp.GRB.OPTIMAL,
            conditional_infeasible=model.Status==gp.GRB.INFEASIBLE,solver_runtime=model.Runtime)
        if model.Status!=gp.GRB.INFEASIBLE:
            raw=float(model.ObjBound)
            if math.isfinite(raw) and abs(raw)<gp.GRB.INFINITY:
                margin=1e-7*max(1.0,abs(raw))
                certificate.update(raw_conditional_bound=raw,margin=margin,conditional_lower_bound=max(0.0,raw-margin))
        if model.SolCount:
            candidate=extract_pool(problem,context,layout,pools,backend);audit=audit_solution(problem,candidate)
            if not audit['valid'] or not math.isclose(audit['objective'],model.ObjVal,abs_tol=1e-5,rel_tol=1e-7):
                raise RuntimeError('All-path group schedule failed original complete audit')
            certificate['audited_full_upper']=audit['objective'];candidate['audit']=audit
            candidate['info']=dict(status='FEASIBLE',objective=audit['objective'],best_bound=None,mip_gap=None,
                proven_optimal=False,proven_infeasible=False,proof_scope=certificate['scope'])
    except TimeoutError:pass
    except NoGroupPath as error:
        certificate.update(status=3,conditional_infeasible=True,permutations_complete=False,
            empty_group_domain_complete=True,empty_group_domain_proof=str(error))
    finally:
        if model is not None:model.dispose()
    certificate['runtime']=time.perf_counter()-started
    return dict(certificate=certificate,result=candidate)
