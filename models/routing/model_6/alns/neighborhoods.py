"""Order destroy/repair and route/reuse neighborhoods for M6."""
from .state import Route, capacity_ok, route_distance
from .construction import priority, local_windows_ok
import time


DESTROY = ('random_orders','related_orders','peak_orders','route_orders')
REPAIR = ('greedy','regret2','randomized')


def destroy(problem,state,value,name,rng):
    ids=list(problem.order_map)
    count=min(len(ids),max(1,rng.randint(1,max(1,len(ids)//3))))
    if name=='related_orders':
        anchor=problem.order_map[rng.choice(ids)]
        ids.sort(key=lambda oid:(problem.order_map[oid].satellite!=anchor.satellite,
                                 abs(problem.order_map[oid].delivery_window[0]-anchor.delivery_window[0])))
    elif name=='peak_orders' and value:
        t=max(value['issue'].values(),key=lambda t:sum(value['issue'][a]<=t<value['clean'][a] for a in ids))
        ids.sort(key=lambda a:(not(value['issue'][a]<=t<value['clean'][a]),-value['issue'][a]))
    elif name=='route_orders':
        routes=state.routes[rng.choice(('e1','e2'))]
        if routes:
            selected={a for a,k in rng.choice(routes).actions}
            ids=[a for a in ids if a in selected]+[a for a in ids if a not in selected]
            count=max(count,len(selected))
    else:rng.shuffle(ids)
    removed=set(ids[:count]);new=state.copy()
    for layer in new.routes:
        for r in new.routes[layer]:r.actions=[a for a in r.actions if a[0] not in removed]
        new.routes[layer]=[r for r in new.routes[layer] if r.actions]
    new.reuse=[(a,b) for a,b in new.reuse if a not in removed and b not in removed]
    return new,removed


def insertion_options(problem,state,layer,action,rng,deadline=None):
    rows=state.routes[layer];o=problem.order_map[action[0]]
    base=problem.depot if layer=='e1' else o.satellite
    proposals=[]
    for i,r in enumerate(rows):
        if deadline is not None and time.perf_counter()>=deadline:return []
        if r.base!=base:continue
        old=route_distance(problem,r)
        oldspan=max(priority(problem,layer,a) for a in r.actions)-min(priority(problem,layer,a) for a in r.actions)
        for j in range(len(r.actions)+1):
            acts=r.actions[:j]+[action]+r.actions[j:]
            if (action[0],'d') in acts and (action[0],'p') in acts and acts.index((action[0],'p'))<acts.index((action[0],'d')):continue
            rr=Route(layer,base,r.vehicle,acts)
            if not capacity_ok(problem,rr) or not local_windows_ok(problem,rr):continue
            spread=max(priority(problem,layer,a) for a in acts)-min(priority(problem,layer,a) for a in acts)
            score=problem.distance_cost[layer]*(route_distance(problem,rr)-old)+.001*(spread-oldspan)/60
            proposals.append((score,('insert',i,j,r.vehicle)))
    n=problem.fleet.e1_count if layer=='e1' else problem.fleet.e2_per_sat_count
    cap=problem.e1_max_trips if layer=='e1' else problem.operating_policy.e2_max_trips_per_vehicle
    for v in range(n):
        if sum(r.base==base and r.vehicle==v for r in rows)>=cap:continue
        rr=Route(layer,base,v,[action])
        if not capacity_ok(problem,rr):continue
        distance=problem.distance_cost[layer]*route_distance(problem,rr)
        target=priority(problem,layer,action)
        # All vehicle positions are possible. A small priority penalty orders ties.
        for i in range(len(rows)+1):
            before=[r for r in rows[:i] if r.base==base and r.vehicle==v]
            after=[r for r in rows[i:] if r.base==base and r.vehicle==v]
            inversion=sum(max(0,min(priority(problem,layer,a) for a in r.actions)-target) for r in before)
            inversion+=sum(max(0,target-min(priority(problem,layer,a) for a in r.actions)) for r in after)
            proposals.append((distance+.001*inversion/60,('new',i,0,v)))
    return sorted(proposals,key=lambda x:x[0])


def repair(problem,state,removed,name,rng,deadline=None):
    for layer in ('e1','e2'):
        pending=[(oid,k) for oid in sorted(removed) for k in ('d','p')]
        while pending:
            if deadline is not None and time.perf_counter()>=deadline:return None
            if name=='regret2':
                choices=[]
                for action in pending:
                    options=insertion_options(problem,state,layer,action,rng,deadline)
                    if not options:return None
                    regret=(options[1][0]-options[0][0]) if len(options)>1 else 1e6
                    choices.append((regret,action,options))
                _,action,options=max(choices,key=lambda x:x[0])
            else:
                action=(rng.choice(pending) if name=='randomized' else min(pending,key=lambda a:priority(problem,layer,a)))
                options=insertion_options(problem,state,layer,action,rng,deadline)
                if not options:return None
            option=options[rng.randrange(min(4,len(options)))] if name=='randomized' else options[0]
            _,(mode,i,j,v)=option
            if mode=='insert':state.routes[layer][i].actions.insert(j,action)
            else:
                base=problem.depot if layer=='e1' else problem.order_map[action[0]].satellite
                state.routes[layer].insert(i,Route(layer,base,v,[action]))
            pending.remove(action)
    return state


def mutate(problem,state,value,rng):
    new=state.copy();mode=rng.choice(('split','merge','relocate','trip_order','reuse'))
    if mode=='reuse':
        pairs=[(a,b) for a in problem.order_map for b in problem.order_map if a!=b and
               value['issue'][b]<value['clean'][a]<=value['issue_latest'][b]]
        if pairs and (not new.reuse or rng.random()<.75):new.reuse.append(rng.choice(pairs))
        elif new.reuse:new.reuse.pop(rng.randrange(len(new.reuse)))
        return new,mode
    layer=rng.choice(('e1','e2'));rows=new.routes[layer]
    if not rows:return None,mode
    i=rng.randrange(len(rows));r=rows[i]
    if mode=='split' and len(r.actions)>1:
        at=rng.randrange(1,len(r.actions));actions=r.actions[at:];r.actions=r.actions[:at]
        v=rng.randrange(problem.fleet.e1_count if layer=='e1' else problem.fleet.e2_per_sat_count)
        rows.insert(i+1,Route(layer,r.base,v,actions))
    elif mode=='merge':
        choices=[j for j,s in enumerate(rows) if j!=i and s.base==r.base]
        if not choices:return None,mode
        j=rng.choice(choices);r.actions+=rows[j].actions;rows.pop(j)
    elif mode=='relocate':
        a=r.actions.pop(rng.randrange(len(r.actions)))
        choices=[j for j,s in enumerate(rows) if s.base==r.base]
        s=rows[rng.choice(choices)];s.actions.insert(rng.randrange(len(s.actions)+1),a)
        new.routes[layer]=[r for r in rows if r.actions]
    elif mode=='trip_order':
        rows.pop(i);rows.insert(rng.randrange(len(rows)+1),r)
        if rng.random()<.3:r.vehicle=rng.randrange(problem.fleet.e1_count if layer=='e1' else problem.fleet.e2_per_sat_count)
    else:return None,mode
    return new,mode
