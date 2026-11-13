"""M5: optimize the pool and routing under fixed order batches and calendar."""
from copy import deepcopy
from dataclasses import replace
import math

from models.common.cycle.instance import MultiBatchInstance
from models.common.cycle.policy import integer
from models.cost.joint_batch import JointCostProfile
from tools.configuration.loader import load_json


class FixedBatchPoolInstance(MultiBatchInstance):
    optimize_pool = True
    require_productive_batches = True

    def __init__(self, network, orders, policy, options, economic_config):
        super().__init__(network, orders, policy, model=5)
        if not self.orders:
            raise ValueError('M5 pool optimization requires at least one order')
        self.options = deepcopy(options)
        if set(options) - {'pool_min', 'pool_max'}:
            raise ValueError('M5 pool_options supports only pool_min and pool_max')
        self.pool_min = integer(options.get('pool_min', 1), 'pool_min', 1)
        self.pool_max = integer(options.get('pool_max', self.total), 'pool_max', 1)
        if not self.pool_min <= self.pool_max <= self.total:
            raise ValueError('Require 1 <= pool_min <= pool_max <= total container uses')
        self.cost_profile = JointCostProfile(
            load_json(economic_config) if isinstance(economic_config, str) else economic_config)
        self.economic_config_path = economic_config if isinstance(economic_config, str) else None
        # The original policy's pool is a reference value, never a fixed decision.
        # Prechecks and decoders use the allowed upper bound for feasibility.
        self.operating_policy = replace(self.operating_policy, container_pool_size=self.pool_max)
        self._projections = {}

    def project_pool(self, pool):
        pool = integer(pool, 'container_pool_size', 1)
        if not self.pool_min <= pool <= self.pool_max:
            raise ValueError('M5 container pool outside decision bounds')
        if pool not in self._projections:
            if len(self._projections) >= 32:
                self._projections.pop(next(iter(self._projections)))
            self._projections[pool] = MultiBatchInstance(
                self.network, self.orders, replace(self.operating_policy, container_pool_size=pool),
                model=5, _physical_source=self)
            self._projections[pool].require_productive_batches = True
        return self._projections[pool]


def attach_pool_cost(problem, result, pool, *, physical=None):
    """Record a chosen pool and price this schedule using the shared M5–M8 contract."""
    from models.common.cycle.audit import _audit
    fixed = problem.project_pool(pool)
    result.update(objective_mode='cost', container_pool_size=pool,
                  operating_policy=fixed.operating_policy.as_dict(),
                  pool_bounds=dict(min=problem.pool_min, max=problem.pool_max),
                  economic_profile_hash=problem.cost_profile.hash,
                  evidence_hash=problem.cost_profile.evidence_hash)
    if physical is None:
        physical = _audit(fixed, {**result, 'info': {**result['info'], 'objective': None}}, False)
    cost = problem.cost_profile.evaluate(physical, pool)
    result.update(total_distance=sum(physical['distances'].values()), cost=cost,
                  cost_components=cost['components'])
    result['info']['objective'] = cost['total']
    return physical


def audit_pool_solution(problem, result, repeat=True):
    """Replay with the selected pool; independently verify the claimed total cost."""
    from models.common.cycle.audit import _audit
    pool = integer(result.get('container_pool_size'), 'container_pool_size', 1)
    fixed = problem.project_pool(pool)
    physical = _audit(fixed, {**result, 'info': {
        **result.get('info', {}), 'objective': result.get('total_distance')}}, repeat)
    errors = physical['errors']
    distance = result.get('total_distance')
    if (isinstance(distance, bool) or not isinstance(distance, (int, float))
            or not math.isfinite(distance)
            or not math.isclose(distance, sum(physical['distances'].values()), rel_tol=1e-7, abs_tol=1e-6)):
        errors.append(dict(code='DISTANCE_OBJECTIVE_MISMATCH'))
    if (result.get('objective_mode') != 'cost'
            or result.get('pool_bounds') != dict(min=problem.pool_min, max=problem.pool_max)):
        errors.append(dict(code='INVALID_POOL_COST_SCHEMA'))
    cost = problem.cost_profile.evaluate(physical, pool)
    claimed = result.get('info', {}).get('objective')
    if (isinstance(claimed, bool) or not isinstance(claimed, (int, float))
            or not math.isfinite(claimed)
            or not math.isclose(claimed, cost['total'], rel_tol=1e-7, abs_tol=1e-6)):
        errors.append(dict(code='COST_OBJECTIVE_MISMATCH'))
    if result.get('cost_components') != cost['components'] or result.get('cost') != cost:
        errors.append(dict(code='COST_COMPONENT_MISMATCH'))
    if (result.get('economic_profile_hash') != problem.cost_profile.hash
            or result.get('evidence_hash') != problem.cost_profile.evidence_hash):
        errors.append(dict(code='ECONOMIC_PROFILE_MISMATCH'))
    physical.update(valid=not errors, objective=cost['total'], cost=cost,
                    total_distance=sum(physical['distances'].values()), container_pool_size=pool)
    return physical
