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
    return run_config, fleet_config
