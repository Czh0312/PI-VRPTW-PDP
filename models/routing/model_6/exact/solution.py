"""Extract MILP visits, preserving grouped handling and physical vehicle IDs."""
from models.common.no_batch.instance import VERSION


def extract_solution(context, x):
    problem = context['problem']
    offset = problem.operating_policy.cycle_start
    def time(var):
        value = var.value(x)
        if abs(value-round(value)) > 1e-4:
            raise ValueError('Nonintegral solver time')
        return int(round(value))+offset
    result = dict(schema_version=3, model=6, policy_version=VERSION, batch_mode='no_batch',
                  time_unit='ticks', e1_trips=[], e2_trips=[])
    for layer, routes in context['routes'].items():
        for r in routes:
            if r['used'].value(x) < .5:
                continue
            first = [a for a,v in r['first'].items() if v.value(x) > .5]
            if len(first) != 1:
                raise ValueError('Invalid route start')
            a, sequence = first[0], []
            while True:
                if a in sequence:
                    raise ValueError('MILP route subtour')
                sequence.append(a)
                if r['last'][a].value(x) > .5:
                    break
                following = [b for (left,b),v in r['arcs'].items() if left == a and v.value(x) > .5]
                if len(following) != 1:
                    raise ValueError('Invalid MILP arc')
                a = following[0]
            visits = []
            for oid, kind in sequence:
                start = time(context['times'][layer][oid,kind])
                if layer == 'e1':
                    site = problem.order_map[oid].satellite
                    if visits and (visits[-1]['site_id'],visits[-1]['kind'],visits[-1]['start']) == (site,kind,start):
                        visits[-1]['order_ids'].append(oid); visits[-1]['quantity'] += 1
                    else:
                        visits.append(dict(site_id=site, kind=kind, start=start, order_ids=[oid], quantity=1))
                else:
                    visits.append(dict(order_id=oid, kind=kind, start=start))
            vehicle = next(i for i,v in enumerate(r['vehicle']) if v.value(x) > .5)
            trip = dict(trip_id=r['label'], vehicle_id=vehicle, loading_start=time(r['start']),
                        departure=time(r['departure']), return_complete=time(r['end']), events=visits)
            if layer == 'e2': trip['satellite_id'] = r['base']
            result[layer+'_trips'].append(trip)
    return result
