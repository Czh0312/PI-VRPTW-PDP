"""Calendar decision grids, independent of the physical travel/service clock."""
from models.common.cycle.policy import integer


def decision_step(policy, seconds):
    if seconds is None:
        return 1
    seconds = integer(seconds, 'calendar_decision_grid_seconds', 1)
    numerator = seconds * policy.time_tick_subdivision
    if numerator % policy.time_tick_seconds:
        raise ValueError('Calendar decision grid must be representable on the physical clock')
    return numerator // policy.time_tick_seconds


def snap_decision(value, lo, hi, step):
    """Nearest allowed absolute tick, clamped to the allowed grid interval."""
    lower, upper = -(-lo // step), hi // step
    if lower > upper:
        raise ValueError('No calendar grid point inside the configured interval')
    return min(upper, max(lower, (value + step // 2) // step)) * step
