"""Guard exact customer counts, input provenance and current study scope."""
from collections import Counter
from copy import deepcopy
import json
import shutil

import pytest

from tools.audit.audit_uniform_benchmark27 import audit_uniform
from tools.configuration.loader import load_routing_case, resolve_project_path
from tools.data.build_formal_benchmark27 import digest
from tools.data.build_uniform_benchmark27 import COUNTS, DEFAULT_OUTPUT, build, read, select_customers
from models.common.cycle import load_instance


@pytest.fixture(scope='module')
def dataset():
    return resolve_project_path(DEFAULT_OUTPUT)


def test_all_216_entries_pass_input_audit_without_any_solver(dataset):
    result=audit_uniform(dataset)
    assert result['valid'] and result['model_configurations']==216
    assert result['solver_runs']==0 and not result['route_feasibility_certified']


@pytest.mark.parametrize('size,count',[('small',7),('medium',20),('large',50)])
def test_every_case_and_model_has_exact_customer_count(dataset,size,count):
    records=[r for r in read(dataset/'manifest.json')['cases'] if r['size']==size]
    assert len(records)==72
    for record in records:
        cfg,fleet=load_routing_case(record['case_path'],use_pool_config=False)
        p=load_instance(cfg,fleet)
        assert len(p.orders)==len(p.network.customers)==p.total==count
        assert p.operating_policy.container_pool_size==count
        for key in ('data','orders','routing_config','operating_policy','economic_config','parameter_contract'):
            assert resolve_project_path(cfg[key]).is_relative_to(dataset)


def test_proportional_selection_retains_all_strata_and_uses_no_result():
    orders=[dict(order_id=f'o{i}',customer_id=f'C{i}',container_count=1,
                 satellite_id='S0' if i<6 else 'S1',delivery_batch_id='b1' if i<6 else 'b2') for i in range(10)]
    chosen=select_customers(orders,5)
    assert chosen==['C0','C1','C2','C6','C7']
    assert chosen==select_customers(list(reversed(orders)),5)
    with pytest.raises(ValueError):select_customers(orders,11)


def test_m3_direct_cost_objective_matches_m8_profile(dataset):
    bases=read(dataset/'manifest.json')['base_cases']
    for base in bases:
        p3=load_instance(*load_routing_case(base['models']['3'],use_pool_config=False))
        p8=load_instance(*load_routing_case(base['models']['8'],use_pool_config=False))
        assert p3.cost_objective and p3.cost_profile.hash==p8.cost_profile.hash
        assert len(p3.batch_map)==1 and p3.operating_policy.container_pool_size==base['customer_count']


def test_refuses_overwrite(dataset):
    with pytest.raises(ValueError,match='overwrite'):build(dataset)


def test_current_registry_distinguishes_full_27_from_trial_9(dataset):
    current=read(resolve_project_path('config/experiments/current.json'))
    assert resolve_project_path(current['dataset'])==dataset
    frozen=read(resolve_project_path(current['frozen_registry']))
    assert digest(dataset/'manifest.json')==frozen['manifest_sha256']
    plan=read(resolve_project_path(current['alns_plan']))
    assert plan['complete_dataset_cases']==27 and len(plan['selected_cases'])==9
    assert len(plan['jobs'])==54 and plan['user_stop_remains_effective']
    assert Counter(c['size'] for c in plan['selected_cases'])==dict(small=3,medium=3,large=3)


def test_active_examples_reference_current_dataset_instead_of_other_cases(dataset):
    for path in resolve_project_path('config/experiments/examples').glob('*.json'):
        cfg,fleet=load_routing_case(path,use_pool_config=False)
        assert resolve_project_path(cfg['data']).is_relative_to(dataset)
        assert len(load_instance(cfg,fleet).orders)==7


def test_missing_order_is_rejected_even_when_artifact_hash_is_updated(dataset,tmp_path):
    target=tmp_path/'tampered'
    shutil.copytree(dataset,target)
    path=target/'model_8/large/L05/orders.json'
    values=read(path);values.pop();path.write_text(json.dumps(values),encoding='utf-8')
    manifest=read(target/'manifest.json')
    manifest['artifact_sha256'][path.relative_to(target).as_posix()]=digest(path)
    # Runtime paths still refer to the original dataset. Repoint all of them
    # and recompute hashes, so semantic inspection must detect the missing order.
    original=str(dataset).replace('\\','/')
    reference='data/formal_benchmark27_uniform_7_20_50_v1'
    for item in target.rglob('*.json'):
        if item.name=='manifest.json':continue
        text=item.read_text(encoding='utf-8-sig')
        item.write_text(text.replace(reference,str(target).replace('\\','/')),encoding='utf-8')
    manifest=json.loads(json.dumps(manifest).replace(reference,str(target).replace('\\','/')))
    manifest['artifact_sha256']={name:digest(target/name) for name in manifest['artifact_sha256']}
    (target/'manifest.json').write_text(json.dumps(manifest),encoding='utf-8')
    with pytest.raises(ValueError):audit_uniform(target)
