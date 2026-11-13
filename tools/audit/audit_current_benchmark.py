"""Check the current case index, frozen bytes and all model input contracts."""
import argparse
from collections import Counter
import json

from models.common.cycle import load_instance
from models.common.no_batch import load_instance as load_no_batch
from tools.configuration.loader import load_routing_case, resolve_project_path
from tools.data.build_current_benchmark import TARGET, digest, read


def audit(dataset=TARGET):
    root = resolve_project_path(dataset)
    manifest = read(root/'manifest.json')
    for relative, expected in manifest['sha256'].items():
        if digest(root/relative) != expected:
            raise ValueError(f'Input hash mismatch: {relative}')
    source = resolve_project_path(manifest['source_dataset'])
    if digest(source/'manifest.json') != manifest['source_manifest_sha256']:
        raise ValueError('Source dataset manifest differs')
    seen = set()
    sizes = Counter()
    physical = {}
    for row in manifest['cases']:
        case_id, model = row['base_case_id'], row['model']
        key = case_id, model
        if key in seen or model not in range(1, 7):
            raise ValueError(f'Duplicate case or invalid model: {key}')
        seen.add(key)
        config, fleet = load_routing_case(row['case_path'], use_pool_config=False)
        if config['model'] != model:
            raise ValueError(f'Model number mismatch: {key}')
        inputs = config['data'], config['routing_config']
        if physical.setdefault(case_id, inputs) != inputs:
            raise ValueError(f'Network or fleet differs within case {case_id}')
        instance = (load_no_batch if model == 6 else load_instance)(config, fleet)
        if instance.total != row['total_container_uses'] or len(instance.orders) != row['customer_count']:
            raise ValueError(f'Demand mismatch: {key}')
        if model in (3, 4) and not instance.operating_policy.consolidated_handoff:
            raise ValueError(f'Missing consolidated handoff: {key}')
        if model == 5 and (not instance.no_work_window or instance.operating_policy.consolidated_handoff):
            raise ValueError(f'Expected the flexible calendar without a fixed work window: {key}')
        if model == 6 and (not instance.no_work_window or instance.objective_mode != 'total_cost'):
            raise ValueError(f'Incorrect no-batch policy or objective: {key}')
        sizes[row['size'], model] += 1
    if len(physical) != 27 or len(seen) != 162 or set(sizes.values()) != {9}:
        raise ValueError('Expected 27 cases with six models and nine cases per size')
    return dict(base_cases=len(physical), model_cases=len(seen), files_verified=len(manifest['sha256']))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', default=str(TARGET))
    print(json.dumps(audit(parser.parse_args().dataset), indent=2))
