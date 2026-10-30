"""Try one stock-master assignment for an original audited feasible upper bound.

Trip slots are capped for this HEURISTIC construction only. No lower bound or
infeasibility from this restricted model is a global claim or a master cut.
"""
from copy import copy
from dataclasses import replace
from datetime import datetime,timezone
import json
import time

from study import HERE,read,save,CASE,load_instance,load_routing_case
from models.routing.model_8.exact.formulation import build_model
from models.routing.model_8.exact.solution import extract_solution
from models.common.cycle.calendar import attach_calendar_decisions
from models.common.cycle.audit import audit_solution
from models.common.solver_config import read_milp_info,accept_milp_audit


if __name__ == '__main__':
    began = time.perf_counter()
    start = datetime.fromisoformat(read(HERE/'started.json')['at'])
    elapsed = (datetime.now(timezone.utc)-start).total_seconds()
    budget = max(.01,min(60.,1190-elapsed))
    pattern = read(HERE/'auxiliary_stock_bound.json')
    p = load_instance(*load_routing_case(CASE,use_pool_config=False))
    k,N = pattern['relaxation_K'],pattern['relaxation_N']
    child = copy(p.for_count(k))
    original_slots = child.operating_policy.e2_max_trips_per_vehicle
    child.operating_policy = replace(child.operating_policy,e2_max_trips_per_vehicle=min(3,original_slots))
    m,ctx = build_model(child,compact_e2=True,strengthening='physical_time',preprocessing=True,vehicle_symmetry=True,
                         deadline=began+budget)
    try:
        bids = [b.id for b in child.operating_policy.batches]
        ctx['pool'].LB = ctx['pool'].UB = N
        for (oid,bid),variable in ctx['assignments'].items():
            variable.LB = variable.UB = int(bid == bids[pattern['delivery_batches'][oid]])
        for oid,position in pattern['recovery_batches'].items():
            for b,bid in enumerate(bids):
                variable = m.getVarByName(f'eligible_{oid}_{bid}')
                variable.LB = variable.UB = int(b >= position)
        m.Params.Threads = 1
        m.Params.Seed = 42
        m.Params.MIPFocus = 1
        m.Params.TimeLimit = max(.01,budget-(time.perf_counter()-began))
        m.optimize()
        summary = dict(kind='HEURISTIC_UPPER_BOUND_ONLY',native_status=int(m.Status),
            K=k,N=N,trip_slot_cap=child.operating_policy.e2_max_trips_per_vehicle,
            original_trip_slot_cap=original_slots,wall_seconds=time.perf_counter()-began,
            sol_count=m.SolCount,used_for_global_lower_bound=False,used_for_infeasibility_cut=False,
            concurrent_with_complete_model=True,seed=42,threads=1)
        if m.SolCount:
            info = read_milp_info(m,proof_scope='restricted_pattern_construction_not_global',build_time=0)
            result = extract_solution(child,m,ctx,info)
            boundaries = [b['start'] for b in result['batches']]+[child.operating_policy.cycle_end]
            attach_calendar_decisions(p,result,k,boundaries)
            result['audit'] = audit_solution(p,result)
            accept_milp_audit(result,m.ObjVal)
            assert result['audit']['valid'] and result['audit']['periodic_replay']['valid']
            summary.update(audited_cost=result['audit']['objective'],business_audit_valid=True,periodic_replay_valid=True)
            save('constructed_result.json',result)
        save('construction_summary.json',summary)
        print(json.dumps(summary),flush=True)
    finally:
        m.dispose()
