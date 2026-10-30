"""Experiment-local M8 minute decisions, with unchanged physical time units.

Install only in an isolated ALNS worker. All calendar/cutoff proposals are
projected BEFORE physical decoding; the independent result check is strict and
never repairs a returned solution. No saved incumbent participates.
"""
from __future__ import annotations

import math


def step(policy):
    return policy.ticks(1, rounding='exact')


def nearest(value, low, high, quantum):
    low = -(-low // quantum) * quantum
    high = high // quantum * quantum
    if low > high:
        raise ValueError('No whole-minute decision inside the allowed interval')
    # Deterministic nearest, ties forward; no physical travel time is rounded.
    return min(high, max(low, ((int(value) + quantum // 2) // quantum) * quantum))


def boundaries_on_grid(parent, count, boundaries):
    policy, _, factor = parent.count_clock(count)
    quantum = step(policy)
    start, end = policy.cycle_start, policy.cycle_end
    if len(boundaries) != count + 1 or boundaries[0] != start or boundaries[-1] != end:
        raise ValueError('Calendar must cover the original cycle')
    if start % quantum or end % quantum:
        raise ValueError('Experiment requires whole-minute cycle endpoints')
    minimum = -(-(2 * parent.minimum_phase_ticks * factor) // quantum) * quantum
    selected = [start]
    for index, value in enumerate(boundaries[1:-1], 1):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value:
            raise ValueError('Calendar proposals must be integer physical ticks')
        selected.append(nearest(value, selected[-1] + minimum,
                                end - minimum * (count - index), quantum))
    return tuple(selected + [end])


def cutoffs_on_grid(problem, cutoffs=None):
    cuts = problem.default_cutoffs if cutoffs is None else cutoffs
    if not isinstance(cuts, dict) or set(cuts) != set(problem.cutoff_bounds):
        raise ValueError('Cutoff proposal must cover every batch')
    quantum = step(problem.operating_policy)
    return {bid: nearest(value, *problem.cutoff_bounds[bid], quantum)
            for bid, value in cuts.items()}


def is_m8(problem):
    return (getattr(problem, 'calendar_model_number', None) == 8
            or (getattr(problem, 'calendar_cycle', False) and problem.model_number == 8))


def install():
    """Canonicalize proposals centrally, including starts, polish and restarts."""
    from models.common.cycle.calendar import CalendarCycleInstance
    from models.common.cycle.joint import JointBatchInstance
    from models.routing.model_8.alns.evaluator import JointEvaluator

    if getattr(CalendarCycleInstance, '_minute_experiment_installed', False):
        return
    old_boundaries = CalendarCycleInstance.validated_boundaries
    old_fixed = CalendarCycleInstance.fixed_calendar
    old_cutoffs = JointBatchInstance.validated_cutoffs
    old_evaluate = JointEvaluator.__call__
    old_contract = CalendarCycleInstance.calendar_contract.fget

    def validated_boundaries(self, count, boundaries):
        if self.model_number == 8:
            boundaries = boundaries_on_grid(self, count, boundaries)
        return old_boundaries(self, count, boundaries)

    def validated_cutoffs(self, cutoffs=None):
        if is_m8(self):
            cutoffs = cutoffs_on_grid(self, cutoffs)
        return old_cutoffs(self, cutoffs)

    def fixed_calendar(self, count, boundaries=None):
        child = old_fixed(self, count, boundaries)
        if self.model_number == 8:
            child.default_cutoffs = cutoffs_on_grid(child)
        return child

    def evaluate(self, assignment, state):
        if is_m8(self.problem):
            # Keep accepted/restarted state identical to the decoded decisions.
            state['cutoffs'] = self.problem.validated_cutoffs(state.get('cutoffs'))
        return old_evaluate(self, assignment, state)

    def contract(self):
        value = old_contract(self)
        if self.model_number == 8:
            value['calendar_decision_grid_seconds'] = 60
        return value

    CalendarCycleInstance.validated_boundaries = validated_boundaries
    CalendarCycleInstance.fixed_calendar = fixed_calendar
    CalendarCycleInstance.calendar_contract = property(contract)
    JointBatchInstance.validated_cutoffs = validated_cutoffs
    JointEvaluator.__call__ = evaluate
    CalendarCycleInstance._minute_experiment_installed = True

    # Canonicalize generated seeds before they become cache/state keys, rather
    # than only at the final decoder boundary. All dynamic references included.
    from models.routing.model_8.alns import construction, search, calendar_moves, calendar_polish
    old_initial = construction.initial_calendars
    old_move = calendar_moves.move_boundaries
    old_deadline = calendar_polish.deadline_calendar_seeds

    def initial(variable):
        seen = set()
        for proposal in old_initial(variable):
            selected = variable.parent.validated_boundaries(variable.count, proposal)
            if selected not in seen:
                seen.add(selected)
                yield selected

    def move(variable, boundaries, rng, operator, result=None):
        return variable.parent.validated_boundaries(variable.count,
                    old_move(variable, boundaries, rng, operator, result))

    def deadline(variable):
        for proposal, assignment, cuts in old_deadline(variable):
            proposal = variable.parent.validated_boundaries(variable.count, proposal)
            child = variable.parent.fixed_calendar(variable.count, proposal)
            yield proposal, assignment, child.validated_cutoffs(cuts)

    construction.initial_calendars = search.initial_calendars = initial
    calendar_moves.move_boundaries = search.move_boundaries = move
    calendar_polish.deadline_calendar_seeds = deadline


def check_result(problem, result):
    """Strict independent check: no normalization, projection or repair."""
    if result.get('info', {}).get('objective') is None:
        return dict(valid=False, not_applicable=True, errors=['NO_FEASIBLE_RESULT'])
    count = result.get('cycle_batch_count')
    if count not in (1, 2, 3, 4):
        return dict(valid=False, errors=['K_OUTSIDE_1_TO_4'])
    policy, _, _ = problem.count_clock(count)
    quantum = step(policy)
    boundaries = result.get('batch_boundaries')
    cuts = result.get('batch_cutoffs', {})
    errors = []
    def integral_minute(value):
        return (not isinstance(value, bool) and isinstance(value, (int, float))
                and math.isfinite(value) and int(value) == value and value % quantum == 0)
    if not isinstance(boundaries, list) or len(boundaries) != count + 1:
        errors.append('MISSING_BOUNDARIES')
    elif not all(integral_minute(v) for v in boundaries):
        errors.append('OFF_GRID_BOUNDARY')
    if set(cuts) != {f'b{i+1}' for i in range(count)} or not all(integral_minute(v) for v in cuts.values()):
        errors.append('OFF_GRID_OR_MISSING_CUTOFF')
    for batch in result.get('batches', []):
        if not all(integral_minute(batch[key]) for key in ('start', 'cutoff', 'end')):
            errors.append('OFF_GRID_BATCH_SCHEDULE')
    return dict(valid=not errors, errors=errors, decision_grid_seconds=60,
                physical_tick_seconds=policy.time_tick_seconds,
                physical_tick_subdivision=policy.time_tick_subdivision,
                cycle_batch_count=count, all_decisions_checked=True)
