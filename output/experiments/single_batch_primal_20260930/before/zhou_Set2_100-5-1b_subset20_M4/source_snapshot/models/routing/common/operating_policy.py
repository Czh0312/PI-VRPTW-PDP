"""Explicit, immutable operating rules; absent configuration preserves legacy runs."""
from dataclasses import asdict, dataclass
import math


@dataclass(frozen=True)
class OperatingPolicy:
    policy_version: str = "legacy"
    dispatch_time: float = 0.0
    day_end: float = None
    e2_dispatch_prep_time: float = 0.0
    e2_return_unload_time: float = 0.0
    depot_return_unload_time: float = 0.0
    container_pool_size: int = None
    time_unit: str = "minutes"

    @property
    def single_day(self):
        return self.policy_version == "single_day_backhaul_v1"

    @classmethod
    def from_config(cls, config):
        if config is None:
            return cls()
        if isinstance(config, cls):
            config = asdict(config)
        if not isinstance(config, dict):
            raise ValueError("operating_policy must be an object")
        extra = set(config) - set(cls.__dataclass_fields__) - {"description", "source_type"}
        if extra:
            raise ValueError(f"Unknown operating_policy fields: {sorted(extra)}")
        if config.get("policy_version") != "single_day_backhaul_v1":
            raise ValueError("Explicit operating_policy requires single_day_backhaul_v1")
        if "day_end" not in config or config["day_end"] is None:
            raise ValueError("single_day_backhaul_v1 requires an explicit day_end")
        values = {k: v for k, v in config.items() if k in cls.__dataclass_fields__}
        for key in ("dispatch_time", "day_end", "e2_dispatch_prep_time",
                    "e2_return_unload_time", "depot_return_unload_time"):
            value = values.get(key, 0.0)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{key} must be a finite nonnegative number")
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{key} must be a finite nonnegative number")
            values[key] = float(value)
        if values["day_end"] <= values["dispatch_time"]:
            raise ValueError("day_end must be after dispatch_time")
        pool = values.get("container_pool_size")
        if pool is not None and (isinstance(pool, bool) or not isinstance(pool, int) or pool < 0):
            raise ValueError("container_pool_size must be a nonnegative integer or null (daily demand)")
        if values.get("time_unit", "minutes") != "minutes":
            raise ValueError("single-day policy requires time_unit=minutes; convert all inputs first")
        return cls(**values)

    def require_model(self, model):
        if self.single_day and int(model) != 3:
            raise ValueError("single_day_backhaul_v1 currently supports only Model 3")

    def validate_problem(self, problem):
        if not self.single_day:
            return
        for dimension in (problem.fleet.container_weight, problem.fleet.container_volume):
            if not math.isfinite(dimension) or dimension < 0:
                raise ValueError("container weight and volume must be finite and nonnegative")
        satellites = {s.id for s in problem.satellites}
        total = 0
        for c in problem.customers:
            counts = (c.container_count_del, c.container_count_pick)
            if any(not math.isfinite(n) or n < 0 or not float(n).is_integer() for n in counts):
                raise ValueError(f"{c.id}: container counts must be nonnegative integers")
            if counts[0] != counts[1]:
                raise ValueError(f"{c.id}: single-day delivery and pickup container counts must match")
            if c.weight_del < 0 or not math.isfinite(c.weight_del):
                raise ValueError(f"{c.id}: invalid delivery weight")
            if c.weight_del > 0 and counts[0] == 0:
                raise ValueError(f"{c.id}: reusable delivery requires a container")
            if (counts[0] or c.weight_del) and c.assigned_sat not in satellites:
                raise ValueError(f"{c.id}: single-day customer requires a valid assigned satellite")
            total += int(counts[0])
        if total and not (problem.fleet.container_weight > 1e-9 or problem.fleet.container_volume > 1e-9):
            raise ValueError("containers require positive weight or volume")
        if self.container_pool_size is not None and self.container_pool_size < total:
            raise ValueError(f"container_pool_size={self.container_pool_size} is below daily demand={total}")

    def as_dict(self, problem=None):
        result = asdict(self)
        if self.single_day:
            result.update(e1_service_order="deliveries_then_pickups",
                          satellite_pickup_policy="all_returned_once",
                          allow_transfers=False, objective_mode="distance")
            if problem is not None:
                demand = int(sum(c.container_count_del for c in problem.customers))
                result["daily_container_demand"] = demand
                result["effective_container_pool_size"] = (
                    demand if self.container_pool_size is None else self.container_pool_size)
        return result


LEGACY_POLICY = OperatingPolicy()


def policy_for(problem):
    return getattr(problem, "operating_policy", LEGACY_POLICY)


def validate_task_inputs(problem, e1_tasks, e2_tasks_by_sat):
    """Prevent stale aggregate demands or externally altered tasks bypassing closure."""
    policy = policy_for(problem)
    if not policy.single_day:
        return
    from .task_builder import build_tasks_for_model
    expected1, expected2 = build_tasks_for_model(problem, 3)

    def signature(tasks):
        return sorted((t.id, t.cust, t.kind, t.w, t.v, t.tw) for t in tasks)

    if signature(e1_tasks) != signature(expected1) or {
        s: signature(ts) for s, ts in e2_tasks_by_sat.items() if ts
    } != {s: signature(ts) for s, ts in expected2.items() if ts}:
        raise ValueError("single-day tasks must match the problem's daily demands and time windows")
