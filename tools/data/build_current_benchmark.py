"""Generate six-model cases from the frozen 27-case benchmark."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import shutil

from tools.configuration.loader import PROJECT_ROOT, resolve_project_path

SOURCE = 'data/formal_benchmark27_uniform_7_20_50_v1'
VERSION = 'benchmark27_models_1_6_v1'
TARGET = PROJECT_ROOT / 'data' / VERSION
MODEL_MAP = {1: 1, 2: 2, 3: 3, 4: 4, 5: 8, 6: 10}


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n',
                    encoding='utf-8', newline='\n')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(target=TARGET):
    source = resolve_project_path(SOURCE)
    original = read(source/'manifest.json')
    if target.exists():
        raise ValueError(f'Use a new target directory: {target}')
    if not target.is_relative_to(PROJECT_ROOT):
        raise ValueError('The target must be inside the repository')
    lookup = {(row['base_case_id'], row['model']): row for row in original['cases']}
    cases = []
    for base in original['base_cases']:
        case_id, size = base['id'], base['size']
        shared = target/'shared'/size/case_id
        shared.mkdir(parents=True)
        old_shared = source/'shared'/size/case_id
        for name in ('network.csv', 'fleet.json', 'economic.json', 'provenance.json',
                     'batch_count_certificate.json', 'design_metrics.json'):
            shutil.copyfile(old_shared/name, shared/name)
        for model, old_model in MODEL_MAP.items():
            reference = 8 if model == 6 else old_model
            row = lookup[case_id, reference]
            old_dir = source/f'model_{reference}'/size/case_id
            case_dir = target/f'model_{model}'/size/case_id
            case_dir.mkdir(parents=True)
            config = read(old_dir/'case.json')
            config.update(model=model, case_name=f'{VERSION}_{case_id}_M{model}', dataset_version=VERSION)
            for key in ('data', 'routing_config', 'economic_config', 'dataset_metadata',
                        'orders', 'operating_policy', 'batch_count_certificate'):
                if key in config:
                    destination = (case_dir if key in ('orders', 'operating_policy') else shared)
                    config[key] = (destination/Path(config[key]).name).relative_to(PROJECT_ROOT).as_posix()
            config.pop('parameter_contract', None)
            if model in (3, 4, 5, 6):
                config['no_work_window'] = True
            if model in (3, 4, 5):
                config['legacy_operating_policy'] = config['operating_policy'].replace(
                    '/policy.json', '/fixed_window_policy.json')
                policy = read(old_dir/'policy.json')
                if model == 5:
                    policy['description'] = 'Reference calendar for the flexible-batch model.'
                    write(case_dir/'fixed_window_policy.json', policy)
                    policy['batches'] = [{'id': b['id']} for b in policy['batches']]
                    description = 'Flexible batch calendar within a repeating day.'
                else:
                    shutil.copyfile(old_dir/'policy.json', case_dir/'fixed_window_policy.json')
                    policy.pop('batches')
                    description = 'Single consolidated handoff per warehouse after unloading.'
                policy.pop('cycle_start')
                policy.pop('cycle_end')
                policy.update(repeat_interval_minutes=1440, description=description)
                write(case_dir/'policy.json', policy)
            if model == 6:
                for key in ('joint_batch_options', 'batch_count_certificate', 'container_pool_size', 'economic_config'):
                    config.pop(key, None)
                config.update(exact_backend='gurobi', no_batch_options={'objective': 'total_cost'})
                policy = read(old_dir/'policy.json')
                for key in ('batches', 'cycle_start', 'cycle_end', 'container_pool_size'):
                    policy.pop(key, None)
                policy.update(policy_version='no_batch_container_cycle_v1', repeat_interval_minutes=1440,
                              description='Physical operation durations from the matched source case.')
                write(case_dir/'policy.json', policy)
                orders = read(old_dir/'orders.json')
                for order in orders:
                    order.pop('delivery_batch_id', None)
                    order.pop('allowed_batch_ids', None)
                write(case_dir/'orders.json', orders)
            else:
                shutil.copyfile(old_dir/'orders.json', case_dir/'orders.json')
                if model in (1, 2):
                    shutil.copyfile(old_dir/'policy.json', case_dir/'policy.json')
            path = case_dir/'case.json'
            write(path, config)
            cases.append(dict(base_case_id=case_id, model=model, size=size, customer_count=row['customer_count'],
                              family_id=row['family_id'], source_block=row['source_block'],
                              total_container_uses=row['total_container_uses'],
                              case_path=path.relative_to(PROJECT_ROOT).as_posix()))
    index = target/'case_index.csv'
    with index.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=cases[0], lineterminator='\n')
        writer.writeheader()
        writer.writerows(cases)
    hashes = {p.relative_to(target).as_posix(): digest(p) for p in sorted(target.rglob('*')) if p.is_file()}
    write(target/'manifest.json', dict(dataset_version=VERSION, source_dataset=SOURCE,
          source_manifest_sha256=digest(source/'manifest.json'), base_case_count=len(original['base_cases']),
          model_config_count=len(cases), model_map=MODEL_MAP, cases=cases, sha256=hashes))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default=str(TARGET))
    args = parser.parse_args()
    build(resolve_project_path(args.output))
