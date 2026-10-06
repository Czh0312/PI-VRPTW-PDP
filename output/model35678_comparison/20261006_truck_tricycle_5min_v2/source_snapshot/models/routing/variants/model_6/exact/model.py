"""Complete joint order-batch, pool and two-echelon cost MILP.

No fixed-batch distance cuts are used. Route-indexed arcs retain a simple
auditable reference formulation, including pure recovery on inactive slots.
"""
from __future__ import annotations

import time
from ..model import validate_problem


def build_model(problem, *, verbose=False, deadline=None):
    validate_problem(problem)
    if getattr(problem, 'uniform_cycle', False):
        raise ValueError('Uniform cycle has one MILP per batch count; use solve_exact or problem.for_count(K)')
    import gurobipy as gp
    from gurobipy import GRB
    p, f = problem.operating_policy, problem.fleet
    H, Q = p.cycle_end, problem.total
    m = gp.Model(f'model{getattr(problem, "calendar_model_number", 6)}_joint_batch_cost_v1')
    m.Params.OutputFlag = int(verbose)
    m.Params.Threads = 1
    m.Params.MIPGap = 0
    m.Params.MIPGapAbs = 0

    def budget():
        if deadline is not None and time.perf_counter() >= deadline:
            raise TimeoutError('M6 model construction reached its total budget')

    try:
        variable_calendar = getattr(problem, 'variable_calendar', False)
        if variable_calendar:
            starts = {b.id: m.addVar(vtype=GRB.INTEGER, lb=problem.start_bounds[b.id][0],
                                    ub=problem.start_bounds[b.id][1], name=f'batch_start_{b.id}')
                      for b in p.batches}
            ends = {b.id: (starts[p.batches[i+1].id] if i+1 < len(p.batches) else p.cycle_end)
                    for i, b in enumerate(p.batches)}
        else:
            starts = {b.id: b.start for b in p.batches}
            ends = {b.id: b.end for b in p.batches}
        if problem.shared_cutoff:
            lo,hi = problem.shared_cutoff_bounds
            offset = m.addVar(vtype=GRB.INTEGER, lb=lo, ub=hi, name='shared_cutoff_offset')
            cutoffs = {b.id:b.start+offset for b in p.batches}
        else:
            cutoffs = {b.id:(m.addVar(vtype=GRB.INTEGER, lb=problem.cutoff_bounds[b.id][0],
                                ub=problem.cutoff_bounds[b.id][1], name=f'cutoff_{b.id}')
                         if problem.optimize_cutoffs else b.cutoff) for b in p.batches}
        if variable_calendar:
            for b in p.batches:
                m.addConstr(cutoffs[b.id] >= starts[b.id]+problem.minimum_phase_ticks)
                m.addConstr(cutoffs[b.id] <= ends[b.id]-problem.minimum_phase_ticks)
        y = {(o.id, b):m.addVar(vtype=GRB.BINARY, name=f'assign_{o.id}_{b}')
             for o in problem.orders for b in problem.legal_batches[o.id]}
        z = {b.id:m.addVar(vtype=GRB.BINARY, name=f'delivery_active_{b.id}') for b in p.batches}
        for o in problem.orders:
            m.addConstr(gp.quicksum(y[o.id,b] for b in problem.legal_batches[o.id]) == 1)
            if variable_calendar:
                for bid in problem.legal_batches[o.id]:
                    m.addConstr((y[o.id,bid] == 1) >> (starts[bid] >= o.release_time))
        for b in p.batches:
            members = [v for (oid,bid),v in y.items() if bid==b.id]
            for v in members:
                m.addConstr(v <= z[b.id])
            m.addConstr(z[b.id] <= gp.quicksum(members))
        counts = {(s,b.id):gp.quicksum(o.count*y[o.id,b.id] for o in problem.orders
                    if o.satellite==s and (o.id,b.id) in y) for s in problem.satellites for b in p.batches}
        weights = {(s,b.id):gp.quicksum((o.goods_weight+o.count*f.container_weight)*y[o.id,b.id]
                    for o in problem.orders if o.satellite==s and (o.id,b.id) in y)
                    for s in problem.satellites for b in p.batches}
        N = m.addVar(vtype=GRB.INTEGER, lb=problem.pool_min, ub=problem.pool_max, name='container_pool')
        supplied = {(s,b.id):m.addVar(vtype=GRB.INTEGER, ub=problem.cutoff_bounds[b.id][1], name=f'supply_{s}_{b.id}')
                    for s in problem.satellites for b in p.batches}
        for (s,bid),value in supplied.items():
            m.addConstr(value <= cutoffs[bid])
        task_times = {(o.id,k):m.addVar(vtype=GRB.INTEGER, lb=(o.delivery_window if k=='d' else o.pickup_window)[0],
                        ub=(o.delivery_window if k=='d' else o.pickup_window)[1], name=f'time_{o.id}_{k}')
                        for o in problem.orders for k in ('d','p')}
        slot_count = sum(min(p.e2_max_trips_per_vehicle,2*sum(o.satellite==s for o in problem.orders))
                         *f.e2_per_sat_count for s in problem.satellites)
        causal_limit = 2*len(problem.orders)+2*slot_count+1
        steps = {key:m.addVar(ub=causal_limit, name=f'causal_{key[0]}_{key[1]}') for key in task_times}
        routes = {'e1':[], 'e2':[]}

        def make_route(nodes, base, layer, label, batch=None):
            budget()
            cap_w, cap_v = (f.e1_weight_cap,f.e1_volume_cap) if layer=='e1' else (f.e2_weight_cap,f.e2_volume_cap)
            unload = p.depot_return_unload_time if layer=='e1' else p.e2_return_unload_time
            used = m.addVar(vtype=GRB.BINARY,name=label+'_used')
            start = m.addVar(vtype=GRB.INTEGER,ub=H,name=label+'_start')
            end = m.addVar(vtype=GRB.INTEGER,ub=H,name=label+'_end')
            visit = m.addVars(len(nodes),vtype=GRB.BINARY,name=label+'_visit')
            times = ({i:task_times[n['order'].id,n['kind']] for i,n in enumerate(nodes)} if layer=='e2'
                     else m.addVars(len(nodes),vtype=GRB.INTEGER,ub=H,name=label+'_time'))
            lw = m.addVars(len(nodes),ub=cap_w,name=label+'_weight')
            lv = m.addVars(len(nodes),ub=cap_v,name=label+'_volume')
            rank = m.addVars(len(nodes),ub=max(1,len(nodes)),name=label+'_rank') if layer=='e1' else None
            arcs = [(-1,j) for j in range(len(nodes))] + [(i,-2) for i in range(len(nodes))]
            for i,left in enumerate(nodes):
                budget()
                for j,right in enumerate(nodes):
                    if i==j or (layer=='e1' and left['kind']=='p' and right['kind']=='d'):
                        continue
                    if layer=='e2' and left['kind']=='p' and right['kind']=='d' and left['order'].id==right['order'].id:
                        continue
                    if left['window'][0]+left['service']+problem.travel(left['site'],right['site']) > right['window'][1]:
                        continue
                    arcs.append((i,j))
            x = m.addVars(arcs,vtype=GRB.BINARY,name=label+'_arc')
            m.addConstr(start <= H*used)
            m.addConstr(end <= H*used)
            m.addConstr(start >= p.cycle_start*used)
            m.addConstr(gp.quicksum(x[a,b] for a,b in arcs if a==-1)==used)
            m.addConstr(gp.quicksum(x[a,b] for a,b in arcs if b==-2)==used)
            initial_w = gp.quicksum(n['w']*visit[i] if layer=='e2' else n['w']
                                    for i,n in enumerate(nodes) if n['kind']=='d')
            initial_v = gp.quicksum(n['v']*visit[i] if layer=='e2' else n['v']
                                    for i,n in enumerate(nodes) if n['kind']=='d')
            m.addConstr(initial_w <= cap_w*used)
            m.addConstr(initial_v <= cap_v*used)
            for i,n in enumerate(nodes):
                m.addConstr(gp.quicksum(x[a,b] for a,b in arcs if b==i)==visit[i])
                m.addConstr(gp.quicksum(x[a,b] for a,b in arcs if a==i)==visit[i])
                m.addConstr(visit[i]<=used)
                m.addConstr((visit[i]==1) >> (times[i]>=n['window'][0]))
                m.addConstr((visit[i]==1) >> (times[i]<=n['window'][1]))
                if layer=='e1':
                    m.addConstr(times[i]<=H*visit[i])
                    m.addConstr(n['quantity']<=Q*visit[i])
                    m.addConstr(n['quantity']>=visit[i])
                    if n['kind']=='d':
                        m.addConstr((visit[i]==1) >> (times[i]+n['service']<=cutoffs[batch.id]))
                        m.addConstr(n['w']<=cap_w*visit[i])
                        m.addConstr((visit[i]==1) >> (n['quantity']==counts[n['site'],batch.id]))
                        m.addConstr((visit[i]==1) >> (n['w']==weights[n['site'],batch.id]))
                        m.addConstr((visit[i]==1) >> (supplied[n['site'],batch.id]==times[i]+n['service']))
                    else:
                        m.addConstr((visit[i]==1) >> (times[i]>=cutoffs[batch.id]))
                        m.addConstr((visit[i]==1) >> (end+p.cleaning_duration<=H))
            distance = gp.LinExpr()
            travel_ticks = gp.LinExpr()
            for a,b in arcs:
                left = base if a==-1 else nodes[a]['site']
                right = base if b==-2 else nodes[b]['site']
                distance += problem.distance(left,right)*x[a,b]
                travel_ticks += problem.travel(left,right)*x[a,b]
                if a==-1:
                    m.addConstr((x[a,b]==1) >> (times[b]>=start+problem.travel(left,right)))
                    if rank is not None:
                        m.addConstr((x[a,b]==1) >> (rank[b]>=1))
                    sign = -1 if nodes[b]['kind']=='d' else 1
                    m.addConstr((x[a,b]==1) >> (lw[b]==initial_w+sign*nodes[b]['w']))
                    m.addConstr((x[a,b]==1) >> (lv[b]==initial_v+sign*nodes[b]['v']))
                elif b==-2:
                    m.addConstr((x[a,b]==1) >> (end>=times[a]+nodes[a]['service']+problem.travel(left,right)+unload))
                else:
                    m.addConstr((x[a,b]==1) >> (times[b]>=times[a]+nodes[a]['service']+problem.travel(left,right)))
                    if rank is not None:
                        m.addConstr((x[a,b]==1) >> (rank[b]>=rank[a]+1))
                    sign = -1 if nodes[b]['kind']=='d' else 1
                    m.addConstr((x[a,b]==1) >> (lw[b]==lw[a]+sign*nodes[b]['w']))
                    m.addConstr((x[a,b]==1) >> (lv[b]==lv[a]+sign*nodes[b]['v']))
                if layer=='e1' and b!=-2 and nodes[b]['kind']=='p' and (a==-1 or nodes[a]['kind']=='d'):
                    m.addConstr((x[a,b]==1) >> (times[b]>=cutoffs[batch.id]+problem.travel(left,right)))
            # Every physical trip includes its traversed arcs, service and
            # unloading. Expose this implication in the LP relaxation too:
            # indicator-only timing otherwise permits negative paid duration.
            m.addConstr(end-start >= travel_ticks
                        +gp.quicksum(n['service']*visit[i] for i,n in enumerate(nodes))
                        +unload*used, name=label+'_duration_lower_bound')
            return dict(nodes=nodes,base=base,used=used,start=start,end=end,visit=visit,
                        times=times,x=x,label=label,distance=distance)

        for b in p.batches:
            for k in range(f.e1_count):
                nodes = []
                for s in problem.satellites:
                    for kind in ('d','p'):
                        q = m.addVar(vtype=GRB.INTEGER,ub=Q,name=f'quantity_{b.id}_{k}_{s}_{kind}')
                        w = m.addVar(ub=f.e1_weight_cap,name=f'goods_weight_{b.id}_{k}_{s}') if kind=='d' else q*f.container_weight
                        nodes.append(dict(site=s,kind=kind,quantity=q,w=w,v=q*f.container_volume,
                                          service=problem.service(s),
                                          window=((problem.start_bounds[b.id][0] if variable_calendar else b.start),
                                                  problem.cutoff_bounds[b.id][1]-problem.service(s))
                                          if kind=='d' else (problem.cutoff_bounds[b.id][0],
                                              problem.end_bounds[b.id][1] if variable_calendar else b.end)))
                r = make_route(nodes,problem.depot,'e1',f'e1_{b.id}_{k}',b)
                r.update(batch=b,vehicle=k)
                hd = m.addVar(vtype=GRB.BINARY,name=r['label']+'_loaded')
                ds = [r['visit'][i] for i,n in enumerate(nodes) if n['kind']=='d']
                for v in ds:
                    m.addConstr(hd>=v)
                m.addConstr(hd<=gp.quicksum(ds))
                only_pick = m.addVar(vtype=GRB.BINARY)
                m.addConstr(only_pick==r['used']-hd)
                m.addConstr((hd==1) >> (r['start']==starts[b.id]+p.e1_load_time))
                m.addConstr((only_pick==1) >> (r['start']>=cutoffs[b.id]))
                m.addConstr((r['used']==1) >> (r['end']<=ends[b.id]))
                m.addConstr((r['used']==1) >> (r['end']+p.e1_turnaround_time<=H))
                r['loaded'] = hd
                r['return_quantity'] = gp.quicksum(n['quantity'] for n in nodes if n['kind']=='p')
                r['work_ticks'] = r['end']-r['start']+p.e1_load_time*hd
                for prior in routes['e1']:
                    if prior['vehicle']==k:
                        both = m.addVar(vtype=GRB.BINARY)
                        m.addConstr(both<=prior['used']); m.addConstr(both<=r['used'])
                        m.addConstr(both>=prior['used']+r['used']-1)
                        m.addConstr((both==1) >> (r['start']-p.e1_load_time*hd>=prior['end']+p.e1_turnaround_time))
                routes['e1'].append(r)
        for s in problem.satellites:
            for b in p.batches:
                entries = [(r,i,n) for r in routes['e1'] if r['batch'].id==b.id
                           for i,n in enumerate(r['nodes']) if n['site']==s and n['kind']=='d']
                m.addConstr(gp.quicksum(n['quantity'] for r,i,n in entries)==counts[s,b.id])
                m.addConstr(gp.quicksum(r['visit'][i] for r,i,n in entries)<=1)

        returned = {o.id:m.addVar(vtype=GRB.INTEGER,ub=problem.cutoff_bounds[p.batches[-1].id][1],name=f'returned_{o.id}')
                    for o in problem.orders}
        q_expected = {(s,b.id):gp.LinExpr() for s in problem.satellites for b in p.batches}
        for o in problem.orders:
            previous = 0
            for b in p.batches:
                h = m.addVar(vtype=GRB.BINARY,name=f'eligible_{o.id}_{b.id}')
                m.addConstr((h==1) >> (returned[o.id]<=cutoffs[b.id]))
                m.addConstr((h==0) >> (returned[o.id]>=cutoffs[b.id]+1))
                m.addConstr(h>=previous)
                q_expected[o.satellite,b.id] += o.count*(h-previous)
                previous = h
            m.addConstr(previous==1)

        for s in problem.satellites:
            local = [o for o in problem.orders if o.satellite==s]
            if not local:
                continue
            nodes = [dict(site=o.customer,order=o,kind=kind,quantity=o.count,
                          w=problem.task(o.id,kind)[1],v=problem.task(o.id,kind)[2],
                          service=o.service_time,window=o.delivery_window if kind=='d' else o.pickup_window)
                     for o in local for kind in ('d','p')]
            first_slots = []
            for k in range(f.e2_per_sat_count):
                previous = None
                for slot in range(min(p.e2_max_trips_per_vehicle,len(nodes))):
                    r = make_route(nodes,s,'e2',f'e2_{s}_{k}_{slot}')
                    r.update(satellite=s,vehicle=k,slot=slot)
                    if slot==0:
                        if first_slots:
                            m.addConstr(r['used']<=first_slots[-1])
                        first_slots.append(r['used'])
                    selects = {b.id:m.addVar(vtype=GRB.BINARY) for b in p.batches}
                    loaded = gp.quicksum(selects.values())
                    m.addConstr(loaded<=r['used'])
                    r['loaded'] = loaded
                    r['work_ticks'] = r['end']-r['start']+p.e2_dispatch_prep_time*loaded
                    ss = m.addVar(ub=causal_limit)
                    se = m.addVar(ub=causal_limit)
                    m.addConstr(ss<=causal_limit*r['used']); m.addConstr(se<=causal_limit*r['used'])
                    r['step_end']=se
                    m.addConstr((r['used']==1) >> (r['end']+p.e2_turnaround_time<=H))
                    m.addConstr((r['used']==1) >> (r['start']>=p.cycle_start+p.e2_dispatch_prep_time*loaded))
                    if previous is not None:
                        m.addConstr(r['used']<=previous['used'])
                        m.addConstr((r['used']==1) >> (r['start']>=previous['end']+p.e2_turnaround_time+p.e2_dispatch_prep_time*loaded))
                        m.addConstr((r['used']==1) >> (ss>=previous['step_end']+1))
                    for b in p.batches:
                        conjunctions = []
                        for i,n in enumerate(nodes):
                            if n['kind']!='d' or (n['order'].id,b.id) not in y:
                                continue
                            a = m.addVar(vtype=GRB.BINARY)
                            m.addConstr(a<=r['visit'][i]); m.addConstr(a<=y[n['order'].id,b.id])
                            m.addConstr(a>=r['visit'][i]+y[n['order'].id,b.id]-1)
                            m.addConstr(a<=selects[b.id])
                            conjunctions.append(a)
                        m.addConstr(selects[b.id]<=gp.quicksum(conjunctions))
                        m.addConstr((selects[b.id]==1) >> (r['start']>=supplied[s,b.id]+p.e2_dispatch_prep_time))
                    for i,n in enumerate(nodes):
                        if n['kind']=='p':
                            m.addConstr((r['visit'][i]==1) >> (returned[n['order'].id]==r['end']))
                    for (a,b),x in r['x'].items():
                        left = ss if a==-1 else steps[nodes[a]['order'].id,nodes[a]['kind']]
                        right = se if b==-2 else steps[nodes[b]['order'].id,nodes[b]['kind']]
                        m.addConstr((x==1) >> (right>=left+1))
                    routes['e2'].append(r)
                    previous=r
        for o in problem.orders:
            for kind in ('d','p'):
                m.addConstr(gp.quicksum(r['visit'][i] for r in routes['e2']
                            for i,n in enumerate(r['nodes']) if n['order'].id==o.id and n['kind']==kind)==1)
            m.addConstr(task_times[o.id,'p']>=task_times[o.id,'d']+o.service_time+o.min_use_time)
            m.addConstr(steps[o.id,'p']>=steps[o.id,'d']+1)
        for s in problem.satellites:
            for b in p.batches:
                entries = [(r,i,n) for r in routes['e1'] if r['batch'].id==b.id
                           for i,n in enumerate(r['nodes']) if n['site']==s and n['kind']=='p']
                m.addConstr(gp.quicksum(n['quantity'] for r,i,n in entries)==q_expected[s,b.id])
                m.addConstr(gp.quicksum(r['visit'][i] for r,i,n in entries)<=1)

        cumulative = gp.LinExpr()
        for b in p.batches:
            budget()
            cumulative += gp.quicksum(counts[s,b.id] for s in problem.satellites)
            releases=[]
            for r in routes['e1']:
                ready,late = (m.addVar(vtype=GRB.BINARY) for _ in range(2))
                m.addConstr(ready+late==r['used'])
                m.addConstr((ready==1) >> (r['end']+p.cleaning_duration<=starts[b.id]))
                m.addConstr((late==1) >> (r['end']+p.cleaning_duration>=starts[b.id]+1))
                released=m.addVar(vtype=GRB.INTEGER,ub=Q)
                m.addConstr(released<=r['return_quantity']); m.addConstr(released<=Q*ready)
                m.addConstr(released>=r['return_quantity']-Q*(1-ready))
                releases.append(released)
            m.addConstr(N+gp.quicksum(releases)>=cumulative,name=f'clean_stock_{b.id}')
        rates=problem.cost_profile.rates
        parts={'container_pool':rates['container_pool_cost_per_period']*N}
        physical_vehicles={}
        for layer in ('e1','e2'):
            parts[layer+'_distance']=gp.quicksum(r['distance'] for r in routes[layer])*rates['distance_scale_to_km']*rates[layer+'_distance_cost_per_km']
            parts[layer+'_work']=gp.quicksum(r['work_ticks'] for r in routes[layer])*p.time_tick_seconds/60*rates[layer+'_work_cost_per_minute']
            parts[layer+'_trips']=gp.quicksum(r['used'] for r in routes[layer])*rates[layer+'_trip_cost']
            groups={}
            for r in routes[layer]:
                key=r['vehicle'] if layer=='e1' else (r['satellite'],r['vehicle'])
                groups.setdefault(key,[]).append(r['used'])
            enabled=[]
            for key,visits in groups.items():
                active=m.addVar(vtype=GRB.BINARY,name=f'vehicle_{layer}_{key}')
                for v in visits:
                    m.addConstr(active>=v)
                m.addConstr(active<=gp.quicksum(visits))
                enabled.append(active)
            physical_vehicles[layer]=enabled
            parts[layer+'_vehicles']=gp.quicksum(enabled)*rates[layer+'_vehicle_cost_per_cycle']
        parts.update(problem.cost_profile.constant_cost(Q))
        m.setObjective(gp.quicksum(parts.values()),GRB.MINIMIZE)
        budget()
        m.update()
        return m,dict(**routes,assignments=y,activation=z,pool=N,cutoffs=cutoffs,cost_parts=parts,
                      batch_starts=starts, batch_ends=ends,
                      physical_vehicles=physical_vehicles)
    except BaseException:
        m.dispose()
        raise
