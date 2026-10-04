"""Load composable project configuration files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def resolve_project_path(path: str | Path) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else PROJECT_ROOT / candidate


def load_json(path: str | Path) -> dict[str, Any]:
    resolved = resolve_project_path(path)
    with resolved.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"配置文件顶层必须是对象: {resolved}")
    return value


def apply_container_pool_size(run_config, fleet_config, value):
    """Override the initial clean container pool without editing source policies."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError('container_pool_size must be a positive integer')
    policy = fleet_config.get('operating_policy') or {}
    if policy.get('policy_version') != 'multi_batch_cutoff_v1':
        raise ValueError('container_pool_size override requires multi_batch_cutoff_v1')
    return ({**run_config, 'container_pool_size': value},
            {**fleet_config, 'operating_policy': {**policy, 'container_pool_size': value}})


def load_routing_case(path: str | Path) -> tuple[dict[str, Any], dict[str, Any]]:
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
    if 'container_pool_size' in run_config:
        run_config, fleet_config = apply_container_pool_size(
            run_config, fleet_config, run_config['container_pool_size'])
    return run_config, fleet_config
