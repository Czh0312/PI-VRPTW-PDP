"""Prepare the six original families at E1=0.5 with unrestricted positive K."""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from configuration.loader import PROJECT_ROOT, load_routing_case, resolve_project_path
from models.routing.common.cycle import load_instance

FAMILIES = (
    'zhou_Set2_100-10-1_subset50', 'zhou_Set2_100-10-1',
    'zhou_Set2_100-10-2_subset50', 'zhou_Set2_100-10-2',
    'zhou_Set2_100-5-1b_subset50', 'zhou_Set2_100-5-1b',
)
PROFILE = 'config/cost/cases/truck_tricycle_pool25_truck0p5_clean02_v1.json'
DESTINATION = 'config/cases/20261007_pool25_truck0p5_allK_v1'
MODES = {6: 'uniform_cycle', 7: 'uniform_independent_cutoffs', 8: 'flexible_cycle'}


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')


def relative(path):
    return path.relative_to(PROJECT_ROOT).as_posix()


def build_suite(destination=DESTINATION):
    root = resolve_project_path(destination)
    if root.exists() and any(root.iterdir()):
        raise ValueError(f'Refusing to replace nonempty suite: {root}')
    root.mkdir(parents=True, exist_ok=True)
    records = []
    for index, family in enumerate(FAMILIES):
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
                orders_path = root/f'{index}_M3_orders.json'
                write(orders_path, orders)
                policy = deepcopy(fleet['operating_policy'])
                policy['batches'] = [dict(id='b1',
                    delivery_duration=p.minutes(p.batches[-1].cutoff-p.cycle_start),
                    return_duration=p.minutes(p.cycle_end-p.batches[-1].cutoff))]
                case.update(orders=relative(orders_path), operating_policy=policy)
            path = root/f'{index}_M{number}.json'
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
    print(build_suite(parser.parse_args(argv).output))


if __name__ == '__main__':
    main()
