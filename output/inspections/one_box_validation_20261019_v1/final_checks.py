"""Read-only release checks; do not change inputs or run an optimizer."""
from pathlib import Path
import hashlib
import json

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    old, new = (ROOT/'data'/name for name in ('formal_benchmark27_v1','formal_benchmark27_one_box_v1'))
    old_manifest, manifest = read(old/'manifest.json'), read(new/'manifest.json')
    counts = {}
    for name, folder, item in (('legacy',old,old_manifest),('one_box',new,manifest)):
        for relative, expected in item['artifact_sha256'].items():
            assert sha(folder/relative)==expected, (name,relative)
        counts[name]=len(item['artifact_sha256'])
    assert sha(ROOT/manifest['generator'])==manifest['generator_sha256']
    assert sha(old/'manifest.json')==manifest['source_dataset_manifest_sha256']
    for base in manifest['base_cases']:
        relative = Path('shared')/base['size']/base['id']
        for name in ('network.csv','fleet.json','economic.json','batch_count_certificate.json'):
            assert (old/relative/name).read_bytes()==(new/relative/name).read_bytes()
        for model in range(1,9):
            relative_model = Path(f'model_{model}')/base['size']/base['id']
            original = {o['order_id']:o for o in read(old/relative_model/'orders.json')}
            orders = {o['order_id']:o for o in read(new/relative_model/'orders.json')}
            assert len(orders)==len(original)==base['customer_count']==base['total_container_uses']
            assert orders.keys()==original.keys()
            for identifier, order in orders.items():
                assert order['container_count']==1
                assert {k:v for k,v in order.items() if k!='container_count'}=={
                    k:v for k,v in original[identifier].items() if k!='container_count'}
            a,b=(read(folder/relative_model/'policy.json') for folder in (old,new))
            assert b['container_pool_size']==base['customer_count']
            assert {k:v for k,v in a.items() if k!='container_pool_size'}=={
                k:v for k,v in b.items() if k!='container_pool_size'}
    first = read(HERE/'s01/manifest.json')
    second = read(HERE/'representatives/manifest.json')
    assert first['source_hashes']==second['source_hashes']
    for name, expected in first['source_hashes'].items():
        prefix = 'sources/current/'
        if name.startswith(prefix):
            assert sha(ROOT/name[len(prefix):])==expected, name
    rows=[read(HERE/'s01/runs'/job['id']/'attempt_001/row.json') for job in first['jobs']]
    assert len(rows)==16 and all(r['valid'] and r['periodic_valid'] for r in rows)
    result=dict(valid=True, artifact_hashes_checked=counts, base_cases_preserved=27,
        model_order_and_policy_pairs_preserved=216, identical_k_certificates=27,
        identical_pilot_source_snapshots=True, current_sources_unchanged_since_pilot=True,
        s01_valid=16, s01_exact_optimal=sum(r['method']=='exact' and r['proven_optimal'] for r in rows),
        new_manifest_sha256=sha(new/'manifest.json'), legacy_manifest_sha256=sha(old/'manifest.json'))
    (HERE/'final_checks.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':
    main()
