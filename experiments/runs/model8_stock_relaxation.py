"""Compact optimistic calendar/stock bound without physical route copies."""
from functools import lru_cache
import itertools

from experiments.runs.model8_compact_prefix import metric_closure
from models.routing.model_8.exact.parameters import necessary_time_bounds


def phase_patterns(problem):
    sites = list(problem.satellites)
    n = len(sites)
    if n > 4:
        raise ValueError('Phase-pattern prototype supports at most four satellites')
    d = metric_closure(problem)
    costs = {}
    for mask in range(1,1 << (2*n)):
        ds = [i for i in range(n) if mask & (1 << i)]
        ps = [i for i in range(n) if mask & (1 << (n+i))]
        costs[mask] = min(sum(d[a,b] for a,b in zip(path,path[1:]))
            for left in itertools.permutations(ds) for right in itertools.permutations(ps)
            for path in [[problem.depot]+[sites[i] for i in left+right]+[problem.depot]])

    @lru_cache(None)
    def cover(mask,vehicles):
        if not mask:
            return 0.
        if not vehicles:
            return float('inf')
        first,subset,best = mask & -mask,mask,float('inf')
        while subset:
            if subset & first:
                best = min(best,costs[subset]+cover(mask ^ subset,vehicles-1))
            subset = (subset-1) & mask
        return best
    return [dict(delivery=dm,pickup=pm,distance=cover(dm | (pm << n),problem.fleet.e1_count))
            for dm in range(1,1 << n) for pm in range(1,1 << n)]


def build_stock_bound(problem,certificate):
    """Relax E2 routing/vehicle schedules; keep optimistic time and stock chains.

    Every original competitive solution projects using its actual order times,
    batch decisions and clean times. E1 phase patterns lower-bound actual E1
    tours; the independent metric-tour bound lower-bounds total E2 cost.
    This model returns a LOWER BOUND, never an original feasible solution.
    """
    import gurobipy as gp
    p = problem.operating_policy
    orders = list(problem.orders)
    n = len(orders)
    if any(o.count != 1 for o in orders):
        raise ValueError('Stock relaxation currently requires one-box orders')
    counts = certificate['retained_K']
    B = max(counts)
    m = gp.Model('m8_optimistic_calendar_stock_bound')
    m.Params.OutputFlag = 0
    m.Params.Threads = 1
    m.Params.Seed = 42
    m.Params.MIPGap = 0
    m.Params.MIPGapAbs = 0
    z = m.addVars(B,vtype=gp.GRB.BINARY)
    selectors = m.addVars(counts,vtype=gp.GRB.BINARY)
    m.addConstr(selectors.sum() == 1)
    m.addConstr(z.sum() == gp.quicksum(k*v for k,v in selectors.items()))
    for b in range(B-1):
        m.addConstr(z[b] >= z[b+1])
    t = m.addVars(B+1,lb=p.cycle_start,ub=p.cycle_end)
    cut = m.addVars(B,lb=p.cycle_start,ub=p.cycle_end)
    m.addConstr(t[0] == p.cycle_start)
    m.addConstr(t[B] == p.cycle_end)
    for b in range(B):
        m.addConstr(cut[b] >= t[b]+problem.minimum_phase_ticks*z[b])
        m.addConstr(cut[b] <= t[b+1]-problem.minimum_phase_ticks*z[b])
        m.addConstr(t[b+1]-t[b] <= problem.cycle_duration*z[b])
    y = m.addVars(n,B,vtype=gp.GRB.BINARY)
    w = m.addVars(n,B,vtype=gp.GRB.BINARY)
    pool = m.addVar(lb=certificate['pool_lower'],ub=problem.pool_max,vtype=gp.GRB.INTEGER)
    delivery = {i:m.addVar(lb=o.delivery_window[0],ub=o.delivery_window[1]) for i,o in enumerate(orders)}
    pickup = {i:m.addVar(lb=o.pickup_window[0],ub=o.pickup_window[1]) for i,o in enumerate(orders)}
    clean = m.addVars(n,lb=p.cycle_start,ub=p.cycle_end)
    bd = necessary_time_bounds(problem)
    latest = {row['order']:row['latest_loading'] for row in bd['intervals']}
    outward,inward = problem.travel_bounds['outward'],problem.travel_bounds['inward']
    for i,o in enumerate(orders):
        s = o.satellite
        inbound = p.e1_load_time+outward[problem.depot][s]+problem.service(s)
        supply = inbound+p.e2_dispatch_prep_time+outward[s][o.customer]
        return_site = o.service_time+inward[s][o.customer]+p.e2_return_unload_time
        tail = problem.service(s)+outward[s][problem.depot]+p.depot_return_unload_time
        m.addConstr(y.sum(i,'*') == 1)
        m.addConstr(w.sum(i,'*') == 1)
        m.addConstr(pickup[i] >= delivery[i]+o.service_time+o.min_use_time)
        for b in range(B):
            m.addConstr(y[i,b] <= z[b])
            m.addConstr(w[i,b] <= z[b])
            m.addConstr(w[i,b] <= gp.quicksum(y[i,h] for h in range(b+1)))
            for premise,consequence in [
                (y[i,b],t[b] >= o.release_time),(y[i,b],t[b] <= latest[o.id]),
                (y[i,b],delivery[i] >= t[b]+supply),(y[i,b],cut[b] >= t[b]+inbound),
                (w[i,b],cut[b] >= pickup[i]+return_site),
                (w[i,b],clean[i] >= cut[b]+tail+p.cleaning_duration),
                (w[i,b],clean[i] <= t[b+1]+p.cleaning_duration)]:
                m.addConstr((premise == 1) >> consequence)
    for row in bd['forced_first_batch']:
        m.addConstr(y[next(i for i,o in enumerate(orders) if o.id == row['order']),0] == 1)
    for b in range(B):
        m.addConstr(y.sum('*',b) >= z[b])
        m.addConstr(y.sum('*',b) <= pool)
        m.addConstr(w.sum('*',b) >= z[b])
        ready = m.addVars(n,vtype=gp.GRB.BINARY)
        for i in range(n):
            m.addConstr(ready[i] <= gp.quicksum(w[i,h] for h in range(b)))
            m.addConstr((ready[i] == 1) >> (clean[i] <= t[b]))
        m.addConstr(gp.quicksum(y[i,h] for i in range(n) for h in range(b+1)) <= pool+ready.sum())
    patterns = phase_patterns(problem)
    alpha = m.addVars(B,len(patterns),lb=0,ub=1)
    for b in range(B):
        m.addConstr(gp.quicksum(alpha[b,h] for h in range(len(patterns))) == z[b])
        for j,s in enumerate(problem.satellites):
            for group,key in [(y,'delivery'),(w,'pickup')]:
                present = m.addVar(vtype=gp.GRB.BINARY)
                quantity = gp.quicksum(group[i,b] for i,o in enumerate(orders) if o.satellite == s)
                m.addConstr(quantity <= n*present)
                m.addConstr(present <= quantity)
                m.addConstr(present == gp.quicksum(alpha[b,h] for h,row in enumerate(patterns) if row[key] & (1 << j)))
    rates = problem.cost_profile.rates
    e1 = rates['distance_scale_to_km']*rates['e1_distance_cost_per_km']*gp.quicksum(
        alpha[b,h]*row['distance'] for b in range(B) for h,row in enumerate(patterns))
    objective = pool*rates['container_pool_cost_per_period']+e1+certificate['e2_tsp_cost_lower_bound']+certificate['constant_cost']
    m.setObjective(objective)
    m.update()
    return m,dict(selectors=selectors,assignments=y,recovery=w,pool=pool,boundaries=t,cutoffs=cut)
