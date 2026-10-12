"""Pair M5 and M6 on the same physical input files, with a declared fixed policy."""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from dataclasses import replace
import json
import math
from pathlib import Path

from tools.configuration.loader import load_routing_case, resolve_project_path
from models.common.cycle import load_instance
from models.common.cycle.joint import JointBatchInstance
from models.common.cycle.policy import MultiBatchPolicy
from models.common.cycle.precheck import check_necessary_conditions
from tools.data.build_model6_uniform_dataset import digest, load_suite, reference, signature, write_json
from tools.data.static_batch_dataset import verify_artifacts

VERSION = 'model5_model6_shared_v1'
DEFAULT_SOURCE = 'data/model6/uniform_v2_20261005/manifest.json'
DEFAULT_OUTPUT = 'data/model5_model6/shared_v1_20261005'
DEFAULT_DESIGN = 'config/datasets/model5_model6_shared_v1.json'
SHARED_KEYS = ('data','orders','routing_config','operating_policy','distance_matrix','duration_matrix','economic_config')
CASE_KEYS = ('m5_case','m6_case','m6_fixed_policy_case')


def reference_policy(source, count, ratio):
    policy = deepcopy(source)
    length = (policy['cycle_end']-policy['cycle_start'])/count
    first = length*ratio
    policy.update(batches=[dict(id=f'b{i+1}',delivery_duration=first,return_duration=length-first)
                           for i in range(count)],
                  description='Shared M5 reference calendar; M6 may optimize within the explicitly configured uniform domain.')
    # Reject unrepresentable minute/tick combinations; never round a calendar.
    checked = MultiBatchPolicy.from_config(policy)
    if checked.batches[-1].end!=checked.cycle_end:
        raise ValueError('Reference calendar must cover the complete cycle')
    return policy


def choose_fixed_assignment(problem):
    assignment = {o.id:(problem.legal_batches[o.id][-1] if problem.legal_batches[o.id]
                         else problem.operating_policy.batches[0].id) for o in problem.orders}
    blocked = [o.id for o in problem.orders if not problem.legal_batches[o.id]]
    return assignment, blocked


def shared_hashes(config):
    return {key:digest(resolve_project_path(config[key])) for key in SHARED_KEYS if key in config}


def verify_pair(record, *, check_declared=True):
    configs, problems = {}, {}
    for key in CASE_KEYS:
        cfg, fleet = load_routing_case(record[key], use_pool_config=False)
        configs[key] = cfg
        problems[key] = load_instance(cfg, fleet)
    baseline, joint, control = (problems[key] for key in CASE_KEYS)
    if baseline.model_number!=5 or not getattr(joint,'uniform_cycle',False) or getattr(control,'uniform_cycle',False):
        raise ValueError('Pair requires M5, uniform M6 and fixed-policy cost control')
    hashes = shared_hashes(configs['m5_case'])
    for key in CASE_KEYS[1:]:
        if hashes!=shared_hashes(configs[key]):
            raise ValueError(f'SHARED_INPUT_MISMATCH: {record["name"]}')
    if check_declared and hashes!=record['shared_input_sha256']:
        raise ValueError(f'SHARED_HASH_MISMATCH: {record["name"]}')
    if not baseline.orders==joint.orders==control.orders or not baseline.operating_policy==joint.operating_policy==control.operating_policy:
        raise ValueError('Loaded physical orders and source calendar must be identical')
    source_cfg, source_fleet = load_routing_case(record['source_m6_case'],use_pool_config=False)
    strip_orders = lambda rows:sorted(({k:v for k,v in o.items() if k not in ('delivery_batch_id','allowed_batch_ids')}
                                     for o in rows),key=lambda o:o['order_id'])
    if strip_orders(json.loads(resolve_project_path(source_cfg['orders']).read_text()))!=strip_orders(
            json.loads(resolve_project_path(configs['m5_case']['orders']).read_text())):
        raise ValueError('Exogenous orders changed during pairing')
    source_policy = {k:v for k,v in source_fleet['operating_policy'].items() if k not in ('batches','description')}
    target_policy = json.loads(resolve_project_path(configs['m5_case']['operating_policy']).read_text())
    if source_policy!={k:v for k,v in target_policy.items() if k not in ('batches','description')}:
        raise ValueError('Cleaning, cycle bounds or physical timing changed during pairing')
    for key in ('data','routing_config','distance_matrix','duration_matrix','economic_config'):
        if key in source_cfg or key in configs['m5_case']:
            if (key not in source_cfg or key not in configs['m5_case'] or
                    digest(resolve_project_path(source_cfg[key]))!=hashes[key]):
                raise ValueError(f'Physical source changed during pairing: {key}')
    count = record['reference_batch_count']
    if count<=1 or count not in joint.batch_count_candidates:
        raise ValueError('M5 reference K must belong to the M6 search domain and exceed one')
    child = joint.for_count(count)
    cuts = {b.id:b.cutoff for b in baseline.operating_policy.batches}
    child.validated_cutoffs(cuts)
    if [(b.id,b.start,b.end) for b in child.operating_policy.batches]!=[
            (b.id,b.start,b.end) for b in baseline.operating_policy.batches]:
        raise ValueError('M5 calendar is outside the M6 uniform decision domain')
    if baseline.operating_policy.container_pool_size!=joint.total or control.pool_min!=joint.total or control.pool_max!=joint.total:
        raise ValueError('Fixed policies must explicitly use N=Q')
    if not joint.pool_min<=joint.total<=joint.pool_max:
        raise ValueError('M5 pool must be inside the M6 pool domain')
    if control.options.get('fixed_assignment')!={o.id:o.batch for o in baseline.orders} or control.optimize_cutoffs:
        raise ValueError('Cost control must preserve M5 assignment and cutoff')
    # Independently reconstruct the declared baseline choice without the fixed
    # assignment filter used by the cost-control problem.
    free = JointBatchInstance(joint.network, joint.orders, joint.operating_policy,
                             dict(pool_min=1,pool_max=joint.total),joint.cost_profile.config,_physical_source=joint)
    expected, blocked = choose_fixed_assignment(free)
    if expected!={o.id:o.batch for o in baseline.orders}:
        raise ValueError('Fixed assignment differs from the prespecified rule')
    check = check_necessary_conditions(baseline, free.travel_bounds)
    return dict(name=record['name'],valid_pair=True,reference_batch_count=count,
                shared_input_sha256=hashes,blocked_assignment_orders=blocked,
                m5_necessary_conditions=check,m6_cost_profile_hash=joint.cost_profile.hash,
                source_calendar_inside_m6_domain=True,
                m6_domain_empty_orders={str(k):[oid for oid,v in joint.for_count(k).legal_batches.items() if not v]
                                       for k in joint.batch_count_candidates})


def build_suite(destination=DEFAULT_OUTPUT, source_manifest=DEFAULT_SOURCE, design_path=DEFAULT_DESIGN, families=None):
    source_path = resolve_project_path(source_manifest)
    source = load_suite(source_path)
    design_file = resolve_project_path(design_path)
    design = json.loads(design_file.read_text())
    if design['dataset_version']!=VERSION:
        raise ValueError('Unexpected shared design version')
    selected = [r for r in source['cases'] if not families or r['family_id'] in families]
    if not selected or (families and {r['family_id'] for r in selected}!=set(families)):
        raise ValueError('Unknown or empty family selection')
    root = resolve_project_path(destination).resolve()
    if root.exists() and any(root.iterdir()):
        raise ValueError('Refusing to overwrite nonempty paired dataset')
    root.mkdir(parents=True,exist_ok=True)
    write_json(root/'design.json',design)
    external = {reference(source_path):digest(source_path), **source['external_artifact_sha256']}
    external.update({reference(source_path.parent/name):expected for name,expected in source['artifact_sha256'].items()})
    cases = []
    for row in selected:
        cfg, fleet = load_routing_case(row['m6_case'],use_pool_config=False)
        original = load_instance(cfg,fleet)
        count = design['mechanism_reference_batch_count'] if row['variant']=='legacy_control' else design['reference_batch_count']
        if count not in original.batch_count_candidates:
            raise ValueError('Reference K outside source M6 domain')
        policy = reference_policy(fleet['operating_policy'],count,design['reference_cutoff_ratio'])
        ids = tuple(b['id'] for b in policy['batches'])
        eligible = JointBatchInstance(original.network,
                     [replace(o,batch=ids[0],allowed_batch_ids=ids) for o in original.orders],policy,
                     dict(pool_min=1,pool_max=original.total),original.cost_profile.config,_physical_source=original)
        assignment, blocked = choose_fixed_assignment(eligible)
        raw_orders = json.loads(resolve_project_path(cfg['orders']).read_text())
        for order in raw_orders:
            order.update(delivery_batch_id=assignment[order['order_id']],allowed_batch_ids=list(ids))
        folder = root/row['family_id']/row['variant']
        write_json(folder/'orders.json',raw_orders)
        write_json(folder/'policy.json',policy)
        metadata = dict(dataset_version=VERSION,source_case=row['m6_case'],source_semantic_sha256=row['semantic_sha256'],
                        source_manifest=reference(source_path),family_id=row['family_id'],variant=row['variant'],
                        source_pair_id=row['source_pair_id'],spatial_block=row['spatial_block'],
                        reference_batch_count=count,reference_cutoff_ratio=design['reference_cutoff_ratio'],
                        fixed_assignment_rule=design['fixed_assignment_rule'],fixed_assignment=assignment,
                        individual_legal_batches={k:list(v) for k,v in eligible.legal_batches.items()},
                        blocked_assignment_orders=blocked,
                        changed_order_fields=['delivery_batch_id','allowed_batch_ids'],
                        changed_policy_fields=['batches','description'],
                        source_preservation=design['source_preservation'],
                        m5_pool_rule=design['m5_pool_rule'],limitations=[
                            'Individual necessary conditions do not guarantee simultaneous routing feasibility',
                            'No failed case is removed or repaired by relaxing physical inputs',
                            'Synthetic costs and appointments; paired variants are not independent samples'])
        write_json(folder/'provenance.json',metadata)
        common = {k:deepcopy(v) for k,v in cfg.items() if k not in ('joint_batch_options','model','case_name','exact_backend')}
        common.update(orders=reference(folder/'orders.json'),operating_policy=reference(folder/'policy.json'),
                      dataset_metadata=reference(folder/'provenance.json'),container_pool_size=original.total)
        m5 = dict(common,model=5,case_name=row['name']+'_M5',exact_backend='multibatch_indexed')
        m6 = dict(common,model=6,case_name=row['name']+'_M6',exact_backend='joint_batch_indexed',
                  joint_batch_options=deepcopy(cfg['joint_batch_options']))
        fixed = dict(m6,case_name=row['name']+'_M6_fixed_policy_cost',
                     joint_batch_options=dict(pool_min=original.total,pool_max=original.total,fixed_assignment=assignment))
        for name,case in [('m5_case',m5),('m6_case',m6),('m6_fixed_policy_case',fixed)]:
            write_json(folder/f'{name}.json',case)
        record = {**deepcopy(row),**{key:reference(folder/f'{key}.json') for key in CASE_KEYS},
                  'case_path':reference(folder/'m6_case.json'),
                  'source_m6_case':row['m6_case'],'source_semantic_sha256':row['semantic_sha256'],
                  'reference_batch_count':count,'reference_cutoff_ratio':design['reference_cutoff_ratio'],
                  'm5_pool_size':original.total,'shared_input_sha256':shared_hashes(m5)}
        record.pop('semantic_sha256',None)
        audit = verify_pair(record)
        record.update(m5_necessary_conditions_passed=audit['m5_necessary_conditions']['passed'],
                      m5_necessary_rejection_codes=sorted({e['code'] for e in audit['m5_necessary_conditions']['errors']}),
                      blocked_assignment_orders=blocked,
                      shared_semantic_sha256=signature(record['shared_input_sha256']))
        cases.append(record)
    for path,expected in external.items():
        if digest(resolve_project_path(path))!=expected:
            raise ValueError(f'Source changed during generation: {path}')
    manifest = dict(schema_version=1,dataset_version=VERSION,schedule_mode='uniform_cycle',
                    information_mode='static_all_known',source_manifest=reference(source_path),
                    source_family_count=len({r['family_id'] for r in cases}),case_count=len(cases),
                    model_config_count=2*len(cases),matched_cost_control_count=len(cases),
                    variant_counts=dict(Counter(r['variant'] for r in cases)),cases=cases,
                    semantic_dataset_sha256=signature([(r['name'],r['shared_semantic_sha256']) for r in cases]),
                    design_sha256=digest(design_file),generator_sha256=digest(Path(__file__)),
                    artifact_sha256={p.relative_to(root).as_posix():digest(p) for p in sorted(root.rglob('*.json'))},
                    external_artifact_sha256=external,
                    limitations=['M5 optimizes distance, M6 cost; evaluate both schedules with identical prices',
                                 'M5 configured pool is not its optimized minimum; schedule minimum is reported separately',
                                 'Fixed-policy cost control separates objective effects from joint decision changes',
                                 'All physical and exogenous inputs shared; reference policy is a fixed decision',
                                 'Infeasible references and failed runs remain in the dataset and denominators'])
    write_json(root/'manifest.json',manifest)
    return root/'manifest.json'


def load_shared_suite(path):
    path = resolve_project_path(path)
    manifest = json.loads(path.read_text())
    if manifest.get('dataset_version')!=VERSION or not manifest.get('cases'):
        raise ValueError('Expected a nonempty M5/M6 shared dataset')
    verify_artifacts(manifest,path.parent)
    for file,expected in manifest['external_artifact_sha256'].items():
        if digest(resolve_project_path(file))!=expected:
            raise ValueError(f'SOURCE_HASH_MISMATCH: {file}')
    if len({r['name'] for r in manifest['cases']})!=len(manifest['cases']):
        raise ValueError('Duplicate shared case names')
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',default=DEFAULT_OUTPUT)
    parser.add_argument('--source-manifest',default=DEFAULT_SOURCE)
    parser.add_argument('--design',default=DEFAULT_DESIGN)
    parser.add_argument('--families',nargs='+')
    args=parser.parse_args(argv)
    print(build_suite(args.output,args.source_manifest,args.design,args.families))


if __name__=='__main__':
    main()
