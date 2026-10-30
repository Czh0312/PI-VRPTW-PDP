"""A bounded independent M01 relaxation; never an original feasible plan."""
from datetime import datetime,timezone
import json
import time

from study import HERE,ROOT,CASE,read,save,load_instance,load_routing_case
from experiments.runs.model8_stock_relaxation import build_stock_bound


if __name__ == '__main__':
    started = datetime.fromisoformat(read(HERE/'started.json')['at'])
    elapsed = (datetime.now(timezone.utc)-started).total_seconds()
    budget = max(.01,min(45.,1190-elapsed))
    begun = time.perf_counter()
    p = load_instance(*load_routing_case(CASE,use_pool_config=False))
    certificate = read(HERE/'transport_certificate.json')
    m,ctx = build_stock_bound(p,certificate)
    try:
        m.Params.TimeLimit = max(.01,budget-(time.perf_counter()-begun))
        m.optimize()
        import math
        bound = m.ObjBound if math.isfinite(m.ObjBound) else None
        result = dict(kind='OPTIMISTIC_STOCK_LOWER_BOUND_NOT_AN_ORIGINAL_PLAN',native_status=int(m.Status),
            global_lower_bound=bound,native_objective=m.ObjVal if m.SolCount else None,
            seconds=time.perf_counter()-begun,seed=42,threads=1,concurrent_with_complete_model=True,
            original_K=list(p.batch_count_candidates),retained_K=certificate['retained_K'],
            variables=m.NumVars,constraints=m.NumConstrs+m.NumGenConstrs)
        if m.SolCount:
            k = next(k for k,v in ctx['selectors'].items() if v.X>.5)
            result.update(relaxation_K=k,relaxation_N=round(ctx['pool'].X),
                delivery_batches={o.id:next(b for b in range(k) if ctx['assignments'][i,b].X>.5) for i,o in enumerate(p.orders)},
                recovery_batches={o.id:next(b for b in range(k) if ctx['recovery'][i,b].X>.5) for i,o in enumerate(p.orders)})
        save('auxiliary_stock_bound.json',result)
        print(json.dumps(result),flush=True)
    finally:
        m.dispose()
