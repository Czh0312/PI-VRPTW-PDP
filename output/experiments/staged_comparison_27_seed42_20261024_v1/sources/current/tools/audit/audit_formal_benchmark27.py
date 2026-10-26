"""Independently check the formal dataset through public input loaders.

No solver is called. Fixed-model necessary conditions are reported separately
from data integrity; neither a passing loader nor necessary checks prove route
feasibility. Do not use a failed fixed-assignment check to reject a free model.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from tools.configuration.loader import load_routing_case, resolve_project_path
from models.common.cycle import load_instance
from models.common.cycle.policy import MultiBatchPolicy
from models.common.cycle.precheck import check_necessary_conditions
from tools.data.formal_benchmark27_bounds import productive_count_certificate
from tools.audit.check_last_recovery import last_recovery_certificate
from tools.data.one_box_contract import CONTRACT, validate_orders, preserved_orders_hash, preserved_policy_hash


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def physical_orders(orders):
    return sorted(({k: v for k, v in o.items()
                    if k not in ('delivery_batch_id', 'allowed_batch_ids')} for o in orders),
                  key=lambda o: o['order_id'])


def audit_dataset(dataset):
    root = resolve_project_path(dataset).resolve()
    manifest = read(root/'manifest.json')
    if manifest['dataset_version'] == 'formal_benchmark27_one_box_v1':
        require(manifest.get('demand_contract') == CONTRACT, 'Missing one-box dataset contract')
    require(manifest['base_case_count'] == 27 and manifest['model_config_count'] == 216,
            'Expected 27 bases and 216 model configurations')
    require(len(manifest['cases']) == 216 and len(manifest['base_cases']) == 27, 'Incorrect manifest sizes')
    expected = {(m, size): 9 for m in range(1, 9) for size in ('small', 'medium', 'large')}
    require(Counter((r['model'], r['size']) for r in manifest['cases']) == expected,
            'Each model must contain nine cases per size')
    require(len({(r['model'], r['base_case_id']) for r in manifest['cases']}) == 216,
            'Duplicate model/base pair')
    found = {p.resolve() for p in root.glob('model_*/*/*/case.json')}
    require(found == {resolve_project_path(r['case_path']).resolve() for r in manifest['cases']},
            'On-disk case files and manifest differ')
    for name, expected_hash in manifest['artifact_sha256'].items():
        path = (root/name).resolve()
        require(root in path.parents and path.is_file(), f'Missing or escaping artifact: {name}')
        require(hashlib.sha256(path.read_bytes()).hexdigest() == expected_hash,
                f'Artifact changed: {name}')
    results = []
    for base in manifest['base_cases']:
        common = root/'shared'/base['size']/base['id']
        if manifest.get('economic_profile_sha256'):
            require(hashlib.sha256((common/'economic.json').read_bytes()).hexdigest()
                    == manifest['economic_profile_sha256'],
                    f'Economic profile differs from declared coefficients: {base["id"]}')
        orders = read(common/'reference_orders.json')
        policy = read(common/'reference_policy.json')
        one_box = manifest.get('demand_contract', {}).get('id') == CONTRACT['id']
        if one_box:
            require(manifest['demand_contract'] == CONTRACT, 'Unknown one-box contract')
            validate_orders(orders, base['customer_count'])
            require(base['total_container_uses'] == base['customer_count'], 'One-box Q differs from customers')
            provenance = read(common/'provenance.json')
            require(preserved_orders_hash(orders) == provenance['preserved_orders_sha256'],
                    'Non-box order fields changed from frozen source')
            require(preserved_policy_hash(policy) == provenance['preserved_policy_sha256'],
                    'Non-pool policy fields changed from frozen source')
        ref_policy = MultiBatchPolicy.from_config(policy)
        certificate = read(common/'batch_count_certificate.json')
        exogenous = physical_orders(orders)
        require(sum(o['container_count'] for o in orders) == base['total_container_uses'], 'Wrong Q')
        require(len({o['customer_id'] for o in orders}) == base['customer_count'], 'Wrong customer count')
        original_policy = {k: v for k, v in policy.items() if k not in ('batches', 'description')}
        domains = []
        for model in range(1, 9):
            path = resolve_project_path(base['models'][str(model)])
            cfg, fleet = load_routing_case(path)
            require('$include' not in read(path), 'Unexpected external configuration include')
            for key in ('data', 'orders', 'routing_config', 'operating_policy', 'economic_config',
                        'dataset_metadata', 'parameter_contract'):
                dependency = resolve_project_path(cfg[key]).resolve()
                require(root in dependency.parents and dependency.is_file(), f'External input: {path}/{key}')
            for key, filename in [('data', 'network.csv'), ('routing_config', 'fleet.json'),
                                  ('economic_config', 'economic.json')]:
                require(resolve_project_path(cfg[key]).resolve() == (common/filename).resolve(),
                        f'Model-specific physical input: {path}/{key}')
            require(cfg['model'] == model and cfg['base_case_id'] == base['id'], f'Model ID mismatch: {path}')
            model_orders = read(resolve_project_path(cfg['orders']))
            if one_box:
                validate_orders(model_orders, base['customer_count'])
            require(physical_orders(model_orders) == exogenous, f'Physical orders differ: {path}')
            actual_policy = read(resolve_project_path(cfg['operating_policy']))
            require({k: v for k, v in actual_policy.items() if k not in ('batches', 'description')}
                    == original_policy, f'Physical policy differs: {path}')
            problem = load_instance(cfg, fleet)
            if one_box:
                require({o.customer for o in problem.orders} == {c.id for c in problem.network.customers},
                        f'One-box customer coverage differs: {path}')
                for order in problem.orders:
                    for kind in problem.task_kinds:
                        _, weight, volume = problem.task(order.id, kind)
                        expected_weight = problem.fleet.container_weight + (order.goods_weight if kind == 'd' else 0)
                        require(abs(weight - expected_weight) < 1e-8 and
                                abs(volume - problem.fleet.container_volume) < 1e-8,
                                f'One-box physical task load differs: {path}/{order.id}/{kind}')
            last_return = last_recovery_certificate(problem) if model == 5 else None
            require(last_return is None or not last_return['infeasible'],
                    f'Fixed calendar cannot finish last recovery and cleaning: {path}')
            require(problem.model_number == model and problem.total == base['total_container_uses'],
                    f'Loaded model/demand differs: {path}')
            require(problem.free_assignment == (model in (2, 4)), f'Wrong assignment freedom: {path}')
            require(problem.reusable == (model >= 3), f'Wrong packaging: {path}')
            require(problem.task_kinds == (('d', 'p') if model >= 3 else ('d',)), f'Wrong task types: {path}')
            require(problem.operating_policy.container_pool_size == problem.total, f'Wrong reference pool: {path}')
            contract = read(resolve_project_path(cfg['parameter_contract']))
            if one_box:
                require(contract.get('demand_contract') == CONTRACT, f'Missing one-box parameter contract: {path}')
            require(contract['model'] == model and contract['demand_container_uses'] == problem.total,
                    f'Parameter contract mismatch: {path}')
            optimized, fixed = contract['optimized_decisions'], contract['fixed_parameters']
            require(('customer_satellite_assignment' in optimized) == (model in (2, 4)),
                    f'Incorrect assignment documentation: {path}')
            if model in (2, 4):
                require('customer_satellite_assignment' not in fixed, f'Free assignment described as fixed: {path}')
            else:
                require(fixed['customer_satellite_assignment'] == {o.customer: o.satellite for o in problem.orders},
                        f'Incorrect fixed satellite mapping: {path}')
            if model <= 5:
                require('joint_batch_options' not in cfg, f'Unexpected joint options: {path}')
                require(len(problem.batch_map) == (1 if model <= 4 else len(ref_policy.batches)),
                        f'Wrong fixed K: {path}')
                require(all(o.allowed_batch_ids == (o.batch,) for o in problem.orders),
                        f'Fixed order binding not explicit: {path}')
                require(fixed['order_batch_assignment'] == {o.id: o.batch for o in problem.orders},
                        f'Fixed order mapping differs: {path}')
                require(fixed['calendar'] == actual_policy['batches'], f'Fixed calendar differs: {path}')
                variable_pool = model == 5 and contract['objective'] == 'cost'
                require(('container_pool_size' in optimized) == variable_pool,
                        f'Incorrect pool decision documentation: {path}')
                if variable_pool:
                    require('container_pool_size' not in fixed and problem.optimize_pool,
                            f'M5 pool must be optimized: {path}')
                elif model >= 3:
                    require(fixed['container_pool_size'] == problem.total, f'Pool not fixed to Q: {path}')
                else:
                    require('container_pool_size' not in fixed, f'Single-use model has a cyclic pool: {path}')
                if model <= 4:
                    require(problem.operating_policy.batches[0].cutoff == ref_policy.batches[-1].cutoff,
                            f'Single-batch final cutoff changed: {path}')
                else:
                    require(problem.operating_policy == ref_policy, f'M5 reference policy changed: {path}')
                    require({o.id: o.batch for o in problem.orders} ==
                            {o['order_id']: o['delivery_batch_id'] for o in orders}, f'M5 assignment changed: {path}')
                precheck = check_necessary_conditions(problem) if model in (1, 3, 5) else None
            else:
                options = cfg['joint_batch_options']
                modes = {6: 'uniform_cycle', 7: 'uniform_independent_cutoffs', 8: 'flexible_cycle'}
                require(options['schedule_mode'] == modes[model], f'Wrong calendar mode: {path}')
                require(resolve_project_path(cfg['batch_count_certificate']).resolve() ==
                        (common/'batch_count_certificate.json').resolve(), f'Wrong K certificate: {path}')
                independently_recomputed = productive_count_certificate(problem, options['min_phase_minutes'])
                require(certificate == independently_recomputed, f'Incorrect K exclusion certificate: {path}')
                require(options['batch_count_candidates'] == certificate['candidates_by_model'][str(model)],
                        f'Uncertified K truncation: {path}')
                require(options['pool_min'] == problem.pool_min == 1 and
                        options['pool_max'] == problem.pool_max == problem.total, f'Pool accidentally fixed: {path}')
                require('fixed_assignment' not in options and 'container_pool_size' in optimized,
                        f'Joint decisions accidentally fixed: {path}')
                require(contract['decision_bounds'] == options, f'Documented decision bounds differ: {path}')
                require(all(set(o.allowed_batch_ids) == set(problem.batch_map) for o in problem.orders),
                        f'Joint model retained fixed batch restrictions: {path}')
                require(problem.require_productive_batches, f'Obsolete productive-batch semantics: {path}')
                expected_k = tuple(certificate['candidates_by_model'][str(model)])
                require(tuple(problem.batch_count_candidates) == expected_k, f'Unexpected K bounds: {path}')
                domains.append(tuple(problem.batch_count_candidates))
                projection_k = 3 if 3 in problem.batch_count_candidates else max(problem.batch_count_candidates)
                child = problem.for_count(projection_k)
                require(child.require_productive_batches, f'Projection lost productive-batch semantics: {path}')
                if model == 6:
                    require(child.shared_cutoff, f'M6 must share the cutoff: {path}')
                elif model == 7:
                    require(not child.shared_cutoff, f'M7 must allow independent cutoffs: {path}')
                else:
                    require(child.variable_calendar, f'M8 must allow variable lengths: {path}')
                require(child.pool_min == 1 and child.pool_max == problem.total, f'Child pool fixed: {path}')
                precheck = None
            results.append(dict(base_case_id=base['id'], model=model, size=base['size'],
                                input_valid=True, total_container_uses=problem.total,
                                last_recovery_relaxation=last_return,
                                fixed_model_necessary_conditions=(dict(passed=precheck['passed'],
                                    errors=precheck['errors']) if precheck is not None else None)))
        require(domains[0] == domains[1] and set(domains[0]) <= set(domains[2]),
                f'Unexpected model-specific certified K exclusions: {base["id"]}')
    failed = [r for r in results if r['fixed_model_necessary_conditions'] is not None
              and not r['fixed_model_necessary_conditions']['passed']]
    return dict(dataset_version=manifest['dataset_version'], valid=True, base_case_count=27,
                model_config_count=216, loaded_models=8, per_model_per_size=9,
                artifact_hashes_checked=len(manifest['artifact_sha256']),
                fixed_model_necessary_condition_failures=failed, cases=results,
                solver_runs=0, route_feasibility='Not certified by input validation',
                manifest_sha256=hashlib.sha256((root/'manifest.json').read_bytes()).hexdigest())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', default='data/formal_benchmark27_uniform_7_20_50_v1')
    parser.add_argument('--output', default='output/inspections/uniform_benchmark27_20261024_v1/input_audit.json')
    args = parser.parse_args()
    result = audit_dataset(args.dataset)
    output = resolve_project_path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items() if k != 'cases'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
