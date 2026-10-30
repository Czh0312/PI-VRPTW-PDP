"""Map an independently audited business witness to a partial native start.

Only semantic routing, assignment and clock decisions are supplied. Auxiliary
inventory, ordering and activation variables are left for exact completion.
"""
import math


def apply_audited_start(model,blocks,selectors,witness):
    import gurobipy as gp

    chosen = witness['cycle_batch_count']
    if chosen not in blocks:
        raise ValueError('Witness K is outside the certified retained domain')
    block = blocks[chosen]
    p,ctx = block['problem'],block['context']
    if not math.isclose(witness['time_tick_seconds'],p.operating_policy.time_tick_seconds,rel_tol=1e-12):
        raise ValueError('Witness and native model must use identical integer clock units')
    provided = set()

    def assign(variable,value):
        if isinstance(variable,gp.Var):
            variable.Start = value
            provided.add(variable.index)

    if selectors is not None:
        for k,selector in selectors.items():
            assign(selector,int(k==chosen))
        prefixes = tuple(f'K{k}_' for k in blocks if k!=chosen)
        if prefixes:
            for variable in model.getVars():
                if variable.VarName.startswith(prefixes):
                    assign(variable,0)
        for name,value in [('batch_count',chosen),('container_pool',witness['container_pool_size'])]:
            variable = model.getVarByName(name)
            if variable is not None:
                assign(variable,value)
    assign(ctx['pool'],witness['container_pool_size'])
    allocation = witness['order_batch_assignment']
    for (oid,bid),variable in ctx['assignments'].items():
        assign(variable,int(allocation[oid]==bid))
    calendar = {b['id']:b for b in witness['batches']}
    for bid,batch in calendar.items():
        assign(ctx['batch_starts'][bid],batch['start'])
        assign(ctx['batch_ends'][bid],batch['end'])
        assign(ctx['cutoffs'][bid],batch['cutoff'])
    for trip in witness['e2_trips']:
        for event in trip['events']:
            assign(ctx['variables'].task_times[event['order_id'],event['kind']],event['start'])
    route_count = 0
    for layer in ('e1','e2'):
        actual = {trip['trip_id']:trip for trip in witness[layer+'_trips']}
        for route in ctx[layer]:
            trip = actual.get(route['label'])
            assign(route['used'],int(trip is not None))
            assign(route['start'],trip['departure'] if trip else 0)
            assign(route['end'],trip['return_complete'] if trip else 0)
            events = {} if trip is None else {(event['site_id' if layer=='e1' else 'order_id'],event['kind']):event
                                              for event in trip['events']}
            positions = {(node['site'] if layer=='e1' else node['order'].id,node['kind']):j
                         for j,node in enumerate(route['nodes'])}
            sequence = [] if trip is None else [positions[event['site_id' if layer=='e1' else 'order_id'],event['kind']]
                                                for event in trip['events']]
            path = [-1]+sequence+[-2]
            edges = set(zip(path,path[1:])) if sequence else set()
            for edge,variable in route['x'].items():
                assign(variable,int(edge in edges))
            for j,node in enumerate(route['nodes']):
                key = (node['site'] if layer=='e1' else node['order'].id,node['kind'])
                event = events.get(key)
                assign(route['visit'][j],int(event is not None))
                if layer=='e1':
                    assign(route['times'][j],event['start'] if event else 0)
                    assign(node['quantity'],event['quantity'] if event else 0)
                    if node['kind']=='d':
                        goods = sum(o.goods_weight+o.count*p.fleet.container_weight for o in p.orders
                                    if o.satellite==node['site'] and allocation[o.id]==route['batch'].id) if event else 0.
                        assign(node['w'],goods)
                        if event:
                            assign(ctx['variables'].supply_times[node['site'],route['batch'].id],event['start']+p.service(node['site']))
            if layer=='e1':
                assign(route['loaded'],int(any(e['kind']=='d' for e in events.values())))
            if trip is not None:
                route_count += 1
    model.update()
    if route_count != len(witness['e1_trips'])+len(witness['e2_trips']):
        raise ValueError('Witness trip labels do not match native vehicle/slot labels')
    return dict(provided_variables=len(provided),matched_routes=route_count,selected_K=chosen,
                pool=witness['container_pool_size'],native_num_starts=model.NumStart)
