"""Conservative future edge-cost floors for elementary label pricing.

Allow every task as an intermediate, including visited/infeasible tasks and
revisits. This larger nonnegative graph is a relaxation of every suffix.
An interrupted shortest-path solve MUST discard its tentative upper bounds.
"""
import heapq
import math
import time


def completion_cost_floor(cost, inward, *, deadline=None):
    n = len(inward)
    if len(cost)!=n or any(len(row)!=n for row in cost):
        raise ValueError('Return-floor matrix dimensions differ')
    amounts = list(inward)+[v for row in cost for v in row]
    if any(not math.isfinite(v) or v<0 for v in amounts):
        raise ValueError('Return floors require finite nonnegative edge costs')
    dist = list(inward)
    heap = [(value,i) for i,value in enumerate(dist)]
    heapq.heapify(heap)
    while heap:
        if deadline is not None and time.perf_counter()>=deadline:
            return [0.0]*n,False
        value,j = heapq.heappop(heap)
        if value!=dist[j]:
            continue
        for i in range(n):
            proposed = cost[i][j]+value
            if proposed<dist[i]:
                dist[i] = proposed
                heapq.heappush(heap,(proposed,i))
    # A shortest nonnegative path has at most n edges after deleting cycles.
    # Give floating sums a conservative downward margin before using a floor.
    margin = 1e-10*max(1.0,n*max(amounts,default=0.0))
    return [max(0.0,value-margin) for value in dist],True
