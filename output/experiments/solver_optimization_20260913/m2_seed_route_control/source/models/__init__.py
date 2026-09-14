"""Optimization models grouped by business responsibility.

New code should import routing implementations from ``models.routing`` and
economic models from ``models.cost``.  The aliases below keep historical
experiment scripts importable while the project transitions away from the old
flat ``models.model_N`` layout.
"""

from importlib import import_module
import sys


_LEGACY_MODULE_ALIASES = {
    "catalog": "models.routing.catalog",
    "alns_base": "models.routing.alns.types",
    "alns_engine": "models.routing.alns.engine",
    "strong_initial_solution": "models.routing.alns.initial_solution",
    "solver_utils": "models.routing.common.exact_utils",
    **{
        f"model_{number}": f"models.routing.variants.model_{number}"
        for number in range(1, 8)
    },
}

for _legacy_name, _canonical_name in _LEGACY_MODULE_ALIASES.items():
    sys.modules.setdefault(
        f"{__name__}.{_legacy_name}",
        import_module(_canonical_name),
    )

del _legacy_name, _canonical_name
