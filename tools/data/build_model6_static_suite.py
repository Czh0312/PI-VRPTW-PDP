"""Adapt existing static cases to M6 without changing demands or service windows.

Physical demand comes from the selected source cases. Release-at-cycle-start
and costs are explicit research assumptions, not observed business data.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path

from configuration.loader import PROJECT_ROOT, load_routing_case, resolve_project_path
from models.routing.common.cycle import load_instance
from tools.data.build_model6_demo_suite import synthetic_profile
from tools.data.static_batch_dataset import load_manifest


def _json(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')


def build_suite(destination,source_manifest='data/manifest.json',families=None,batch_count=3,*,
                all_cases=False,optimize_cutoffs=False,min_phase_minutes=1,
                uniform_cycle=False,batch_count_candidates=None):
    if (not isinstance(optimize_cutoffs,bool) or isinstance(min_phase_minutes,bool)
            or not isinstance(min_phase_minutes,(int,float)) or not math.isfinite(min_phase_minutes)
            or min_phase_minutes<=0):
        raise ValueError('Require boolean cutoff optimization and positive finite phase minimum')
    if not isinstance(uniform_cycle,bool) or (uniform_cycle and optimize_cutoffs):
        raise ValueError('Uniform cycle and individual cutoff optimization are separate modes')
    if batch_count_candidates is not None and not uniform_cycle:
        raise ValueError('batch_count_candidates requires uniform_cycle')
    families=None if all_cases else families or ['lade_0823_10aoi','lade_0823_20aoi','lade_0823_30aoi']
    original_manifest,records=load_manifest(source_manifest,groups=['multi_batch'],
                                          batch_counts=None if all_cases else [batch_count],family_ids=families)
    if families is not None and {r['family_id'] for r in records}!=set(families):
        raise ValueError('All requested families must have the requested batch count')
    if families is not None:
        records.sort(key=lambda r:families.index(r['family_id']))
    root=resolve_project_path(destination).resolve()
    if root.exists() and any(root.iterdir()):
        raise ValueError(f'Refusing to overwrite nonempty suite directory: {root}')
    root.mkdir(parents=True,exist_ok=True)
    cases=[]
    for record in records:
        config,fleet=load_routing_case(record['case_path'],use_pool_config=False)
        source_problem=load_instance(config,fleet)
        policy=deepcopy(fleet['operating_policy'])
        orders_path=resolve_project_path(config['orders'])
        orders=json.loads(orders_path.read_text(encoding='utf-8'))
        allowed=[b['id'] for b in policy['batches']]
        # All orders known in advance does not prove all goods are released;
        # this is declared as an assumption, never inferred from ready windows.
        for order in orders:
            order.update(release_time=policy['cycle_start'],allowed_batch_ids=allowed)
        name=record['case_id'] if all_cases else record['family_id']
        folder=root/name; folder.mkdir()
        _json(folder/'orders.json',orders)
        profile=synthetic_profile()
        minutes=policy['cycle_end']-policy['cycle_start']
        profile.update(description='Artificial objective coefficients on static research scenarios; no empirical cost claim. Zhou distances remain coordinate units, not observed kilometres.',
                       evaluation_period=f'one {minutes:g}-minute cycle',
                       price_basis='Artificial unit prices held identical across all cases and methods')
        _json(folder/'economic.json',profile)
        shared=deepcopy(config)
        for key in ('data','routing_config','operating_policy','algorithm_config','distance_matrix','duration_matrix'):
            if isinstance(shared.get(key),str):
                shared[key]=str(resolve_project_path(shared[key]).resolve())
        shared.update(orders=str(folder/'orders.json'),economic_config=str(folder/'economic.json'),
                      container_pool_size=source_problem.operating_policy.container_pool_size,
                      save_plots=False,source_kind=record['source_kind'],
                      description='Original static physical inputs preserved; explicit full-stock-at-cycle-start assumption and artificial costs.')
        source_keys=('data','orders','routing_config','operating_policy','distance_matrix','duration_matrix','dataset_metadata')
        source_paths=[resolve_project_path(config[k]).resolve() for k in source_keys if isinstance(config.get(k),str)]
        source_paths.append(resolve_project_path(record['case_path']).resolve())
        provenance=dict(source_case=record['case_path'],source_case_id=record['case_id'],
                        family_id=record['family_id'],batch_count=record['batch_count'],source_kind=record['source_kind'],
                        order_count=len(orders),total_container_uses=source_problem.total,
                        source_input_sha256={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in source_paths},
                        changed_fields=['release_time','allowed_batch_ids'],
                        release_assumption='All goods available at original cycle_start; not an observed release record',
                        unchanged_fields=['container_count','goods_weight','customer_id','satellite_id','delivery_batch_id',
                                          'delivery_ready','delivery_due','pickup_ready','pickup_due','service_time','min_use_time'],
                        cost_source_type='synthetic_mechanism',currency='synthetic_units')
        if uniform_cycle:
            provenance.update(schedule_mode='uniform_cycle',calendar_assumption='Repartition entire original cycle uniformly; all batches share a relative cutoff',
                              batch_count_candidates=(list(batch_count_candidates) if batch_count_candidates is not None
                                                      else 'all_positive_integers'),
                              min_phase_minutes=min_phase_minutes)
        _json(folder/'adaptation.json',provenance)
        shared['dataset_metadata']=str(folder/'adaptation.json')
        _json(folder/'m5_case.json',{**shared,'model':5,'case_name':name+'_M5','exact_backend':'multibatch_indexed'})
        options=dict(pool_min=1,pool_max=source_problem.total)
        if uniform_cycle:
            options.update(schedule_mode='uniform_cycle',min_phase_minutes=min_phase_minutes,
                           batch_count_candidates=(list(batch_count_candidates) if batch_count_candidates is not None
                                                   else 'all_positive_integers'))
        elif optimize_cutoffs:
            calendar=source_problem.operating_policy
            bounds={b.id:[calendar.minutes(b.start)+min_phase_minutes,
                          calendar.minutes(b.end)-min_phase_minutes] for b in calendar.batches}
            if any(lo>hi for lo,hi in bounds.values()):
                raise ValueError('Minimum phase duration leaves no cutoff domain')
            options.update(optimize_cutoffs=True,cutoff_bounds_minutes=bounds)
        _json(folder/'m6_case.json',{**shared,'model':6,'case_name':name+'_M6',
                                  'exact_backend':'joint_batch_indexed',
                                  'joint_batch_options':options})
        if uniform_cycle:
            # Validate the generated domain before advertising it in a manifest.
            uniform_config,_=load_routing_case(folder/'m6_case.json',use_pool_config=False)
            load_instance(uniform_config,fleet)
        cases.append(dict(name=name,m5_case=str(folder/'m5_case.json'),m6_case=str(folder/'m6_case.json'),
                          orders=len(orders),total_container_uses=source_problem.total,currency='synthetic_units',
                          source_kind=record['source_kind'],source_case=record['case_path'],
                          source_case_id=record['case_id'],family_id=record['family_id'],size=record['size'],
                          candidate_batch_count=record['batch_count']))
    manifest=dict(schema_version=1,suite='model6_static_v2_adaptation',source_manifest=str(resolve_project_path(source_manifest)),
                  schedule_mode='uniform_cycle' if uniform_cycle else 'candidate_slots',
                  batch_count_candidates=batch_count_candidates if uniform_cycle else None,
                  optimize_cutoffs=optimize_cutoffs,min_phase_minutes=min_phase_minutes if optimize_cutoffs or uniform_cycle else None,
                  cases=cases,coverage=dict(all_multibatch=all_cases,selected_count=len(cases),
                                           source_multibatch_count=sum(r['group']=='multi_batch' for r in original_manifest['cases'])),
                  description=('Physical demands, customer windows, fleets and cycle bounds preserved; M6 repartitions the full cycle uniformly with a shared cutoff. '
                               if uniform_cycle else 'Original physical demands, windows, fleets and calendars preserved; ')
                              +'release availability and cost coefficients are explicit assumptions. Not real CNY savings.')
    _json(root/'manifest.json',manifest)
    return root/'manifest.json'


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',default='data/model6/static_v2_lade_20261004')
    parser.add_argument('--source-manifest',default='data/manifest.json')
    parser.add_argument('--families',nargs='+')
    parser.add_argument('--batch-count',type=int,choices=[2,3],default=3)
    parser.add_argument('--all-multibatch',action='store_true',help='Adapt every B2/B3 case in the current source manifest')
    parser.add_argument('--optimize-cutoffs',action='store_true',help='M6 may optimize cutoffs inside fixed candidate starts/ends; M5 stays fixed')
    parser.add_argument('--uniform-cycle',action='store_true',help='M6 basic model: equal cycle partitions and one shared relative cutoff')
    parser.add_argument('--batch-count-candidates',type=int,nargs='+',help='Configured K domain for uniform partitions')
    parser.add_argument('--min-phase-minutes',type=float,default=1,help='Explicit scenario minimum for each phase, not observed processing time')
    args=parser.parse_args(argv)
    if args.all_multibatch and args.families:
        parser.error('--all-multibatch cannot be combined with --families')
    print(build_suite(args.output,args.source_manifest,args.families,args.batch_count,all_cases=args.all_multibatch,
                      optimize_cutoffs=args.optimize_cutoffs,min_phase_minutes=args.min_phase_minutes,
                      uniform_cycle=args.uniform_cycle,batch_count_candidates=args.batch_count_candidates))


if __name__=='__main__':
    main()
