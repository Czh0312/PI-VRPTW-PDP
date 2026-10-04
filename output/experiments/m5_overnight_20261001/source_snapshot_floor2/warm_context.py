import gzip
import json

from models.routing.solvers.exact.multibatch_e1_routes import enumerate_e1_routes
from models.routing.solvers.exact.multibatch_joint_inventory import cutoff_domains, set_joint_projection
from models.routing.solvers.exact.multibatch_route_timing import compress_supply_profiles
from models.routing.solvers.exact.multibatch_route_pricing import route_context
from models.routing.common.cycle.precheck import travel_lower_bounds


def context_for_export(problem, model, directory):
    p = problem.operating_policy
    patterns = [r for b in p.batches for r in enumerate_e1_routes(problem, b)]
    def var(name):
        v = model.getVarByName(name)
        assert v is not None, name
        return v
    e1 = dict(patterns=patterns, chosen={i: var(f'route[{i}]') for i in range(len(patterns))},
        quantities={(i,s): var(f'quantity_{i}_{s}') for i,r in enumerate(patterns) for s in r['pickup']},
        returns={o.id: var('e2_return_'+o.id) for o in problem.orders},
        allocation={(o.id,b.id): var(f'cutoff_{o.id}_{b.id}') for o in problem.orders for b in p.batches},
        order_times={(o.id,k): var(('delivery_' if k=='d' else 'pickup_time_')+o.id)
                     for o in problem.orders for k in ('d','p')})
    travel = travel_lower_bounds(problem)
    contexts = sorted((route_context(problem,s,travel) for s in problem.satellites),
                      key=lambda c:(len(c['nodes']),c['satellite']))
    routes = {}
    for index,ctx in enumerate(contexts):
        if not ctx['nodes']: continue
        site=ctx['satellite']
        with gzip.open(directory/f'site_{index}_best_pricing_timing.json.gz','rt') as stream:
            universe=json.load(stream)
        universe['groups']=[dict(complete=g['complete']) for g in universe['timing_groups']]
        rows=cutoff_domains(problem,ctx,universe,travel)
        levels={n['key'][0]:sorted({r['supply'][site]+p.e2_dispatch_prep_time for r in patterns
                if r['batch']==n['order'].batch and site in r['delivery']})
                for n in ctx['nodes'] if n['key'][1]=='d'}
        rows=compress_supply_profiles(rows,levels)
        site_index=sorted({o.satellite for o in problem.orders}).index(site)
        choices={(i,bid):var(f'joint_route_{site_index}_{i}_{j}') for i,r in enumerate(rows)
                 for j,bid in enumerate(r['allowed_cutoffs'])}
        routes[site]=dict(rows=rows,choices=choices,context=ctx)
    return dict(e1=e1,backend='patterns',routes=routes)
