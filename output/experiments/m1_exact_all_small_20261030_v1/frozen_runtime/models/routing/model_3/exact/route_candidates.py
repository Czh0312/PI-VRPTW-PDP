"""Deterministic bounded improvement of fresh relaxed route candidates only."""
import time


def polish(groups, *, evaluate, distance, feasible, keep, deadline):
    groups = list(groups)
    moves, evaluations = 0, 0
    before = sum(row['distance'] for row in groups)

    def replace_delta(sequence, position, bit):
        left = sequence[position-1] if position else -1
        right = sequence[position+1] if position+1 < len(sequence) else -1
        old = sequence[position]
        return distance(left, bit)+distance(bit, right)-distance(left, old)-distance(old, right)

    while time.perf_counter() < deadline:
        proposal = None
        ranked = sorted(range(len(groups)), key=lambda i: (-groups[i]['distance'], i))
        for i in ranked:
            if proposal is not None or time.perf_counter() >= deadline:
                break
            sequence = groups[i]['sequence']
            for position, bit in enumerate(sequence):
                if proposal is not None or time.perf_counter() >= deadline:
                    break
                reduced = sequence[:position]+sequence[position+1:]
                left = evaluate(reduced) if reduced else dict(mask=0, distance=0., sequence=())
                evaluations += 1
                if left is None:
                    continue
                removal = left['distance']-groups[i]['distance']
                for j in ranked:
                    target = reduced if j == i else groups[j]['sequence']
                    for insertion in range(len(target)+1):
                        if time.perf_counter() >= deadline:
                            break
                        a = target[insertion-1] if insertion else -1
                        b = target[insertion] if insertion < len(target) else -1
                        delta = removal+distance(a, bit)+distance(bit, b)-distance(a, b)
                        if delta >= -1e-7:
                            continue
                        candidate = evaluate(target[:insertion]+(bit,)+target[insertion:])
                        evaluations += 1
                        if candidate is None:
                            continue
                        proposed = [candidate if k == j else left if k == i else row
                                    for k, row in enumerate(groups)]
                        proposed = [row for row in proposed if row['sequence']]
                        if feasible(proposed):
                            proposal = proposed
                            break
                    if proposal is not None or time.perf_counter() >= deadline:
                        break
        if proposal is None:
            for i in ranked:
                if proposal is not None or time.perf_counter() >= deadline:
                    break
                sequence = groups[i]['sequence']
                for a in range(len(sequence)-1):
                    for b in range(a+2, len(sequence)+1):
                        if time.perf_counter() >= deadline:
                            break
                        changed = sequence[:a]+tuple(reversed(sequence[a:b]))+sequence[b:]
                        length = sum(distance(left, right) for left, right in zip((-1,)+changed, changed+(-1,)))
                        if length >= groups[i]['distance']-1e-7:
                            continue
                        candidate = evaluate(changed)
                        evaluations += 1
                        if candidate is None:
                            continue
                        proposed = [candidate if k == i else row for k, row in enumerate(groups)]
                        if feasible(proposed):
                            proposal = proposed
                            break
                    if proposal is not None or time.perf_counter() >= deadline:
                        break
        if proposal is None:
            for i in ranked:
                if proposal is not None or time.perf_counter() >= deadline:
                    break
                first = groups[i]['sequence']
                for j in ranked:
                    if j <= i:
                        continue
                    second = groups[j]['sequence']
                    for a in range(len(first)+1):
                        if proposal is not None or time.perf_counter() >= deadline:
                            break
                        for b in range(len(second)+1):
                            if time.perf_counter() >= deadline:
                                break
                            pa, na = first[a-1] if a else -1, first[a] if a < len(first) else -1
                            pb, nb = second[b-1] if b else -1, second[b] if b < len(second) else -1
                            if distance(pa, nb)+distance(pb, na)-distance(pa, na)-distance(pb, nb) >= -1e-7:
                                continue
                            one, two = first[:a]+second[b:], second[:b]+first[a:]
                            left = evaluate(one) if one else dict(mask=0, distance=0., sequence=())
                            right = evaluate(two) if two else dict(mask=0, distance=0., sequence=())
                            evaluations += 2
                            if left is None or right is None:
                                continue
                            proposed = [left if k == i else right if k == j else row
                                        for k, row in enumerate(groups)]
                            proposed = [row for row in proposed if row['sequence']]
                            if feasible(proposed):
                                proposal = proposed
                                break
                    if proposal is not None or time.perf_counter() >= deadline:
                        break
        if proposal is None:
            for i in ranked:
                if proposal is not None or time.perf_counter() >= deadline:
                    break
                first = groups[i]['sequence']
                for j in ranked:
                    if j <= i:
                        continue
                    second = groups[j]['sequence']
                    for a, bit_a in enumerate(first):
                        if proposal is not None or time.perf_counter() >= deadline:
                            break
                        for b, bit_b in enumerate(second):
                            if time.perf_counter() >= deadline:
                                break
                            delta = replace_delta(first, a, bit_b)+replace_delta(second, b, bit_a)
                            if delta >= -1e-7:
                                continue
                            left = evaluate(first[:a]+(bit_b,)+first[a+1:])
                            right = evaluate(second[:b]+(bit_a,)+second[b+1:])
                            evaluations += 2
                            if left is None or right is None:
                                continue
                            proposed = [left if k == i else right if k == j else row
                                        for k, row in enumerate(groups)]
                            if feasible(proposed):
                                proposal = proposed
                                break
                    if proposal is not None or time.perf_counter() >= deadline:
                        break
        if proposal is None:
            break
        assert sum(row['distance'] for row in proposal) < sum(row['distance'] for row in groups)-1e-7
        groups = proposal
        moves += 1
        for row in groups:
            keep(row)
    return groups, dict(local_search_moves=moves, candidate_evaluations=evaluations,
                        initial_candidate_distance=before,
                        final_candidate_distance=sum(row['distance'] for row in groups))
