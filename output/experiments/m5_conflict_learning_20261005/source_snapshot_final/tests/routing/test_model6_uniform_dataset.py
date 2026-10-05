"""Dataset integrity, independent clocks, paired controls and failure retention."""
from copy import deepcopy
import json

import pytest

from configuration.loader import load_routing_case, resolve_project_path
from models.routing.common.cycle import load_instance
from tools.data.build_model6_uniform_dataset import (
    ARMS, DEFAULT_DESIGN, appointment_orders, build_suite, load_suite, treatment_config,
)
from tools.audit.audit_model6_uniform_dataset import audit_suite


@pytest.fixture(scope='module')
def generated(tmp_path_factory):
    return build_suite(tmp_path_factory.mktemp('uniform_v2')/'suite',
                       families=['zhou_Set1_7-2-1','lade_0823_10aoi','mechanism_cutoff_choice'])


def test_no_overwrite_and_hash_tamper_rejected(tmp_path):
    path=build_suite(tmp_path/'suite',families=['mechanism_cutoff_choice'])
    with pytest.raises(ValueError,match='overwrite'):
        build_suite(tmp_path/'suite',families=['mechanism_cutoff_choice'])
    manifest=load_suite(path)
    case=resolve_project_path(manifest['cases'][0]['m6_case'])
    case.write_text(case.read_text()+' ')
    with pytest.raises(ValueError,match='HASH_MISMATCH'):
        load_suite(path)


def test_audit_recomputes_manifest_semantic_hash(tmp_path):
    path=build_suite(tmp_path/'suite',families=['mechanism_cutoff_choice'])
    manifest=json.loads(path.read_text())
    manifest['cases'][0]['semantic_sha256']='0'*64
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError,match='SEMANTIC_HASH_MISMATCH'):
        audit_suite(path)


def test_reproducible_semantics_across_output_directories(generated,tmp_path):
    second=build_suite(tmp_path/'other',families=['zhou_Set1_7-2-1','lade_0823_10aoi','mechanism_cutoff_choice'])
    a,b=load_suite(generated),load_suite(second)
    assert a['semantic_dataset_sha256']==b['semantic_dataset_sha256']
    # All bytes match after replacing only the output-root references.
    for file in a['artifact_sha256']:
        left=(generated.parent/file).read_text().replace(str(generated.parent),'<SUITE>')
        right=(second.parent/file).read_text().replace(str(second.parent),'<SUITE>')
        assert left==right,file


def test_appointments_do_not_depend_on_k_or_routing_parameters():
    cfg,fleet=load_routing_case('data/multi_batch/small/lade_0823_10aoi/b3/case.json',use_pool_config=False)
    orders=json.loads(resolve_project_path(cfg['orders']).read_text())
    design=json.loads(resolve_project_path(DEFAULT_DESIGN).read_text())
    a=appointment_orders(orders,'lade_0823_10aoi',fleet['operating_policy'],design,'spread_medium')
    changed={**design,'batch_count_candidates':[1,2,3,4,5,6],'reference_cutoff_ratio':0.1,'pool_rates':{'base':99}}
    assert a==appointment_orders(orders,'lade_0823_10aoi',fleet['operating_policy'],changed,'spread_medium')


def test_nested_windows_and_sensitivity_pairs(generated):
    records={r['variant']:r for r in load_suite(generated)['cases'] if r['family_id']=='lade_0823_10aoi'}
    def inputs(v):
        c,f=load_routing_case(records[v]['m6_case'],use_pool_config=False)
        return json.loads(resolve_project_path(c['orders']).read_text()),c,f
    narrow,_,_=inputs('spread_narrow'); medium,base,base_fleet=inputs('spread_medium'); wide,_,_=inputs('spread_wide')
    for n,m,w in zip(narrow,medium,wide):
        assert w['delivery_ready']<=m['delivery_ready']<=n['delivery_ready']
        assert n['delivery_due']<=m['delivery_due']<=w['delivery_due']
        assert n['pickup_ready']==m['pickup_ready']==w['pickup_ready']
        assert n['pickup_due']<=m['pickup_due']<=w['pickup_due']
    for variant in ('clean_fast','clean_slow','fleet_tight','capacity_tight','pool_low','pool_high','work_high','extended_k'):
        assert inputs(variant)[0]==medium
    repeat,_,_=inputs('repeat_orders')
    assert len(repeat)>len(medium)
    assert sum(o['container_count'] for o in repeat)==sum(o['container_count'] for o in medium)
    assert sum(o['goods_weight'] for o in repeat)==pytest.approx(sum(o['goods_weight'] for o in medium))
    released,_,_=inputs('release_staggered')
    assert any(o['release_time']>base_fleet['operating_policy']['cycle_start'] for o in released)


def test_same_cost_and_physical_inputs_across_ablations(generated):
    row=next(r for r in load_suite(generated)['cases'] if r['name']=='lade_0823_10aoi__spread_medium')
    cfg,fleet=load_routing_case(row['m6_case'],use_pool_config=False)
    problems={arm:load_instance(treatment_config({**cfg,'_source_case':row['m6_case']},arm),fleet) for arm in ARMS}
    joint=problems['joint']
    for p in problems.values():
        assert p.orders==joint.orders
        assert p.cost_profile.hash==joint.cost_profile.hash
        assert p.operating_policy==joint.operating_policy
    assert problems['fixed_k'].batch_count_candidates==(3,)
    assert problems['full_pool'].pool_min==joint.total
    assert all(len(v)<=1 for v in problems['fixed_assignment'].legal_batches.values())
    assert not problems['fixed_cutoff'].optimize_cutoffs
    with pytest.raises(ValueError,match='Reference K'):
        treatment_config({**cfg,'_source_case':row['m6_case']},'fixed_k',reference_count=6)


def test_audit_retains_rejected_domains_and_late_delivery_coverage(generated):
    report=audit_suite(generated)
    assert report['families']==3 and report['demand_conservation_valid']
    assert report['cases']==31  # Two 15-variant anchors and one preserved mechanism.
    rows=[r for r in report['domain_rows'] if r['case']=='lade_0823_10aoi__spread_medium' and r['k']==3]
    assert rows[0]['last_batch_eligible_orders']>0
    rejected=[r for r in report['domain_rows'] if r['variant']=='release_staggered' and r['k']==1]
    assert rejected and all(r['proven_infeasible_by_necessary_conditions'] for r in rejected)
    assert all(r['empty_order_ids'] for r in rejected)


def test_dry_run_preserves_cases_and_does_not_create_output(generated,tmp_path,capsys):
    from experiments.runs.run_model6_dataset import main
    out=tmp_path/'not_created'
    plan=main(['--manifest',str(generated),'--output',str(out),'--variants','spread_medium',
               '--arms','joint','fixed_k','fixed_cutoff','fixed_assignment','full_pool',
               '--seeds','42','43','--dry-run'])
    assert plan['case_count']==2 and plan['job_count']==20
    assert not out.exists()
