"""Exact DAG propagation using dense indices; no schedule inputs are cached."""


def propagate_times(bounds, edges):
    """Update lower bounds in place; return False for any directed timing cycle.

    Preserve node/edge insertion order and the reference max/add arithmetic.
    Only dictionary access during the graph walk is replaced by list access.
    """
    nodes = list(bounds)
    indices = {node: i for i, node in enumerate(nodes)}
    values = list(bounds.values())
    children = [[] for _ in nodes]
    indegree = [0]*len(nodes)
    for parent, child, delta in edges:
        a, b = indices[parent], indices[child]
        children[a].append((b, delta))
        indegree[b] += 1
    queue = [i for i, degree in enumerate(indegree) if not degree]
    visited = 0
    while queue:
        node = queue.pop()
        visited += 1
        ready = values[node]
        for child, delta in children[node]:
            values[child] = max(values[child], ready+delta)
            indegree[child] -= 1
            if not indegree[child]:
                queue.append(child)
    bounds.update(zip(nodes, values))
    return visited == len(nodes)
