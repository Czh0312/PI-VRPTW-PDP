"""Cutoff proposals and bounded refinement; full evaluation decides feasibility."""
import time

from models.routing.model_8.alns.state import clone_joint_state


def _cutoff_events(problem, cuts, result, bid):
    """Candidate breakpoints, not feasibility claims; the decoder replays each.

    Shared cutoffs collect events from *all* batches. Cleaning thresholds use
    the current route's end and can change when quantities/routes change.
    """
    p = problem.operating_policy
    lo, hi = problem.cutoff_bounds[bid]
    targets = set()

    def add(batch, value):
        if problem.shared_cutoff:
            value = problem.batch_map[bid].start + value - batch.start
        elif batch.id != bid:
            return
        for t in (value - 1, value, value + 1):
            if lo <= t <= hi:
                targets.add(t)

    for batch in p.batches if problem.shared_cutoff else (problem.batch_map[bid],):
        for trip in result["e2_trips"]:
            if any(e["kind"] == "p" for e in trip["events"]):
                add(batch, trip["return_complete"])
    for trip in result["e1_trips"]:
        batch = problem.batch_map[trip["batch_id"]]
        for event in trip["events"]:
            if event["kind"] == "d":
                add(batch, event["start"] + problem.service(event["site_id"]))
        end = trip["return_complete"]
        add(batch, cuts[batch.id] + batch.end - end)
        if any(e["kind"] == "p" for e in trip["events"]):
            deadlines = [b.start for b in p.batches if b.start > batch.start] + [p.cycle_end]
            for deadline in deadlines:
                add(batch, cuts[batch.id] + deadline - p.cleaning_duration - end)
    return targets


def _with_cutoff(problem, state, bid, selected):
    proposed = clone_joint_state(state)
    cuts = problem.validated_cutoffs(state.get("cutoffs"))
    if problem.shared_cutoff:
        offset = selected - problem.batch_map[bid].start
        cuts = {b.id: b.start + offset for b in problem.operating_policy.batches}
    else:
        cuts[bid] = selected
    proposed["cutoffs"] = problem.proposed_cutoffs(cuts)
    return proposed


def _polish_cutoff(problem, evaluator, assignment, state, deadline, limit=16):
    """Bounded descent over physical breakpoints; always retain the input state."""
    score, result, _ = evaluator(assignment, state)
    best = clone_joint_state(state)
    if result is None:
        return best
    cuts = problem.validated_cutoffs(state.get("cutoffs"))
    bids = list(cuts)[:1] if problem.shared_cutoff else list(cuts)
    candidates = []
    for bid in bids:
        targets = _cutoff_events(problem, cuts, result, bid) | set(problem.cutoff_bounds[bid])
        for target in targets - {cuts[bid]}:
            candidates.append((abs(target - cuts[bid]), bid, target))
    for _, bid, target in sorted(candidates)[:limit]:
        if time.perf_counter() >= deadline:
            break
        proposed = _with_cutoff(problem, state, bid, target)
        value, candidate, _ = evaluator(assignment, proposed)
        if candidate is not None and value < score - 1e-8:
            best, score = proposed, value
    return best


def _cutoff_move(problem, evaluator, assignment, state, rng, align=False):
    """Move a cutoff without restricting second-phase E2 recovery."""
    proposed = clone_joint_state(state)
    cuts = problem.validated_cutoffs(state.get("cutoffs"))
    movable = [bid for bid, (lo, hi) in problem.cutoff_bounds.items() if lo < hi]
    if not movable:
        return proposed
    bid = rng.choice(movable)
    lo, hi = problem.cutoff_bounds[bid]
    old = cuts[bid]
    targets = {
        lo,
        hi,
        max(lo, old - max(1, (hi - lo) // 8)),
        min(hi, old + max(1, (hi - lo) // 8)),
        rng.randint(lo, hi),
    }
    if align:
        _, result, _ = evaluator(assignment, state)
        if result is not None:
            targets.update(_cutoff_events(problem, cuts, result, bid))
    targets.discard(old)
    selected = rng.choice(sorted(targets)) if targets else old
    return _with_cutoff(problem, state, bid, selected)
