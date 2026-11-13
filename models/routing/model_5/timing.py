"""Remove unpriced waiting from an audited flexible-batch schedule."""

from models.common.cycle.joint import lift_result
from models.common.cycle.timing import earliest_trip_schedule


def close_schedule(problem, count, result):
    """Rebuild the final batch end from physical completion and its cutoff."""
    if not problem.no_work_window:
        return result
    batches = result['batches']
    bounds = [b['start'] for b in batches] + [batches[-1]['end']]
    assignment, pool = result['order_batch_assignment'], result['container_pool_size']
    cutoffs = dict(result['batch_cutoffs'])
    last = batches[-1]['id']
    child = problem.fixed_calendar(count, bounds)
    fixed = child.project(assignment, pool, cutoffs)
    policy, step = fixed.operating_policy, child.calendar_decision_step
    minimum = -(-child.options['min_phase_ticks'] // step)*step

    if all(problem.cost_profile.rates[f'{layer}_work_cost_per_minute'] == 0 for layer in ('e1', 'e2')):
        # Preserve return eligibility at every earlier cutoff. For the final
        # round, the earliest cutoff includes all remaining E2 unloads and E1 deliveries.
        raw = earliest_trip_schedule(fixed, result)
        ready = [t['return_complete'] for t in raw['e2_trips'] if any(e['kind'] == 'p' for e in t['events'])]
        ready.extend(e['start']+fixed.service(e['site_id']) for t in raw['e1_trips']
                     if t['batch_id'] == last for e in t['events'] if e['kind'] == 'd')
        cutoff = max(batches[-1]['start']+minimum, -(-max(ready) // step)*step)
        trial_cuts = {**cutoffs, last: min(cutoffs[last], cutoff)}
        trial_problem = child.project(assignment, pool, trial_cuts)
        raw = earliest_trip_schedule(trial_problem, raw)
        trial = lift_result(child, raw, assignment, pool, result['solver'], trial_cuts)
        if trial['audit']['valid']:
            result, cutoffs = trial, trial_cuts

    finish = problem.finish_tick(policy, result)
    end = max(-(-finish // step)*step, cutoffs[last]+minimum)
    if end >= bounds[-1]:
        return result
    shorter = problem.fixed_calendar(count, [*bounds[:-1], end])
    finished = lift_result(shorter, result, assignment, pool, result['solver'], cutoffs)
    return finished if finished['audit']['valid'] else result
