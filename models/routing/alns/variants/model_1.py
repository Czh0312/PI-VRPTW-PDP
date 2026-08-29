"""Model 1-specific ALNS evaluator and operators."""
# -*- coding: utf-8 -*-
"""
Model 1 (纯配送) 的 ALNS 求解器
"""
import random
import time

from models.routing.alns.types import (
    Route, Solution, FEAS_TOL, clean_empty_routes,
)
from models.routing.alns.engine import ALNSConfig, run_alns_search
from models.routing.alns.initial_solution import build_strong_initial_solution


# =====================================================================
#  可行性检查与目标值计算
# =====================================================================

class Evaluator:
    def __init__(self, problem, e1_tasks, e2_tasks_by_sat,
                 vehicles_per_satellite=None):
        self.problem = problem
        self.vehicles_per_satellite = (
            vehicles_per_satellite
            if vehicles_per_satellite is not None
            else problem.fleet.e2_per_sat_count
        )
        self.e1_task_map = {t.id: t for t in e1_tasks}
        self.e2_task_map = {}
        self.e2_task_sat = {}
        self.e2_tasks_by_sat = e2_tasks_by_sat
        for sat_id, tasks in e2_tasks_by_sat.items():
            for t in tasks:
                self.e2_task_map[t.id] = t
                self.e2_task_sat[t.id] = sat_id

        self.task_to_phys = {}
        for t in e1_tasks:
            self.task_to_phys[t.id] = t.cust
        for tasks in e2_tasks_by_sat.values():
            for t in tasks:
                self.task_to_phys[t.id] = t.cust

    def e2_task_satellite(self, task_id):
        return self.e2_task_sat.get(task_id)

    def e2_start_times(self, sol):
        return self.e1_delivery_departure_times(sol) or {}

    def phys(self, node_id):
        return self.task_to_phys.get(node_id, node_id)

    def dist(self, a, b):
        return self.problem.distance(self.phys(a), self.phys(b))

    def travel(self, a, b):
        return self.problem.travel_time(self.phys(a), self.phys(b))

    def service_time(self, node_id):
        phys_id = self.phys(node_id)
        node_obj = self.problem.nodes.get(phys_id)
        return getattr(node_obj, 'service_time', 0.0) if node_obj else 0.0

    def route_distance(self, route):
        path = route.full_path()
        total = 0.0
        for i in range(len(path) - 1):
            total += self.dist(path[i], path[i + 1])
        return total

    def total_cost(self, sol):
        cost = 0.0
        for r in sol.e1_routes:
            cost += self.route_distance(r)
        for sat_id, routes in sol.e2_routes.items():
            for r in routes:
                cost += self.route_distance(r)
        return cost

    def check_e1_route_feasible(self, route):
        total_w, total_v = 0.0, 0.0
        for nid in route.nodes:
            t = self.e1_task_map.get(nid)
            if t:
                total_w += t.w
                total_v += t.v
        if total_w > self.problem.e1_weight_cap + FEAS_TOL:
            return False
        if total_v > self.problem.e1_volume_cap + FEAS_TOL:
            return False

        current_time = 0.0
        prev = route.base
        for nid in route.nodes:
            travel = self.travel(prev, nid)
            current_time += travel
            t = self.e1_task_map.get(nid)
            if t:
                if current_time < t.tw[0]:
                    current_time = t.tw[0]
                if current_time > t.tw[1] + FEAS_TOL:
                    return False
                current_time += self.service_time(nid)
            prev = nid
        return True

    def e1_delivery_departure_times(self, sol):
        departure = {}
        for route in sol.e1_routes:
            current_time = 0.0
            prev = route.base
            for nid in route.nodes:
                current_time += self.travel(prev, nid)
                task = self.e1_task_map.get(nid)
                if task:
                    if current_time < task.tw[0]:
                        current_time = task.tw[0]
                    if current_time > task.tw[1] + FEAS_TOL:
                        return None
                    current_time += self.service_time(nid)
                    departure[task.cust] = current_time
                prev = nid
        return departure

    def e1_schedule(self, sol, pickup_ready=None):
        """Compatibility interface used by the shared strong constructor."""
        departure = self.e1_delivery_departure_times(sol)
        return departure, {} if departure is not None else None

    def simulate_e2_route(self, route, sat_id, start_time):
        total_w, total_v = 0.0, 0.0
        for nid in route.nodes:
            t = self.e2_task_map.get(nid)
            if t:
                total_w += t.w
                total_v += t.v
        if total_w > self.problem.e2_weight_cap + FEAS_TOL:
            return False, {}, None
        if total_v > self.problem.e2_volume_cap + FEAS_TOL:
            return False, {}, None

        current_time = 0.0
        prev = sat_id
        arrivals = {}
        for nid in route.nodes:
            travel = self.travel(prev, nid)
            current_time += travel
            abs_arrival = start_time + current_time
            t = self.e2_task_map.get(nid)
            if t:
                if abs_arrival < t.tw[0]:
                    current_time = t.tw[0] - start_time
                    abs_arrival = t.tw[0]
                if abs_arrival > t.tw[1] + FEAS_TOL:
                    return False, {}, None
                arrivals[nid] = abs_arrival
                current_time += self.service_time(nid)
            prev = nid
        return_time = start_time + current_time + self.travel(prev, sat_id)
        return True, arrivals, return_time

    def check_e2_route_feasible(self, route, sat_id, start_time):
        feasible, _, _ = self.simulate_e2_route(route, sat_id, start_time)
        return feasible

    def is_feasible(self, sol):
        if len(sol.e1_routes) > self.problem.e1_vehicle_count:
            return False

        visited_e1 = []
        for r in sol.e1_routes:
            if not self.check_e1_route_feasible(r):
                return False
            visited_e1.extend(r.nodes)
        required_e1 = set(self.e1_task_map)
        if set(visited_e1) != required_e1:
            return False
        if len(visited_e1) != len(required_e1):
            return False

        e2_start_by_sat = self.e1_delivery_departure_times(sol)
        if e2_start_by_sat is None:
            return False

        for sat_id, routes in sol.e2_routes.items():
            if len(routes) > self.vehicles_per_satellite:
                return False
            if routes and sat_id not in e2_start_by_sat:
                return False
            start_time = e2_start_by_sat.get(sat_id, 0.0)
            visited_e2 = []
            for r in routes:
                if not self.check_e2_route_feasible(r, sat_id, start_time):
                    return False
                visited_e2.extend(r.nodes)
            required = {t.id for t in self.e2_tasks_by_sat.get(sat_id, [])}
            if set(visited_e2) != required:
                return False
            if len(visited_e2) != len(required):
                return False
        return True


# =====================================================================
#  初始解构造
# =====================================================================

def build_initial_solution(problem, e1_tasks, e2_tasks_by_sat, evaluator):
    sol = Solution()
    depot_id = problem.depot.id
    sat_ids = [s.id for s in problem.satellites]

    remaining = list(e1_tasks)
    remaining.sort(key=lambda t: evaluator.dist(depot_id, t.cust))

    while remaining:
        route = Route(depot_id)
        cur_w, cur_v = 0.0, 0.0
        to_remove = []
        for t in remaining:
            if (cur_w + t.w <= problem.e1_weight_cap and
                    cur_v + t.v <= problem.e1_volume_cap):
                route.nodes.append(t.id)
                cur_w += t.w
                cur_v += t.v
                to_remove.append(t)
        for t in to_remove:
            remaining.remove(t)
        if route.nodes:
            sol.e1_routes.append(route)
        elif remaining:
            forced = remaining.pop(0)
            sol.e1_routes.append(Route(depot_id, [forced.id]))

    e2_start_by_sat = evaluator.e1_delivery_departure_times(sol) or {}

    for sat_id in sat_ids:
        tasks = list(e2_tasks_by_sat.get(sat_id, []))
        if not tasks:
            sol.e2_routes[sat_id] = []
            continue

        tasks.sort(key=lambda t: t.tw[0])
        routes = []
        unassigned = list(tasks)

        while unassigned:
            route = Route(sat_id)
            cur_w, cur_v = 0.0, 0.0
            cur_time = 0.0
            start_time = e2_start_by_sat.get(sat_id, 0.0)
            prev = sat_id
            assigned_this_route = []

            for t in unassigned:
                if cur_w + t.w > problem.e2_weight_cap:
                    continue
                if cur_v + t.v > problem.e2_volume_cap:
                    continue
                travel = evaluator.travel(prev, t.id)
                rel_arr = cur_time + travel
                arr = start_time + rel_arr
                if arr < t.tw[0]:
                    rel_arr = t.tw[0] - start_time
                    arr = t.tw[0]
                if arr > t.tw[1] + FEAS_TOL:
                    continue
                route.nodes.append(t.id)
                cur_w += t.w
                cur_v += t.v
                cur_time = rel_arr + evaluator.service_time(t.id)
                prev = t.id
                assigned_this_route.append(t)

            for t in assigned_this_route:
                unassigned.remove(t)

            if route.nodes:
                routes.append(route)
            else:
                if unassigned:
                    forced = unassigned.pop(0)
                    routes.append(Route(sat_id, [forced.id]))

        sol.e2_routes[sat_id] = routes

    sol.cost = evaluator.total_cost(sol)
    return sol


# =====================================================================
#  破坏算子
# =====================================================================

def random_removal(sol, evaluator, num_remove, echelon='e2'):
    removed = []
    if echelon == 'e2':
        all_nodes = []
        for sat_id, routes in sol.e2_routes.items():
            for r in routes:
                for nid in r.nodes:
                    all_nodes.append((sat_id, r, nid))
        random.shuffle(all_nodes)
        for sat_id, r, nid in all_nodes[:num_remove]:
            if nid in r.nodes:
                r.nodes.remove(nid)
                removed.append(nid)
    elif echelon == 'e1':
        all_nodes = []
        for r in sol.e1_routes:
            for nid in r.nodes:
                all_nodes.append((r, nid))
        random.shuffle(all_nodes)
        for r, nid in all_nodes[:num_remove]:
            if nid in r.nodes:
                r.nodes.remove(nid)
                removed.append(nid)
    return removed


def worst_removal(sol, evaluator, num_remove, echelon='e2'):
    savings = []
    if echelon == 'e2':
        for sat_id, routes in sol.e2_routes.items():
            for r in routes:
                path = r.full_path()
                for idx, nid in enumerate(r.nodes):
                    pos = idx + 1
                    if pos >= len(path) - 1:
                        continue
                    prev_n = path[pos - 1]
                    next_n = path[pos + 1]
                    saving = (evaluator.dist(prev_n, nid) +
                              evaluator.dist(nid, next_n) -
                              evaluator.dist(prev_n, next_n))
                    savings.append((saving, sat_id, r, nid))
    elif echelon == 'e1':
        for r in sol.e1_routes:
            path = r.full_path()
            for idx, nid in enumerate(r.nodes):
                pos = idx + 1
                if pos >= len(path) - 1:
                    continue
                prev_n = path[pos - 1]
                next_n = path[pos + 1]
                saving = (evaluator.dist(prev_n, nid) +
                          evaluator.dist(nid, next_n) -
                          evaluator.dist(prev_n, next_n))
                savings.append((saving, None, r, nid))

    savings.sort(key=lambda x: -x[0])
    removed = []
    for saving, sat_id, r, nid in savings[:num_remove]:
        if nid in r.nodes:
            r.nodes.remove(nid)
            removed.append(nid)
    return removed


def shaw_removal(sol, evaluator, num_remove, echelon='e2'):
    if echelon == 'e2':
        all_nodes = []
        for sat_id, routes in sol.e2_routes.items():
            for r in routes:
                for nid in r.nodes:
                    all_nodes.append((sat_id, r, nid))
        if not all_nodes:
            return []

        seed_sat, seed_r, seed_nid = random.choice(all_nodes)
        seed_task = evaluator.e2_task_map.get(seed_nid)

        similarities = []
        for sat_id, r, nid in all_nodes:
            if nid == seed_nid:
                continue
            t = evaluator.e2_task_map.get(nid)
            if not t or not seed_task:
                continue
            dist_sim = evaluator.dist(seed_nid, nid)
            tw_sim = abs(seed_task.tw[0] - t.tw[0])
            score = dist_sim + 0.1 * tw_sim
            similarities.append((score, sat_id, r, nid))

        similarities.sort(key=lambda x: x[0])
        removed = [seed_nid]
        seed_r.nodes.remove(seed_nid)

        for score, sat_id, r, nid in similarities[:num_remove - 1]:
            if nid in r.nodes:
                r.nodes.remove(nid)
                removed.append(nid)
        return removed
    else:
        return random_removal(sol, evaluator, num_remove, echelon)


def route_segment_removal(sol, evaluator, num_remove, echelon='e2'):
    """Remove one contiguous route segment to enable route-level reshaping."""
    if echelon == 'e2':
        candidates = [route for routes in sol.e2_routes.values()
                      for route in routes if route.nodes]
    else:
        candidates = [route for route in sol.e1_routes if route.nodes]
    if not candidates:
        return []
    route = random.choice(candidates)
    length = min(len(route.nodes), max(1, num_remove))
    start = random.randint(0, len(route.nodes) - length)
    removed = route.nodes[start:start + length]
    del route.nodes[start:start + length]
    clean_empty_routes(sol)
    return removed


# =====================================================================
#  修复算子
# =====================================================================

def greedy_insertion(sol, removed, evaluator, echelon='e2'):
    if echelon == 'e2':
        e2_start_by_sat = evaluator.e2_start_times(sol)
        for nid in removed:
            task = evaluator.e2_task_map.get(nid)
            if not task:
                continue
            sat_id = evaluator.e2_task_satellite(nid)
            if not sat_id:
                continue
            start_time = e2_start_by_sat.get(sat_id, 0.0)

            best_cost = float('inf')
            best_route = None
            best_pos = None

            routes = sol.e2_routes.get(sat_id, [])
            for r in routes:
                old_cost = evaluator.route_distance(r)
                for pos in range(len(r.nodes) + 1):
                    old_nodes = r.nodes[:]
                    r.nodes.insert(pos, nid)
                    if evaluator.check_e2_route_feasible(r, sat_id, start_time):
                        insert_cost = evaluator.route_distance(r) - old_cost
                        if insert_cost < best_cost:
                            best_cost = insert_cost
                            best_route = r
                            best_pos = pos
                    r.nodes = old_nodes

            if len(routes) < evaluator.vehicles_per_satellite:
                new_route = Route(sat_id, [nid])
                if evaluator.check_e2_route_feasible(
                        new_route, sat_id, start_time):
                    new_cost = evaluator.route_distance(new_route)
                    if new_cost < best_cost:
                        best_cost = new_cost
                        best_route = None
                        best_pos = None

            if best_pos is not None:
                best_route.nodes.insert(best_pos, nid)
            else:
                new_route = Route(sat_id, [nid])
                if sat_id not in sol.e2_routes:
                    sol.e2_routes[sat_id] = []
                sol.e2_routes[sat_id].append(new_route)

    elif echelon == 'e1':
        depot_id = evaluator.problem.depot.id
        for nid in removed:
            task = evaluator.e1_task_map.get(nid)
            if not task:
                continue

            best_cost = float('inf')
            best_route = None
            best_pos = None

            for r in sol.e1_routes:
                old_cost = evaluator.route_distance(r)
                for pos in range(len(r.nodes) + 1):
                    old_nodes = r.nodes[:]
                    r.nodes.insert(pos, nid)
                    if evaluator.check_e1_route_feasible(r):
                        insert_cost = evaluator.route_distance(r) - old_cost
                        if insert_cost < best_cost:
                            best_cost = insert_cost
                            best_route = r
                            best_pos = pos
                    r.nodes = old_nodes

            if len(sol.e1_routes) < evaluator.problem.e1_vehicle_count:
                new_route = Route(depot_id, [nid])
                if evaluator.check_e1_route_feasible(new_route):
                    new_cost = evaluator.route_distance(new_route)
                    if new_cost < best_cost:
                        best_cost = new_cost
                        best_route = None
                        best_pos = None

            if best_pos is not None:
                best_route.nodes.insert(best_pos, nid)
            else:
                new_route = Route(depot_id, [nid])
                sol.e1_routes.append(new_route)


def regret_insertion(sol, removed, evaluator, echelon='e2', k=2):
    if echelon == 'e2':
        e2_start_by_sat = evaluator.e2_start_times(sol)
        pending = list(removed)
        while pending:
            best_regret = -float('inf')
            best_nid = None
            best_route_info = None

            for nid in list(pending):
                task = evaluator.e2_task_map.get(nid)
                if not task:
                    pending.remove(nid)
                    continue

                sat_id = evaluator.e2_task_satellite(nid)
                if not sat_id:
                    pending.remove(nid)
                    continue
                start_time = e2_start_by_sat.get(sat_id, 0.0)

                insertion_costs = []
                routes = sol.e2_routes.get(sat_id, [])
                for r in routes:
                    old_cost = evaluator.route_distance(r)
                    for pos in range(len(r.nodes) + 1):
                        old_nodes = r.nodes[:]
                        r.nodes.insert(pos, nid)
                        if evaluator.check_e2_route_feasible(r, sat_id,
                                                             start_time):
                            c = evaluator.route_distance(r) - old_cost
                            insertion_costs.append((c, r, pos))
                        r.nodes = old_nodes

                if len(routes) < evaluator.vehicles_per_satellite:
                    new_route = Route(sat_id, [nid])
                    if evaluator.check_e2_route_feasible(
                            new_route, sat_id, start_time):
                        insertion_costs.append((
                            evaluator.route_distance(new_route), None, 0))

                if not insertion_costs:
                    continue

                insertion_costs.sort(key=lambda x: x[0])
                regret = 0.0
                for i in range(1, min(k, len(insertion_costs))):
                    regret += insertion_costs[i][0] - insertion_costs[0][0]

                if regret > best_regret:
                    best_regret = regret
                    best_nid = nid
                    best_route_info = insertion_costs[0]

            if best_nid is None:
                break

            if best_route_info is None:
                break
            pending.remove(best_nid)
            _, route, pos = best_route_info
            if route is not None:
                route.nodes.insert(pos, best_nid)
            else:
                sat_id = evaluator.e2_task_satellite(best_nid)
                if sat_id:
                    new_route = Route(sat_id, [best_nid])
                    if sat_id not in sol.e2_routes:
                        sol.e2_routes[sat_id] = []
                    sol.e2_routes[sat_id].append(new_route)

    elif echelon == 'e1':
        greedy_insertion(sol, removed, evaluator, echelon='e1')


# =====================================================================
#  ALNS 主循环
# =====================================================================

def solve_alns(problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite,
               max_iterations=5000, max_time=120, seed=42, verbose=True,
               strong_initial_solution=True, return_info=False,
               target_objective=None, target_tolerance=1e-6,
               alns_config_overrides=None):
    random.seed(seed)
    start_time = time.time()

    evaluator = Evaluator(problem, e1_tasks, e2_tasks_by_sat,
                          vehicles_per_satellite=vehicles_per_satellite)

    if strong_initial_solution:
        sol = build_strong_initial_solution(
            problem, e1_tasks, e2_tasks_by_sat, evaluator)
    else:
        sol = build_initial_solution(
            problem, e1_tasks, e2_tasks_by_sat, evaluator)
    if not evaluator.is_feasible(sol):
        if verbose:
            print("\n[WARN] Model 1 ALNS 初始解不满足全局时间/容量约束")
        info = {"objective": None, "runtime": time.time() - start_time,
                "iterations": 0, "feasible": False, "status": "NO_SOLUTION"}
        return (None, None, info) if return_info else (None, None)

    sol.cost = evaluator.total_cost(sol)
    best_sol = sol.copy()
    best_cost = sol.cost

    if verbose:
        print(f"\n{'=' * 60}")
        print(f"ALNS 求解器 (Model 1 纯配送)")
        print(f"{'=' * 60}")
        print(f"初始解目标值: {sol.cost:.2f}")
        print(f"E1 路径数: {len(sol.e1_routes)}")
        e2_count = sum(len(v) for v in sol.e2_routes.values())
        print(f"E2 路径数: {e2_count}")

    result = run_alns_search(
        initial_solution=sol,
        evaluator=evaluator,
        destroy_ops=[random_removal, worst_removal, shaw_removal,
                     route_segment_removal],
        repair_ops=[greedy_insertion, regret_insertion],
        config=ALNSConfig(max_iterations=max_iterations, max_time=max_time,
                          target_cost=target_objective,
                          target_tolerance=target_tolerance,
                          **(alns_config_overrides or {})),
        verbose=verbose,
        start_time=start_time,
    )
    best_sol = result.best_solution
    if best_sol is None:
        info = {"objective": None, "runtime": result.elapsed,
                "iterations": result.iterations, "feasible": False,
                "status": "NO_SOLUTION"}
        return (None, None, info) if return_info else (None, None)
    best_cost = result.best_cost
    elapsed = result.elapsed
    if verbose:
        print(f"\n{'=' * 60}")
        print(f"ALNS 求解完成")
        print(f"最优目标值: {best_cost:.2f}")
        print(f"总耗时: {elapsed:.2f} 秒")
        print(f"迭代次数: {result.iterations}")
        print(f"{'=' * 60}")

    e1_routes_out = [r.full_path() for r in best_sol.e1_routes]
    e2_routes_out = {}
    for sat_id, routes in best_sol.e2_routes.items():
        e2_routes_out[sat_id] = [r.full_path() for r in routes]

    if verbose:
        _print_solution(best_sol, evaluator)

    info = {
        "objective": float(best_cost), "runtime": float(elapsed),
        "iterations": int(result.iterations),
        "feasible": bool(evaluator.is_feasible(best_sol)),
        "status": "FEASIBLE" if evaluator.is_feasible(best_sol) else "NO_SOLUTION",
        "strong_initial_solution": bool(strong_initial_solution),
        "best_found_time": result.best_found_time,
        "target_hit_time": result.target_hit_time,
        "termination_reason": result.termination_reason,
        "restarts": result.restarts,
        "local_search_improvements": result.local_search_improvements,
        "alns_diagnostics": result.diagnostics,
    }
    return ((e1_routes_out, e2_routes_out, info) if return_info
            else (e1_routes_out, e2_routes_out))


# =====================================================================
#  辅助函数
# =====================================================================

def _simulate_load_m1(route, evaluator, task_map, base_id):
    init_w = sum(task_map[n].w for n in route.nodes if task_map.get(n))
    init_v = sum(task_map[n].v for n in route.nodes if task_map.get(n))
    W_load, V_load = init_w, init_v
    result = [(base_id, W_load, V_load)]
    for nid in route.nodes:
        t = task_map.get(nid)
        if t:
            W_load -= t.w
            V_load -= t.v
        result.append((nid, W_load, V_load))
    result.append((base_id, W_load, V_load))
    return result


def _print_solution(sol, evaluator):
    print("\n--- E1 路线 ---")
    for i, r in enumerate(sol.e1_routes):
        path = r.full_path()
        dist = evaluator.route_distance(r)
        print(f"  卡车 {i}: {' -> '.join(path)}  (距离={dist:.2f})")
        loads = _simulate_load_m1(r, evaluator, evaluator.e1_task_map, r.base)
        load_strs = []
        for idx, (n, Wv, Vv) in enumerate(loads):
            is_first = (idx == 0)
            is_last = (idx == len(loads) - 1)
            tag = n if is_first else f"{n}_arr" if is_last else str(n)
            load_strs.append(f"{tag}(W={Wv:.1f},V={Vv:.1f})")
        print(f"    载量: {' -> '.join(load_strs)}")

    print("\n--- E2 路线 ---")
    for sat_id, routes in sol.e2_routes.items():
        if not routes:
            continue
        print(f"  前置仓 {sat_id}:")
        for j, r in enumerate(routes):
            path = r.full_path()
            dist = evaluator.route_distance(r)
            print(f"    车辆 {j}: {' -> '.join(path)}  (距离={dist:.2f})")
            loads = _simulate_load_m1(r, evaluator, evaluator.e2_task_map, sat_id)
            load_strs = []
            for idx, (n, Wv, Vv) in enumerate(loads):
                is_first = (idx == 0)
                is_last = (idx == len(loads) - 1)
                tag = n if is_first else f"{n}_arr" if is_last else str(n)
                load_strs.append(f"{tag}(W={Wv:.1f},V={Vv:.1f})")
            print(f"      载量: {' -> '.join(load_strs)}")
