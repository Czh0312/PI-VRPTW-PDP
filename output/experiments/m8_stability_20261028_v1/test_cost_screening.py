"""Validate conditional-cover pruning against exact original M8 optima."""
from dataclasses import replace
from itertools import combinations
import json
import math
from pathlib import Path
import sys

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0,str(HERE))
from prepare import module
from cost_screening import exact_cover_bounds,screen_route_library
from route_bounds import add_shared_route_bounds
from experiments.runs.research_model8_shared_relaxation import enumerate_trip_library
from models.common.cycle import load_instance
from models.common.cycle.audit import audit_solution
from models.common.cycle.calendar import CalendarCycleInstance
from models.routing.model_8.exact.formulation import build_model
from tests.routing.test_model8_physical_bounds import instance
from tools.configuration.loader import load_routing_case

phase = module(ROOT/'output/experiments/m8_s04_reduction_validation_20261026_v1/phase_bound.py','screen_phase_test')


def test_exact_cover_DP_matches_independent_subset_enumeration():
    p = instance('ordinary')
    library,_ = enumerate_trip_library(p)
    full = (1 << (2*len(p.orders)))-1
    best = {0:0.}
    for size in range(1,len(library)+1):
        for chosen in combinations(library,size):
            mask,distance = 0,0.
            for row in chosen:
                if mask & row['mask']:
                    break
                mask |= row['mask']
                distance += row['distance']
            else:
                best[mask] = min(best.get(mask,float('inf')),distance)
    value,conditional = exact_cover_bounds(library,len(p.orders))
    assert value == pytest.approx(best[full])
    for row in library:
        expected = row['distance']+best.get(full ^ row['mask'],float('inf'))
        if math.isfinite(expected):
            assert conditional[row['mask']] == pytest.approx(expected)
        else:
            assert math.isinf(conditional[row['mask']])


@pytest.mark.parametrize('kind',['ordinary','late_release','multiple_satellites'])
@pytest.mark.parametrize('refined',[False,True])
@pytest.mark.parametrize('count',[1,2,3])
def test_cost_screening_preserves_original_exact_optimum(kind,refined,count):
    import gurobipy as gp

    source = instance(kind)
    policy = replace(source.operating_policy,cycle_end=133) if refined else source.operating_policy
    p = CalendarCycleInstance(source.network,source.orders,policy,dict(batch_count_candidates=[1,2,3]),
                              source.cost_profile.config,model=8)
    child = p.for_count(count)
    original,ctx = build_model(child,compact_e2=True)
    try:
        original.Params.TimeLimit = 20
        original.optimize()
        assert original.Status in (gp.GRB.OPTIMAL,gp.GRB.INFEASIBLE)
        status = original.Status
        upper = original.ObjVal if original.SolCount else 1e6
    finally:
        original.dispose()
    library,_ = enumerate_trip_library(child)
    kept,proof = screen_route_library(child,library,upper,child.pool_min,phase.phase_patterns(child))
    model,ctx = build_model(child,compact_e2=True)
    try:
        add_shared_route_bounds(child,model,ctx,kept)
        model.addConstr(model.getObjective() <= upper+1e-7)
        model.Params.TimeLimit = 20
        model.optimize()
        assert model.Status == status
        if model.SolCount:
            assert model.ObjVal == pytest.approx(upper,abs=1e-6)
            assert proof['separable_cost_lower_bound'] <= upper+1e-6
    finally:
        model.dispose()


@pytest.mark.parametrize('case',['S03','S08','S05'])
def test_actual_audited_witness_routes_survive_cost_screening(case):
    context = json.loads((HERE/'inputs'/case/'context.json').read_text(encoding='utf-8-sig'))
    p = load_instance(*load_routing_case(Path(context['case_path']),use_pool_config=False))
    result = json.loads(Path(context['witness_path']).read_text(encoding='utf-8-sig'))
    audit = audit_solution(p,result)
    assert audit['valid'] and audit['periodic_replay']['valid']
    child = p.for_count(result['cycle_batch_count'])
    library,_ = enumerate_trip_library(child)
    kept,proof = screen_route_library(child,library,audit['objective'],context['reduced_N'][0],phase.phase_patterns(child))
    assert proof['separable_cost_lower_bound'] <= audit['objective']+1e-6
    masks = {row['mask'] for row in kept}
    index = {o.id:i for i,o in enumerate(child.orders)}
    for trip in result['e2_trips']:
        mask = sum(1 << (index[e['order_id']]+(len(index) if e['kind']=='p' else 0)) for e in trip['events'])
        assert mask in masks
