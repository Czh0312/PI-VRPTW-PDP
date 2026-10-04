"""E1 task relocation/swap in the same joint search as E2 routes and handoffs."""
from copy import deepcopy
import time

from .neighborhoods import _insert_group
from .state import TOL


def e1_move(e, current, rng, operator, deadline, beam=6):
    paths = current['e1_routes']
    owners = {n: k for k, route in enumerate(paths) for n in route[1:-1]}
    if not owners:
        return None, None
    anchor = rng.choice(list(owners))
    donor = owners[anchor]
    proposals = []
    group = [anchor]
    if operator == 'e1_swap' and len(owners) > 1:
        other = rng.choice([n for n in owners if n != anchor])
        trial = deepcopy(current)
        ra, rb = trial['e1_routes'][donor], trial['e1_routes'][owners[other]]
        ia, ib = ra.index(anchor), rb.index(other)
        ra[ia], rb[ib] = other, anchor
        group.append(other)
        proposals.append(trial)
    else:
        receiver = rng.randrange(len(paths))
        if rng.random() < .3:
            paired = e.e1[anchor].cust + ('p' if e.e1[anchor].kind == 'd' else 'd')
            if paired in owners:
                group.append(paired)
        removed = deepcopy(current)
        for route in removed['e1_routes']:
            route[:] = [n for n in route if n not in group]
        for route in _insert_group(e, removed['e1_routes'][receiver], group, beam):
            trial = deepcopy(removed)
            trial['e1_routes'][receiver] = route
            proposals.append(trial)
    best = None
    for trial in proposals:
        if time.perf_counter() >= deadline:
            break
        try:
            audit = e.audit(trial)
        except ValueError:
            continue
        if best is None or audit['objective'] < best[1]['objective'] - TOL:
            best = trial, audit
    return best, {'tasks': group, 'echelon': 'e1'}
