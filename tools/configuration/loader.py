"""Load composable project configuration files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BENCHMARK_ROOT = PROJECT_ROOT / 'data' / 'basetest'
DATASET_ECONOMIC_FILENAME = 'economic.json'
ROOT_CONFIG_ARCHIVE = PROJECT_ROOT / 'archive/config/20261118_root_config'
LEGACY_CONFIG_ROOT = PROJECT_ROOT / 'archive/config/20261007_before_classification'
LEGACY_DATA_ROOT = PROJECT_ROOT / 'archive/datasets/legacy_20261015/data'
RETIRED_CASE_ROOT = PROJECT_ROOT / 'archive/datasets/retired_20261024'
PRE_RENUMBERING_ROOT = PROJECT_ROOT / 'archive/datasets/pre_renumbering_20261113'
RUN_HISTORY = PROJECT_ROOT / 'output/history/pre_renumbering_20261113'
FROZEN_BENCHMARK_ROOT = PROJECT_ROOT / 'archive/datasets/benchmark27_models_1_6_v1'
FROZEN_SHARED_BENCHMARK_ROOT = PROJECT_ROOT / 'archive/datasets/benchmark27_shared_v2'
FROZEN_BENCHMARK_ECONOMIC_REFERENCE = 'config/cost/synthetic/daily_routing.json'
ARCHIVED_COST_REFERENCES = PROJECT_ROOT / 'archive/reference/cost'


def resolve_project_path(path: str | Path) -> Path:
    """Resolve current inputs first, then moved and archived references.

    Historical JSON and provenance keep their original names and hashes.
    """
    candidate = Path(path)
    candidate = candidate if candidate.is_absolute() else PROJECT_ROOT / candidate
    if not candidate.exists():
        try:
            relative = candidate.resolve().relative_to(PROJECT_ROOT)
        except ValueError:
            pass
        else:
            if relative.parts[:2] in (('data', 'small'), ('data', 'medium'), ('data', 'large')):
                relocated = BENCHMARK_ROOT.joinpath(*relative.parts[1:])
                if relocated.exists():
                    return relocated
            if relative.parts[:1] == ('config',):
                archived = ROOT_CONFIG_ARCHIVE.joinpath(*relative.parts[1:])
                if archived.exists():
                    return archived
            if relative.parts and relative.parts[0] in ('data', 'config'):
                archived = PRE_RENUMBERING_ROOT / relative
                if archived.exists():
                    return archived
                retired = RETIRED_CASE_ROOT / relative
                if retired.exists():
                    return retired
            if relative.parts[:2] == ('data', 'benchmark27_models_1_6_v1'):
                archived = FROZEN_BENCHMARK_ROOT.joinpath(*relative.parts[2:])
                if archived.exists():
                    return archived
            if relative.parts[:2] == ('data', 'benchmark27_shared_v2'):
                archived = FROZEN_SHARED_BENCHMARK_ROOT.joinpath(*relative.parts[2:])
                if archived.exists():
                    return archived
            if relative.as_posix() == FROZEN_BENCHMARK_ECONOMIC_REFERENCE:
                archived = FROZEN_SHARED_BENCHMARK_ROOT / 'economic.json'
                if archived.is_file():
                    return archived
            if relative.parts[:2] == ('reference', 'cost'):
                archived = ARCHIVED_COST_REFERENCES.joinpath(*relative.parts[2:])
                if archived.exists():
                    return archived
            if relative.parts[:3] == ('output', 'single_runs', 'model10'):
                archived_run = RUN_HISTORY.joinpath(*relative.parts[2:])
                if archived_run.exists():
                    return archived_run
            if relative.parts[:2] in (('output', 'experiments'), ('output', 'reports'),
                                      ('output', 'visualizations')):
                archived_run = RUN_HISTORY.joinpath('local_20261031', *relative.parts[1:])
                if archived_run.exists():
                    return archived_run
        try:
            relative = candidate.resolve().relative_to(PROJECT_ROOT / 'config')
        except ValueError:
            pass
        else:
            archived = LEGACY_CONFIG_ROOT / relative
            if archived.is_file():
                return archived
        try:
            relative = candidate.resolve().relative_to(PROJECT_ROOT / 'data')
        except ValueError:
            pass
        else:
            archived = LEGACY_DATA_ROOT / relative
            if archived.exists():
                return archived
    return candidate


def resolve_dataset_root(path: str | Path) -> Path:
    """Resolve an explicit dataset directory, including the former benchmark root."""
    root = resolve_project_path(path)
    return BENCHMARK_ROOT if root.resolve() == BENCHMARK_ROOT.parent else root


def config_sources(path: str | Path) -> list[Path]:
    """List the input and its explicit JSON includes; reject include cycles."""
    sources, active = [], set()

    def visit(source):
        source = resolve_project_path(source).resolve()
        if source in active:
            raise ValueError(f'Configuration include cycle: {source}')
        if source in sources:
            return
        active.add(source)
        if source.suffix == '.json':
            value = json.loads(source.read_text(encoding='utf-8-sig'))
            includes = value.get('$include', []) if isinstance(value, dict) else []
            if not isinstance(includes, list) or not all(isinstance(p, str) for p in includes):
                raise ValueError(f'$include must be a list of configuration paths: {source}')
            for included in includes:
                visit(included)
        active.remove(source)
        sources.append(source)

    visit(path)
    return sources


def _merge_config(base, overrides):
    result = dict(base)
    for key, value in overrides.items():
        result[key] = (_merge_config(result[key], value)
                       if isinstance(result.get(key), dict) and isinstance(value, dict) else value)
    return result


def load_json(path: str | Path) -> dict[str, Any]:
    values = {}
    for source in config_sources(path):
        value = json.loads(source.read_text(encoding='utf-8-sig'))
        if not isinstance(value, dict):
            raise ValueError(f'配置文件顶层必须是对象: {source}')
        merged = {}
        for included in value.pop('$include', []):
            merged = _merge_config(merged, values[resolve_project_path(included).resolve()])
        values[source] = _merge_config(merged, value)
    return values[resolve_project_path(path).resolve()]


def apply_container_pool_size(run_config, fleet_config, value):
    """Override the initial clean container pool without editing source policies."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError('container_pool_size must be a positive integer')
    policy = fleet_config.get('operating_policy') or {}
    if policy.get('policy_version') != 'multi_batch_cutoff_v1':
        raise ValueError('container_pool_size override requires multi_batch_cutoff_v1')
    return ({**run_config, 'container_pool_size': value},
            {**fleet_config, 'operating_policy': {**policy, 'container_pool_size': value}})


def _select_benchmark_model(case, model):
    reference_policy = case.pop('reference_batch_policy', case.get('operating_policy'))
    no_batch_policy = case.pop('no_batch_policy')
    model = case.get('model') if model is None else model
    if type(model) is not int or model not in range(1, 7):
        raise ValueError('Shared benchmark cases require a model number (1–6); use --model N')
    case['model'] = model
    case.setdefault('case_name', f"{case['dataset_version']}_{case['base_case_id']}_M{model}")
    case['no_work_window'] = model >= 3 and case.get('no_work_window', True)
    if model == 5:
        total = case['total_container_uses']
        case['operating_policy'] = reference_policy
        case.setdefault('exact_backend', 'joint_batch_indexed')
        case['joint_batch_options'] = {
            'schedule_mode': 'flexible_cycle', 'batch_count_candidates': list(range(1, total + 1)),
            'min_phase_minutes': 1, 'pool_min': 1, 'pool_max': total,
            **case.get('joint_batch_options', {}),
        }
    elif model == 6:
        case['operating_policy'] = no_batch_policy
        case.pop('container_pool_size', None)
        case.setdefault('exact_backend', 'gurobi')
        case['no_batch_options'] = {'objective': 'total_cost', **case.get('no_batch_options', {})}
    else:
        case.setdefault('exact_backend', 'multibatch_indexed')
    return case


def load_case_settings(case: str | Path) -> dict[str, Any]:
    from .benchmark import is_case_id

    if is_case_id(case) or Path(case).suffix.lower() == '.csv':
        return {'benchmark_case': str(case), 'model': 3}
    return load_json(case)


def routing_case_source(case: str | Path, config: dict[str, Any]) -> Path:
    from .benchmark import is_case_id

    return resolve_project_path(config['data'] if is_case_id(case) else case)


def load_routing_case(path: str | Path, *, model=None, use_pool_config=True) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load run and fleet settings, selecting a model for a shared benchmark case."""
    case = load_case_settings(path)
    if 'benchmark_case' in case:
        from .benchmark import case_path, is_case_id, load_case

        reference = case.pop('benchmark_case')
        table = case_path(BENCHMARK_ROOT, reference) if is_case_id(reference) else resolve_project_path(reference)
        case = _merge_config(load_case(table), case)
        case.setdefault('model', 3)
        case = _select_benchmark_model(case, model)
    elif 'reference_batch_policy' in case:
        case = _select_benchmark_model(case, model)
    required = {"model", "solver", "data", "routing_config"}
    missing = sorted(required - case.keys())
    if missing:
        raise ValueError(f"案例配置缺少字段 {missing}: {path}")

    algorithm = load_json(case["algorithm_config"]) if case.get("algorithm_config") else {}
    run_config = {**algorithm, **case}
    fleet = case['routing_config']
    fleet_config = load_json(fleet) if isinstance(fleet, (str, Path)) else dict(fleet)
    if "operating_policy" in case:
        policy = case["operating_policy"]
        fleet_config = {**fleet_config, "operating_policy": (
            load_json(policy) if isinstance(policy, str) else policy)}
    version = (fleet_config.get('operating_policy') or {}).get('policy_version')
    if version in ('multi_batch_cutoff_v1', 'no_batch_container_cycle_v1'):
        if case.get('schema_version') != 2 or not case.get('orders'):
            raise ValueError('Multibatch cases require schema_version=2 and orders')
        if version == 'no_batch_container_cycle_v1' and case['model'] != 6:
            raise ValueError('The no-batch policy requires model 6')
    elif case.get('schema_version', 1) != 1 or case.get('orders'):
        raise ValueError('schema_version=2/orders requires multi_batch_cutoff_v1')
    fixed_pool = case['model'] in (1, 2, 3, 4)
    if version == 'multi_batch_cutoff_v1' and use_pool_config and fixed_pool:
        from .container_pools import apply_pool_config
        run_config, fleet_config = apply_pool_config(run_config, fleet_config)
    elif 'container_pool_size' in run_config and case['model'] != 6:
        run_config, fleet_config = apply_container_pool_size(
            run_config, fleet_config, run_config['container_pool_size'])
    return run_config, fleet_config
