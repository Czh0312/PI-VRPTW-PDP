"""Use spare CPU within the same wall-clock window, M01/seed 42 only.

This is a second complete solver search sharing this run's proved certificates.
Its eight-thread timing is kept separate from the single-thread comparison.
"""
from datetime import datetime, timezone
import math
import os
import time

from study import HERE, ROOT, CASE, read, save, sha, load_instance, load_routing_case
from experiments.runs.model8_compact_prefix import build_prefix, supply_start, extract_prefix
from experiments.runs.model8_frontier import normalize_vehicle_ids, add_frontier_cuts
from models.common.cycle.audit import audit_solution
from models.common.solver_config import read_milp_info


if __name__ == '__main__':
    import gurobipy as gp
    begun = time.perf_counter()
    outer = datetime.fromisoformat(read(HERE/'started.json')['at'])
    elapsed = (datetime.now(timezone.utc)-outer).total_seconds()
    budget = max(.01, 1185-elapsed)
    deadline = begun+budget
    p = load_instance(*load_routing_case(CASE, use_pool_config=False))
    cert = read(HERE/'transport_certificate.json')
    frontier = read(HERE/'stock_frontier.json')
    witness = normalize_vehicle_ids(p, read(HERE/'heuristic_best_result.json'))
    initial = audit_solution(p, witness)
    assert initial['valid'] and initial['periodic_replay']['valid']
    m, ctx = build_prefix(p, cert['retained_K'], certificate=cert,
        deadline=deadline, vehicle_symmetry=True)
    add_frontier_cuts(p, m, ctx, frontier, cert)
    try:
        supply_start(p, m, ctx, witness)
        params = dict(Threads=8, Seed=42, Method=2, DegenMoves=0, CutPasses=2,
                      Cuts=1, MIPFocus=3, Heuristics=.05, MIPGap=0, MIPGapAbs=0)
        for key, value in params.items():
            m.setParam(key, value)
        m.Params.TimeLimit = max(.01, deadline-time.perf_counter())
        m.Params.LogFile = os.path.relpath(HERE/'parallel_gurobi.log', ROOT)
        m.Params.OutputFlag = 1
        m.Params.LogToConsole = 0
        last = [-20.]
        def observe(model, where):
            if where == gp.GRB.Callback.MIP:
                seconds = model.cbGet(gp.GRB.Callback.RUNTIME)
                if seconds-last[0] >= 10:
                    last[0] = seconds
                    value = model.cbGet(gp.GRB.Callback.MIP_OBJBND)
                    save('parallel_progress.json', dict(seconds=seconds,
                        lower_bound=value if math.isfinite(value) and abs(value) < 1e90 else None,
                        incumbent=model.cbGet(gp.GRB.Callback.MIP_OBJBST),
                        nodes=model.cbGet(gp.GRB.Callback.MIP_NODCNT)))
        m.optimize(observe)
        assert m.Status not in (gp.GRB.INFEASIBLE, gp.GRB.INF_OR_UNBD)
        info = read_milp_info(m, proof_scope='complete_original_M8_competitive_domain_parallel_search', build_time=0)
        if m.SolCount:
            result = extract_prefix(p, m, ctx, info)
            audit = audit_solution(p, result)
            assert audit['valid'] and audit['periodic_replay']['valid']
            save('parallel_best_audited_result.json', result)
            save('parallel_audit.json', audit)
        else:
            audit = initial
        raw_bound = m.ObjBound
        bound = raw_bound if math.isfinite(raw_bound) and abs(raw_bound) < 1e90 else None
        lower = max(frontier['global_lower_bound'], bound) if bound is not None else frontier['global_lower_bound']
        assert lower <= audit['objective']+1e-5
        summary = dict(kind='COMPLETE_ORIGINAL_DOMAIN_SEARCH', case='M01', seed=42, threads=8,
            reused_certificates_computed_in_same_run=True, concurrent_with_single_thread_model=True,
            budget_seconds=budget, total_wall_seconds=time.perf_counter()-begun,
            ended_at_outer_seconds=(datetime.now(timezone.utc)-outer).total_seconds(),
            parameters=params, native_status=int(m.Status), native_runtime=m.Runtime,
            native_lower_bound=bound, global_lower_bound=lower, sol_count=m.SolCount,
            native_objective=m.ObjVal if m.SolCount else None,
            audited_upper_bound=audit['objective'], nodes=m.NodeCount,
            business_audit_valid=audit['valid'], periodic_replay_valid=audit['periodic_replay']['valid'],
            script_sha256=sha(__file__))
        save('parallel_summary.json', summary)
        print(summary, flush=True)
    finally:
        m.dispose()
