"""One demand-based container pool setting shared by every current run."""
import csv
from decimal import Decimal, ROUND_CEILING
import json
import math

from .loader import apply_container_pool_size, load_json, resolve_project_path

DEFAULT_POOL_CONFIG = 'config/containers/pool_ratio.json'


def load_container_pools(path=DEFAULT_POOL_CONFIG):
    config = load_json(path)
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
    if path is None and 'container_pool_size' in config:
        return apply_container_pool_size(config, fleet, config['container_pool_size'])
    path = str(resolve_project_path(path or config.get('pool_config') or DEFAULT_POOL_CONFIG).resolve())
    settings = load_container_pools(path)
    orders_path = resolve_project_path(config['orders'])
    with orders_path.open(encoding='utf-8-sig', newline='') as handle:
        orders = json.load(handle) if orders_path.suffix == '.json' else list(csv.DictReader(handle))
    if not isinstance(orders, list):
        raise ValueError('Orders must be a JSON list or CSV table')
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
    return apply_container_pool_size({**config, 'pool_config': path}, fleet,
                                     configured_pool(settings, total))
