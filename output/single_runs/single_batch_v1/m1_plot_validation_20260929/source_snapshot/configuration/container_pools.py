"""Per-dataset, per-batch pool quantities for frozen dataset experiments."""
from .loader import load_json


def load_container_pools(path, records):
    """Validate all configured family/batch keys against the complete manifest."""
    config = load_json(path)
    if (config.get('schema_version') != 1
            or set(config) - {'schema_version', 'description', 'families'}
            or not isinstance(config.get('families'), dict) or not config['families']):
        raise ValueError('Pool config requires schema_version=1 and nonempty families')
    known = {}
    for record in records:
        known.setdefault(record['family_id'], set()).add(f'b{record["batch_count"]}')
    for family, batches in config['families'].items():
        if family not in known:
            raise ValueError(f'Unknown pool config family: {family}')
        if not isinstance(batches, dict) or not batches or set(batches) - known[family]:
            raise ValueError(f'Unknown or empty pool config batch keys: {family}')
        for batch, value in batches.items():
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f'Pool config {family}/{batch} must be a positive integer')
    return config['families']


def configured_pool(pools, record):
    family, batch = record['family_id'], f'b{record["batch_count"]}'
    if batch not in pools.get(family, {}):
        raise ValueError(f'Missing pool config for selected case: {family}/{batch}')
    return pools[family][batch]
