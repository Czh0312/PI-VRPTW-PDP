"""Prepare the six original families at E1=0.5 with unrestricted positive K."""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from tools.configuration.loader import PROJECT_ROOT, load_routing_case, resolve_project_path
from models.common.cycle import load_instance

FAMILIES = (
    'zhou_Set2_100-10-1_subset50', 'zhou_Set2_100-10-1',
    'zhou_Set2_100-10-2_subset50', 'zhou_Set2_100-10-2',
    'zhou_Set2_100-5-1b_subset50', 'zhou_Set2_100-5-1b',
)
PROFILE = 'config/cost/scenarios/truck_tricycle_pool25_truck0p5_clean02_v1.json'
DESTINATION = 'config/experiments/pool25_truck0p5_allK_20261007'
ORDER_DESTINATION = 'output/generated_datasets/experiment_inputs/pool25_truck0p5_allK_20261007'
MODES = {6: 'uniform_cycle', 7: 'uniform_independent_cutoffs', 8: 'flexible_cycle'}


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')


def relative(path):
    try:
        return path.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path)


def build_suite(destination=DESTINATION, orders_output=None):
    root = resolve_project_path(destination).resolve()
    if orders_output is None:
        orders_output = (ORDER_DESTINATION if root == PROJECT_ROOT/DESTINATION else
                         PROJECT_ROOT/'output/generated_datasets/experiment_inputs'/root.name if root.is_relative_to(PROJECT_ROOT/'config') else
                         root.parent/(root.name+'_data'))
    orders_root = resolve_project_path(orders_output).resolve()
    if orders_root.is_relative_to(PROJECT_ROOT/'config'):
        raise ValueError('Order data belongs outside config/')
    for target in (root, orders_root):
        if target.exists() and any(target.iterdir()):
            raise ValueError(f'Refusing to replace nonempty suite: {target}')
    root.mkdir(parents=True, exist_ok=True)
    orders_root.mkdir(parents=True, exist_ok=True)
    records = []
    for family in FAMILIES:
        source = PROJECT_ROOT/'data/model5_model6/shared_v1_20261005'/family/'spread_medium/m6_case.json'
        config, fleet = load_routing_case(source, use_pool_config=False)
        original = load_instance(config, fleet)
        p = original.operating_policy
        for number in (3, 5, 6, 7, 8):
            case = deepcopy(config)
            case.update(model=number, case_name=f'{family}_truck0p5_M{number}',
                        economic_config=PROFILE, alns_time=300, alns_iter=1000000,
                        seed=42, save_plots=False,
                        exact_backend='multibatch_indexed' if number in (3, 5) else 'joint_batch_indexed')
            if number in (3, 5):
                case.pop('joint_batch_options', None)
            else:
                case['joint_batch_options'].update(schedule_mode=MODES[number],
                                                   batch_count_candidates='all_positive_integers')
            if number == 3:
                orders = json.loads(resolve_project_path(config['orders']).read_text(encoding='utf-8'))
                orders = [dict(o, delivery_batch_id='b1', allowed_batch_ids=['b1']) for o in orders]
                orders_path = orders_root/f'{family}_model_3_orders.json'
                write(orders_path, orders)
                policy = deepcopy(fleet['operating_policy'])
                policy['batches'] = [dict(id='b1',
                    delivery_duration=p.minutes(p.batches[-1].cutoff-p.cycle_start),
                    return_duration=p.minutes(p.cycle_end-p.batches[-1].cutoff))]
                case.update(orders=relative(orders_path), operating_policy=policy)
            case['$include'] = ['config/solvers/alns/comparison_300s.json',
                                'config/solvers/exact/fixed_batch_30s.json' if number in (3, 5) else
                                'config/solvers/exact/calendar_30s.json']
            for key in ('alns_time', 'alns_iter', 'seed', 'exact_time', 'exact_backend'):
                case.pop(key, None)
            path = root/f'{family}_model_{number}.json'
            write(path, case)
            problem = load_instance(*load_routing_case(path, use_pool_config=False))
            if number >= 6:
                assert problem.cost_profile.rates['e1_distance_cost_per_km'] == .5
            records.append(dict(family=family, model=number, case_path=relative(path),
                                orders=len(problem.orders), demand=problem.total,
                                batch_count_candidates=(list(problem.batch_count_candidates) if number >= 6 else None)))
    write(root/'manifest.json', dict(
        status='PREPARED_NOT_RUN', date='2026-10-07', economic_config=PROFILE,
        economic_config_sha256=hashlib.sha256(resolve_project_path(PROFILE).read_bytes()).hexdigest(),
        models=[3, 5, 6, 7, 8], alns_seconds=300, iterations=1000000, seed=42,
        batch_count_domain='all_positive_integers', cases=records,
        physical_bound='floor(cycle_duration_ticks / (2 * minimum_phase_ticks))',
        interpretation='M3/M5 retain fixed calendars and distance objectives; M6–M8 optimize cost and K.'))
    return root/'manifest.json'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default=DESTINATION)
    parser.add_argument('--orders-output', help='订单文件目录，必须位于 config/ 之外')
    args = parser.parse_args(argv)
    print(build_suite(args.output, args.orders_output))


if __name__ == '__main__':
    main()
