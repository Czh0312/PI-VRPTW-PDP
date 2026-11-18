"""Resolve fixed container pools from demand or an explicit setting."""
from decimal import Decimal, ROUND_CEILING
import math

from .loader import apply_container_pool_size, load_json, resolve_project_path


def load_container_pools(path=None):
    config = load_json(path) if path is not None else {'demand_ratio': 1.0}
    if set(config) != {'demand_ratio'}:
        raise ValueError('Pool config requires only demand_ratio')
    ratio = config['demand_ratio']
    if (isinstance(ratio, bool) or not isinstance(ratio, (int, float))
            or not math.isfinite(ratio) or ratio <= 0):
        raise ValueError('demand_ratio must be a finite positive number')
    return config


def configured_pool(settings, total_demand):
    """Round upwards using decimal arithmetic; never silently enlarge a pool."""
    return max(1, int((Decimal(str(settings['demand_ratio'])) * total_demand)
                      .to_integral_value(rounding=ROUND_CEILING)))


def apply_pool_config(config, fleet, path=None):
    """Resolve shared settings, or replay an already frozen explicit quantity."""
    from models.common.data_loader import read_order_rows

    if path is None and 'container_pool_size' in config:
        return apply_container_pool_size(config, fleet, config['container_pool_size'])
    pool_path = path or config.get('pool_config') or None
    if pool_path is not None:
        pool_path = str(resolve_project_path(pool_path).resolve())
        config = {**config, 'pool_config': pool_path}
    settings = load_container_pools(pool_path)
    orders = read_order_rows(resolve_project_path(config['orders']))
    total = 0
    for order in orders:
        raw = order['container_count']
        try:
            value = Decimal(str(raw))
        except ArithmeticError:
            raise ValueError('container_count must be a positive integer') from None
        if not value.is_finite() or value <= 0 or value != value.to_integral_value():
            raise ValueError('container_count must be a positive integer')
        total += int(value)
    return apply_container_pool_size(config, fleet, configured_pool(settings, total))
