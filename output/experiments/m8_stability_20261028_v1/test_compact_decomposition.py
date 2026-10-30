"""Conditional empty-slot removal retains the exact original optimum."""
from dataclasses import replace
from pathlib import Path
import sys

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0,str(HERE))
from prepare import module
from decomposition_compact import solve
from models.common.cycle.calendar import CalendarCycleInstance
from models.common.cycle.audit import audit_solution
from models.routing.model_8.exact import solve_exact
from experiments.runs.research_model8_shared_relaxation import enumerate_trip_library
from tests.routing.test_model8_physical_bounds import instance

phase = module(ROOT/'output/experiments/m8_s04_reduction_validation_20261026_v1/phase_bound.py','compact_phase_test')


@pytest.mark.parametrize('kind',['ordinary','late_release','multiple_satellites'])
@pytest.mark.parametrize('seed',[7,42])
def test_compact_followers_keep_original_exact_optimum_and_parent_policy(kind,seed):
    source = instance(kind)
    original_policy = replace(source.operating_policy,cycle_end=132,e2_max_trips_per_vehicle=12)
    p = CalendarCycleInstance(source.network,source.orders,original_policy,dict(batch_count_candidates=[1,2,3]),
                              source.cost_profile.config,model=8)
    initial = solve_exact(p,time_limit=20,milp_options=dict(formulation='compact'))
    pool = initial['container_pool_size']
    p = CalendarCycleInstance(source.network,source.orders,original_policy,
                              dict(batch_count_candidates=[1,2,3],pool_min=pool,pool_max=pool),source.cost_profile.config,model=8)
    witness = solve_exact(p,time_limit=20,milp_options=dict(formulation='compact'))
    assert witness['info']['proven_optimal']
    caps = {k:p.for_count(k).operating_policy.e2_max_trips_per_vehicle for k in p.batch_count_candidates}
    library,_ = enumerate_trip_library(p)
    result,info = solve(p,witness,list(p.batch_count_candidates),pool,0.,library,phase,20,seed)
    assert info['global_optimality_certified'],info
    assert info['global_lower_bound'] == pytest.approx(witness['info']['objective'],abs=1e-6)
    assert audit_solution(p,result)['valid']
    assert audit_solution(p,result)['periodic_replay']['valid']
    assert p.operating_policy.e2_max_trips_per_vehicle == 12
    assert caps == {k:p.for_count(k).operating_policy.e2_max_trips_per_vehicle for k in p.batch_count_candidates}
    for event in info['events']:
        assert event['derived_slot_cap'] <= event['original_slot_cap']
