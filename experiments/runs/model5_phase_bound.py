"""First-echelon phase bounds for the Model 5 research relaxation."""
from functools import lru_cache
import itertools

from experiments.runs.research_model5_shared_relaxation import shortest_distance_closure


def phase_patterns(problem):
    sites = list(problem.satellites)
    m = len(sites)
    assert m <= 4, 'Research enumeration is intended for small satellite sets'
    d = shortest_distance_closure(problem)
    trip = {}
    for mask in range(1, 1 << (2*m)):
        delivery = [i for i in range(m) if mask & (1 << i)]
        pickup = [i for i in range(m) if mask & (1 << (m+i))]
        candidates = []
        for ds in itertools.permutations(delivery):
            for ps in itertools.permutations(pickup):
                sequence = [problem.depot]+[sites[i] for i in ds+ps]+[problem.depot]
                candidates.append(sum(d[a,b] for a,b in zip(sequence,sequence[1:])))
        trip[mask] = min(candidates)

    @lru_cache(None)
    def cover(mask, vehicles):
        if not mask:
            return 0.
        if not vehicles:
            return float('inf')
        first = mask & -mask
        best = float('inf')
        subset = mask
        while subset:
            if subset & first:
                best = min(best, trip[subset]+cover(mask ^ subset, vehicles-1))
            subset = (subset-1) & mask
        return best

    return [dict(delivery=dmask, pickup=pmask,
                 distance=cover(dmask | (pmask << m), problem.fleet.e1_count))
            for dmask in range(1,1 << m) for pmask in range(1,1 << m)]


def augment(problem, model, ctx):
    import gurobipy as gp
    patterns = phase_patterns(problem)
    B = max(ctx['selectors'])
    alpha = model.addVars(B, len(patterns), vtype=gp.GRB.BINARY, name='phase_e1_pattern')
    for b in range(B):
        model.addConstr(gp.quicksum(alpha[b,h] for h in range(len(patterns))) == model.getVarByName(f'active[{b}]'))
        for i,o in enumerate(problem.orders):
            sitebit = 1 << problem.satellites.index(o.satellite)
            model.addConstr(ctx['assignments'][i,b] <= gp.quicksum(alpha[b,h] for h,r in enumerate(patterns) if r['delivery'] & sitebit))
            model.addConstr(ctx['recovery'][i,b] <= gp.quicksum(alpha[b,h] for h,r in enumerate(patterns) if r['pickup'] & sitebit))
    new_distance = gp.quicksum(alpha[b,h]*r['distance'] for b in range(B) for h,r in enumerate(patterns))
    rates = problem.cost_profile.rates
    model.setObjective(model.getObjective()+rates['distance_scale_to_km']*rates['e1_distance_cost_per_km']*(new_distance-ctx['e1_distance']))
    model.update()
    return patterns, new_distance
