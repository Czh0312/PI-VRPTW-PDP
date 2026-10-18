"""Coordinated ownership proposals; distance ranks moves, never proves them."""


def consolidation_candidates(problem, assignment):
    """Close an occupied satellite or consolidate at a promising site.

    Several customers may need to move together before eliminating an E1
    stop pays off. Single-customer moves cannot cross that assignment barrier.
    All proposals still require the ordinary physical decoder and audit.
    """
    sites = list(problem.satellites)
    if len(sites) < 2 or not assignment:
        return []
    customers = sorted(assignment)
    distance = problem.distance
    costs = {c: {s: distance(s, c) + distance(c, s) for s in sites} for c in customers}
    candidates, seen = [], {tuple(sorted(assignment.items()))}

    def add(candidate):
        key = tuple(sorted(candidate.items()))
        if key not in seen:
            seen.add(key)
            candidates.append(candidate)

    occupied = sorted(set(assignment.values()),
                      key=lambda s: (sum(v == s for v in assignment.values()), s))
    for closed in occupied:
        add({c: (min((s for s in sites if s != closed), key=costs[c].get)
                 if assignment[c] == closed else assignment[c]) for c in customers})
    ranked = sorted(sites, key=lambda s: sum(costs[c][s] for c in customers)
                    + distance(problem.depot, s) + distance(s, problem.depot))
    for site in ranked[:3]:
        add({c: site for c in customers})
    return candidates
