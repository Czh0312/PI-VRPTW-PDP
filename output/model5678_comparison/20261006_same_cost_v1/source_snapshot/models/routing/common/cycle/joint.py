"""M6 input, projection and audit. Contains no optimization/search dependency."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import math

from configuration.loader import load_json
from .instance import MultiBatchInstance
from .policy import integer
from .state import empty_result

STRATEGY = 'joint_batch_cost_v1'


class JointBatchInstance(MultiBatchInstance):
    joint_batch = True

    def __init__(self, network, orders, policy, options, economic_config, *, _physical_source=None):
        super().__init__(network, orders, policy, model=6, _physical_source=_physical_source)
        if not self.orders:
            raise ValueError('M6 requires at least one order')
        self.options = deepcopy(options)
        if set(options)-{'pool_min', 'pool_max', 'fixed_assignment',
                         'optimize_cutoffs', 'cutoff_bounds_minutes', 'shared_cutoff', 'shared_min_phase_ticks',
                         'min_phase_ticks'}:
            raise ValueError('Unknown joint_batch_options')
        self.optimize_cutoffs = options.get('optimize_cutoffs', False)
        if not isinstance(self.optimize_cutoffs, bool):
            raise ValueError('optimize_cutoffs must be boolean')
        self.shared_cutoff = options.get('shared_cutoff', False)
        if not isinstance(self.shared_cutoff, bool) or (self.shared_cutoff and not self.optimize_cutoffs):
            raise ValueError('shared_cutoff requires boolean true and optimize_cutoffs')
        bounds = options.get('cutoff_bounds_minutes')
        if bounds is not None and not self.optimize_cutoffs:
            raise ValueError('cutoff_bounds_minutes requires optimize_cutoffs')
        if bounds is not None and (not isinstance(bounds, dict) or set(bounds) != set(self.batch_map)):
            raise ValueError('cutoff_bounds_minutes must cover exactly all batches')
        # Input bounds are absolute minutes; decisions and saved results use
        # integer ticks. Fixed starts/ends preserve candidate departure slots.
        self.cutoff_bounds = {}
        minimum = integer(options.get('min_phase_ticks', 1), 'min_phase_ticks', 1)
        for b in self.operating_policy.batches:
            if not self.optimize_cutoffs:
                lo = hi = b.cutoff
            elif bounds is None:
                lo, hi = b.start+minimum, b.end-minimum
            else:
                interval = bounds[b.id]
                if not isinstance(interval, (list, tuple)) or len(interval) != 2:
                    raise ValueError('Each cutoff bound must be [lower_minute, upper_minute]')
                lo = self.operating_policy.ticks(interval[0], 'ceil')
                hi = self.operating_policy.ticks(interval[1], 'floor')
            if not b.start < lo <= hi < b.end:
                raise ValueError(f'{b.id}: cutoff bounds must leave both phases positive')
            self.cutoff_bounds[b.id] = (lo, hi)
        self.default_cutoffs = {b.id:min(max(b.cutoff, self.cutoff_bounds[b.id][0]),
                                       self.cutoff_bounds[b.id][1])
                                for b in self.operating_policy.batches}
        if self.shared_cutoff:
            durations = {b.end-b.start for b in self.operating_policy.batches}
            if len(durations) != 1:
                raise ValueError('Shared cutoff requires equal batch durations')
            minimum = integer(options.get('shared_min_phase_ticks',1),'shared_min_phase_ticks',1)
            lo = max(minimum,max(self.cutoff_bounds[b.id][0]-b.start for b in self.operating_policy.batches))
            hi = min(next(iter(durations))-minimum,min(self.cutoff_bounds[b.id][1]-b.start for b in self.operating_policy.batches))
            if lo > hi:
                raise ValueError('No common relative cutoff is inside all batch bounds')
            self.shared_cutoff_bounds = (lo, hi)
            offset = min(max(next(iter(self.default_cutoffs.values()))-self.operating_policy.batches[0].start, lo), hi)
            self.cutoff_bounds = {b.id:(b.start+lo,b.start+hi) for b in self.operating_policy.batches}
            self.default_cutoffs = {b.id:b.start+offset for b in self.operating_policy.batches}
        elif 'shared_min_phase_ticks' in options:
            raise ValueError('shared_min_phase_ticks requires shared_cutoff')
        self.pool_min = integer(options.get('pool_min', 1), 'pool_min', 1)
        self.pool_max = integer(options.get('pool_max', self.total), 'pool_max', 1)
        if not self.pool_min <= self.pool_max <= self.total:
            raise ValueError('Require 1 <= pool_min <= pool_max <= total container uses')
        from models.cost.joint_batch import JointCostProfile
        self.cost_profile = JointCostProfile(load_json(economic_config) if isinstance(economic_config, str) else economic_config)
        self.economic_config_path = economic_config if isinstance(economic_config, str) else None
        for o in self.orders:
            if o.release_time is None or o.allowed_batch_ids is None:
                raise ValueError(f'{o.id}: M6 requires explicit release_time and allowed_batch_ids')
            integer(o.release_time, 'release_time')
            if len(set(o.allowed_batch_ids)) != len(o.allowed_batch_ids) or any(
                    b not in self.batch_map for b in o.allowed_batch_ids):
                raise ValueError(f'{o.id}: invalid or duplicate allowed_batch_ids')
        from .precheck import travel_lower_bounds
        self.travel_bounds = travel_lower_bounds(self)
        self.legal_batches = {}
        fixed = options.get('fixed_assignment')
        if fixed is not None and (not isinstance(fixed, dict) or set(fixed) != set(self.order_map)):
            raise ValueError('fixed_assignment must cover exactly all orders')
        for o in self.orders:
            candidates = []
            for bid in o.allowed_batch_ids:
                b = self.batch_map[bid]
                if b.start < o.release_time:
                    continue
                supplied = b.start + self.operating_policy.e1_load_time + self.travel_bounds['outward'][self.depot][o.satellite] + self.service(o.satellite)
                delivered = max(o.delivery_window[0], supplied+self.operating_policy.e2_dispatch_prep_time+
                                self.travel_bounds['outward'][o.satellite][o.customer])
                picked = max(o.pickup_window[0], delivered+o.service_time+o.min_use_time)
                returned = picked+o.service_time+self.travel_bounds['inward'][o.satellite][o.customer]+self.operating_policy.e2_return_unload_time
                if (supplied <= self.cutoff_bounds[bid][1] and delivered <= o.delivery_window[1]
                        and picked <= o.pickup_window[1]
                        and returned <= self.cutoff_bounds[self.operating_policy.batches[-1].id][1]):
                    candidates.append(bid)
            self.legal_batches[o.id] = tuple(bid for bid in candidates if fixed is None or fixed[o.id] == bid)

    def validated_cutoffs(self, cutoffs=None):
        if cutoffs is None:
            cutoffs = self.default_cutoffs
        if not isinstance(cutoffs, dict) or set(cutoffs) != set(self.batch_map):
            raise ValueError('batch_cutoffs must cover exactly all batches')
        checked = {bid:integer(value, 'batch_cutoff', 1) for bid,value in cutoffs.items()}
        for bid,value in checked.items():
            lo,hi = self.cutoff_bounds[bid]
            if not lo <= value <= hi:
                raise ValueError(f'{bid}: cutoff outside configured bounds')
        if self.shared_cutoff and len({checked[b.id]-b.start for b in self.operating_policy.batches}) != 1:
            raise ValueError('All batches must share the same relative cutoff')
        return checked

    def project(self, assignment, pool=None, cutoffs=None):
        if not isinstance(assignment, dict) or set(assignment) != set(self.order_map):
            raise ValueError('Order batch assignment must cover exactly all orders')
        if any(assignment[o.id] not in self.legal_batches[o.id] for o in self.orders):
            raise ValueError('Illegal order batch assignment')
        pool = self.pool_max if pool is None else integer(pool, 'container_pool_size', 1)
        # Projection may use Q temporarily to decode an insufficient-pool proposal.
        if pool > self.total:
            raise ValueError('Projection pool exceeds total container uses')
        checked = self.validated_cutoffs(cutoffs)
        p = replace(self.operating_policy, container_pool_size=pool,
                    batches=tuple(replace(b, cutoff=checked[b.id])
                                  for b in self.operating_policy.batches))
        orders = [replace(o, batch=assignment[o.id]) for o in self.orders]
        return MultiBatchInstance(self.network, orders, p, model=3 if len(p.batches)==1 else 5,
                                  _physical_source=self)


def joint_empty_result(problem, method):
    result = empty_result(problem, method)
    result.update(strategy=STRATEGY, objective_mode='cost',
                  model_module='models.routing.variants.model_6',
                  candidate_schedule=[dict(id=b.id, start=b.start, cutoff=b.cutoff, end=b.end)
                                      for b in problem.operating_policy.batches],
                  economic_profile_hash=problem.cost_profile.hash,
                  evidence_hash=problem.cost_profile.evidence_hash,
                  cutoff_mode='optimized' if problem.optimize_cutoffs else 'fixed',
                  shared_cutoff=problem.shared_cutoff,
                  cutoff_bounds={bid:list(bounds) for bid,bounds in problem.cutoff_bounds.items()})
    return result


def lift_result(problem, raw, assignment, pool, method, cutoffs=None):
    """Attach joint decisions, then check them against the original instance."""
    checked = problem.validated_cutoffs(cutoffs)
    fixed = problem.project(assignment, pool, checked)
    result = deepcopy(raw)
    result.update(model=6, strategy=STRATEGY, solver=method, objective_mode='cost',
                  operating_policy=fixed.operating_policy.as_dict(),
                  batches=empty_result(fixed, method)['batches'],
                  model_module='models.routing.variants.model_6',
                  batch_mode='joint_candidate_batches',
                  order_batch_assignment=dict(assignment), container_pool_size=pool,
                  delivery_batch_activation={b.id: bool(fixed.batch_demands[b.id]) for b in fixed.operating_policy.batches},
                  delivery_batch_count=sum(bool(v) for v in fixed.batch_demands.values()),
                  candidate_schedule=joint_empty_result(problem, method)['candidate_schedule'],
                  batch_cutoffs=checked,
                  cutoff_mode='optimized' if problem.optimize_cutoffs else 'fixed',
                  shared_cutoff=problem.shared_cutoff,
                  cutoff_bounds={bid:list(bounds) for bid,bounds in problem.cutoff_bounds.items()},
                  economic_profile_hash=problem.cost_profile.hash,
                  evidence_hash=problem.cost_profile.evidence_hash)
    from .audit import _audit
    physical = _audit(fixed, {**result, 'model':fixed.model_number,
                             'info':{**result['info'], 'objective':None}}, True)
    if not physical['valid']:
        result['audit'] = physical
        result['info'].update(status='AUDIT_FAILED', proven_optimal=False, objective=None)
        return result
    cost = problem.cost_profile.evaluate(physical, pool)
    result.update(total_distance=physical['objective'], cost=cost, cost_components=cost['components'],
                  cutoff_snapshots=physical['cutoff_snapshots'], depot_releases=physical['depot_releases'],
                  recovery_rounds=physical['cutoff_snapshots'])
    result['info']['objective'] = cost['total']
    result['audit'] = audit_joint_solution(problem, result)
    return result


def audit_joint_solution(problem, result, repeat=True):
    """Reconstruct assigned demand and replay; distrust cached solver metrics."""
    try:
        errors = []
        if result.get('model') != 6 or result.get('strategy') != STRATEGY or result.get('objective_mode') != 'cost':
            errors.append(dict(code='INVALID_JOINT_SCHEMA'))
        assignment = result.get('order_batch_assignment')
        pool = integer(result.get('container_pool_size'), 'container_pool_size', 1)
        if not problem.pool_min <= pool <= problem.pool_max:
            errors.append(dict(code='POOL_BOUNDS'))
        # Old fixed-calendar results remain auditable. Flexible-calendar
        # results must explicitly save the selected, bounded decisions.
        cutoffs = result.get('batch_cutoffs')
        if cutoffs is None and problem.optimize_cutoffs:
            raise ValueError('Missing optimized batch_cutoffs')
        checked = problem.validated_cutoffs(cutoffs)
        fixed = problem.project(assignment, pool, checked)
        if (result.get('cutoff_mode', 'fixed') != ('optimized' if problem.optimize_cutoffs else 'fixed')
                or result.get('shared_cutoff', False) != problem.shared_cutoff
                or ('cutoff_bounds' in result and result['cutoff_bounds'] != {
                    bid:list(bounds) for bid,bounds in problem.cutoff_bounds.items()})):
            errors.append(dict(code='CUTOFF_CONTRACT_MISMATCH'))
        expected = {b.id: bool(fixed.batch_demands[b.id]) for b in fixed.operating_policy.batches}
        if (result.get('delivery_batch_activation') != expected
                or result.get('delivery_batch_count') != sum(expected.values())):
            errors.append(dict(code='BATCH_ACTIVATION_MISMATCH'))
        if result.get('candidate_schedule') != joint_empty_result(problem, result.get('solver'))['candidate_schedule']:
            errors.append(dict(code='CANDIDATE_CALENDAR_MISMATCH'))
        from .audit import _audit
        physical = _audit(fixed, {**result, 'model':fixed.model_number,
                                 'info':{**result.get('info',{}), 'objective':result.get('total_distance')}}, repeat)
        errors.extend(physical['errors'])
        cost = problem.cost_profile.evaluate(physical, pool)
        claimed = result.get('info', {}).get('objective')
        if claimed is None or not math.isfinite(claimed) or not math.isclose(claimed, cost['total'], rel_tol=1e-7, abs_tol=1e-6):
            errors.append(dict(code='COST_OBJECTIVE_MISMATCH'))
        if result.get('cost_components') != cost['components'] or result.get('cost') != cost:
            errors.append(dict(code='COST_COMPONENT_MISMATCH'))
        if (result.get('economic_profile_hash') != problem.cost_profile.hash
                or result.get('evidence_hash') != problem.cost_profile.evidence_hash):
            errors.append(dict(code='ECONOMIC_PROFILE_MISMATCH'))
        if result.get('recovery_rounds') != physical['cutoff_snapshots']:
            errors.append(dict(code='RECOVERY_ROUND_MISMATCH'))
        physical.update(valid=not errors, errors=errors, objective=cost['total'],
                        total_distance=sum(physical['distances'].values()), cost=cost,
                        order_batch_assignment=assignment, container_pool_size=pool)
        return physical
    except (KeyError, TypeError, ValueError, IndexError, AttributeError, OverflowError) as exc:
        return dict(valid=False, errors=[dict(code='MALFORMED_JOINT_SOLUTION', detail=str(exc))])
