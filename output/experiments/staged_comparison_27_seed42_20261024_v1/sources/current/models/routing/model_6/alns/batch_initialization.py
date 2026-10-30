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
                distance = max(0, order.delivery_window[0]-batch.start)
                candidate = (tuple(updated), assignment+(bid,), temporal+distance)
                proposed.append(candidate)
        if not proposed:
            return []
        proposed.sort(key=lambda x: (max(x[0]), sum(v*v for v in x[0]), x[2], x[1]))
        beam = proposed[:width]
    return [{order.id: bid for order, bid in zip(orders, assignment)}
            for _, assignment, _ in beam[:limit]]
