"""Convert an already audited M10 schedule into a topology with timing anchors."""
from models.common.no_batch.audit import audit_solution
from .state import Route, State


def from_result(problem,result):
    audit=audit_solution(problem,result)
    if not audit['valid']:raise ValueError('M10 warm start failed independent audit')
    state=State()
    for layer in ('e1','e2'):
        for trip in sorted(result[layer+'_trips'],key=lambda r:r['loading_start']):
            actions=[]
            for event in trip['events']:
                ids=event['order_ids'] if layer=='e1' else [event['order_id']]
                actions.extend((oid,event['kind']) for oid in ids)
            state.routes[layer].append(Route(layer,problem.depot if layer=='e1' else trip['satellite_id'],trip['vehicle_id'],actions))
    # Reuse successor matching reproduces the incumbent's available stock even
    # when an earliest schedule would otherwise move later load operations up.
    issued=[];returned=[]
    for t in result['e1_trips']:
        for e in t['events']:
            for oid in e['order_ids']:
                if e['kind']=='d':issued.append((t['loading_start'],oid))
                else:returned.append((t['return_complete']+problem.operating_policy.cleaning_duration,oid))
    free=list(sorted(returned))
    for time,oid in sorted(issued):
        eligible=[(i,t,a) for i,(t,a) in enumerate(free) if t<=time and a!=oid]
        if eligible:
            i,_,a=max(eligible,key=lambda x:x[1]);free.pop(i);state.reuse.append((a,oid))
    return state
