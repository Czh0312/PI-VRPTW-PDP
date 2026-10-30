"""Exact preservation on independent tiny M8s and real audited projections."""
from dataclasses import replace
from pathlib import Path
import sys
import json

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0,str(HERE))
from route_bounds import add_shared_route_bounds, first_echelon_patterns
from experiments.runs.research_model8_shared_relaxation import enumerate_trip_library
from models.common.cycle import load_instance
from models.common.cycle.audit import audit_solution
from models.common.cycle.calendar import CalendarCycleInstance, attach_calendar_decisions
from models.routing.model_8.exact.formulation import build_model
from models.routing.model_8.exact.solution import extract_solution
from models.common.solver_config import read_milp_info, accept_milp_audit
from tests.routing.test_model8_physical_bounds import instance
from tools.configuration.loader import load_routing_case


@pytest.mark.parametrize('kind',['ordinary','late_release','multiple_satellites'])
@pytest.mark.parametrize('refined',[False,True])
@pytest.mark.parametrize('count',[1,2,3])
def test_complete_exact_optimum_and_feasibility_preserved(kind,refined,count):
    import gurobipy as gp

    p = instance(kind)
    if refined:
        p = CalendarCycleInstance(p.network,p.orders,replace(p.operating_policy,cycle_end=133),
                                  dict(batch_count_candidates=[1,2,3]),p.cost_profile.config,model=8)
    if count not in p.batch_count_candidates:
        p = CalendarCycleInstance(p.network,p.orders,p.operating_policy,
                                  dict(batch_count_candidates=[1,2,3]),p.cost_profile.config,model=8)
    child = p.for_count(count)
    library,_ = enumerate_trip_library(child)
    values = []
    statuses = []
    for strengthened in (False,True):
        model,ctx = build_model(child,compact_e2=True,strengthening='physical_time')
        try:
            if strengthened:
                add_shared_route_bounds(child,model,ctx,library)
            model.Params.OutputFlag = 0
            model.Params.TimeLimit = 20
            model.Params.Threads = 1
            model.Params.Seed = 42
            model.optimize()
            assert model.Status in (gp.GRB.OPTIMAL,gp.GRB.INFEASIBLE)
            statuses.append(model.Status)
            if model.SolCount:
                info = read_milp_info(model,proof_scope='tiny_complete_M8',build_time=0)
                result = extract_solution(child,model,ctx,info)
                bounds = [b['start'] for b in result['batches']]+[child.operating_policy.cycle_end]
                attach_calendar_decisions(p,result,count,bounds)
                result['audit'] = audit_solution(p,result)
                accept_milp_audit(result,model.ObjVal)
                assert result['audit']['valid'] and result['audit']['periodic_replay']['valid']
                values.append(result['audit']['objective'])
        finally:
            model.dispose()
    assert statuses[0] == statuses[1]
    if values:
        assert len(values) == 2 and values[0] == pytest.approx(values[1],abs=1e-6)


@pytest.mark.parametrize('case',['S03','S08','S05'])
def test_audited_real_witness_has_valid_shared_mask_extension(case):
    context = json.loads((HERE/'inputs'/case/'context.json').read_text(encoding='utf-8-sig'))
    p = load_instance(*load_routing_case(Path(context['case_path']),use_pool_config=False))
    result = json.loads(Path(context['witness_path']).read_text(encoding='utf-8-sig'))
    audit = audit_solution(p,result)
    assert audit['valid'] and audit['periodic_replay']['valid']
    library,_ = enumerate_trip_library(p)
    columns = {row['mask']:row for row in library}
    orders = list(p.orders)
    index = {o.id:i for i,o in enumerate(orders)}
    by_id = {o.id:o for o in orders}
    selected = []
    covered = 0
    for trip in result['e2_trips']:
        tasks = [index[e['order_id']]+(len(orders) if e['kind']=='p' else 0) for e in trip['events']]
        mask = sum(1 << task for task in tasks)
        assert not covered & mask
        covered |= mask
        col = columns[mask]
        assert col['satellite'] == trip['satellite_id']
        delivery_batches = {result['order_batch_assignment'][e['order_id']]
                            for e in trip['events'] if e['kind']=='d'}
        assert len(delivery_batches) <= 1
        nodes = [trip['satellite_id']]+[by_id[e['order_id']].customer for e in trip['events']]+[trip['satellite_id']]
        actual = sum(p.distance(a,b) for a,b in zip(nodes,nodes[1:]))
        assert col['distance'] <= actual+1e-7
        for left,right in zip(tasks,tasks[1:]):
            assert col['mask'] & ((1 << left)|(1 << right)) == (1 << left)|(1 << right)
        selected.append(col)
    assert covered == (1 << (2*len(orders)))-1
    for site in p.satellites:
        assert sum(row['satellite']==site for row in selected) == sum(t['satellite_id']==site for t in result['e2_trips'])
    sites = list(p.satellites)
    patterns = {row['mask']:row['distance'] for row in first_echelon_patterns(p)}
    for trip in result['e1_trips']:
        mask = sum(1 << (sites.index(e['site_id'])+(len(sites) if e['kind']=='p' else 0)) for e in trip['events'])
        nodes = [p.depot]+[e['site_id'] for e in trip['events']]+[p.depot]
        assert patterns[mask] <= sum(p.distance(a,b) for a,b in zip(nodes,nodes[1:]))+1e-7


def test_unsupported_multi_box_rejected_before_modifying_model():
    import gurobipy as gp

    p = instance('unequal_counts')
    model = gp.Model()
    try:
        with pytest.raises(ValueError,match='one-box'):
            add_shared_route_bounds(p,model,{},[])
    finally:
        model.dispose()


@pytest.mark.parametrize('kind',['ordinary','late_release','multiple_satellites'])
@pytest.mark.parametrize('refined',[False,True])
def test_unified_all_K_extension_preserves_complete_optimum(kind,refined):
    import gurobipy as gp
    from models.common.cycle.unified_milp import build_unified_model
    from models.routing.model_8.exact import solve_exact

    p = instance(kind)
    if refined:
        p = CalendarCycleInstance(p.network,p.orders,replace(p.operating_policy,cycle_end=133),
                                  dict(batch_count_candidates=[1,2,3]),p.cost_profile.config,model=8)
    baseline = solve_exact(p,time_limit=20,milp_options=dict(formulation='compact'))
    assert baseline['info']['proven_optimal']

    def strengthened(child,**kwargs):
        model,ctx = build_model(child,**kwargs)
        library,_ = enumerate_trip_library(child)
        add_shared_route_bounds(child,model,ctx,library)
        return model,ctx

    model,ctx = build_unified_model(p,build_fixed=strengthened,compact_e2=True)
    try:
        model.Params.OutputFlag = 0
        model.Params.TimeLimit = 20
        model.optimize()
        assert model.Status == gp.GRB.OPTIMAL
        assert model.ObjVal == pytest.approx(baseline['info']['objective'],abs=1e-6)
        k = next(k for k,v in ctx['selectors'].items() if v.X>.5)
        block = ctx['blocks'][k]
        result = extract_solution(block['problem'],model,block['context'],
                                  read_milp_info(model,proof_scope='all_K_extension',build_time=0))
        bounds = [b['start'] for b in result['batches']]+[block['problem'].operating_policy.cycle_end]
        attach_calendar_decisions(p,result,k,bounds)
        audit = audit_solution(p,result)
        assert audit['valid'] and audit['periodic_replay']['valid']
    finally:
        model.dispose()
