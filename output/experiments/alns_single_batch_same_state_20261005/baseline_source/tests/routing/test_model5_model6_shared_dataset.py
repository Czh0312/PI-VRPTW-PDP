"""Matched physical inputs, fixed-policy scope and release-time enforcement."""
from dataclasses import replace
import json
import random

import pytest

from configuration.loader import load_routing_case, resolve_project_path
from models.routing.common.cycle import MultiBatchInstance, load_instance
from models.routing.common.cycle.audit import audit_solution
from models.routing.common.cycle.precheck import check_necessary_conditions
from models.routing.solvers.alns.evaluator import evaluate, initial_state
from tests.routing.test_multibatch_cycle import fixture, hand_solution
from tests.routing.test_model6_joint import exact_available
from tools.data.build_model5_model6_shared_dataset import build_suite, load_shared_suite, verify_pair


def unavailable_problem(kind):
    original=fixture()
    order=replace(original.orders[0],release_time=1) if kind=='release' else replace(original.orders[0],allowed_batch_ids=('b2',))
    return MultiBatchInstance(original.network,[order,*original.orders[1:]],original.operating_policy,model=5)


@pytest.mark.parametrize('kind,code',[('release','ORDER_RELEASE_AFTER_BATCH_START'),('allowed','ORDER_BATCH_NOT_ALLOWED')])
def test_availability_checked_by_precheck_decoder_and_independent_audit(kind,code):
    p=unavailable_problem(kind)
    check=check_necessary_conditions(p)
    assert check['proven_infeasible'] and code in {e['code'] for e in check['errors']}
    _,_,errors=evaluate(p,initial_state(p,random.Random(42)))
    assert code in errors
    replay=audit_solution(p,hand_solution(p))
    assert not replay['valid'] and code in {e['code'] for e in replay['errors']}


@pytest.mark.parametrize('kind',['release','allowed'])
@pytest.mark.parametrize('method',['alns','exact'])
def test_public_m5_entries_reject_bad_availability_without_importing_gurobi(kind,method,monkeypatch):
    import builtins
    original_import=builtins.__import__
    def guarded(name,*args,**kwargs):
        if name.startswith('gurobipy'):
            raise AssertionError('Necessary rejection must not need a Gurobi license')
        return original_import(name,*args,**kwargs)
    monkeypatch.setattr(builtins,'__import__',guarded)
    if method=='exact':
        from models.routing.variants.model_5.exact.solver import solve_exact
        result=solve_exact(unavailable_problem(kind),time_limit=.2)
    else:
        from models.routing.variants.model_5.alns.solver import solve_alns
        result=solve_alns(unavailable_problem(kind),max_time=.2)
    assert result['info']['status']=='INFEASIBLE' and result['info']['proven_infeasible']


@pytest.mark.parametrize('kind',['release','allowed'])
def test_direct_m5_milp_enforces_availability(kind,exact_available):
    import gurobipy as gp
    from models.routing.variants.model_5.exact.model import build_model
    model,_=build_model(unavailable_problem(kind))
    try:
        model.Params.TimeLimit=3
        model.optimize()
        assert model.Status==gp.GRB.INFEASIBLE
    finally:
        model.dispose()


def test_release_exactly_at_loading_and_missing_legacy_fields_remain_valid():
    old=fixture()
    p=MultiBatchInstance(old.network,[replace(o,release_time=old.batch_map[o.batch].start,allowed_batch_ids=(o.batch,))
                                     for o in old.orders],old.operating_policy,model=5)
    for problem in (old,p):
        assert check_necessary_conditions(problem)['passed']
        assert audit_solution(problem,hand_solution(problem))['valid']


@pytest.fixture(scope='module')
def paired(tmp_path_factory):
    return build_suite(tmp_path_factory.mktemp('m5_m6_shared')/'suite',
                       families=['zhou_Set1_7-2-1','lade_0823_10aoi','mechanism_cutoff_choice'])


def test_pair_preserves_every_exogenous_order_field_and_uses_shared_files(paired):
    manifest=load_shared_suite(paired)
    assert manifest['case_count']==31
    for record in manifest['cases']:
        checked=verify_pair(record)
        assert checked['valid_pair'] and checked['source_calendar_inside_m6_domain']
        configs=[load_routing_case(record[key],use_pool_config=False)[0] for key in ('m5_case','m6_case','m6_fixed_policy_case')]
        for key in ('data','orders','routing_config','operating_policy','economic_config'):
            assert len({c[key] for c in configs})==1
        assert record['reference_batch_count']==(2 if record['variant']=='legacy_control' else 3)
    row=next(r for r in manifest['cases'] if r['name']=='lade_0823_10aoi__spread_medium')
    p=load_instance(*load_routing_case(row['m5_case'],use_pool_config=False))
    assert len({o.batch for o in p.orders})==3
    assert p.operating_policy.container_pool_size==p.total


def test_failed_fixed_policy_is_kept_and_has_the_same_cost_control_domain(paired):
    row=next(r for r in load_shared_suite(paired)['cases'] if r['name']=='zhou_Set1_7-2-1__release_staggered')
    checked=verify_pair(row)
    assert not checked['m5_necessary_conditions']['passed']
    assert checked['blocked_assignment_orders']
    p=load_instance(*load_routing_case(row['m6_fixed_policy_case'],use_pool_config=False))
    assert any(not values for values in p.legal_batches.values())


def test_one_sided_physical_change_rejected_before_running(paired,tmp_path):
    row=next(r for r in load_shared_suite(paired)['cases'] if r['name']=='lade_0823_10aoi__spread_medium')
    config,_=load_routing_case(row['m5_case'],use_pool_config=False)
    orders=json.loads(resolve_project_path(config['orders']).read_text())
    orders[0]['delivery_due']+=1
    path=tmp_path/'orders.json';path.write_text(json.dumps(orders))
    case=tmp_path/'m5.json';case.write_text(json.dumps({**config,'orders':str(path)}))
    with pytest.raises(ValueError,match='SHARED_INPUT_MISMATCH'):
        verify_pair({**row,'m5_case':str(case)})


def test_rebuild_reproduces_shared_semantics_and_protects_existing_data(paired,tmp_path):
    second=build_suite(tmp_path/'again',families=['zhou_Set1_7-2-1','lade_0823_10aoi','mechanism_cutoff_choice'])
    assert load_shared_suite(second)['semantic_dataset_sha256']==load_shared_suite(paired)['semantic_dataset_sha256']
    with pytest.raises(ValueError,match='overwrite'):
        build_suite(paired.parent,families=['zhou_Set1_7-2-1'])


def test_dry_run_pairs_all_models_same_seed_and_budget(paired,tmp_path,capsys):
    from experiments.runs.run_model5_model6_shared import main
    output=tmp_path/'unused'
    plan=main(['--manifest',str(paired),'--output',str(output),'--variants','spread_medium',
               '--seeds','42','43','--time','3','--dry-run'])
    assert plan['case_count']==2 and plan['job_count']==12
    assert all(j['budget']==3 for j in plan['jobs'])
    assert {j['treatment'] for j in plan['jobs']}=={'M5_distance','M6_uniform','M6_fixed_policy_cost'}
    assert not output.exists()
