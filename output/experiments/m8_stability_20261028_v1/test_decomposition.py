"""External full-pattern cost cuts must preserve the independent exact optimum."""
from dataclasses import replace
from pathlib import Path
import sys

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0,str(HERE))
from prepare import module
from decomposition import solve
from models.common.cycle.calendar import CalendarCycleInstance
from models.common.cycle.audit import audit_solution
from models.routing.model_8.exact import solve_exact
from experiments.runs.research_model8_shared_relaxation import enumerate_trip_library
from tests.routing.test_model8_physical_bounds import instance

phase = module(ROOT/'output/experiments/m8_s04_reduction_validation_20261026_v1/phase_bound.py','decomp_phase_test')


@pytest.mark.parametrize('kind',['ordinary','late_release','multiple_satellites'])
@pytest.mark.parametrize('seed',[7,42])
def test_pattern_cuts_close_at_the_independent_original_exact_optimum(kind,seed):
    source = instance(kind)
    p = CalendarCycleInstance(source.network,source.orders,replace(source.operating_policy,cycle_end=132),
                              dict(batch_count_candidates=[1,2,3]),source.cost_profile.config,model=8)
    first = solve_exact(p,time_limit=20,milp_options=dict(formulation='compact'))
    assert first['info']['proven_optimal']
    fixed_pool = first['container_pool_size']
    # The original problem for this test explicitly has a fixed pool. Actual
    # benchmark fixed pools have separate full-domain necessary certificates.
    p = CalendarCycleInstance(source.network,source.orders,replace(source.operating_policy,cycle_end=132),
                              dict(batch_count_candidates=[1,2,3],pool_min=fixed_pool,pool_max=fixed_pool),
                              source.cost_profile.config,model=8)
    witness = solve_exact(p,time_limit=20,milp_options=dict(formulation='compact'))
    assert witness['info']['proven_optimal']
    library,_ = enumerate_trip_library(p)
    result,info = solve(p,witness,list(p.batch_count_candidates),fixed_pool,0.,library,phase,20,seed)
    assert info['global_optimality_certified'],info
    assert info['global_lower_bound'] == pytest.approx(witness['info']['objective'],abs=1e-6)
    assert info['audited_upper_bound'] == pytest.approx(witness['info']['objective'],abs=1e-6)
    audit = audit_solution(p,result)
    assert audit['valid'] and audit['periodic_replay']['valid']
    for event in info['events']:
        if event['cut_kind']=='PROVEN_NO_SOLUTION_UNDER_INITIAL_COST_CAP':
            assert event['native_status']==3
        elif event['cut_kind']=='VALID_CONDITIONAL_LOWER_BOUND':
            assert event['native_bound'] is not None
