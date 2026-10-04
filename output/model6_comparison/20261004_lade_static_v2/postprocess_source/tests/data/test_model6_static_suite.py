"""Larger M6 inputs preserve source demand rather than inflate toy examples."""
import hashlib
import json
from pathlib import Path

import pytest

from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from tools.data.build_model6_static_suite import build_suite


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
