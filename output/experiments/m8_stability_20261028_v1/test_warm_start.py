"""Fix every supplied start value: it must have a truly feasible extension."""
from dataclasses import replace
from pathlib import Path
import json
import math
import sys

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0,str(HERE))
from warm_start import apply_audited_start
from models.common.cycle import load_instance
from models.common.cycle.audit import audit_solution
from models.common.cycle.calendar import CalendarCycleInstance
from models.common.cycle.unified_milp import build_unified_model
from models.routing.model_8.exact import solve_exact
from models.routing.model_8.exact.formulation import build_model
from tests.routing.test_model8_physical_bounds import instance
from tools.configuration.loader import load_routing_case


def fix_provided_values(model):
    import gurobipy as gp
    count = 0
    for variable in model.getVars():
        value = variable.Start
        if abs(value) < gp.GRB.INFINITY:
            variable.LB = value
            variable.UB = value
            count += 1
    model.update()
    return count


@pytest.mark.parametrize('kind',['ordinary','late_release','multiple_satellites'])
@pytest.mark.parametrize('refined',[False,True])
def test_partial_native_start_preserves_actual_tiny_feasible_schedule(kind,refined):
    import gurobipy as gp

    p = instance(kind)
    if refined:
        p = CalendarCycleInstance(p.network,p.orders,replace(p.operating_policy,cycle_end=133),
                                  dict(batch_count_candidates=[1,2,3]),p.cost_profile.config,model=8)
    witness = solve_exact(p,time_limit=20,milp_options=dict(formulation='compact'))
    assert witness['audit']['valid'] and witness['info']['proven_optimal']
    model,ctx = build_unified_model(p,build_fixed=build_model,compact_e2=True)
    try:
        evidence = apply_audited_start(model,ctx['blocks'],ctx['selectors'],witness)
        assert evidence['provided_variables'] > 0
        assert fix_provided_values(model) == evidence['provided_variables']
        model.Params.OutputFlag = 0
        model.Params.TimeLimit = 20
        model.optimize()
        assert model.Status == gp.GRB.OPTIMAL
        assert model.ObjVal == pytest.approx(witness['audit']['objective'],abs=1e-6)
    finally:
        model.dispose()


@pytest.mark.parametrize('case',['S03','S08','S05'])
def test_actual_supplied_start_values_have_a_complete_original_model_extension(case):
    import gurobipy as gp

    context = json.loads((HERE/'inputs'/case/'context.json').read_text(encoding='utf-8-sig'))
    p = load_instance(*load_routing_case(Path(context['case_path']),use_pool_config=False))
    witness = json.loads(Path(context['witness_path']).read_text(encoding='utf-8-sig'))
    audit = audit_solution(p,witness)
    assert audit['valid'] and audit['periodic_replay']['valid']
    k = witness['cycle_batch_count']
    child = p.for_count(k)
    model,ctx = build_model(child,compact_e2=True)
    try:
        evidence = apply_audited_start(model,{k:dict(problem=child,context=ctx)},None,witness)
        assert fix_provided_values(model) == evidence['provided_variables']
        model.Params.OutputFlag = 0
        model.Params.TimeLimit = 20
        model.optimize()
        assert model.Status == gp.GRB.OPTIMAL
        assert model.ObjVal == pytest.approx(audit['objective'],abs=1e-6)
    finally:
        model.dispose()
