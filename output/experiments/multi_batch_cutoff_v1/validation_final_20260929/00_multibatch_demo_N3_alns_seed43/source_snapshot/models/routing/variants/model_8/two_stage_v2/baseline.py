"""Immutable, validated stage-one route snapshot; no Gurobi dependency."""
from collections import Counter
from copy import deepcopy
import hashlib
import json


def prepare_baseline(problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite,
                     routes, TFp=9999.0):
    e1, e2 = deepcopy(routes)
    satellites = {s.id for s in problem.satellites}
    if set(e2) - satellites:
        raise ValueError("M8 基础解包含未知前置仓")
    if len(e1) > problem.e1_vehicle_count:
        raise ValueError("M8 基础解超出 E1 车辆数")

    def check(paths, base, expected, limit):
        if len(paths) > limit:
            raise ValueError("M8 基础解超出车辆数")
        for path in paths:
            if len(path) < 3 or path[0] != base or path[-1] != base:
                raise ValueError("M8 基础路线必须从所属仓出发并返回，且非空")
        found = Counter(n for path in paths for n in path[1:-1])
        if found != Counter(t.id for t in expected):
            raise ValueError("M8 基础解必须恰好服务全部原始任务一次，不得包含交接节点")

    check(e1, problem.depot.id, e1_tasks, problem.e1_vehicle_count)
    for sat in satellites:
        e2.setdefault(sat, [])
        check(e2[sat], sat, e2_tasks_by_sat.get(sat, []), vehicles_per_satellite)
    distance = _check_schedule_and_capacity(problem, e1_tasks, e2_tasks_by_sat, e1, e2, TFp)
    serialized = json.dumps([e1, e2], sort_keys=True, ensure_ascii=False)
    return {
        "e1_routes": e1, "e2_routes": e2,
        "objective": distance,
        "route_fingerprint": hashlib.sha256(serialized.encode()).hexdigest(),
    }


def _check_schedule_and_capacity(problem, e1_tasks, e2_tasks_by_sat, e1, e2, tfp):
    """Longest-path feasibility of all fixed-route temporal dependencies.

    A greedy independent route simulation can reject feasible inter-vehicle
    waiting. These difference constraints propagate all two-echelon waits.
    """
    tasks = {t.id: t for t in e1_tasks}
    tasks.update({t.id: t for items in e2_tasks_by_sat.values() for t in items})
    lower = {n: max(0.0, t.tw[0]) for n, t in tasks.items()}
    upper = {n: t.tw[1] for n, t in tasks.items()}
    for t in e1_tasks:
        if t.kind == 'p' and tfp < 9000:
            lower[t.id] = max(lower[t.id], tfp)
    edges = []
    distance = 0.0

    def add_route(path, key, wc, vc):
        nonlocal distance
        w = sum(tasks[n].w for n in path[1:-1] if tasks[n].kind == 'd')
        v = sum(tasks[n].v for n in path[1:-1] if tasks[n].kind == 'd')
        loads = [(w, v)]
        start, end = ('start', key), ('end', key)
        lower[start] = lower[end] = 0.0
        previous, physical, service = start, path[0], 0.0
        for node in path[1:-1]:
            task = tasks[node]
            edges.append((previous, node, service + problem.travel_time(physical, task.cust)))
            distance += problem.distance(physical, task.cust)
            sign = -1 if task.kind == 'd' else 1
            w, v = w + sign * task.w, v + sign * task.v
            loads.append((w, v))
            previous, physical = node, task.cust
            service = float(getattr(problem.nodes[physical], 'service_time', 0.0))
        edges.append((previous, end, service + problem.travel_time(physical, path[-1])))
        distance += problem.distance(physical, path[-1])
        if any(w < -1e-6 or v < -1e-6 or w > wc + 1e-6 or v > vc + 1e-6
               for w, v in loads):
            raise ValueError('M8 基础解违反容量约束')
        return start, end

    for index, path in enumerate(e1):
        start, _ = add_route(path, ('e1', index), problem.e1_weight_cap, problem.e1_volume_cap)
        upper[start] = 0.0
    for sat, paths in e2.items():
        for index, path in enumerate(paths):
            start, end = add_route(path, (sat, index), problem.e2_weight_cap, problem.e2_volume_cap)
            if f'{sat}d' in tasks:
                edges.append((f'{sat}d', start, float(getattr(problem.nodes[sat], 'service_time', 0))))
            if f'{sat}p' in tasks:
                edges.append((end, f'{sat}p', 0.0))
    for task in tasks.values():
        delivery = f'{task.cust}d'
        if task.kind == 'p' and delivery in tasks:
            edges.append((delivery, task.id, float(getattr(problem.nodes[task.cust], 'service_time', 0))))
    for _ in range(len(lower) + 1):
        changed = False
        for left, right, elapsed in edges:
            value = lower[left] + elapsed
            if value > lower[right] + 1e-7:
                lower[right] = value
                changed = True
        if any(t > upper.get(n, float('inf')) + 1e-6 for n, t in lower.items()):
            raise ValueError('M8 基础解违反时间窗或两级衔接约束')
        if not changed:
            return float(distance)
    raise ValueError('M8 基础解存在不可行的时序环')

