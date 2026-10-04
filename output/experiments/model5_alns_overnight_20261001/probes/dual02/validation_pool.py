from copy import deepcopy
import gurobipy as gp
import validation_core as check

def validate_slots(p,initial,slots,extra):
    original={t['trip_id']:t for t in initial['e2_trips']}
    assert len({t['trip_id'] for t in slots})==len(slots)
    assert set(original)<=set(t['trip_id'] for t in slots)
    for slot in slots:
        assert slot['satellite_id'] in p.satellites and 0<=slot['vehicle_id']<p.fleet.e2_per_sat_count
        assert 0<=slot['trip_index']<p.operating_policy.e2_max_trips_per_vehicle
        if slot['trip_id'] in original:
            assert {k:v for k,v in slot.items() if k!='trip_index'}=={k:v for k,v in original[slot['trip_id']].items() if k!='trip_index'}
        else:
            assert extra>0 and slot['_pool_optional'] and slot['events']==[]
            assert slot['departure']==slot['return_complete']==0
    for site in {o.satellite for o in p.orders}:
        for vehicle in range(p.fleet.e2_per_sat_count):
            old=sorted([t for t in initial['e2_trips'] if (t['satellite_id'],t['vehicle_id'])==(site,vehicle)],key=lambda t:t['trip_index'])
            rows=sorted([t for t in slots if (t['satellite_id'],t['vehicle_id'])==(site,vehicle)],key=lambda t:t['trip_index'])
            assert len(rows)==min(len(old)+extra,p.operating_policy.e2_max_trips_per_vehicle)
            assert [t['trip_id'] for t in rows if t['trip_id'] in original]==[t['trip_id'] for t in old]
            assert len({t['trip_index'] for t in rows})==len(rows)

def project_pool(p,initial,result,slots,pools,path):
    model=gp.read(str(path))
    try:
        variables={v.VarName:v for v in model.getVars()}
        def fix(name,value):
            var=variables[name];var.LB=var.UB=value
        patterns=check.all_patterns(p)
        lookup={(r['batch'],r['delivery'],r['pickup']):i for i,r in enumerate(patterns)}
        chosen=set();amounts={}
        for trip in result['e1_trips']:
            ds=tuple(e['site_id'] for e in trip['events'] if e['kind']=='d')
            ps=tuple(e['site_id'] for e in trip['events'] if e['kind']=='p')
            i=lookup[trip['batch_id'],ds,ps];chosen.add(i)
            for e in trip['events']:
                if e['kind']=='p':amounts[i,e['site_id']]=e['quantity']
        for i,r in enumerate(patterns):
            check.close(variables[f'route[{i}]'].Obj,r['cost']);fix(f'route[{i}]',int(i in chosen))
            for site in r['pickup']:fix(f'quantity_{i}_{site}',amounts.get((i,site),0))
        active={t['trip_id']:t for t in result['e2_trips']}
        # The unchanged fallback may retain its original slot indices; IDs
        # still identify the same chronologically ordered physical slots.
        for i,(slot,pool) in enumerate(zip(slots,pools)):
            trip=active.get(slot['trip_id']);keys=[(e['order_id'],e['kind']) for e in trip['events']] if trip else []
            selected=next((j for j,c in enumerate(pool) if [tuple(k) for k in c['tasks']]==keys),None) if trip else None
            assert not trip or selected is not None
            fix(f'pool_used_{i}',int(trip is not None))
            fix(f'pool_start_{i}',trip['departure'] if trip else 0);fix(f'pool_end_{i}',trip['return_complete'] if trip else 0)
            for j,column in enumerate(pool):
                check.close(variables[f'pool_choice_{i}[{j}]'].Obj,column['cost'])
                fix(f'pool_choice_{i}[{j}]',int(j==selected))
        for trip in result['e2_trips']:
            for e in trip['events']:
                oid,kind=e['order_id'],e['kind']
                fix(('delivery_' if kind=='d' else 'pickup_time_')+oid,e['start'])
                if kind=='p':
                    fix('e2_return_'+oid,trip['return_complete'])
                    first=next(b.id for b in p.operating_policy.batches if trip['return_complete']<=b.cutoff)
                    for b in p.operating_policy.batches:fix(f'cutoff_{oid}_{b.id}',int(first==b.id))
        for o in p.orders:
            for kind in ('d','p'):
                constraint=model.getConstrByName(f'pool_cover_{o.id}_{kind}')
                assert constraint.Sense=='=' and constraint.RHS==1
                expected={f'pool_choice_{i}[{j}]':1 for i,pool in enumerate(pools) for j,c in enumerate(pool)
                          if (o.id,kind) in [tuple(k) for k in c['tasks']]}
                assert check.expr(model.getRow(constraint))==expected
        # Validate every pair of slots, including pairs separated by unused
        # placeholders, against the physical timing inequalities.
        rows=set()
        for c in model.getConstrs():
            if c.Sense=='>':
                terms=check.expr(model.getRow(c))
                if any(name.startswith('pool_start_') for name in terms):
                    rows.add((c.RHS,tuple(sorted(terms.items()))))
        M=p.operating_policy.cycle_end+p.operating_policy.e2_turnaround_time
        pairs=0
        for site in {o.satellite for o in p.orders}:
            for vehicle in range(p.fleet.e2_per_sat_count):
                indices=sorted([i for i,t in enumerate(slots) if (t['satellite_id'],t['vehicle_id'])==(site,vehicle)],key=lambda i:slots[i]['trip_index'])
                for pos,i in enumerate(indices):
                    for j in indices[pos+1:]:
                        terms={f'pool_start_{j}':1,f'pool_end_{i}':-1,f'pool_used_{i}':-M,f'pool_used_{j}':-M}
                        if p.operating_policy.e2_dispatch_prep_time:
                            terms.update({f'pool_choice_{j}[{k}]':-p.operating_policy.e2_dispatch_prep_time
                                          for k,c in enumerate(pools[j]) if any(kind=='d' for _,kind in c['tasks'])})
                        assert (p.operating_policy.e2_turnaround_time-2*M,tuple(sorted(terms.items()))) in rows
                        pairs+=1
        model.Params.OutputFlag=0;model.Params.Threads=1;model.Params.TimeLimit=30
        model.Params.FeasibilityTol=model.Params.IntFeasTol=1e-9
        model.optimize();assert model.Status==gp.GRB.OPTIMAL
        check.close(model.ObjVal,result['info']['objective'])
        return dict(path=str(path),full_solution_projection=True,projected_objective=model.ObjVal,
                    physical_pair_rows=pairs,all_tasks_exactly_covered=True,patterns=len(patterns))
    finally:model.dispose()
