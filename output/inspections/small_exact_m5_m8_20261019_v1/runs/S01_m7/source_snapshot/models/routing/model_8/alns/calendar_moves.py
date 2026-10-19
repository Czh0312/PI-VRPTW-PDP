"""Unequal-length calendar boundary proposals at a fixed batch count."""


def boundary_events(variable, result=None):
    p = variable.operating_policy
    events = {o.release_time for o in variable.orders}
    for o in variable.orders:
        outbound = (
            p.e1_load_time
            + variable.travel_bounds["outward"][variable.depot][o.satellite]
            + variable.service(o.satellite)
            + p.e2_dispatch_prep_time
            + variable.travel_bounds["outward"][o.satellite][o.customer]
        )
        events.add(o.delivery_window[1] - outbound)
        events.add(o.delivery_window[0] - outbound)
    if result is not None:
        for trip in result.get("e1_trips", []):
            events.add(trip["return_complete"] + p.cleaning_duration)
            events.add(trip["return_complete"] + p.e1_turnaround_time)
    return sorted(
        {
            t + delta
            for t in events
            for delta in (-1, 0, 1)
            if p.cycle_start < t + delta < p.cycle_end
        }
    )


def move_boundaries(variable, boundaries, rng, operator, result=None):
    k, minimum = variable.count, 2 * variable.minimum_phase_ticks
    if k == 1:
        return boundaries
    changed = list(boundaries)
    if operator == "redistribute_lengths":
        extra = boundaries[-1] - boundaries[0] - minimum * k
        breaks = [0] + sorted(rng.randint(0, extra) for _ in range(k - 1)) + [extra]
        for i in range(1, k):
            changed[i] = boundaries[0] + minimum * i + breaks[i]
    else:
        index = rng.randrange(1, k)
        lo, hi = boundaries[index - 1] + minimum, boundaries[index + 1] - minimum
        events = [
            t for t in boundary_events(variable, result) if lo <= t <= hi and t != changed[index]
        ]
        if operator == "align_boundary" and events:
            changed[index] = rng.choice(events)
        elif rng.random() < 0.5:
            step = max(1, (hi - lo) // 10)
            changed[index] = min(hi, max(lo, changed[index] + rng.choice((-step, -1, 1, step))))
        else:
            changed[index] = rng.randint(lo, hi)
    return variable.parent.validated_boundaries(k, changed)
