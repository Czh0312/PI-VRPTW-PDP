"""Validate the one-factor E5 contrast before interpreting repriced schedules."""
from collections import defaultdict
from copy import deepcopy
import math
from pathlib import Path

from experiments.runs import run_thesis_completion as driver


def physical_signature(config):
    value = deepcopy(config)
    value.pop('economic_config')
    # Frozen copies use different paths even when their contents match.
    # Preserve every restriction; unlike E2 none may vary across prices.
    for key in ('data', 'orders', 'routing_config', 'operating_policy',
                'dataset_metadata', 'distance_matrix', 'duration_matrix'):
        if isinstance(value.get(key), str):
            value[key] = driver.sha(Path(value[key]))
    return value


def validate_price_design(study, rows):
    study = Path(study)
    groups, configs = defaultdict(dict), {}
    for row in rows:
        key = (row['base_case'], row['model'], row['seed'])
        factor = row['price_factor']
        if factor not in (.5, 1., 2.) or factor in groups[key]:
            raise ValueError('Duplicate or unsupported price point')
        groups[key][factor] = row
    evidence = []
    for (case, model, seed), points in sorted(groups.items()):
        if set(points) != {.5, 1., 2.}:
            raise ValueError('Each price comparison requires all three registered points')
        baseline_physical = baseline_domain = baseline_economics = base_rate = None
        for factor in (1., .5, 2.):
            row = points[factor]
            path = study/row['case_path']
            if path not in configs:
                configs[path] = driver.read(path)
            config = configs[path]
            physical = physical_signature(config)
            economic = config['economic_config']
            economic = deepcopy(driver.read(Path(economic)) if isinstance(economic, str) else economic)
            rate = economic.pop('container_pool_cost_per_period')
            economic.pop('description', None)  # Only a scenario label may differ.
            domain = (row['counts'], row['method'], row['budget'], row['iterations'], row['version'])
            if factor == 1.:
                baseline_physical, baseline_domain = physical, domain
                baseline_economics, base_rate = economic, rate
            if physical != baseline_physical or domain != baseline_domain:
                raise ValueError(f'Unmatched physical input or search domain: {case}, M{model}, seed{seed}')
            if economic != baseline_economics or not math.isclose(rate, base_rate*factor, rel_tol=1e-12, abs_tol=1e-12):
                raise ValueError(f'Price contrast changes more than the registered pool rate: {case}')
            evidence.append(dict(id=row['id'], case=case, model=model, seed=seed,
                factor=factor, pool_rate=rate, case_sha256=driver.sha(path),
                baseline_id=points[1.]['id'], matched=True))
    return evidence
