"""Build the shared benchmark from the frozen six-model inputs."""
import argparse
from collections import defaultdict
import csv
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory

from tools.configuration.loader import (
    FROZEN_BENCHMARK_ECONOMIC_REFERENCE, FROZEN_SHARED_BENCHMARK_ROOT, PROJECT_ROOT, resolve_project_path,
)
from tools.data.build_current_benchmark import TARGET as FROZEN_BENCHMARK_ROOT, digest, read, write

ORDER_DECISIONS = {'satellite_id', 'delivery_batch_id', 'allowed_batch_ids'}
CALENDAR_FIELDS = {'batches', 'cycle_start', 'cycle_end', 'repeat_interval_minutes',
                   'container_pool_size', 'policy_version', 'description'}


def base_orders(path):
    return [{key: value for key, value in row.items() if key not in ORDER_DECISIONS}
            for row in read(path)]


def write_unassigned_network(source, target):
    with source.open(encoding='utf-8-sig', newline='') as incoming, target.open(
            'w', encoding='utf-8', newline='') as outgoing:
        reader = csv.DictReader(incoming)
        fields = [key for key in reader.fieldnames if key != 'assigned_sat']
        writer = csv.DictWriter(outgoing, fields, extrasaction='ignore', lineterminator='\n')
        writer.writeheader()
        writer.writerows(reader)


def _build(source, root, target):
    manifest = read(source/'manifest.json')
    for relative, expected in manifest['sha256'].items():
        if digest(source/relative) != expected:
            raise ValueError(f'Frozen source input changed: {relative}')
    source_cases = defaultdict(dict)
    for row in manifest['cases']:
        source_cases[row['base_case_id']][row['model']] = row

    def reference(relative):
        return (target/relative).relative_to(PROJECT_ROOT).as_posix()

    first = read(source/'model_1/small/S01/policy.json')
    operations = {k: v for k, v in first.items() if k not in CALENDAR_FIELDS}
    window = {k: first[k] for k in ('cycle_start', 'cycle_end')}
    periodic = read(source/'model_6/small/S01/policy.json')['repeat_interval_minutes']
    # The fixed-window cycle parser and consolidated-handoff parser share the
    # same policy file. Consolidated handoff uses the one-day default horizon.
    if periodic != 1440:
        raise ValueError('Expected a daily reset period in the frozen benchmark')
    write(root/'operations.json', {**operations, **window})
    write(root/'no_batch_policy.json', {'$include': [reference('operations.json')],
          'policy_version': 'no_batch_container_cycle_v1', 'repeat_interval_minutes': periodic})
    economic_hash = digest(resolve_project_path(FROZEN_BENCHMARK_ECONOMIC_REFERENCE))
    if manifest.get('economic_config_sha256', economic_hash) != economic_hash:
        raise ValueError('The registered fee profile differs from the frozen benchmark')
    defaults = read(source/'model_1/small/S01/case.json')
    write(root/'defaults.json', {
        **{k: defaults[k] for k in ('schema_version', 'solver', 'exact_time', 'alns_iter', 'seed', 'save_plots')},
        'dataset_version': FROZEN_SHARED_BENCHMARK_ROOT.name,
        'order_assignment': 'nearest', 'economic_config': FROZEN_BENCHMARK_ECONOMIC_REFERENCE,
        'no_batch_policy': reference('no_batch_policy.json'),
    })

    cases = []
    for case_id, models in source_cases.items():
        info = {k: v for k, v in models[1].items() if k not in ('model', 'case_path')}
        relative = Path(info['size'])/case_id
        shared = source/'shared'/relative
        source_model = {n: source/f'model_{n}'/relative for n in range(1, 7)}
        configs = {n: read(folder/'case.json') for n, folder in source_model.items()}
        orders = base_orders(source_model[1]/'orders.json')
        for n, folder in source_model.items():
            raw = read(folder/'policy.json')
            if (base_orders(folder/'orders.json') != orders
                    or {k: v for k, v in raw.items() if k not in CALENDAR_FIELDS} != operations
                    or raw.get('repeat_interval_minutes', periodic) != periodic):
                raise ValueError(f'{case_id} M{n}: orders or physical operations differ')
            if digest(resolve_project_path(configs[n]['economic_config'])) != economic_hash:
                raise ValueError(f'{case_id} M{n}: pricing differs from the common profile')
        dest = root/relative
        dest.mkdir(parents=True)
        write_unassigned_network(shared/'network.csv', dest/'network.csv')
        for name in ('fleet.json', 'provenance.json'):
            shutil.copyfile(shared/name, dest/name)
        write(dest/'orders.json', orders)
        total = info['total_container_uses']
        for policy_name, policy_source in (
                ('single_batch_policy.json', source_model[1]/'policy.json'),
                ('reference_batch_policy.json', source_model[5]/'fixed_window_policy.json')):
            policy = read(policy_source)
            if {k: policy[k] for k in window} != window:
                raise ValueError(f'{case_id}: unexpected fixed calendar horizon in {policy_source.name}')
            write(dest/policy_name, {'$include': [reference('operations.json')],
                  'policy_version': policy['policy_version'], 'container_pool_size': total,
                  'batches': policy['batches']})
        write(dest/'case.json', {
            '$include': [reference('defaults.json')], 'base_case_id': case_id,
            'data': reference(relative/'network.csv'), 'orders': reference(relative/'orders.json'),
            'routing_config': reference(relative/'fleet.json'),
            'dataset_metadata': reference(relative/'provenance.json'),
            'operating_policy': reference(relative/'single_batch_policy.json'),
            'reference_batch_policy': reference(relative/'reference_batch_policy.json'),
            'total_container_uses': total, 'container_pool_size': total,
            'alns_time': configs[1]['alns_time'],
        })
        cases.append({**info, 'case_path': reference(relative/'case.json')})

    with (root/'case_index.csv').open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=cases[0], lineterminator='\n')
        writer.writeheader()
        writer.writerows(cases)
    hashes = {p.relative_to(root).as_posix(): digest(p) for p in sorted(root.rglob('*')) if p.is_file()}
    write(root/'manifest.json', dict(dataset_version=FROZEN_SHARED_BENCHMARK_ROOT.name,
          source_dataset=FROZEN_BENCHMARK_ROOT.relative_to(PROJECT_ROOT).as_posix(),
          source_manifest_sha256=digest(source/'manifest.json'), base_case_count=len(cases),
          economic_config=FROZEN_BENCHMARK_ECONOMIC_REFERENCE, economic_config_sha256=economic_hash,
          models=list(range(1, 7)), cases=cases, sha256=hashes))


def build(target):
    target = Path(target).resolve()
    if not target.is_relative_to(PROJECT_ROOT) or target.exists():
        raise ValueError(f'Use a new target directory inside the repository: {target}')
    target.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=target.parent) as temp:
        staging = Path(temp)/target.name
        staging.mkdir()
        _build(resolve_project_path(FROZEN_BENCHMARK_ROOT), staging, target)
        staging.rename(target)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    build(resolve_project_path(parser.parse_args().output))
