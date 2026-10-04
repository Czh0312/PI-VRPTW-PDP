"""Nine pool quantities: three dataset sizes by three batch counts."""
from .loader import load_json


def load_container_pools(path):
    """Require a complete 3 x 3 table of positive integer pool sizes."""
    config = load_json(path)
    if (config.get('schema_version') != 2
            or set(config) - {'schema_version', 'description', 'sizes'}
            or not isinstance(config.get('sizes'), dict)
            or set(config['sizes']) != {'small', 'medium', 'large'}):
        raise ValueError('Pool config requires schema_version=2 and sizes: small, medium, large')
    for size, batches in config['sizes'].items():
        if not isinstance(batches, dict) or set(batches) != {'b1', 'b2', 'b3'}:
            raise ValueError(f'Pool config {size} requires batch keys: b1, b2, b3')
        for batch, value in batches.items():
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f'Pool config {size}/{batch} must be a positive integer')
    return config['sizes']


def configured_pool(pools, record):
    size, batch = record['size'], f'b{record["batch_count"]}'
    if batch not in pools.get(size, {}):
        raise ValueError(f'Missing pool config for selected case: {size}/{batch}')
    return pools[size][batch]
