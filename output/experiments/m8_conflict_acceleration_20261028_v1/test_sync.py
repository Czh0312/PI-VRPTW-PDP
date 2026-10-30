"""New necessary links retain independently solved exact plans and clocks."""
from dataclasses import replace

import gurobipy as gp
import pytest

from common import HERE,ROOT,load,module,project
from master_sync import augment,violations
from experiments.runs.research_model8_shared_relaxation import build_relaxation,enumerate_trip_library
from models.common.cycle.calendar import CalendarCycleInstance
from models.common.cycle.audit import audit_solution
from models.routing.model_8.exact import solve_exact
from tests.routing.test_model8_physical_bounds import instance

phase=module(ROOT/'output/experiments/m8_s04_reduction_validation_20261026_v1/phase_bound.py','sync_test_phase')


def check_projection(p,witness,library):
    event=project(p,witness)
    assert not violations(p,event)
    model,ctx=build_relaxation(p,library)
    try:
        phase.augment(p,model,ctx)
        augment(p,model,ctx,library)
        ctx['selectors'][event['K']].LB=1
        ctx['pool'].LB=ctx['pool'].UB=witness['container_pool_size']
        for i,o in enumerate(p.orders):
            ctx['assignments'][i,event['delivery_batches'][o.id]].LB=1
            ctx['recovery'][i,event['recovery_batches'][o.id]].LB=1
        for c,row in enumerate(library):
            if row['mask'] in event['masks']:
                ctx['route'][c].LB=1
        model.addConstr(model.getObjective()<=audit_solution(p,witness)['objective']+1e-6)
        model.Params.TimeLimit=15
        model.optimize()
        assert model.Status==gp.GRB.OPTIMAL
    finally:
        model.dispose()


@pytest.mark.parametrize('kind',['ordinary','late_release','multiple_satellites'])
@pytest.mark.parametrize('refined',[False,True])
def test_original_optimal_plan_projects_with_sync_links(kind,refined):
    source=instance(kind)
    p=CalendarCycleInstance(source.network,source.orders,replace(source.operating_policy,cycle_end=133 if refined else 132),
        dict(batch_count_candidates=[1,2,3]),source.cost_profile.config,model=8)
    witness=solve_exact(p,time_limit=20,milp_options=dict(formulation='compact'))
    assert witness['info']['proven_optimal']
    library,_=enumerate_trip_library(p)
    check_projection(p,witness,library)


@pytest.mark.parametrize('case',['S03','S08','S05'])
def test_real_original_witness_survives_sync_links(case):
    p,witness,_,library,_=load(case)
    check_projection(p,witness,library)


@pytest.mark.parametrize('kind',['ordinary','late_release','multiple_satellites'])
def test_sync_complete_solver_preserves_independent_optimum(kind):
    from decomposition_sync import solve

    source=instance(kind)
    policy=replace(source.operating_policy,cycle_end=132)
    p=CalendarCycleInstance(source.network,source.orders,policy,dict(batch_count_candidates=[1,2,3]),source.cost_profile.config,model=8)
    witness=solve_exact(p,time_limit=20,milp_options=dict(formulation='compact'))
    assert witness['info']['proven_optimal']
    pool=witness['container_pool_size']
    p=CalendarCycleInstance(source.network,source.orders,policy,
        dict(batch_count_candidates=[1,2,3],pool_min=pool,pool_max=pool),source.cost_profile.config,model=8)
    library,_=enumerate_trip_library(p)
    result,info=solve(p,witness,list(p.batch_count_candidates),pool,0.,library,phase,20,42)
    assert info['global_optimality_certified']
    assert info['global_lower_bound']==pytest.approx(witness['info']['objective'],abs=1e-6)
    assert audit_solution(p,result)['periodic_replay']['valid']
