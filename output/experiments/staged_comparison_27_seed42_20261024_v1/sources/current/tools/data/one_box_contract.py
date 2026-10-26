"""Versioned one-customer/one-standard-box input contract (not bin packing)."""
from collections import Counter
import hashlib
import json


CONTRACT = dict(
    id='one_customer_one_box_v1', boxes_per_customer=1, orders_per_customer=1,
    goods_weight='Preserve the complete original customer goods weight.',
    delivery_weight='goods_weight + container_weight',
    pickup_weight='container_weight', task_volume='container_volume',
    assumption='Each customer demand fits one standard box; no bin-packing optimization.',
)


def validate_orders(orders, customer_count):
    if len(orders) != customer_count or any(n != 1 for n in Counter(
            o['customer_id'] for o in orders).values()):
        raise ValueError('One-box contract requires exactly one order per customer')
    if any(type(o['container_count']) is not int or o['container_count'] != 1 for o in orders):
        raise ValueError('One-box contract requires container_count=1 for every customer')
    if len({o['order_id'] for o in orders}) != customer_count:
        raise ValueError('One-box contract requires unique order IDs')


def preserved_orders_hash(orders):
    """Includes weights, windows and bindings; only the changed box count is omitted."""
    rows = sorted(({k: v for k, v in o.items() if k != 'container_count'} for o in orders),
                  key=lambda o: o['order_id'])
    return hashlib.sha256(json.dumps(rows, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':')).encode('utf-8')).hexdigest()


def preserved_policy_hash(policy):
    value = {k: v for k, v in policy.items() if k not in ('container_pool_size', 'description')}
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
