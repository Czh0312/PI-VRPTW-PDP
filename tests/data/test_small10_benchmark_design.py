"""Validate the customer-count revision and guard frozen physical inputs."""
import json

import pytest

from tools.audit.audit_benchmark27_spacetime import audit_design
from tools.data.build_benchmark27_small10 import build, read, select_customers, SOURCE
from tools.configuration.loader import PROJECT_ROOT


@pytest.fixture(scope='module')
def revised_dataset(tmp_path_factory):
    return build(tmp_path_factory.mktemp('small10')/'dataset')


def test_selection_uses_numeric_ids_and_rejects_invented_customers():
    orders=[dict(customer_id=f'C{i}') for i in range(12)]
    assert select_customers(orders,'first')==[f'C{i}' for i in range(10)]
    assert select_customers(orders,'last')==[f'C{i}' for i in range(2,12)]
    with pytest.raises(ValueError):
        select_customers(orders[:7],'first')


def test_all_216_inputs_pass_independent_design_audit(revised_dataset):
    result=audit_design(revised_dataset)
    assert result['valid'] and result['model_config_count']==216
    assert [r['customers'] for r in result['cases'] if r['id'].startswith('S')]==[10]*9
    assert result['period_frequency']=={2:9,3:9,4:9}
    assert read(revised_dataset/'source_integrity.json')['passed']


def test_retained_cases_preserve_model_inputs(revised_dataset):
    source=PROJECT_ROOT/SOURCE
    manifest=read(revised_dataset/'manifest.json')
    for base in manifest['base_cases']:
        if base['size']=='small' and base['id'] not in ('S04','S05','S06'):
            continue
        for model in range(1,9):
            relative=f'model_{model}/{base["size"]}/{base["id"]}'
            for filename in ('orders.json','policy.json','parameters.json'):
                assert (source/relative/filename).read_bytes()==(revised_dataset/relative/filename).read_bytes()
            old=read(source/relative/'case.json')
            new=read(revised_dataset/relative/'case.json')
            for field in ('case_name','dataset_version'):
                old.pop(field)
                new.pop(field)
            normalized=json.loads(json.dumps(new,ensure_ascii=False).replace(str(revised_dataset).replace('\\','/'), SOURCE))
            assert normalized==old


def test_frozen_dataset_cannot_be_overwritten(revised_dataset):
    with pytest.raises(ValueError,match='overwrite'):
        build(revised_dataset)


def test_changed_customer_weight_fails_independent_audit(revised_dataset):
    path=revised_dataset/'shared/small/S01/reference_orders.json'
    original=path.read_bytes()
    manifest_path=revised_dataset/'manifest.json'
    original_manifest=manifest_path.read_bytes()
    try:
        orders=read(path)
        orders[0]['goods_weight']+=1
        path.write_text(json.dumps(orders),encoding='utf-8')
        # Even after the checksum and provenance are updated, inconsistent
        # physical demand must still fail the independent input audit.
        from tools.data.build_formal_benchmark27 import digest
        from tools.data.one_box_contract import preserved_orders_hash
        provenance_path=revised_dataset/'shared/small/S01/provenance.json'
        original_provenance=provenance_path.read_bytes()
        provenance=read(provenance_path)
        provenance['preserved_orders_sha256']=preserved_orders_hash(orders)
        provenance_path.write_text(json.dumps(provenance),encoding='utf-8')
        manifest=read(manifest_path)
        for changed in (path,provenance_path):
            manifest['artifact_sha256'][changed.relative_to(revised_dataset).as_posix()]=digest(changed)
        manifest_path.write_text(json.dumps(manifest),encoding='utf-8')
        with pytest.raises(ValueError):
            audit_design(revised_dataset)
    finally:
        path.write_bytes(original)
        manifest_path.write_bytes(original_manifest)
        if 'original_provenance' in locals():
            provenance_path.write_bytes(original_provenance)
