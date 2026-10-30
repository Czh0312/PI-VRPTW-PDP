"""Independent path-cover enumeration and licensed optimum preservation."""
import importlib.util
from itertools import permutations
from pathlib import Path
from types import SimpleNamespace as NS
from functools import partial
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'m8_temporal_preprocess_ablation_20261024_v1/source'))

spec=importlib.util.spec_from_file_location('m8_route_subset_bounds',Path(__file__).with_name('route_bounds.py'))
bounds=importlib.util.module_from_spec(spec);spec.loader.exec_module(bounds)


def test_all_capacitated_six_task_path_covers_obey_subset_bounds():
    weights={('a','d'):2,('b','d'):1,('c','d'):1,('a','p'):1,('b','p'):1,('c','p'):1}
    p=NS(satellites=['S'],orders=[NS(id=o,satellite='S') for o in 'abc'],
         fleet=NS(e2_weight_cap=2,e2_volume_cap=2),task=lambda o,k:(1,weights[o,k],1))
    rows=list(bounds.subset_bounds(p));checked=0
    for perm in permutations(range(6)):
        for mask in range(1<<5):
            trips=[[]]
            for j,node in enumerate(perm):
                trips[-1].append(node)
                if j<5 and mask&(1<<j):trips.append([])
            if any(sum(weights['abc'[i//2],'d' if i%2==0 else 'p'] for i in trip if i%2==parity)>2
                   for trip in trips for parity in (0,1)):
                continue
            arcs=[(a,b) for trip in trips for a,b in zip(trip,trip[1:])]
            for row in rows:
                tasks=set(row['tasks'])
                assert sum(a in tasks and b in tasks for a,b in arcs)<=len(tasks)-row['minimum_routes']
            checked+=1
    assert checked>1000


def test_capacity_rounding_cannot_add_a_trip_from_float_noise():
    assert bounds.conservative_ceiling(.1+.2,.3)==1
    assert bounds.conservative_ceiling(7,3)==3


@pytest.mark.parametrize('formulation',['expanded','compact'])
@pytest.mark.parametrize('kind',['ordinary','multiple_satellites','unequal_counts'])
def test_static_route_bounds_preserve_the_full_domain_optimum(formulation,kind):
    from models.routing.model_8.exact import solve_exact
    from models.routing.model_8.exact.formulation import build_model
    from models.routing.model_8.exact.solution import extract_solution
    from models.routing.model_8.exact.preprocessing import infeasible_time_domain_certificate
    from models.common.cycle.unified_milp import solve_unified_milp
    from models.common.cycle.audit import audit_solution
    from tests.routing.test_model8_physical_bounds import instance
    p=instance(kind)
    baseline=solve_exact(p,time_limit=25,preprocessing=True,milp_options=dict(formulation=formulation))
    def fixed(child,**kwargs):
        model,context=build_model(child,preprocessing=True,**kwargs)
        context['route_capacity_bounds']=bounds.add_capacity_bounds(child,model,context)
        return model,context
    enhanced=solve_unified_milp(p,build_fixed=fixed,extract_fixed=extract_solution,time_limit=25,
        milp_options=dict(formulation=formulation),count_screening=True,
        count_certificate=infeasible_time_domain_certificate)
    assert baseline['info']['proven_optimal'] and enhanced['info']['proven_optimal']
    assert enhanced['info']['objective']==pytest.approx(baseline['info']['objective'])
    audit=audit_solution(p,enhanced)
    assert audit['valid'] and audit['periodic_replay']['valid']
