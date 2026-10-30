"""Read-only model construction census. No optimization and no source edits."""
from pathlib import Path
import json
import diagnose as d

def main():
    config=d.BASE/'cases/S01_m8_k1_4.json'
    parent=d.load_instance(*d.load_routing_case(config,use_pool_config=False))
    rows=[]
    for compact in (False,True):
        for count in parent.batch_count_candidates:
            problem=parent.for_count(count)
            model,ctx=d.build_model(problem,compact_e2=compact)
            model.update()
            row=dict(formulation='compact' if compact else 'expanded',K=count,
                variables=model.NumVars,binary=model.NumBinVars,
                other_integer=model.NumIntVars-model.NumBinVars,
                continuous=model.NumVars-model.NumIntVars,
                linear_constraints=model.NumConstrs,indicator_constraints=model.NumGenConstrs,
                e1_route_slots=len(ctx['e1']),e2_route_slots=len(ctx['e2']),
                order_batch_assignment_variables=len(ctx['assignments']),
                task_times=len(ctx['variables'].task_times),
                batch_start_variables=sum(isinstance(v,d.gp.Var) for v in ctx['batch_starts'].values()),
                cutoff_variables=sum(isinstance(v,d.gp.Var) for v in ctx['cutoffs'].values()))
            rows.append(row);model.dispose()
    result=dict(case='S01',customers=len(parent.orders),satellites=len(parent.satellites),
        e1_vehicles=parent.fleet.e1_count,e2_vehicles_per_satellite=parent.fleet.e2_per_sat_count,
        candidate_K=list(parent.batch_count_candidates),
        cycle_minutes=(parent.operating_policy.cycle_end-parent.operating_policy.cycle_start)*parent.operating_policy.time_tick_seconds/60,
        time_tick_seconds=parent.operating_policy.time_tick_seconds,
        configured_max_e2_trips_per_vehicle=parent.operating_policy.e2_max_trips_per_vehicle,
        rows=rows,optimization_performed=False)
    d.save('size_inventory.json',result)
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
