"""Load one case CSV, its scale configuration, and dataset rates."""
import re


DEFAULT_CASE_ID = 'S01'
SIZES = {'S': 'small', 'M': 'medium', 'L': 'large'}
OPERATIONS = dict(time_unit='minutes', time_tick_seconds=1, source_type='scenario_assumption',
                  cycle_start=480, cycle_end=1260, cleaning_duration=15, e1_load_time=2,
                  e2_dispatch_prep_time=1, e2_return_unload_time=1, depot_return_unload_time=1,
                  e1_turnaround_time=0, e2_turnaround_time=1, e2_max_trips_per_vehicle=12,
                  terminal_policy='all_clean_at_depot')


def is_case_id(value):
    return re.fullmatch(r'[SML][0-9]{2}', str(value).upper()) is not None


def case_path(root, case_id):
    case_id = case_id.upper()
    return root / SIZES[case_id[0]] / f'{case_id}.csv'


def case_paths(root):
    return [path for size in SIZES.values() for path in sorted((root/size).glob('*.csv'))]


def case_info(path):
    from models.common.data_loader import read_order_rows
    from models.common.cycle.policy import integer

    orders = read_order_rows(path)
    if not orders:
        raise ValueError(f'Case has no customer orders: {path}')
    return dict(
        base_case_id=path.stem, size=path.parent.name, customer_count=len(orders),
        total_container_uses=sum(integer(float(o['container_count']), 'container_count', 1) for o in orders),
    )


def load_case(path):
    from .loader import BENCHMARK_ROOT, DATASET_ECONOMIC_FILENAME, load_json

    config = load_json(path.with_name('config.json'))
    info = case_info(path)
    fleet = config['fleet']
    dataset = path.parent.parent
    economic_path = dataset / DATASET_ECONOMIC_FILENAME
    dataset_input = dataset.parent.resolve() == BENCHMARK_ROOT.parent or economic_path.is_file()
    version = dataset.name if dataset_input else 'benchmark27_v5'
    if not dataset_input:
        # Standalone CSVs can still use the benchmark rate profile.
        economic_path = BENCHMARK_ROOT / DATASET_ECONOMIC_FILENAME
    if 'cases' in config:
        # Frozen CSV inputs from before fleet standardization carry overrides.
        fleet = {**fleet, **config['cases'].get(path.stem, {}).get('fleet', {})}
        version = 'benchmark27_v4'
    total = info['total_container_uses']
    policy = dict(OPERATIONS, policy_version='multi_batch_cutoff_v1',
                  container_pool_size=total, batches=[{'id': 'b1'}])
    return dict(
        **info, schema_version=2, dataset_version=version, solver='alns',
        exact_time=300, alns_time=config['alns_time'], alns_iter=10**9, seed=42, save_plots=False,
        order_assignment='nearest', data=str(path), orders=str(path), routing_config=fleet,
        benchmark_config=str(path.with_name('config.json')), economic_config=str(economic_path.resolve()),
        container_pool_size=total, operating_policy=policy,
        no_batch_policy=dict(OPERATIONS, policy_version='no_batch_container_cycle_v1', repeat_interval_minutes=1440),
    )
