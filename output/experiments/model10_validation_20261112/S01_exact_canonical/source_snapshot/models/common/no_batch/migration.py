"""Read-only migration of independently valid fixed-assignment cycle results."""
from copy import deepcopy
from dataclasses import fields

from models.common.cycle.audit import audit_solution as audit_cycle
from .instance import NoBatchInstance, NoBatchPolicy, VERSION
from .audit import audit_solution


def migrate_cycle_result(source, result, *, options=None):
    if source.model_number not in (3,5,6,7,8) or source.free_assignment:
        raise ValueError('M10 conversion requires a fixed-assignment reusable cycle model')
    original_audit=audit_cycle(source,result)
    if not original_audit['valid']:
        raise ValueError(f'Source cycle failed audit: {original_audit["errors"][:3]}')
    fixed=source
    if getattr(source,'calendar_cycle',False):
        fixed=source.selected_problem(result)
    elif getattr(source,'uniform_cycle',False):
        fixed=source.for_count(result['cycle_batch_count'])
    if getattr(fixed,'joint_batch',False):
        fixed=fixed.project(result['order_batch_assignment'],result['container_pool_size'],
                            result.get('batch_cutoffs'),result.get('batch_loading_starts'))
    if getattr(fixed,'optimize_pool',False):
        # Pool-optimization sources expose the selected physical projection.
        fixed=fixed.project_pool(result['container_pool_size'])
    p=fixed.operating_policy
    if p.time_tick_subdivision!=1:
        raise ValueError('Source uses subsecond ticks; explicit clock conversion is required')
    names={x.name for x in fields(NoBatchPolicy)}-{'repeat_interval','policy_version','terminal_policy'}
    policy=NoBatchPolicy(**{name:getattr(p,name) for name in names},
                         repeat_interval=1440*60//p.time_tick_seconds)
    problem=NoBatchInstance(fixed.network,fixed.orders,policy,options)
    new=dict(schema_version=3,model=10,policy_version=VERSION,batch_mode='no_batch',time_unit='ticks',
             e1_trips=[],e2_trips=[],source_model=source.model_number,
             source_container_pool=p.container_pool_size)
    empty=[]
    for trip in result['e2_trips']:
        t=deepcopy(trip);t.pop('trip_index',None)
        delivery=any(e['kind']=='d' for e in t['events'])
        t['loading_start']=t['departure']-(p.e2_dispatch_prep_time if delivery else 0)
        new['e2_trips'].append(t)
        for event in t['events']:
            if event['kind']=='p':empty.append((t['return_complete'],event['order_id'],t['satellite_id']))
    collected=set()
    for trip in sorted(result['e1_trips'],key=lambda r:r['departure']):
        t=deepcopy(trip);bid=t.pop('batch_id')
        delivery=any(e['kind']=='d' for e in t['events'])
        t['loading_start']=t['departure']-(p.e1_load_time if delivery else 0)
        new['e1_trips'].append(t)
        for event in t['events']:
            if event['kind']=='d':
                event['order_ids']=[o.id for o in fixed.orders if o.satellite==event['site_id'] and o.batch==bid]
            else:
                event['order_ids']=[]
    # Allocate identical returned boxes by FIFO, in collection-event order.
    pickups=sorted((e['start'],t['trip_id'],j,e) for t in new['e1_trips'] for j,e in enumerate(t['events']) if e['kind']=='p')
    for start,_,_,event in pickups:
        available=[oid for when,oid,s in sorted(empty) if when<=start and s==event['site_id'] and oid not in collected]
        event['order_ids']=available[:event['quantity']]
        collected.update(event['order_ids'])
    checked=audit_solution(problem,new)
    if not checked['valid']:
        raise ValueError(f'M10 migrated solution failed audit: {checked["errors"][:3]}')
    if checked['minimum_pool_for_schedule']>p.container_pool_size:
        raise ValueError('Migration unexpectedly increased the initial pool')
    new.update(container_pool_size=checked['minimum_pool_for_schedule'],audit=checked,feasible=True)
    return problem,new
