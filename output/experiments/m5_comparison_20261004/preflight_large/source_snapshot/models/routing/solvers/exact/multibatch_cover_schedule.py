"""Exact physical scheduling conditional on fixed complete E2 paths.

Every active site's ordered paths are fixed. Each real vehicle has
min(number_of_site_paths, original_max_trips) interchangeable positions.
Any physical schedule of these paths can be packed into these positions
in vehicle order, including its original waiting, preparation, turnaround,
use times and actual returns. The result is a conditional certificate,
never an unconditional full-M5 bound or optimality proof.
"""
import json
import math
from pathlib import Path
import time
from .multibatch_cover_pool import preserve_cover_routes
from .multibatch_route_pool import add_route_pool, extract_pool
from .multibatch_e1_routes import build_e1_routes
from .multibatch_e1_inventory import build_e1_inventory
from ...common.cycle.audit import audit_solution


def build_cover_schedule(problem, covers, *, deadline=None):
    if (problem.model_number != 5 or problem.free_assignment or not problem.reusable
            or len(problem.operating_policy.batches) <= 1):
        raise ValueError('Fixed reusable multibatch M5 required')
    active = {o.satellite for o in problem.orders}
    if not isinstance(covers, dict) or set(covers) != active:
        raise ValueError('Complete covers for exactly every active site required')
    trips = []
    for site in sorted(active):
        if not covers[site]:
            raise ValueError('Nonempty complete cover required')
        count = min(len(covers[site]), problem.operating_policy.e2_max_trips_per_vehicle)
        for vehicle in range(problem.fleet.e2_per_sat_count):
            for index in range(count):
                trips.append(dict(trip_id=f'cover_{site}_{vehicle}_{index}', satellite_id=site,
                    vehicle_id=vehicle, trip_index=index, events=[], departure=0,
                    return_complete=0, _pool_optional=True))
    layout = dict(e1_trips=[], e2_trips=trips)
    pools, preservation = preserve_cover_routes(problem, layout, [[] for _ in trips], covers, deadline=deadline)
    backend = ('patterns' if len(active) <= 4 and problem.operating_policy.e1_turnaround_time == 0
               else 'indexed')
    model, context = (build_e1_routes(problem, deadline=deadline) if backend == 'patterns'
                      else build_e1_inventory(problem, deadline=deadline))
    try:
        add_route_pool(problem, model, context, layout, pools, deadline=deadline)
    except Exception:
        model.dispose()
        raise
    return model, context, layout, pools, backend, preservation


def conditional_cover_schedule(problem, covers, *, time_limit=90, seed=42, diagnostics_dir=None):
    if not math.isfinite(time_limit) or time_limit <= 0:
        raise ValueError('Finite positive total budget required')
    started = time.perf_counter()
    deadline = started + time_limit
    certificate = dict(scope='exact_physical_schedule_conditioned_on_all_fixed_complete_paths',
        full_proven_optimal=False, full_proven_infeasible=False, global_lower_bound=None,
        conditional_optimal=False, conditional_infeasible=False, conditional_lower_bound=0.0,
        status='TIME_LIMIT_BUILD', original_business_and_physical_fleet_preserved=True,
        original_E2_trip_limit_preserved=True, canonical_position_completeness=True)
    model, candidate = None, None
    try:
        model, context, layout, pools, backend, preservation = build_cover_schedule(problem,covers,deadline=deadline)
        certificate.update(backend=backend, slots=len(layout['e2_trips']),
            route_choices=sum(map(len,pools)), cover_paths=preservation['cover_paths'])
        if diagnostics_dir is not None:
            out=Path(diagnostics_dir);out.mkdir(exist_ok=True,parents=True)
            model.write(str(out/'route_pool.mps.gz'))
            for name,value in [('columns.json',pools),('slots.json',layout['e2_trips']),('covers.json',covers)]:
                (out/name).write_text(json.dumps(value,indent=2)+'\n')
        model.Params.Threads=1
        model.Params.Seed=int(seed)
        model.Params.IntFeasTol=model.Params.FeasibilityTol=1e-9
        model.Params.TimeLimit=max(0,deadline-time.perf_counter())
        model.optimize()
        import gurobipy as gp
        certificate.update(status=int(model.Status),conditional_optimal=model.Status==gp.GRB.OPTIMAL,
            conditional_infeasible=model.Status==gp.GRB.INFEASIBLE,solver_runtime=model.Runtime)
        if model.Status != gp.GRB.INFEASIBLE:
            raw=float(model.ObjBound)
            if math.isfinite(raw) and abs(raw)<gp.GRB.INFINITY:
                margin=1e-7*max(1.0,abs(raw))
                certificate.update(raw_conditional_bound=raw,margin=margin,
                    conditional_lower_bound=max(0.0,raw-margin))
        if model.SolCount:
            candidate=extract_pool(problem,context,layout,pools,backend)
            audit=audit_solution(problem,candidate)
            if not audit['valid'] or not math.isclose(audit['objective'],model.ObjVal,abs_tol=1e-5,rel_tol=1e-7):
                raise RuntimeError('Conditional schedule failed original complete audit')
            certificate['audited_full_upper']=audit['objective']
            candidate['audit']=audit
            candidate['info']=dict(status='FEASIBLE',objective=audit['objective'],best_bound=None,mip_gap=None,
                proven_optimal=False,proven_infeasible=False,proof_scope=certificate['scope'])
    except TimeoutError:
        pass
    finally:
        if model is not None:model.dispose()
    certificate['runtime']=time.perf_counter()-started
    return dict(certificate=certificate,result=candidate)
