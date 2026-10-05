"""Equal-length cycle partition and a shared relative cutoff, independent of solvers."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import math

from .joint import JointBatchInstance, audit_joint_solution
from .policy import Batch, integer, nonnegative


class UniformCycleInstance(JointBatchInstance):
    uniform_cycle = True

    def __init__(self, network, orders, policy, options, economic_config):
        allowed = {'schedule_mode', 'batch_count_candidates', 'min_phase_minutes', 'pool_min', 'pool_max'}
        if not isinstance(options, dict) or set(options)-allowed:
            raise ValueError('Uniform cycle options cannot include fixed-slot cutoffs or assignments')
        if options.get('schedule_mode') != 'uniform_cycle':
            raise ValueError('Expected schedule_mode=uniform_cycle')
        super().__init__(network, orders, policy,
                         {key:options[key] for key in ('pool_min','pool_max') if key in options}, economic_config)
        # Old slot IDs cannot encode restrictions on a newly partitioned clock.
        # Accept only explicit freedom across the entire source calendar.
        if any(set(o.allowed_batch_ids) != set(self.batch_map) for o in self.orders):
            raise ValueError('Uniform cycle requires every order to allow all source batches; preserve restrictions explicitly')
        raw = options.get('batch_count_candidates', list(range(1,len(self.batch_map)+1)))
        if not isinstance(raw,(list,tuple)) or not raw:
            raise ValueError('batch_count_candidates must be a nonempty list')
        counts = [integer(k,'batch_count_candidate',1) for k in raw]
        if len(set(counts)) != len(counts):
            raise ValueError('Duplicate batch count candidate')
        self.batch_count_candidates = tuple(sorted(counts))
        minimum = nonnegative(options.get('min_phase_minutes',1), 'min_phase_minutes')
        if minimum <= 0:
            raise ValueError('min_phase_minutes must be positive')
        self.minimum_phase_ticks = self.operating_policy.ticks(minimum)
        self.cycle_duration = self.operating_policy.cycle_end-self.operating_policy.cycle_start
        for k in counts:
            if self.cycle_duration % k:
                raise ValueError(f'K={k}: cycle ticks must be divisible by K for exactly equal batches; choose finer time_tick_seconds')
            if self.cycle_duration//k < 2*self.minimum_phase_ticks:
                raise ValueError(f'K={k}: both phases must meet min_phase_minutes')
        self.options = deepcopy(options)
        self._count_problems = {}

    @property
    def uniform_contract(self):
        p = self.operating_policy
        return dict(schedule_mode='uniform_cycle', batch_count_candidates=list(self.batch_count_candidates),
                    minimum_phase_ticks=self.minimum_phase_ticks, cycle_start=p.cycle_start,
                    cycle_end=p.cycle_end, time_tick_seconds=p.time_tick_seconds)

    def for_count(self, count):
        count = integer(count,'cycle_batch_count',1)
        if count not in self.batch_count_candidates:
            raise ValueError('Batch count is outside the configured search domain')
        if count not in self._count_problems:
            p = self.operating_policy
            length = self.cycle_duration//count
            minimum = self.minimum_phase_ticks
            offset = min(max(length//2,minimum),length-minimum)
            batches = tuple(Batch(f'b{i+1}',p.cycle_start+i*length,
                                  p.cycle_start+i*length+offset,p.cycle_start+(i+1)*length)
                            for i in range(count))
            ids = tuple(b.id for b in batches)
            orders = [replace(o,batch=ids[0],allowed_batch_ids=ids) for o in self.orders]
            child = JointBatchInstance(self.network,orders,replace(p,batches=batches),
                        dict(pool_min=self.pool_min,pool_max=self.pool_max,optimize_cutoffs=True,
                             shared_cutoff=True,shared_min_phase_ticks=minimum),
                        self.cost_profile.config,_physical_source=self)
            self._count_problems[count] = child
        return self._count_problems[count]


def attach_uniform_decisions(problem, result, count):
    """Annotate the selected partition; never alter an extracted physical schedule."""
    child = problem.for_count(count)
    b = child.operating_policy.batches[0]
    offset = result['batch_cutoffs'][b.id]-b.start
    result.update(schedule_mode='uniform_cycle',batch_mode='uniform_cycle',cycle_batch_count=count,
                  batch_duration_ticks=b.end-b.start,shared_cutoff_offset_ticks=offset,
                  shared_cutoff_ratio=offset/(b.end-b.start),uniform_cycle_config=problem.uniform_contract,
                  actual_recovery_round_count=len({row['batch_id'] for row in result['recovery_rounds'] if row['quantity']>0}))
    return result


def audit_uniform_solution(problem, result, repeat=True):
    """Rebuild the equal calendar from K; reject cached/tampered phase contracts."""
    try:
        count = integer(result['cycle_batch_count'],'cycle_batch_count',1)
        child = problem.for_count(count)
        length = problem.cycle_duration//count
        offset = integer(result['shared_cutoff_offset_ticks'],'shared_cutoff_offset_ticks',1)
        if (result.get('schedule_mode') != 'uniform_cycle' or result.get('batch_mode') != 'uniform_cycle'
                or result.get('uniform_cycle_config') != problem.uniform_contract
                or result.get('batch_duration_ticks') != length
                or result.get('batch_cutoffs') != {b.id:b.start+offset for b in child.operating_policy.batches}):
            raise ValueError('Uniform cycle partition/cutoff contract mismatch')
        ratio = result['shared_cutoff_ratio']
        if isinstance(ratio,bool) or not math.isfinite(ratio) or not math.isclose(ratio,offset/length,rel_tol=1e-12,abs_tol=1e-12):
            raise ValueError('Shared cutoff ratio mismatch')
        audited = audit_joint_solution(child,result,repeat)
        recovery_count = len({row['batch_id'] for row in audited.get('cutoff_snapshots',[]) if row['quantity']>0})
        if result.get('actual_recovery_round_count') != recovery_count:
            audited['errors'].append(dict(code='UNIFORM_RECOVERY_COUNT_MISMATCH'))
            audited['valid'] = False
        audited.update(cycle_batch_count=count,batch_duration_ticks=length,shared_cutoff_offset_ticks=offset)
        return audited
    except (KeyError,TypeError,ValueError,IndexError,AttributeError,OverflowError) as exc:
        return dict(valid=False,errors=[dict(code='MALFORMED_UNIFORM_SOLUTION',detail=str(exc))])
