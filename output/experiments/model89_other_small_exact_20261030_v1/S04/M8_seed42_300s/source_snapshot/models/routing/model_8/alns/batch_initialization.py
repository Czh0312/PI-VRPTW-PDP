"""Balanced, time-aware assignment seeds; no exact solver or saved solutions."""


def balanced_assignments(problem, limit=8, width=32):
    if len(problem.orders) > 16 or len(problem.operating_policy.batches) < 2:
        return []
    batches = problem.operating_policy.batches
    positions = {b.id: i for i, b in enumerate(batches)}
    orders = sorted(problem.orders, key=lambda o: (
        len(problem.legal_batches[o.id]), -o.count, o.delivery_window[1], o.id))
    beam = [(tuple(0 for _ in batches), (), 0.)]
    for index, order in enumerate(orders):
        proposed = []
        remaining = len(orders)-index-1
        for loads, assignment, temporal in beam:
            for bid in problem.legal_batches[order.id]:
                updated = list(loads)
                updated[positions[bid]] += order.count
                if updated.count(0) > remaining:
                    continue
                batch = problem.batch_map[bid]
                p = problem.operating_policy
                outward = (p.e1_load_time
                    + problem.travel_bounds['outward'][problem.depot][order.satellite]
                    + problem.service(order.satellite) + p.e2_dispatch_prep_time
                    + problem.travel_bounds['outward'][order.satellite][order.customer])
                arrival = batch.start+outward
                # Reference-calendar lateness is a ranking penalty only.
                # Variable boundaries may still make this assignment legal.
                distance = (max(0, arrival-order.delivery_window[1])*(p.cycle_end-p.cycle_start+1)
                            + max(0, order.delivery_window[0]-arrival))
                candidate = (tuple(updated), assignment+(bid,), temporal+distance)
                proposed.append(candidate)
        if not proposed:
            return []
        proposed.sort(key=lambda x: (max(x[0]), sum(v*v for v in x[0]), x[2], x[1]))
        beam = proposed[:width]
    return [{order.id: bid for order, bid in zip(orders, assignment)}
            for _, assignment, _ in beam[:limit]]
