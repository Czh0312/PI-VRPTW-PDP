"""Necessary trip/recovery links missing from the optimistic shared master.

All pickups in one physical E2 trip unload at its common end, so the original
first-eligible-cutoff rule recovers them in the same batch. A trip loaded in
batch b cannot unload before that batch starts, so its recovery cannot be in
an earlier batch. Actual route orders and times remain completely free.
"""


def augment(problem,model,ctx,library):
    import gurobipy as gp

    orders=list(problem.orders)
    n=len(orders)
    B=max(ctx['selectors'])
    pickups={row['mask']>>n for row in library if (row['mask']>>n).bit_count()>1}
    same=model.addVars(sorted(pickups),range(B),lb=0,ub=1,name='sync_pickup_batch')
    for group in sorted(pickups):
        for b in range(B):
            for i in range(n):
                if group & (1<<i):
                    model.addConstr(same[group,b]<=ctx['recovery'][i,b])
    precedence=0
    for c,row in enumerate(library):
        dm=row['mask'] & ((1<<n)-1)
        pm=row['mask']>>n
        if pm in pickups:
            model.addConstr(ctx['route'][c]<=gp.quicksum(same[pm,b] for b in range(B)))
        if dm and pm:
            i=(dm & -dm).bit_length()-1
            j=(pm & -pm).bit_length()-1
            for b in range(1,B):
                model.addConstr(ctx['route'][c]+gp.quicksum(ctx['assignments'][i,h] for h in range(b,B))
                    +gp.quicksum(ctx['recovery'][j,h] for h in range(b))<=2)
                precedence+=1
    model.update()
    return dict(pickup_groups=len(pickups),conditional_precedence_rows=precedence,
                proof='Common E2 unload time implies a common first eligible recovery cutoff; '
                      'a delivery-loaded trip cannot recover before its loading batch.')


def violations(problem,event):
    orders=list(problem.orders)
    n=len(orders)
    failures=[]
    for mask in event['masks']:
        delivery={event['delivery_batches'][orders[i].id] for i in range(n) if mask & (1<<i)}
        pickup={event['recovery_batches'][orders[i].id] for i in range(n) if mask & (1<<(n+i))}
        if len(pickup)>1:
            failures.append(dict(mask=mask,reason='different_recovery_batches_in_one_trip'))
        if delivery and pickup and max(delivery)>min(pickup):
            failures.append(dict(mask=mask,reason='trip_recovery_before_delivery_loading_batch'))
    return failures
