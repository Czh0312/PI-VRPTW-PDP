"""Check the actual twenty-order witness against all new cuts and symmetries."""
import time

import gurobipy as gp
import pytest

from study import CASE,HERE,read,load_instance,load_routing_case
from experiments.runs.model8_compact_prefix import transport_certificate,build_prefix,supply_start
from experiments.runs.model8_frontier import stock_frontier,normalize_vehicle_ids,add_frontier_cuts
from models.common.cycle.audit import audit_solution


def test_actual_M01_witness_remains_feasible_under_frontier_and_symmetry():
    p = load_instance(*load_routing_case(CASE,use_pool_config=False))
    witness = read(HERE/'initial_result.json')
    audit = audit_solution(p,witness)
    assert audit['valid'] and audit['periodic_replay']['valid']
    normalized = normalize_vehicle_ids(p,witness)
    assert audit_solution(p,normalized)['objective'] == pytest.approx(audit['objective'],abs=1e-8)
    certificate = transport_certificate(p,audit['objective'])
    frontier = stock_frontier(p,certificate,deadline=time.perf_counter()+30,per_pair_seconds=1)
    certificate['retained_K'] = frontier['retained_K']
    m,ctx = build_prefix(p,frontier['retained_K'],certificate=certificate,vehicle_symmetry=True)
    try:
        add_frontier_cuts(p,m,ctx,frontier,certificate)
        supply_start(p,m,ctx,normalized)
        # Fix only this independent feasibility-check model. The experiment
        # supplies the same values as a MIP start and keeps every choice free.
        for variable in m.getVars():
            if variable.Start != gp.GRB.UNDEFINED:
                variable.LB = variable.UB = variable.Start
        m.Params.TimeLimit = 20
        m.Params.Seed = 42
        m.Params.Threads = 1
        m.optimize()
        assert m.SolCount > 0
        assert m.ObjVal == pytest.approx(audit['objective'],abs=1e-5)
    finally:
        m.dispose()
