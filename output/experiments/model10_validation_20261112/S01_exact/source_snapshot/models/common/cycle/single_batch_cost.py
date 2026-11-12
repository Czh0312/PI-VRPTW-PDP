"""M3 cost comparison with its original fixed assignment and calendar."""
from __future__ import annotations

import math

from models.common.cycle.instance import MultiBatchInstance
from models.cost.joint_batch import JointCostProfile
from tools.configuration.loader import load_json


class SingleBatchCostInstance(MultiBatchInstance):
    """Keep M3's physical decisions; price them with the M5–M8 contract."""

    cost_objective = True

    def __init__(self, network, orders, policy, economic_config):
        super().__init__(network, orders, policy, model=3)
        self.cost_profile = JointCostProfile(
            load_json(economic_config) if isinstance(economic_config, str) else economic_config)


def attach_single_batch_cost(problem, result):
    """Price the explicit schedule, including occupied time and activations."""
    from models.common.cycle.audit import _audit

    physical = _audit(problem, result, False)
    pool = problem.operating_policy.container_pool_size
    distance = sum(physical['distances'].values())
    cost = problem.cost_profile.evaluate(physical, pool)
    result.update(objective_mode='cost', total_distance=distance,
                  container_pool_size=pool, cost=cost, cost_components=cost['components'],
                  economic_profile_hash=problem.cost_profile.hash,
                  evidence_hash=problem.cost_profile.evidence_hash)
    result['info']['objective'] = cost['total']
    return physical


def audit_single_batch_cost(problem, result, repeat=True):
    """Independently replay and recompute all claimed costs and metadata."""
    from models.common.cycle.audit import _audit

    physical = _audit(problem, {**result, 'info': {
        **result.get('info', {}), 'objective': result.get('total_distance')}}, repeat)
    errors = physical['errors']
    distance = sum(physical['distances'].values())
    pool = problem.operating_policy.container_pool_size
    cost = problem.cost_profile.evaluate(physical, pool)

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
