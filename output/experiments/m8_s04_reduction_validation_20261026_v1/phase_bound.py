"""Strengthen the shared lower bound with phase-ordered E1 route patterns.

For every pair of delivery/recovery satellite sets, enumerate all E1 tours
with deliveries before pickups, and partition their task set into at most
the available number of E1 vehicles. Capacity, service times and synchronization
are relaxed. Shortest-path distance closure makes shortcutting safe.
"""
from functools import lru_cache
import itertools
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from experiments.runs.research_model8_shared_relaxation import (
    enumerate_trip_library, build_relaxation, shortest_distance_closure)
from models.common.cycle import load_instance
from models.common.cycle.audit import audit_solution
from tools.configuration.loader import load_routing_case

HERE = Path(__file__).resolve().parent


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


def main():
    p = load_instance(*load_routing_case(ROOT/'data/formal_benchmark27_uniform_7_20_50_v1/model_8/small/S04/case.json', use_pool_config=False))
    begun = time.perf_counter()
    library,_ = enumerate_trip_library(p)
    model,ctx = build_relaxation(p,library)
    try:
        patterns,new_distance = augment(p,model,ctx)
        model.Params.TimeLimit = 30
        model.optimize()
        assert model.Status == 2
        result = json.loads((HERE/'enhanced/result.json').read_text(encoding='utf-8'))
        audit = audit_solution(p,result)
        assert audit['valid'] and audit['periodic_replay']['valid']
        upper = audit['objective']
        assert model.ObjBound <= upper+1e-6
        report = dict(kind='PHASE_RESPECTING_SHARED_FULL_K_LOWER_BOUND',
                      original_K=list(p.batch_count_candidates), lower_bound=model.ObjBound,
                      relaxation_objective=model.ObjVal, upper_bound=upper,
                      absolute_gap=upper-model.ObjBound, gap_over_upper=(upper-model.ObjBound)/upper,
                      external_full_domain_optimality_certified=abs(upper-model.ObjBound)<1e-6,
                      full_model_native_status_unchanged=result['info']['status'],
                      variables=model.NumVars, optimize_seconds=model.Runtime,
                      wall_seconds=time.perf_counter()-begun,
                      E1_distance_lower_bound=new_distance.getValue(),
                      E2_distance_lower_bound=ctx['e2_distance'].getValue(),
                      patterns=patterns, audited_original_solution='enhanced/result.json')
        (HERE/'phase_bound.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False),flush=True)
    finally:
        model.dispose()


if __name__ == '__main__':
    main()
