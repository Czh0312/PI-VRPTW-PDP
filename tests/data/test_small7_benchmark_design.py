"""Guard the seven-customer freeze and the filter-only comparison contract."""
import json

import pytest

from tools.audit.audit_benchmark27_spacetime import audit_design
from tools.configuration.loader import PROJECT_ROOT
from tools.data.build_benchmark27_small7 import SOURCE, build, read, select_customers


@pytest.fixture(scope='module')
def revised_dataset(tmp_path_factory):
    return build(tmp_path_factory.mktemp('small7')/'dataset')


def test_numeric_selection_and_source_size():
    orders=[dict(customer_id=f'C{i}') for i in reversed(range(10))]
    assert select_customers(orders)==[f'C{i}' for i in range(7)]
    with pytest.raises(ValueError):
        select_customers(orders[:7])


def test_all_216_inputs_pass_independent_audit(revised_dataset):
    result=audit_design(revised_dataset)
    assert result['valid'] and result['model_config_count']==216
    assert [r['customers'] for r in result['cases'] if r['id'].startswith('S')]==[7]*9
    assert result['period_frequency']=={2:9,3:9,4:9}
    protocol=read(revised_dataset/'comparison_protocol.json')
    assert protocol['E1_volume_by_customer_count']['7']==10
    assert protocol['E2_total_by_customer_count']['7']==4
    assert read(revised_dataset/'source_integrity.json')['passed']


def test_retained_physical_inputs_and_all_model_orders(revised_dataset):
    source=PROJECT_ROOT/SOURCE
    for base in read(revised_dataset/'manifest.json')['base_cases']:
        relative=f'shared/{base["size"]}/{base["id"]}'
        for filename in ('fleet.json','economic.json'):
            assert (source/relative/filename).read_bytes()==(revised_dataset/relative/filename).read_bytes()
        for model in range(1,9):
            relative=f'model_{model}/{base["size"]}/{base["id"]}'
            if base['size']=='small':
                selected=base['customer_selection']['selected_customer_ids']
                expected=[o for o in read(source/relative/'orders.json') if o['customer_id'] in selected]
                assert read(revised_dataset/relative/'orders.json')==expected
                cfg=read(revised_dataset/relative/'case.json')
                options=cfg.get('pool_options',cfg.get('joint_batch_options',{}))
                assert cfg.get('container_pool_size',options.get('pool_max'))==7
                if model>=6:
                    assert max(options['batch_count_candidates'])<=7
            else:
                for filename in ('orders.json','policy.json','parameters.json'):
                    assert (source/relative/filename).read_bytes()==(revised_dataset/relative/filename).read_bytes()


def test_refuses_frozen_overwrite(revised_dataset):
    with pytest.raises(ValueError,match='overwrite'):
        build(revised_dataset)


def test_source_time_window_tampering_fails_even_after_hash_update(revised_dataset):
    from tools.data.build_formal_benchmark27 import digest
    from tools.data.one_box_contract import preserved_orders_hash
    paths=[revised_dataset/p for p in ('shared/small/S01/reference_orders.json',
        'shared/small/S01/provenance.json','manifest.json')]
    originals=[p.read_bytes() for p in paths]
    try:
        orders=read(paths[0]);orders[0]['delivery_ready']+=1
        paths[0].write_text(json.dumps(orders),encoding='utf-8')
        provenance=read(paths[1]);provenance['preserved_orders_sha256']=preserved_orders_hash(orders)
        paths[1].write_text(json.dumps(provenance),encoding='utf-8')
        manifest=read(paths[2])
        for p in paths[:2]:
            manifest['artifact_sha256'][p.relative_to(revised_dataset).as_posix()]=digest(p)
        paths[2].write_text(json.dumps(manifest),encoding='utf-8')
        with pytest.raises(ValueError):
            audit_design(revised_dataset)
    finally:
        for p,payload in zip(paths,originals):
            p.write_bytes(payload)
