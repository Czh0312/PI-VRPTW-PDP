"""Load composable project configuration files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LEGACY_CONFIG_ROOT = PROJECT_ROOT / 'archive/config/20261007_before_classification'
LEGACY_DATA_ROOT = PROJECT_ROOT / 'archive/datasets/legacy_20261015/data'


def resolve_project_path(path: str | Path) -> Path:
    """Resolve current inputs first, then archived config/data references.

    Historical JSON and provenance keep their original names and hashes. Only
    existing archived inputs are redirected; new output paths stay untouched.
    """
    candidate = Path(path)
    candidate = candidate if candidate.is_absolute() else PROJECT_ROOT / candidate
    if not candidate.exists():
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


def load_routing_case(path: str | Path, *, use_pool_config=True) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return merged run settings and routing/fleet parameters for one case."""
    case = load_json(path)
    required = {"model", "solver", "data", "routing_config"}
    missing = sorted(required - case.keys())
    if missing:
        raise ValueError(f"案例配置缺少字段 {missing}: {path}")

    algorithm = load_json(case["algorithm_config"]) if case.get("algorithm_config") else {}
    run_config = {**algorithm, **case}
    fleet_config = load_json(case["routing_config"])
    if "operating_policy" in case:
        policy = case["operating_policy"]
        fleet_config = {**fleet_config, "operating_policy": (
            load_json(policy) if isinstance(policy, str) else policy)}
    version = (fleet_config.get('operating_policy') or {}).get('policy_version')
    if version == 'multi_batch_cutoff_v1':
        if case.get('schema_version') != 2 or not case.get('orders'):
            raise ValueError('Multibatch cases require schema_version=2 and orders')
    elif case.get('schema_version', 1) != 1 or case.get('orders'):
        raise ValueError('schema_version=2/orders requires multi_batch_cutoff_v1')
    if version == 'multi_batch_cutoff_v1' and use_pool_config and case['model'] not in (6, 7, 8):
        from .container_pools import apply_pool_config
        run_config, fleet_config = apply_pool_config(run_config, fleet_config)
    elif 'container_pool_size' in run_config:
        run_config, fleet_config = apply_container_pool_size(
            run_config, fleet_config, run_config['container_pool_size'])
    return run_config, fleet_config
