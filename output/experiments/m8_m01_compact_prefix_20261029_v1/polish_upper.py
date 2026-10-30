"""Brief same-case/seed upper-bound search; never an optimality certificate."""
from datetime import datetime,timezone
import time

from study import HERE,read,save,CASE,load_instance,load_routing_case
from models.routing.model_8.alns.solver import solve_alns
from models.common.cycle.audit import audit_solution


if __name__ == '__main__':
    start = datetime.fromisoformat(read(HERE/'started.json')['at'])
    elapsed = (datetime.now(timezone.utc)-start).total_seconds()
    budget = max(.01,min(20.,1188-elapsed))
    begun = time.perf_counter()
    p = load_instance(*load_routing_case(CASE,use_pool_config=False))
    witness = read(HERE/'initial_result.json')
    result = solve_alns(p,max_time=budget,max_iterations=1000000000,seed=42,initial_result=witness)
    audit = audit_solution(p,result)
    summary = dict(kind='ALNS_UPPER_BOUND_ONLY',seconds=time.perf_counter()-begun,budget_seconds=budget,
        seed=42,concurrent_with_complete_model=True,business_audit_valid=audit['valid'],
        periodic_replay_valid=audit.get('periodic_replay',{}).get('valid',False),
        audited_cost=audit.get('objective'),proves_optimality=False)
    if audit['valid'] and audit['periodic_replay']['valid']:
        save('polished_result.json',result)
    save('polishing_summary.json',summary)
    print(summary,flush=True)
