"""Build the preregistered twelve-case E0 development/holdout mechanism suite.

Example: python -m tools.data.build_thesis_completion_suite --output config/cases/thesis_completion/pilot_v1
All instances are synthetic. Pair members share a source family and are not
independent empirical samples. Selection never reads solver results.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import csv
import hashlib
import json
from pathlib import Path

from tools.configuration.loader import PROJECT_ROOT


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')


def reference(path):
    try:
        return path.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def build_suite(destination):
    root = Path(destination).resolve()
    if root.exists() and any(root.iterdir()):
        raise ValueError(f'Refusing to overwrite existing inputs: {root}')
    root.mkdir(parents=True, exist_ok=True)
    records = []
    structures = ('ordinary', 'repeated_orders', 'tight_pool', 'late_return', 'release_limited', 'cleaning_boundary')
    for structure in structures:
        for variant in range(2):
            name = f'{structure}_{variant}'
            source_name = 'two_satellites' if structure == 'ordinary' and variant else 'reuse_high_pool'
            source = PROJECT_ROOT/'data/model6/mechanism_v1'/source_name
            orders = json.loads((source/'orders.json').read_text(encoding='utf-8'))
            policy = json.loads((source/'policy.json').read_text(encoding='utf-8'))
            economics = json.loads((source/'economic.json').read_text(encoding='utf-8'))
            fleet = json.loads((source/'fleet.json').read_text(encoding='utf-8'))
            with (source/'network.csv').open(encoding='utf-8', newline='') as handle:
                reader = csv.DictReader(handle)
                fields, nodes = reader.fieldnames, list(reader)
            # H=132 is divisible by every E0 K, so M6/M7 do not refine the
            # one-minute clock relative to M8. Old 130-minute values remain
            # unchanged except for the explicitly expanded cycle horizon.
            policy['cycle_end'] = 132
            economics.update(container_pool_cost_per_period=30, evaluation_period='one 132-minute cycle')
            rule = ['Reuse published synthetic mechanism input', 'Set cycle_end=132 and pool rate=30']
            if structure == 'repeated_orders':
                repeated = deepcopy(orders[0])
                repeated.update(order_id='oC1_repeat', delivery_ready=66, delivery_due=100,
                                pickup_ready=80, pickup_due=110, release_time=60)
                orders[0].update(delivery_due=25, pickup_ready=20, pickup_due=35)
                orders.append(repeated)
                if variant:
                    extra = deepcopy(orders[1])
                    extra.update(order_id='oC2_repeat', delivery_ready=70, pickup_ready=85, pickup_due=115)
                    orders.append(extra)
                rule.append('Add separately identified repeated-customer orders with disjoint early/late windows')
            elif structure == 'late_return':
                orders = orders[:1]
                orders[0].update(delivery_due=20, pickup_ready=117+2*variant, pickup_due=117+2*variant)
                rule.append(f'One early delivery and fixed pickup at {117+2*variant} minutes')
            elif structure == 'release_limited':
                orders[0].update(delivery_due=20)
                orders[1].update(release_time=70+20*variant, delivery_ready=75+20*variant,
                                 delivery_due=108, pickup_due=119)
                rule.append(f'Second order is released at {70+20*variant} minutes')
            elif structure == 'cleaning_boundary':
                orders = orders[:1]
                orders[0].update(delivery_due=20, pickup_ready=126+variant, pickup_due=126+variant)
                policy['cleaning_duration'] = 6
                for node in nodes:
                    node.update(x='0', y='0')
                rule.append(f'Zero travel; pickup at {126+variant}, cleaning duration 6, boundary at 132')
            elif structure == 'tight_pool' and variant:
                for order in list(orders):
                    extra = deepcopy(order)
                    extra['order_id'] += '_repeat'
                    orders.append(extra)
                rule.append('Duplicate both orders under distinct IDs; cap pool at two boxes')
            total = sum(o['container_count'] for o in orders)
            pool_max = (1+variant) if structure == 'tight_pool' else total
            policy['container_pool_size'] = pool_max
            if structure == 'tight_pool':
                rule.append(f'Constrain the optimized pool to N <= {pool_max}')
            folder = root/name
            folder.mkdir()
            with (folder/'network.csv').open('w', encoding='utf-8', newline='') as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerows(nodes)
            for filename, value in [('orders.json', orders), ('fleet.json', fleet),
                                    ('policy.json', policy), ('economic.json', economics)]:
                write(folder/filename, value)
            paths = {}
            for model in (6, 7, 8):
                options = dict(batch_count_candidates=[1, 2, 3], min_phase_minutes=1,
                               pool_min=1, pool_max=pool_max)
                options['schedule_mode'] = {6: 'uniform_cycle', 7: 'uniform_independent_cutoffs', 8: 'flexible_cycle'}[model]
                case = dict(schema_version=2, model=model, solver='alns', save_plots=False,
                            data=reference(folder/'network.csv'), orders=reference(folder/'orders.json'),
                            routing_config=reference(folder/'fleet.json'), operating_policy=reference(folder/'policy.json'),
                            economic_config=reference(folder/'economic.json'), joint_batch_options=options,
                            source_kind='synthetic_mechanism', case_name=name, seed=42, alns_iter=1000000000)
                target = folder/f'model_{model}.json'
                write(target, case)
                paths[str(model)] = reference(target)
            records.append(dict(id=name, structure=structure, split='development' if variant == 0 else 'holdout',
                                source_family=source_name, selection='fixed before solver execution',
                                generation_rules=rule, cases=paths, orders=len(orders), demand=total,
                                source_hashes={p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                               for p in source.iterdir() if p.name in
                                               ('network.csv', 'orders.json', 'policy.json', 'fleet.json', 'economic.json')}))
    manifest = dict(schema_version=1, suite='thesis_e0_v1', cases=records,
                    domain=[1, 2, 3], time_tick_seconds=60, subdivisions=1,
                    diagnostic_cases=['repeated_orders_0', 'late_return_0', 'tight_pool_0'],
                    exact_strategy_cases=['ordinary_0', 'repeated_orders_0', 'release_limited_0'],
                    limitations=['Synthetic mechanisms; paired variants are not independent instance families.',
                                 'E0 establishes only the configured K={1,2,3} domain.',
                                 'Holdout cases may be evaluated but must not be used for tuning.'])
    write(root/'manifest.json', manifest)
    return root/'manifest.json'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    print(build_suite(parser.parse_args().output))


if __name__ == '__main__':
    main()
