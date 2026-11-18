"""Per-period cost and audited schedule scoring for M1–M4."""
from __future__ import annotations

import math

from models.common.cycle.instance import MultiBatchInstance
from models.common.period_cost import JointCostProfile
from tools.configuration.loader import load_json


class SingleBatchCostInstance(MultiBatchInstance):
    """Add period prices to the selected model's physical inputs."""

    cost_objective = True

    def __init__(self, network, orders, policy, economic_config, *, model=3, no_work_window=False):
        super().__init__(network, orders, policy, model=model, no_work_window=no_work_window)
        self.cost_profile = JointCostProfile(
            load_json(economic_config) if isinstance(economic_config, str) else economic_config)
        if not self.reusable and 'single_use_package_price' not in self.cost_profile.rates:
            raise ValueError('M1/M2 require single_use_package_price in economic_config')


def attach_single_batch_cost(problem, result):
    """Price the explicit schedule, including occupied time and activations."""
    from models.common.cycle.audit import _audit_with_assignment

    physical = _audit_with_assignment(problem, {**result, 'info': {}}, False)
    pool = problem.operating_policy.container_pool_size if problem.reusable else 0
    distance = sum(physical['distances'].values())
    cost = problem.cost_profile.evaluate(physical, pool, reusable=problem.reusable)
    result.update(objective_mode='cost', total_distance=distance,
                  container_pool_size=pool, cost=cost, cost_components=cost['components'],
                  economic_profile_hash=problem.cost_profile.hash,
                  evidence_hash=problem.cost_profile.evidence_hash)
    result['info']['objective'] = cost['total']
    return physical


def score_single_batch(problem, result, distance, violations, shortage):
    result['info']['objective'] = distance
    if getattr(problem, 'cost_objective', False) and not violations:
        try:
            physical = attach_single_batch_cost(problem, result)
        except ValueError:
            return 1e12, result, ['INVALID_SCHEDULE']
        violations.extend(error['code'] for error in physical['errors'])
    score = result['info']['objective'] + len(violations) * 1e6 + shortage * 1e5
    return score, result, violations


def audit_single_batch_cost(problem, result, repeat=True):
    """Independently replay and recompute all claimed costs and metadata."""
    from models.common.cycle.audit import _audit_with_assignment

    physical = _audit_with_assignment(problem, {**result, 'info': {
        **result.get('info', {}), 'objective': result.get('total_distance')}}, repeat)
    errors = physical['errors']
    distance = sum(physical['distances'].values())
    pool = problem.operating_policy.container_pool_size if problem.reusable else 0
    cost = problem.cost_profile.evaluate(physical, pool, reusable=problem.reusable)

    def matches(value, expected):
        return (not isinstance(value, bool) and isinstance(value, (int, float))
                and math.isfinite(value)
                and math.isclose(value, expected, rel_tol=1e-7, abs_tol=1e-6))

    if not matches(result.get('total_distance'), distance):
        errors.append(dict(code='DISTANCE_OBJECTIVE_MISMATCH'))
    if result.get('objective_mode') != 'cost' or result.get('container_pool_size') != pool:
        errors.append(dict(code='INVALID_SINGLE_BATCH_COST_SCHEMA'))
    if not matches(result.get('info', {}).get('objective'), cost['total']):
        errors.append(dict(code='COST_OBJECTIVE_MISMATCH'))
    if result.get('cost_components') != cost['components'] or result.get('cost') != cost:
        errors.append(dict(code='COST_COMPONENT_MISMATCH'))
    if (result.get('economic_profile_hash') != problem.cost_profile.hash
            or result.get('evidence_hash') != problem.cost_profile.evidence_hash):
        errors.append(dict(code='ECONOMIC_PROFILE_MISMATCH'))
    physical.update(valid=not errors, objective=cost['total'], cost=cost,
                    total_distance=distance, container_pool_size=pool)
    return physical
