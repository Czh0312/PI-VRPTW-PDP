"""Canonical model numbering used by the project.

The project was renumbered once.  Keep the mapping here so command-line
drivers, result migrations, and documentation share one source of truth.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class ModelSpec:
    number: int
    slug: str
    description: str


MODEL_SPECS = {
    1: ModelSpec(1, "fixed_pure_delivery", "固定分仓、纯配送"),
    2: ModelSpec(2, "free_pure_delivery", "自由分仓、纯配送"),
    3: ModelSpec(3, "fixed_pickup_delivery", "固定分仓、配送与延后回收"),
    4: ModelSpec(4, "free_pickup_delivery", "自由分仓、配送与延后回收"),
    5: ModelSpec(5, "exact_container_relay", "固定分仓、E2空容器单次接力联合精确优化"),
    6: ModelSpec(
        6,
        "compact_homogeneous_relay",
        "固定分仓、同质空箱异步事件地点交接紧凑联合优化",
    ),
    7: ModelSpec(7, "iterative_transshipment", "固定分仓、迭代中转试探"),
}

# Number stored by old source code/results -> current canonical number.
LEGACY_TO_CURRENT = {1: 1, 7: 2, 2: 3, 6: 4, 3: 5, 4: 6, 5: 7}
CURRENT_TO_LEGACY = {current: legacy for legacy, current in LEGACY_TO_CURRENT.items()}


def require_model_number(number: int) -> int:
    """Validate and return a canonical model number."""
    if number not in MODEL_SPECS:
        raise ValueError(f"模型编号必须为 1–7，收到: {number}")
    return number
