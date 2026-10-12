"""Larger M6 inputs preserve source demand rather than inflate toy examples."""
import hashlib
import json
from pathlib import Path

import pytest

from tools.configuration.loader import load_routing_case,resolve_project_path
from models.common.cycle import load_instance
from tools.data.build_model6_static_suite import build_suite


def test_uniform_adaptation_repartitions_m6_without_changing_m5_or_customer_windows(tmp_path):
    manifest=build_suite(tmp_path/'uniform',families=['lade_0823_10aoi'],uniform_cycle=True,
                         batch_count_candidates=[1,2,3],min_phase_minutes=1)
    payload=json.loads(manifest.read_text())
    record=payload['cases'][0]
    p5=load_instance(*load_routing_case(record['m5_case'],use_pool_config=False))
    p6=load_instance(*load_routing_case(record['m6_case'],use_pool_config=False))
    assert payload['schedule_mode']=='uniform_cycle' and payload['min_phase_minutes']==1 and p6.uniform_cycle
    assert p5.orders==p6.orders and p5.operating_policy==p6.operating_policy
    assert p6.total==131 and p6.batch_count_candidates==(1,2,3)
    for k in (1,2,3):
        child=p6.for_count(k)
        assert {b.end-b.start for b in child.operating_policy.batches}=={780*60//k}
        assert child.operating_policy.batches[-1].end==p5.operating_policy.cycle_end
        assert child.shared_cutoff_bounds==(60,780*60//k-60)
        assert [o.delivery_window for o in child.orders]==[o.delivery_window for o in p5.orders]


def test_lade_adaptation_preserves_demand_windows_fleet_and_travel(tmp_path):
    manifest=build_suite(tmp_path/'suite',families=['lade_0823_10aoi'])
    record=json.loads(manifest.read_text())['cases'][0]
    assert record['orders']==10 and record['total_container_uses']==131
    source_config,source_fleet=load_routing_case(record['source_case'],use_pool_config=False)
    source=load_instance(source_config,source_fleet)
    c5,f5=load_routing_case(record['m5_case'],use_pool_config=False)
    c6,f6=load_routing_case(record['m6_case'],use_pool_config=False)
    p5,p6=load_instance(c5,f5),load_instance(c6,f6)
    assert f5==f6==source_fleet
    assert p5.orders==p6.orders and p5.total==p6.total==source.total
    for original,adapted in zip(source.orders,p6.orders):
        for field in ('id','customer','satellite','batch','count','goods_weight','delivery_window',
                      'pickup_window','service_time','min_use_time'):
            assert getattr(original,field)==getattr(adapted,field)
        assert adapted.release_time==source.operating_policy.cycle_start
    p6.project({o.id:o.batch for o in p6.orders})
    for a in source.network.nodes:
        for b in source.network.nodes:
            assert source.distance(a,b)==p6.distance(a,b)
            assert source.travel(a,b)==p6.travel(a,b)
    assert p6.cost_profile.config['currency']=='synthetic_units'
    assert p6.cost_profile.config['evaluation_period']=='one 780-minute cycle'
    provenance=json.loads(Path(c6['dataset_metadata']).read_text())
    assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in provenance['source_input_sha256'].items())
    with pytest.raises(ValueError,match='overwrite'):
        build_suite(tmp_path/'suite',families=['lade_0823_10aoi'])


def test_missing_source_family_is_rejected_before_output(tmp_path):
    with pytest.raises(ValueError,match='matched no cases'):
        build_suite(tmp_path/'suite',families=['missing_family'])
    assert not (tmp_path/'suite').exists()


def test_full_adaptation_covers_each_multibatch_config_once(tmp_path):
    from tools.data.static_batch_dataset import load_manifest
    _,source=load_manifest('data/manifest.json',groups=['multi_batch'])
    manifest=build_suite(tmp_path/'all',all_cases=True)
    payload=json.loads(manifest.read_text())
    records=payload['cases']
    assert len(records)==len(source)==186
    assert payload['coverage']['all_multibatch'] is True
    assert {r['name'] for r in records}=={r['case_id'] for r in source}
    assert len({r['m6_case'] for r in records})==186
    assert {r['candidate_batch_count'] for r in records}=={2,3}
    originals={r['case_id']:r for r in source}
    for adapted in records:
        original=originals[adapted['name']]
        assert adapted['total_container_uses']==original['total_container_uses']
        assert adapted['orders']==original['order_count']
        assert adapted['family_id']==original['family_id']
        assert adapted['size']==original['size']
        source_config,source_fleet=load_routing_case(adapted['source_case'],use_pool_config=False)
        new_config,new_fleet=load_routing_case(adapted['m5_case'],use_pool_config=False)
        assert new_fleet==source_fleet
        old_orders=json.loads(resolve_project_path(source_config['orders']).read_text())
        new_orders=json.loads(resolve_project_path(new_config['orders']).read_text())
        assert len(old_orders)==len(new_orders)
        allowed=[b['id'] for b in source_fleet['operating_policy']['batches']]
        for old,new in zip(old_orders,new_orders):
            assert {k:v for k,v in new.items() if k not in ('release_time','allowed_batch_ids')}==old
            assert new['release_time']==source_fleet['operating_policy']['cycle_start']
            assert new['allowed_batch_ids']==allowed
        for key in ('data','routing_config','operating_policy','distance_matrix','duration_matrix'):
            if source_config.get(key):
                assert resolve_project_path(source_config[key]).read_bytes()==resolve_project_path(new_config[key]).read_bytes()


def test_variable_cutoff_adaptation_changes_only_m6_temporal_decisions(tmp_path):
    manifest=build_suite(tmp_path/'flexible',families=['lade_0823_10aoi'],
                         optimize_cutoffs=True,min_phase_minutes=10)
    payload=json.loads(manifest.read_text()); record=payload['cases'][0]
    p5=load_instance(*load_routing_case(record['m5_case'],use_pool_config=False))
    p6=load_instance(*load_routing_case(record['m6_case'],use_pool_config=False))
    assert payload['optimize_cutoffs'] and p6.optimize_cutoffs
    assert p5.orders==p6.orders and p5.operating_policy==p6.operating_policy
    for b in p6.operating_policy.batches:
        assert p6.cutoff_bounds[b.id]==(b.start+600,b.end-600)
    assert p6.cost_profile.config['currency']=='synthetic_units'
