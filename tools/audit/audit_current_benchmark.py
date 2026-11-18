"""Check the benchmark tables and all six model input contracts."""
import argparse
from collections import Counter
import csv
from dataclasses import replace
import json

from models.common.cycle import load_instance
from models.common.no_batch import load_instance as load_no_batch
from tools.configuration.benchmark import case_info, case_paths
from tools.configuration.loader import (
    BENCHMARK_ROOT, DATASET_ECONOMIC_FILENAME, FROZEN_BENCHMARK_ECONOMIC_REFERENCE, load_json, load_routing_case,
    resolve_dataset_root, resolve_project_path,
)
from tools.data.build_current_benchmark import digest, read
from tools.data.build_shared_benchmark import ORDER_DECISIONS


def _archived_cases(root):
    manifest = read(root/'manifest.json')
    shared = 'models' in manifest
    if shared:
        economic = manifest['economic_config']
        if (economic != FROZEN_BENCHMARK_ECONOMIC_REFERENCE
                or digest(resolve_project_path(economic)) != manifest['economic_config_sha256']):
            raise ValueError('Shared benchmark fee input differs from its frozen reference')
    for relative, expected in manifest['sha256'].items():
        if digest(root/relative) != expected:
            raise ValueError(f'Input hash mismatch: {relative}')
    source = resolve_project_path(manifest['source_dataset'])
    if digest(source/'manifest.json') != manifest['source_manifest_sha256']:
        raise ValueError('Source dataset manifest differs')
    rows = manifest['cases']
    if shared:
        for row in rows:
            cfg = load_json(row['case_path'])
            orders = read(resolve_project_path(cfg['orders']))
            with resolve_project_path(cfg['data']).open(encoding='utf-8-sig', newline='') as handle:
                if 'assigned_sat' in next(csv.reader(handle)) or any(ORDER_DECISIONS & o.keys() for o in orders):
                    raise ValueError(f"Found assignment fields in shared inputs: {row['base_case_id']}")
        rows = [{**row, 'model': model} for row in rows for model in manifest['models']]
    return rows, shared, len(manifest['sha256']) + int(shared)


def audit(dataset=BENCHMARK_ROOT):
    root = resolve_dataset_root(dataset)
    current = not (root/'manifest.json').is_file()
    if current:
        tables = case_paths(root)
        for table in tables:
            with table.open(encoding='utf-8-sig', newline='') as handle:
                if ({'assigned_sat'} | ORDER_DECISIONS) & set(next(csv.reader(handle), ())):
                    raise ValueError(f'Found assignment fields in {table}')
        cases = [dict(case_info(path), case_path=path) for path in tables]
        rows = [dict(row, model=model) for row in cases for model in range(1, 7)]
        shared = True
    else:
        rows, shared, files_verified = _archived_cases(root)
    expected_rates = (str((root/DATASET_ECONOMIC_FILENAME).resolve())
                      if current else FROZEN_BENCHMARK_ECONOMIC_REFERENCE)
    seen = set()
    sizes = Counter()
    inputs_by_case = {}
    fleet_by_size = {}
    for row in rows:
        case_id, model = row['base_case_id'], row['model']
        key = case_id, model
        if key in seen or model not in range(1, 7):
            raise ValueError(f'Duplicate case or invalid model: {key}')
        seen.add(key)
        config, fleet = load_routing_case(row['case_path'], model=model, use_pool_config=False)
        if config['model'] != model:
            raise ValueError(f'Model number mismatch: {key}')
        if shared and config['economic_config'] != expected_rates:
            raise ValueError(f'Expected the shared benchmark economic profile: {key}')
        if current and config['data'] != config['orders']:
            raise ValueError(f'Expected one table for nodes and orders: {key}')
        if current:
            size = row['size']
            if fleet_by_size.setdefault(size, config['routing_config']) != config['routing_config']:
                raise ValueError(f'Fleet differs within scale {size}: {key}')
        instance = (load_no_batch if model == 6 else load_instance)(config, fleet)
        orders = tuple(replace(o, satellite='', batch='', allowed_batch_ids=None) for o in instance.orders)
        inputs = config['data'], config['routing_config'], instance.cost_profile.hash, orders
        if inputs_by_case.setdefault(case_id, inputs) != inputs:
            raise ValueError(f'Network, fleet, pricing or physical orders differ within case {case_id}')
        if shared and (config['order_assignment'] != 'nearest' or any(o.satellite != min(
                instance.satellites, key=lambda site: (instance.distance(site, o.customer), site))
                for o in instance.orders)):
            raise ValueError(f'Incorrect nearest-warehouse projection: {key}')
        if instance.free_assignment != (model in (2, 4)):
            raise ValueError(f'Incorrect assignment decision: {key}')
        if instance.total != row['total_container_uses'] or len(instance.orders) != row['customer_count']:
            raise ValueError(f'Demand mismatch: {key}')
        if model in (3, 4) and not instance.operating_policy.consolidated_handoff:
            raise ValueError(f'Missing consolidated handoff: {key}')
        if model == 5 and (not instance.no_work_window or instance.operating_policy.consolidated_handoff):
            raise ValueError(f'Expected the flexible calendar without a fixed work window: {key}')
        if model == 6 and (not instance.no_work_window or instance.objective_mode != 'total_cost'):
            raise ValueError(f'Incorrect no-batch policy or objective: {key}')
        sizes[row['size'], model] += 1
    if len(inputs_by_case) != 27 or len(seen) != 162 or set(sizes.values()) != {9}:
        raise ValueError('Expected 27 cases with six models and nine cases per size')
    result = dict(base_cases=len(inputs_by_case), model_cases=len(seen))
    if current:
        return dict(result, case_tables=len(tables))
    return dict(result, files_verified=files_verified)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', default=str(BENCHMARK_ROOT))
    print(json.dumps(audit(parser.parse_args().dataset), indent=2))
