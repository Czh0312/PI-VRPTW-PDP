"""Decode joint decisions through the existing independently audited projection."""
from models.common.cycle.joint import lift_result
from models.common.cycle.state import empty_result


def extract_solution(problem, m, ctx, info):
    """Extract routes, allocation, pool and calendar; preserve projection audit."""
    import gurobipy as gp

    def integral(v):
        value=v if isinstance(v,int) else v.getValue() if isinstance(v,gp.LinExpr) else v.X
        if abs(value-round(value))>1e-4:
            raise RuntimeError(f'Nonintegral M9 schedule or quantity: {value}')
        return int(round(value))

    assignment={oid:bid for (oid,bid),v in ctx['assignments'].items() if v.X>.5}
    pool=integral(ctx['pool'])
    cutoffs={bid:integral(value) for bid,value in ctx['cutoffs'].items()}
    selected_problem = problem
    customer_assignment = {customer: site for (customer, site), value
                           in ctx['customer_assignments'].items() if value.X > .5}
    if getattr(problem, 'variable_calendar', False):
        boundaries = [integral(ctx['batch_starts'][b.id]) for b in problem.operating_policy.batches]
        boundaries.append(problem.operating_policy.cycle_end)
        selected_problem = problem.with_boundaries(boundaries)
        info['proof_scope'] = 'free_assignment_variable_boundaries_joint_cost_integer_time_MILP'
    selected_problem = selected_problem.assigned(customer_assignment)
    fixed=selected_problem.project(assignment,pool,cutoffs)
    raw=empty_result(fixed,'exact')
    raw['info']={**info,'objective':None}
    for layer in ('e1','e2'):
        for r in ctx[layer]:
            if r['used'].X<.5:
                continue
            following={a:b for (a,b),v in r['x'].items() if v.X>.5}
            order,cursor=[],-1
            while following.get(cursor,-2)!=-2:
                cursor=following[cursor]
                if cursor in order:
                    raise RuntimeError('Cycle in M9 incumbent')
                order.append(cursor)
            if set(order)!={i for i,v in r['visit'].items() if v.X>.5}:
                raise RuntimeError('Disconnected M9 incumbent')
            trip=dict(trip_id=r['label'],vehicle_id=r['vehicle'],departure=integral(r['start']),
                      return_complete=integral(r['end']),events=[])
            if layer=='e1':
                trip['batch_id']=r['batch'].id
            else:
                trip.update(satellite_id=r['satellite'],trip_index=r['slot'])
            for i in order:
                n=r['nodes'][i]
                event=dict(kind=n['kind'],start=integral(r['times'][i]),quantity=integral(n['quantity']))
                event['site_id' if layer=='e1' else 'order_id']=n['site'] if layer=='e1' else n['order'].id
                trip['events'].append(event)
            raw[layer+'_trips'].append(trip)
    result=lift_result(selected_problem,raw,assignment,pool,'exact',cutoffs)
    result['assignment'] = customer_assignment
    return result
