"""Dense propagation checked against the standard-library graph ordering."""
from graphlib import CycleError, TopologicalSorter
import random

import pytest

from models.routing.solvers.alns.single_batch_timing import propagate_times


@pytest.mark.parametrize('seed', range(20))
def test_dense_times_match_independent_graphlib_longest_paths(seed):
    rng = random.Random(seed)
    nodes = [('task', i) for i in range(1+seed % 8)]
    edges = [(a, b, rng.randrange(20)) for i, a in enumerate(nodes)
             for b in nodes[i+1:] if rng.random() < .4]
    if edges:
        a, b, delta = edges[0]
        edges.append((a, b, delta+1))
    rng.shuffle(edges)
    rng.shuffle(nodes)
    bounds = {node: rng.random()*50 for node in nodes}
    expected = dict(bounds)
    parents = {node: {a for a, b, _ in edges if b == node} for node in nodes}
    for node in TopologicalSorter(parents).static_order():
        expected[node] = max([expected[node]]+[expected[a]+delta for a, b, delta in edges if b == node])
    saved = list(edges)
    assert propagate_times(bounds, edges) and bounds == expected and edges == saved


@pytest.mark.parametrize('edges', [
    [(0, 0, 0)], [(0, 1, 0), (1, 0, 0)], [(0, 1, 4), (1, 2, 5), (2, 1, 0)]])
def test_dense_times_reject_even_zero_duration_cycles(edges):
    bounds = {node: 0 for a, b, _ in edges for node in (a, b)}
    with pytest.raises(CycleError):
        TopologicalSorter({n: {a for a, b, _ in edges if b == n} for n in bounds}).prepare()
    assert not propagate_times(bounds, edges)


def test_dense_times_support_empty_graph_and_disconnected_bounds():
    assert propagate_times({}, [])
    bounds = {('start', 2): 31, ('end', 4): 50}
    assert propagate_times(bounds, []) and list(bounds.values()) == [31, 50]
