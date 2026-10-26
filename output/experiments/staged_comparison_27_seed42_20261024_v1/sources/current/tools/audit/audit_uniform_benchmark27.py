"""Independent input-only audit of all 216 uniform-size entries."""
import argparse
from collections import Counter
import json

from tools.configuration.loader import resolve_project_path
from tools.data.build_uniform_benchmark27 import COUNTS, VERSION, network_rows, read, select_customers
from tools.data.build_formal_benchmark27 import digest, write_json
from tools.audit.audit_formal_benchmark27 import audit_dataset, require


def audit_uniform(dataset=f'data/{VERSION}'):
    root = resolve_project_path(dataset)
    manifest = read(root/'manifest.json')
    require(manifest['dataset_version']==VERSION,'Wrong uniform dataset version')
    require(manifest['customer_count_by_size']==COUNTS,'Wrong exact size contract')
    source = resolve_project_path(manifest['source_dataset'])
    require(digest(source/'manifest.json')==manifest['source_dataset_manifest_sha256'],'Source manifest differs')
    standard = audit_dataset(root)
    require(not standard['fixed_model_necessary_condition_failures'],'Fixed-model necessary conditions failed')
    require(Counter((b['size'],b['customer_count']) for b in manifest['base_cases'])=={(s,n):9 for s,n in COUNTS.items()},'Wrong size distribution')
    for base in manifest['base_cases']:
        name,size = base['id'],base['size']
        old = source/'shared'/size/name
        new = root/'shared'/size/name
        before = read(old/'reference_orders.json')
        selected = select_customers(before,COUNTS[size])
        require(base['customer_selection']['selected_customer_ids']==selected,f'{name}: selection differs')
        require(read(new/'reference_orders.json')==[o for o in before if o['customer_id'] in selected],f'{name}: source order fields altered')
        _,before_rows = network_rows(old/'network.csv')
        _,after_rows = network_rows(new/'network.csv')
        require(after_rows==[r for r in before_rows if r['node_type']!='c' or r['node_id'] in selected],f'{name}: coordinates/site assignment changed')
        for filename in ('fleet.json','economic.json'):
            require((old/filename).read_bytes()==(new/filename).read_bytes(),f'{name}: source resource/economics changed')
        require(read(new/'reference_policy.json')=={**read(old/'reference_policy.json'),'container_pool_size':COUNTS[size]},f'{name}: physical policy changed')
    return dict(valid=True,dataset_version=VERSION,base_cases=27,model_configurations=216,
        base_case_count=27,model_config_count=216,
        customer_count_by_size=COUNTS,cases_per_size=9,solver_runs=0,route_feasibility_certified=False,
        source_preservation=True,standard_input_validation=standard)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset',default=f'data/{VERSION}')
    parser.add_argument('--output',default='output/inspections/uniform_benchmark27_20261024_v1/input_audit.json')
    args=parser.parse_args()
    result=audit_uniform(args.dataset)
    write_json(resolve_project_path(args.output),result)
    print(json.dumps({k:v for k,v in result.items() if k!='standard_input_validation'},ensure_ascii=False))
