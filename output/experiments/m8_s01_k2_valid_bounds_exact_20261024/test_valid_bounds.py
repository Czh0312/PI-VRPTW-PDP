"""Validate redundant physical bounds on tiny exact two-batch problems."""
import importlib.util
from pathlib import Path
import pytest
spec=importlib.util.spec_from_file_location('bounds_trial',Path(__file__).with_name('solve_k2.py'))
trial=importlib.util.module_from_spec(spec);spec.loader.exec_module(trial)
from tests.routing.test_model78_calendar import calendar_fixture

@pytest.mark.parametrize('late_release',[False,True])
def test_fixed_k2_optimum_unchanged_and_audited(late_release):
    parent=calendar_fixture(8,counts=(2,),late_release=late_release)
    child=parent.for_count(2);objectives=[]
    for strengthen in (False,True):
        model,ctx=trial.d.build_model(child,compact_e2=True)
        model.update()
        if strengthen:trial.add_valid_bounds(child,model,ctx)
        model.Params.TimeLimit=20;model.Params.OutputFlag=0
        model.optimize()
        assert model.Status==trial.gp.GRB.OPTIMAL
        info=trial.d.read_milp_info(model,proof_scope='tiny_fixed_K2_test',build_time=0)
        result=trial.d.extract_solution(child,model,ctx,info)
        boundaries=[b['start'] for b in result['batches']]+[child.operating_policy.cycle_end]
        trial.d.attach_calendar_decisions(parent,result,2,boundaries)
        audit=trial.d.audit_solution(parent,result)
        assert audit['valid'] and audit['periodic_replay']['valid']
        assert audit['objective']==pytest.approx(model.ObjVal)
        objectives.append(model.ObjVal);model.dispose()
    assert objectives[0]==pytest.approx(objectives[1])
