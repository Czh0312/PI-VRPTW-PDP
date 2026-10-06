"""Verify both model inputs, source preservation and nested reference policies."""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import json

from configuration.loader import resolve_project_path
from tools.data.build_model5_model6_shared_dataset import load_shared_suite, verify_pair
from tools.data.build_model6_uniform_dataset import signature, write_json


def audit_suite(path):
    manifest=load_shared_suite(path)
    rows=[]
    for record in manifest['cases']:
        checked=verify_pair(record)
        if signature(checked['shared_input_sha256'])!=record['shared_semantic_sha256']:
            raise ValueError('Shared semantic fingerprint mismatch')
        pre=checked['m5_necessary_conditions']
        if pre['passed']!=record['m5_necessary_conditions_passed']:
            raise ValueError('Stored necessary-condition status is stale')
        rows.append(dict(case=record['name'],family_id=record['family_id'],variant=record['variant'],
                         source_kind=record['source_kind'],customer_count=record['customer_count'],
                         reference_k=record['reference_batch_count'],shared_inputs_valid=True,
                         calendar_inside_m6_domain=True,m5_necessary_passed=pre['passed'],
                         m5_rejection_codes=sorted({e['code'] for e in pre['errors']}),
                         m5_pool_lower_bound=pre['pool_lower_bound'],m5_configured_pool=record['m5_pool_size'],
                         blocked_assignment_orders=checked['blocked_assignment_orders'],
                         m6_empty_domains=checked['m6_domain_empty_orders']))
    return dict(dataset_version=manifest['dataset_version'],cases=len(rows),
                shared_inputs_valid=all(r['shared_inputs_valid'] for r in rows),source_preservation_valid=True,
                reference_calendars_inside_m6_domain=all(r['calendar_inside_m6_domain'] for r in rows),
                reference_k_counts=dict(Counter(r['reference_k'] for r in rows)),
                m5_necessary_passed=sum(r['m5_necessary_passed'] for r in rows),
                m5_necessary_rejected=sum(not r['m5_necessary_passed'] for r in rows),rows=rows,
                evidence_scope='Input equivalence and necessary conditions, not a certificate of simultaneous route feasibility',
                material_passport=dict(origin='academic-research-suite/experiment-agent',mode='validate',status='ANALYZED'),
                interpretation_checks='11/11: preserve source/scale strata and paired dependence, all failed references, no significance or causal claims')


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',default='data/model5_model6/shared_v1_20261005/manifest.json')
    parser.add_argument('--output',required=True)
    args=parser.parse_args(argv)
    out=resolve_project_path(args.output)
    if out.exists() and any(out.iterdir()):
        raise ValueError('Refusing to overwrite audit output')
    result=audit_suite(args.manifest)
    out.mkdir(parents=True,exist_ok=True)
    write_json(out/'audit.json',result)
    with (out/'cases.csv').open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=list(result['rows'][0]));writer.writeheader()
        writer.writerows({k:json.dumps(v) if isinstance(v,(dict,list)) else v for k,v in row.items()} for row in result['rows'])
    print(json.dumps({k:v for k,v in result.items() if k!='rows'},ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
