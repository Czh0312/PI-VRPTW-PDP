"""Certified optimistic route-cover costs screen masks before model lifting."""
from functools import lru_cache
import math

from models.routing.model_8.exact.parameters import necessary_time_bounds


def exact_cover_bounds(library,n):
    by_task = [[] for _ in range(2*n)]
    for row in library:
        for task in range(2*n):
            if row['mask'] & (1 << task):
                by_task[task].append((row['mask'],row['distance']))

    @lru_cache(None)
    def minimum(mask):
        if not mask:
            return 0.
        first = (mask & -mask).bit_length()-1
        return min((distance+minimum(mask ^ subset) for subset,distance in by_task[first]
                    if subset & mask == subset),default=float('inf'))

    full = (1 << (2*n))-1
    return minimum(full),{row['mask']:row['distance']+minimum(full ^ row['mask']) for row in library}


def e1_cover_lower_bound(problem,phase_patterns):
    sites = list(problem.satellites)
    count = len(problem.operating_policy.batches)
    size = len(sites)
    active = sum(1 << sites.index(site) for site in {o.satellite for o in problem.orders})
    required = active | (active << size)
    by_id = {o.id:o for o in problem.orders}
    bounds = necessary_time_bounds(problem)
    forced = 0
    for row in bounds['forced_first_batch']:
        forced |= 1 << sites.index(by_id[row['order']].satellite)
    patterns = [row for row in phase_patterns if not (row['delivery'] & ~active)
                and not (row['pickup'] & ~active)]

    @lru_cache(None)
    def minimum(batch,covered):
        if batch == count:
            return 0. if covered & required == required else float('inf')
        return min((row['distance']+minimum(batch+1,covered | row['delivery'] | (row['pickup'] << size))
                    for row in patterns if batch or row['delivery'] & forced == forced),default=float('inf'))

    return minimum(0,0),dict(forced_first_delivery_sites=forced,required_visit_kinds=required,
                             batches=count,phase_pattern_count=len(patterns))


def screen_route_library(problem,library,upper,pool_min,phase_patterns):
    if any(o.count != 1 for o in problem.orders):
        raise ValueError('Cost screening currently requires one-box orders')
    if not problem.require_productive_batches:
        raise ValueError('Cost screening requires productive batches')
    rates = problem.cost_profile.rates
    if any(value < 0 for value in rates.values() if isinstance(value,(int,float))):
        raise ValueError('Cost screening requires nonnegative omitted costs')
    d1,first = e1_cover_lower_bound(problem,phase_patterns)
    d2,conditional = exact_cover_bounds(library,len(problem.orders))
    constant = sum(problem.cost_profile.constant_cost(problem.total).values())
    base = rates['container_pool_cost_per_period']*pool_min+constant
    base += rates['distance_scale_to_km']*rates['e1_distance_cost_per_km']*d1
    multiplier = rates['distance_scale_to_km']*rates['e2_distance_cost_per_km']
    rows,kept = [],[]
    for row in library:
        lower = base+multiplier*conditional[row['mask']]
        competitive = math.isfinite(lower) and lower <= upper+1e-6
        if competitive:
            kept.append(row)
        rows.append(dict(mask=row['mask'],conditional_cost_lower_bound=lower if math.isfinite(lower) else None,
                         kept=competitive,proof='Selected mask distance plus exact minimum cover of all '
                         'remaining tasks, mandatory E1 phase visits and minimum pool cost.'))
    lower = base+multiplier*d2
    return kept,dict(original_columns=len(library),retained_columns=len(kept),
                     screened_columns=len(library)-len(kept),upper_bound=upper,pool_minimum=pool_min,
                     first_echelon_distance_lower_bound=d1,second_echelon_distance_lower_bound=d2,
                     separable_cost_lower_bound=lower if math.isfinite(lower) else None,
                     first_echelon_proof=first,rows=rows,
                     excluded_reason='Cannot improve or tie audited U within 1e-6; not business infeasibility')
