"""M7/M8 calendar domains and independent reconstruction, without solvers.

Public instances own the allowed domain. Fixed-calendar children use the shared
joint cost/physical contract; only the public result carries the numbered model.
"""
from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from dataclasses import replace
import math

from models.common.cycle.joint import JointBatchInstance, audit_joint_solution, STRATEGY
from models.common.cycle.policy import Batch, integer, nonnegative
from models.common.cycle.batch_counts import batch_count_domain, equal_batch_clock

SCHEDULE_MODES = {7: 'uniform_independent_cutoffs', 8: 'flexible_cycle'}
STRATEGIES = {7: 'independent_cutoff_cost_v1', 8: 'flexible_batch_cost_v1'}


class CalendarCycleInstance(JointBatchInstance):
    calendar_cycle = True

    def __init__(self, network, orders, policy, options, economic_config, *, model):
        if model not in SCHEDULE_MODES:
            raise ValueError('CalendarCycleInstance requires model 7 or 8')
        allowed = {'schedule_mode', 'batch_count_candidates', 'min_phase_minutes', 'pool_min', 'pool_max',
                   'loading_mode'}
        if not isinstance(options, dict) or set(options)-allowed:
            raise ValueError('Unknown M7/M8 joint_batch_options')
        mode = SCHEDULE_MODES[model]
        loading_mode = options.get('loading_mode', 'batch_start')
        if loading_mode not in ('batch_start', 'optimized') or (model != 7 and loading_mode != 'batch_start'):
            raise ValueError('Optimized loading is defined only for the M7 extended baseline')
        if options.get('schedule_mode', mode) != mode:
            raise ValueError(f'M{model} requires schedule_mode={mode}')
        super().__init__(network, orders, policy,
                         {k: options[k] for k in ('pool_min', 'pool_max', 'loading_mode') if k in options}, economic_config)
        if any(set(o.allowed_batch_ids) != set(self.batch_map) for o in self.orders):
            raise ValueError('Repartitioning requires every order to allow all source batches')
        minimum = nonnegative(options.get('min_phase_minutes', 1), 'min_phase_minutes')
        if minimum <= 0:
            raise ValueError('min_phase_minutes must be positive')
        self.minimum_phase_ticks = self.operating_policy.ticks(minimum)
        self.cycle_duration = self.operating_policy.cycle_end-self.operating_policy.cycle_start
        self.model_number = model
        self.schedule_mode = mode
        self.batch_count_candidates = batch_count_domain(options, self.cycle_duration, self.minimum_phase_ticks)
        self.options = deepcopy({**options, 'schedule_mode': mode})
        self._calendars = OrderedDict()
        self._counts = OrderedDict()

    @property
    def calendar_contract(self):
        p = self.operating_policy
        contract = dict(model=self.model_number, schedule_mode=self.schedule_mode,
                    batch_count_candidates=list(self.batch_count_candidates),
                    minimum_phase_ticks=self.minimum_phase_ticks, cycle_start=p.cycle_start,
                    cycle_end=p.cycle_end, time_tick_seconds=p.time_tick_seconds)
        if self.optimize_loading:
            contract['loading_mode'] = self.loading_mode
        return contract

    def balanced_boundaries(self, count):
        count = self.validate_count(count)
        p, _, factor = self.count_clock(count)
        return tuple(p.cycle_start+i*self.cycle_duration*factor//count for i in range(count+1))

    def count_clock(self, count):
        if self.model_number == 7:
            return equal_batch_clock(self, count)
        return self.operating_policy, self.orders, 1

    def validate_count(self, count):
        count = integer(count, 'cycle_batch_count', 1)
        if count not in self.batch_count_candidates:
            raise ValueError('Batch count is outside the configured search domain')
        return count

    def validated_boundaries(self, count, boundaries):
        count = self.validate_count(count)
        if not isinstance(boundaries, (list, tuple)) or len(boundaries) != count+1:
            raise ValueError('batch_boundaries must contain K+1 integer ticks')
        checked = tuple(integer(v, 'batch_boundary') for v in boundaries)
        p, _, factor = self.count_clock(count)
        if checked[0] != p.cycle_start or checked[-1] != p.cycle_end:
            raise ValueError('Batch calendar must cover the entire original cycle')
        if any(b-a < 2*self.minimum_phase_ticks*factor for a, b in zip(checked, checked[1:])):
            raise ValueError('Batch lengths must leave both minimum phases')
        if self.model_number == 7 and checked != self.balanced_boundaries(count):
            raise ValueError('M7 requires equal batch lengths')
        return checked

    def fixed_calendar(self, count, boundaries=None):
        if boundaries is None:
            boundaries = self.balanced_boundaries(count)
        key = self.validated_boundaries(count, boundaries)
        if key not in self._calendars:
            p, source_orders, factor = self.count_clock(count)
            minimum = self.minimum_phase_ticks*factor
            batches = tuple(Batch(f'b{i+1}', a, (a+b)//2, b)
                            for i, (a, b) in enumerate(zip(key, key[1:])))
            ids = tuple(b.id for b in batches)
            orders = [replace(o, batch=ids[0], allowed_batch_ids=ids) for o in source_orders]
            child = JointBatchInstance(self.network, orders, replace(p, batches=batches),
                        dict(pool_min=self.pool_min, pool_max=self.pool_max, optimize_cutoffs=True,
                             min_phase_ticks=minimum, loading_mode=self.loading_mode),
                        self.cost_profile.config, _physical_source=self)
            child.calendar_model_number = self.model_number
            self._calendars[key] = child
            if len(self._calendars) > 64:
                self._calendars.popitem(last=False)
        self._calendars.move_to_end(key)
        return self._calendars[key]

    def for_count(self, count):
        count = self.validate_count(count)
        if self.model_number == 7:
            return self.fixed_calendar(count)
        if count not in self._counts:
            self._counts[count] = VariableCalendarInstance(self, count)
            if len(self._counts) > 8:
                self._counts.popitem(last=False)
        self._counts.move_to_end(count)
        return self._counts[count]

    def selected_problem(self, result):
        return self.fixed_calendar(result['cycle_batch_count'], result['batch_boundaries'])


class VariableCalendarInstance(JointBatchInstance):
    """One K's complete integer boundary domain, consumed by the MILP only."""
    variable_calendar = True

    def __init__(self, parent, count):
        reference = parent.fixed_calendar(count)
        super().__init__(reference.network, reference.orders, reference.operating_policy,
                         reference.options, reference.cost_profile.config, _physical_source=parent)
        self.parent, self.count = parent, count
        self.calendar_model_number = 8
        self.minimum_phase_ticks = minimum = parent.minimum_phase_ticks
        p = self.operating_policy
        self.start_bounds = {b.id: ((p.cycle_start, p.cycle_start) if i == 0 else
                                  (p.cycle_start+2*minimum*i, p.cycle_end-2*minimum*(count-i)))
                             for i, b in enumerate(p.batches)}
        self.end_bounds = {b.id: ((p.cycle_end, p.cycle_end) if i == count-1 else
                                self.start_bounds[p.batches[i+1].id])
                           for i, b in enumerate(p.batches)}
        self.cutoff_bounds = {b.id: (self.start_bounds[b.id][0]+minimum,
                                    self.end_bounds[b.id][1]-minimum) for b in p.batches}
        # Never screen against the reference equal calendar. In particular a
        # late release may become feasible when the batch boundary moves.
        self.legal_batches = {o.id: tuple(b.id for b in p.batches
                                         if self.start_bounds[b.id][1] >= o.release_time)
                              for o in self.orders}

    def with_boundaries(self, boundaries):
        return self.parent.fixed_calendar(self.count, boundaries)

    def project(self, *args, **kwargs):
        raise ValueError('Resolve variable batch boundaries before physical projection')


def attach_calendar_decisions(problem, result, count, boundaries):
    boundaries = problem.validated_boundaries(count, boundaries)
    durations = [b-a for a, b in zip(boundaries, boundaries[1:])]
    offsets = {f'b{i+1}': result['batch_cutoffs'][f'b{i+1}']-a for i, a in enumerate(boundaries[:-1])}
    result.update(model=problem.model_number, strategy=STRATEGIES[problem.model_number],
                  model_module=f'models.routing.model_{problem.model_number}.{result["solver"]}',
                  schedule_mode=problem.schedule_mode, batch_mode=problem.schedule_mode,
                  calendar_config=problem.calendar_contract, cycle_batch_count=count,
                  batch_boundaries=list(boundaries), batch_durations_ticks=durations,
                  cutoff_offsets_ticks=offsets,
                  cutoff_ratios={f'b{i+1}': offsets[f'b{i+1}']/d for i, d in enumerate(durations)},
                  actual_recovery_round_count=len({row['batch_id'] for row in result['recovery_rounds']
                                                   if row['quantity'] > 0}))
    return result


def audit_calendar_solution(problem, result, repeat=True):
    try:
        if (result.get('model') != problem.model_number or result.get('strategy') != STRATEGIES[problem.model_number]
                or result.get('schedule_mode') != problem.schedule_mode or result.get('batch_mode') != problem.schedule_mode
                or result.get('calendar_config') != problem.calendar_contract):
            raise ValueError('Calendar model/domain contract mismatch')
        child = problem.selected_problem(result)
        durations = [b.end-b.start for b in child.operating_policy.batches]
        offsets = {b.id: result['batch_cutoffs'][b.id]-b.start for b in child.operating_policy.batches}
        if result.get('batch_durations_ticks') != durations or result.get('cutoff_offsets_ticks') != offsets:
            raise ValueError('Calendar duration/offset metadata mismatch')
        ratios = result['cutoff_ratios']
        if set(ratios) != set(offsets) or any(isinstance(ratios[b.id], bool) or
                not math.isclose(ratios[b.id], offsets[b.id]/(b.end-b.start), rel_tol=1e-12, abs_tol=1e-12)
                for b in child.operating_policy.batches):
            raise ValueError('Calendar cutoff ratio mismatch')
        physical = audit_joint_solution(child, {**result, 'model': 6, 'strategy': STRATEGY}, repeat)
        recovery_count = len({row['batch_id'] for row in physical.get('cutoff_snapshots', [])
                              if row['quantity'] > 0})
        if result.get('actual_recovery_round_count') != recovery_count:
            physical['errors'].append(dict(code='CALENDAR_RECOVERY_COUNT_MISMATCH'))
            physical['valid'] = False
        physical.update(cycle_batch_count=len(durations), batch_durations_ticks=durations)
        return physical
    except (KeyError, TypeError, ValueError, IndexError, AttributeError, OverflowError) as exc:
        return dict(valid=False, errors=[dict(code='MALFORMED_CALENDAR_SOLUTION', detail=str(exc))])
