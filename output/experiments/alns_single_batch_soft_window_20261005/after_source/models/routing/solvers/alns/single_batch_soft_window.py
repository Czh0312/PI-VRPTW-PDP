"""Experimental search score; decoded feasibility and output rules stay hard."""
import math


def bounded_window_score(problem, raw):
    """Replace only the isolated, bounded E2-window search penalty.

    Units are seconds for the cap and minutes for the linear penalty. This is
    not a feasibility relaxation: preserve the original violation list/result.
    """
    score, result, issues = raw
    if not issues or set(issues) != {'E2_TIME_WINDOW'}:
        return raw
    distance = result['info'].get('objective')
    if distance is None or not math.isfinite(distance):
        return raw
    p = problem.operating_policy
    late = []
    def seconds_after(actual, due):
        delta = (actual-due)*p.time_tick_seconds
        return max(0, delta) if math.isfinite(delta) else delta
    for trip in result['e2_trips']:
        late.append(seconds_after(trip['departure'], p.cycle_end))
        for event in trip['events']:
            order = problem.order_map[event['order_id']]
            due = order.delivery_window[1] if event['kind'] == 'd' else order.pickup_window[1]
            if event['kind'] == 'd':
                due = min(due, p.batches[0].cutoff-order.service_time)
            late.append(seconds_after(event['start'], due))
        late.append(seconds_after(trip['return_complete'], p.cycle_end-p.e2_turnaround_time))
    if not late or not all(math.isfinite(value) for value in late) or not 0 < max(late) <= 300:
        return raw
    return distance + 10*sum(late)/60, result, issues


class BoundedWindowCache:
    """Delegate immutable decoding/cache behavior; alter search score only."""
    def __init__(self, problem, base):
        self.problem, self.base = problem, base
        self.eligible_scores = 0
        self.last_softened = False

    def __getattr__(self, name):
        return getattr(self.base, name)

    def __call__(self, state):
        raw = self.base(state)
        adjusted = bounded_window_score(self.problem, raw)
        self.last_softened = adjusted is not raw
        if adjusted is not raw:
            self.eligible_scores += 1
        return adjusted
