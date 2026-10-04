import math
from models.routing.solvers.exact.multibatch_route_pricing import route_context
from validation_columns import close, replay_column
from types import SimpleNamespace
check=SimpleNamespace(replay_column=replay_column)
columns=0
repair_checks=0

def count_repair_independent(p,site,w):
    """Independently solve the correction LP and check its full-domain proof."""
    import gurobipy as gp
    pi={tuple(k):v for k,v in w['duals']};K=w['maximum_trips'];nu=w['fleet_dual']
    eligible={g['category']:g['lower_bound'] for g in w['pricing']['groups'] if not g['infeasible']}
    rows={bid:(count,value) for bid,count,value in w['delivery_counts']}
    sizes={b:sum((None if k[1]=='p' else p.order_map[k[0]].batch)==b for k in pi) for b in eligible}
    m=gp.Model('independent_count_dual_repair');m.Params.OutputFlag=0;m.Params.Threads=1
    try:
        offset=m.addVar();task={b:m.addVar() for b in eligible}
        reductions={b:m.addVar(ub=rows[b][1]) for b in eligible if b in rows}
        for b,rho in eligible.items():m.addConstr(offset+task[b]+reductions.get(b,0)>=-rho)
        m.setObjective(K*offset+gp.quicksum(sizes[b]*task[b] for b in eligible)
                       +gp.quicksum(rows[b][0]*reductions[b] for b in reductions))
        m.optimize();assert m.Status==gp.GRB.OPTIMAL
        original=math.fsum(pi.values())+K*nu+math.fsum(n*lam for n,lam in rows.values())\
                 +math.fsum(n*lam for _,_,n,lam in w.get('subset_counts',[]))
        optimal=original-m.ObjVal
    finally:m.dispose()
    record=w['dual_repair'];assert record['repair_mode']=='delivery_count_first'
    close(record['bound'],optimal)
    updated={tuple(k):v for k,v in record['repaired_duals']};assert set(updated)==set(pi)
    offset=record['fleet_shift'];assert offset>=0
    close(record['repaired_fleet_dual'],nu-offset)
    changes=dict(record['delivery_count_shifts']);new_prices=dict(record['repaired_delivery_duals'])
    assert set(changes)==set(new_prices)==set(rows)
    shifts=dict(record['category_shifts']);assert set(shifts)==set(eligible)
    for bid,(required,lam) in rows.items():
        assert -1e-9<=changes[bid]<=lam+1e-9 and new_prices[bid]>=-1e-9
        close(new_prices[bid],lam-changes[bid])
    for b,rho in eligible.items():
        assert shifts[b]>=-1e-9
        # All routes of delivery category b hit its count row and at least
        # one delivery task. Pickup-only routes contain at least one pickup.
        # Other task reductions are nonnegative and further relax the row.
        assert rho+offset+changes.get(b,0)+shifts[b]>=-1e-8
    for key,value in pi.items():
        close(updated[key],value-shifts.get(None if key[1]=='p' else p.order_map[key[0]].batch,0))
    value=math.fsum(updated.values())+K*record['repaired_fleet_dual']\
          +math.fsum(required*new_prices[bid] for bid,(required,_) in rows.items())\
          +math.fsum(n*lam for _,_,n,lam in w.get('subset_counts',[]))
    close(record['bound'],value)
    return dict(bound=optimal)

def repair_independent(p,site,w):
    if w.get('dual_repair',{}).get('repair_mode')=='delivery_count_first':
        return count_repair_independent(p,site,w)
    pi={tuple(k):v for k,v in w['duals']};K=w['maximum_trips'];nu=w['fleet_dual']
    eligible=[g for g in w['pricing']['groups'] if not g['infeasible']]
    best=None
    for offset in {0.0}|{max(0,-g['lower_bound']) for g in eligible}:
        shifts={g['category']:max(0,-g['lower_bound']-offset) for g in eligible}
        new={key:value-shifts.get(None if key[1]=='p' else p.order_map[key[0]].batch,0) for key,value in pi.items()}
        # In each delivery category at least one D is mandatory; in the
        # pickup-only category at least one P is mandatory. Other changes
        # only increase reduced cost. This covers all paths, not a seed pool.
        assert all(g['lower_bound']+offset+shifts[g['category']]>=-1e-9 for g in eligible)
        bound=math.fsum(new.values())+K*(nu-offset)+math.fsum(count*value for _,count,value in w.get('delivery_counts',[]))+math.fsum(count*value for _,_,count,value in w.get('subset_counts',[]))
        if best is None or bound>best['bound']:
            best=dict(bound=bound,repaired_duals=[[list(k),v] for k,v in new.items()],
                repaired_fleet_dual=nu-offset,fleet_shift=offset,category_shifts=[[k,v] for k,v in shifts.items()])
    if 'dual_repair' in w:
        record=w['dual_repair'];close(record['bound'],best['bound'])
        updated={tuple(k):v for k,v in record['repaired_duals']}
        # Equivalent breakpoints may tie after adding the capacity constant.
        # Validate the recorded feasible repair and its optimal value, not
        # which equally good breakpoint floating-point rounding selected.
        offset=record['fleet_shift'];assert offset>=0
        shifts={g['category']:max(0,-g['lower_bound']-offset) for g in eligible}
        assert all(g['lower_bound']+offset+shifts[g['category']]>=-1e-9 for g in eligible)
        close(record['repaired_fleet_dual'],nu-offset)
        for key,value in pi.items():
            close(updated[key],value-shifts.get(None if key[1]=='p' else p.order_map[key[0]].batch,0))
        close(record['bound'],math.fsum(updated.values())+K*(nu-offset)
              +math.fsum(count*value for _,count,value in w.get('delivery_counts',[]))
              +math.fsum(count*value for _,_,count,value in w.get('subset_counts',[])))
    return best

def verify_capacity_site(p, payload, context=None):
    row=payload['site'];site=row['satellite'];context=context or route_context(p,site)
    node_map=context['lookup'];candidates=[0.0];repairs=[]
    global columns,repair_checks
    for col in row['columns']:check.replay_column(p,site,col);columns+=1
    for index,w in enumerate(row['iterations']):
        if w['method']!='integer_pricing':continue
        pi={tuple(k):v for k,v in w['duals']};nu=w['fleet_dual'];K=w['maximum_trips']
        assert set(pi)==set(node_map) and nu<=0 and K==context['maximum_trips']
        category={};total=0.0;subset={}
        for bid,count,value in w.get('delivery_counts',[]):
            ns=[n for n in node_map.values() if n['key'][1]=='d' and n['order'].batch==bid]
            required=max(1,max(math.ceil(math.fsum(n[r] for n in ns)/(cap+1e-6)) for r,cap in [('weight',p.fleet.e2_weight_cap),('volume',p.fleet.e2_volume_cap)]))
            assert count==required and value>=0;category[bid]=value;total+=count*value
        for sid,keys,count,value in w.get('subset_counts',[]):
            keys=frozenset(tuple(k) for k in keys)
            assert keys and keys<=set(node_map) and all(k[1]=='d' for k in keys)
            ns=[node_map[k] for k in keys]
            required=max(1,max(math.ceil(math.fsum(n[r] for n in ns)/(cap+1e-6)) for r,cap in [('weight',p.fleet.e2_weight_cap),('volume',p.fleet.e2_volume_cap)]))
            assert count==required and value>=0;subset[sid]=(keys,value);total+=count*value
        price=w['pricing'];groups=price['groups']
        assert {g['category'] for g in groups}=={None}|{n['order'].batch for n in node_map.values() if n['key'][1]=='d'}
        for g in groups:
            if g['infeasible']:continue
            eligible={k for k,n in node_map.items() if k[1]=='p' or n['order'].batch==g['category']}
            prizes=[value for keys,value in subset.values() if keys&eligible]
            analytic=-math.fsum(max(0,pi[k]) for k in eligible)-nu-math.fsum(max(0,v) for v in prizes)-category.get(g['category'],0)
            if g['runtime']==0 and g['margin']==0:
                close(g['lower_bound'],analytic);close(g['raw_lower_bound'],analytic)
            elif g['raw_lower_bound'] is None:close(g['lower_bound'],analytic)
            else:
                margin=1e-7*max(1,math.fsum(abs(pi[k]) for k in eligible)+math.fsum(abs(v) for v in prizes),abs(nu))
                close(g['margin'],margin);close(g['lower_bound'],max(analytic,g['raw_lower_bound']-margin))
        close(price['lower_bound'],min(g['lower_bound'] for g in groups if not g['infeasible']))
        for col in w.get('new_columns',[]):check.replay_column(p,site,col);columns+=1
        if price['reduced_cost'] is not None:
            col=w['new_column'];check.replay_column(p,site,col)
            batch={p.order_map[oid].batch for oid,kind in col['tasks'] if kind=='d'}
            reduced=(0 if w['phase']=='feasibility' else col['cost'])-math.fsum(pi[tuple(k)] for k in col['tasks'])-nu-math.fsum(category.get(b,0) for b in batch)-math.fsum(value for keys,value in subset.values() if keys.intersection(tuple(tuple(k) for k in col['tasks'])))
            close(reduced,price['reduced_cost'])
            assert price['lower_bound']<=reduced+1e-5
        if w['phase']=='distance':
            standard=math.fsum(pi.values())+K*nu+total+K*min(0,price['lower_bound'])
            fixed=repair_independent(p,site,w);repair_checks+=1
            close(w['standard_candidate_bound'],standard)
            close(w['candidate_bound'],max(standard,fixed['bound']))
            candidates.append(w['candidate_bound']);repairs.append((w['candidate_bound'],index))
    close(row['bound'],max(candidates))
    return max(repairs, default=(0.0, None))
